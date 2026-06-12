from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from video_agent.contracts import ARTIFACT_SCRIPT, ARTIFACT_SCENES, ARTIFACT_SEO
from video_agent.operator import (
    _gemini_qa_prompt,
    extract_json_object,
    extract_json_objects,
    promote_operator_qa,
)
from video_agent.orchestrator.job_state import load_job, save_job
from video_agent.orchestrator.orchestrator import _now
from video_agent.utils.json_io import read_yaml, write_json as _write_json
from video_agent.storage.atomic import atomic_write_text

from video_agent.orchestrator.stages._shared import (
    StageInputMissingError,
    _resolve_artifact,
    _complete_stage,
    SCRIPT_RAW_PATH,
    SCENES_RAW_PATH,
    SEO_RAW_PATH,
    SCRIPT_QA_RAW_PATH,
    SCENES_QA_RAW_PATH,
    SEO_QA_RAW_PATH,
)

SessionFn = Callable[[Sequence[str]], Awaitable[str]]

_QA_ARTIFACT_FILE = {
    "script": ARTIFACT_SCRIPT,
    "scenes": ARTIFACT_SCENES,
    "seo": ARTIFACT_SEO,
}
_QA_RAW_PATH = {
    "script": SCRIPT_QA_RAW_PATH,
    "scenes": SCENES_QA_RAW_PATH,
    "seo": SEO_QA_RAW_PATH,
}

__all__ = [
    "_QA_ARTIFACT_FILE",
    "_QA_RAW_PATH",
    "promote_qa_stage",
    "_auto_run_then_promote",
    "_auto_qa",
    "auto_script_qa_stage",
    "auto_scenes_qa_stage",
    "auto_seo_qa_stage",
    "_QA_STAGE_FN",
    "_ARTIFACT_PROMOTER",
    "_ARTIFACT_RAW_PATH",
    "_reset_promote_and_qa",
    "auto_rework_artifact",
    "_max_retries_per_qa",
    "auto_qa_with_rework",
]


def promote_qa_stage(
    job_dir: Path,
    artifact: str,
    raw_response: str,
    *,
    channel_path: Path | None = None,
) -> Path:
    """Promote a raw Gemini QA response into ``operator/gemini/<art>_qa.json``.

    ``artifact`` is one of ``script``, ``scenes``, ``seo``. The stage
    name written to the job state is ``<artifact>_qa``. Verdict must
    be PASS to advance; on NEEDS_REWORK (or any other non-PASS value)
    the parsed QA JSON is still saved so the operator can inspect the
    issues, and ``StageInputMissingError`` is raised with the issue
    list so /run-all halts cleanly at this stage.
    """
    if artifact not in _QA_ARTIFACT_FILE:
        raise StageInputMissingError(f"Unsupported QA artifact: {artifact}")
    stage_name = f"{artifact}_qa"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError(f"Missing raw Gemini QA response for {artifact}")

    raw_path = job_dir / _QA_RAW_PATH[artifact]
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(raw_path, raw_response, encoding="utf-8")

    qa_output = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"

    try:
        result = promote_operator_qa(job_dir, artifact, raw_path)
    except ValueError as exc:
        # Verdict != PASS or shape invalid. We still want a parsed QA
        # file on disk so the operator can read the issues/required
        # changes without grepping raw text. Parse defensively; if the
        # raw response is not JSON at all, surface the original error.
        try:
            parsed = extract_json_object(raw_response)
        except Exception:
            # extract_json_object failed (e.g. truncated prefix from Gemini UI).
            # Try extract_json_objects which tolerates corrupt leading chunks.
            objects = extract_json_objects(raw_response)
            if not objects:
                raise StageInputMissingError(str(exc)) from exc
            # Use the last complete object (most likely the full response).
            parsed = objects[-1]
        qa_payload = {
            "artifact": artifact,
            "verdict": str(parsed.get("verdict", "")).upper() or "MISSING",
            "issues": parsed.get("issues") or [],
            "required_changes": (
                parsed.get("required_changes")
                or parsed.get("suggested_fixes")
                or []
            ),
            "scores": parsed.get("scores") or {},
        }
        qa_output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(qa_output, qa_payload)
        raise StageInputMissingError(
            f"Gemini QA verdict for {artifact} is "
            f"{qa_payload['verdict']}: {qa_payload['issues']}"
        ) from exc

    qa_payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    if artifact == "seo":
        # Late facade import: _enforce_seo_language_qa is in seo.py, accessed via facade
        from video_agent.orchestrator import stages as stages_pkg
        stages_pkg._enforce_seo_language_qa(
            job_dir,
            result.output_path,
            qa_payload,
            channel_path=channel_path,
        )
        qa_payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    elif artifact == "scenes":
        # Late facade import: _enforce_scenes_visual_prompt_english is in scenes.py
        from video_agent.orchestrator import stages as stages_pkg
        stages_pkg._enforce_scenes_visual_prompt_english(job_dir, result.output_path, qa_payload)
        qa_payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    verdict = str(qa_payload.get("verdict", "")).upper()
    if verdict != "PASS":
        issues = qa_payload.get("issues") or qa_payload.get("required_changes") or []
        raise StageInputMissingError(
            f"Gemini QA verdict for {artifact} is {verdict or 'MISSING'}: {issues}"
        )

    _complete_stage(job_dir, stage_name, result.output_path)
    return result.output_path


async def _auto_run_then_promote(
    *,
    job_dir: Path,
    channel_path: Path,
    prompt_path: Path,
    runner: Callable[[Path, Path], Path],
    promoter: Callable[[Path, Path, str], Path],
    session_fn: SessionFn,
    run_stage_name: str,
    promote_stage_name: str,
    briefing_stage_name: str,
) -> Path:
    """Generic auto-stage: render prompt, send task into the session, promote.

    The caller (typically /run-all) is responsible for opening the
    persistent temp chat and sending the initial briefing **once** at
    the start. This stage helper only builds and sends the
    stage-specific task message; the model already has the channel DNA
    in its context.

    If ``session_fn`` is the legacy one-shot ``run_session``, the
    standalone /stages/X/auto routes wrap it so that the initial
    briefing is sent as the first message of the same one-shot tab.
    """
    state = load_job(job_dir)
    if state.current_stage == run_stage_name:
        runner(job_dir, channel_path)
        state = load_job(job_dir)
    if state.current_stage != promote_stage_name:
        raise StageInputMissingError(
            f"Cannot auto-run {run_stage_name}/{promote_stage_name} from "
            f"current_stage={state.current_stage!r}"
        )

    if not prompt_path.exists():
        raise StageInputMissingError(f"Missing prompt file {prompt_path}")
    prompt_text = prompt_path.read_text(encoding="utf-8")

    from video_agent.orchestrator.briefing import build_task_prompt

    task = build_task_prompt(
        briefing_stage_name,
        prompt_text,
        channel_config=read_yaml(channel_path),
    )

    raw_response = await session_fn([task])
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise StageInputMissingError(
            "browser-worker returned an empty response for "
            f"{promote_stage_name}"
        )

    # Continuation loop: if the model truncated mid-JSON, send "Continúa"
    # and append until the JSON parses or we give up (max 4 continuations).
    from video_agent.operator import extract_json_objects

    _CONTINUE_MSG = (
        "Continúa exactamente desde donde te quedaste, "
        "sin repetir nada de lo anterior."
    )
    _max_continuations = 4
    for _attempt in range(_max_continuations):
        if extract_json_objects(raw_response):
            break  # valid JSON extracted — proceed to promote
        # Check if there's any JSON start; if not, don't bother continuing
        if "{" not in raw_response:
            break
        # Looks truncated — ask the model to continue
        continuation = await session_fn([_CONTINUE_MSG])
        if not isinstance(continuation, str) or not continuation.strip():
            break
        raw_response = raw_response + continuation
    else:
        pass  # exhausted continuations — let promoter decide

    max_promote_retries = 2
    for attempt in range(max_promote_retries + 1):
        try:
            return promoter(job_dir, channel_path, raw_response)
        except (ValueError, StageInputMissingError) as exc:
            if attempt >= max_promote_retries:
                raise StageInputMissingError(
                    f"Promotion failed for {promote_stage_name} after {attempt + 1} attempts: {exc}"
                ) from exc

            import logging
            logger = logging.getLogger("video_agent.orchestrator.stages")
            logger.warning(
                "Promote attempt %d failed for %s, re-sending prompt. Error: %s",
                attempt + 1,
                promote_stage_name,
                exc,
            )

            # Re-send the prompt
            raw_response = await session_fn([task])
            if not isinstance(raw_response, str) or not raw_response.strip():
                raise StageInputMissingError(
                    "browser-worker returned an empty response during retry for "
                    f"{promote_stage_name}"
                )

            # Re-run the continuation loop on the new response
            for _attempt in range(_max_continuations):
                if extract_json_objects(raw_response):
                    break
                if "{" not in raw_response:
                    break
                continuation = await session_fn([_CONTINUE_MSG])
                if not isinstance(continuation, str) or not continuation.strip():
                    break
                raw_response = raw_response + continuation


async def _auto_qa(
    *,
    job_dir: Path,
    channel_path: Path,
    artifact: str,
    session_fn: SessionFn,
) -> Path:
    stage_name = f"{artifact}_qa"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot auto-run {stage_name} from current_stage={state.current_stage!r}"
        )
    artifact_path = _resolve_artifact(job_dir, _QA_ARTIFACT_FILE[artifact])
    if not artifact_path.exists():
        raise StageInputMissingError(f"Missing {artifact_path}")

    artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    channel_config = read_yaml(channel_path)
    base_prompt = _gemini_qa_prompt(artifact, artifact_payload, channel_config)

    from video_agent.orchestrator.briefing import build_task_prompt

    task = build_task_prompt(stage_name, base_prompt, channel_config=channel_config)

    raw_response = await session_fn([task])
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise StageInputMissingError(
            f"browser-worker returned an empty Gemini QA response for {artifact}"
        )
    return promote_qa_stage(job_dir, artifact, raw_response, channel_path=channel_path)


async def auto_script_qa_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    return await _auto_qa(
        job_dir=job_dir,
        channel_path=channel_path,
        artifact="script",
        session_fn=session_fn,
    )


async def auto_scenes_qa_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    import os
    if os.environ.get("SCENES_SHARDED_GENERATION", "").strip() == "1":
        # Late facade import: auto_scenes_qa_stage_sharded is in sharding.py
        from video_agent.orchestrator import stages as stages_pkg
        return await stages_pkg.auto_scenes_qa_stage_sharded(job_dir, channel_path, session_fn)
    return await _auto_qa(
        job_dir=job_dir,
        channel_path=channel_path,
        artifact="scenes",
        session_fn=session_fn,
    )


async def auto_seo_qa_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    return await _auto_qa(
        job_dir=job_dir,
        channel_path=channel_path,
        artifact="seo",
        session_fn=session_fn,
    )


# populated after module body — forward references to same-file fns are safe
_QA_STAGE_FN: dict[str, Callable] = {}
_ARTIFACT_PROMOTER: dict[str, Callable] = {}
_ARTIFACT_RAW_PATH = {
    "script": SCRIPT_RAW_PATH,
    "scenes": SCENES_RAW_PATH,
    "seo": SEO_RAW_PATH,
}


def _reset_promote_and_qa(job_dir: Path, artifact: str) -> None:
    """Reset ``<artifact>_promote`` and ``<artifact>_qa`` to pending."""
    promote_name = f"{artifact}_promote"
    qa_name = f"{artifact}_qa"
    state = load_job(job_dir)
    for s in state.stages:
        if s.name in (promote_name, qa_name):
            s.status = "pending"
            s.started_at = None
            s.completed_at = None
            s.error = None
    state.current_stage = promote_name
    state.updated_at = _now()
    save_job(job_dir, state)


async def auto_rework_artifact(
    artifact: str,
    job_dir: Path,
    channel_path: Path,
    chatgpt_fn: SessionFn,
    validation_feedback: str | None = None,
) -> Path:
    """Send QA issues back to ChatGPT and re-promote the artifact.

    Reads ``operator/gemini/<artifact>_qa.json`` to extract issues and
    required_changes, resets the ``<artifact>_promote`` + ``<artifact>_qa``
    stages to pending, sends a rework message into the persistent
    ChatGPT tab, and re-runs the promoter with the new response.
    """
    qa_path = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"
    if not qa_path.exists():
        raise StageInputMissingError(
            f"Missing {qa_path}; cannot rework {artifact}"
        )
    qa_payload = json.loads(qa_path.read_text(encoding="utf-8"))
    issues = qa_payload.get("issues") or []
    required_changes = qa_payload.get("required_changes") or []
    channel_config = read_yaml(channel_path)
    expected_language = (
        (channel_config.get("seo") or {}).get("language")
        or (channel_config.get("audience") or {}).get("language")
        or "es-ES"
    )

    _reset_promote_and_qa(job_dir, artifact)

    issue_lines = "\n".join(f"- {i}" for i in issues) or "- (sin issues listadas)"
    change_lines = (
        "\n".join(f"- {c}" for c in required_changes)
        or "- (sin required_changes listadas)"
    )
    validation_block = ""
    if validation_feedback and validation_feedback.strip():
        validation_block = (
            "\n\n## Error de validación del intento anterior\n"
            f"{validation_feedback.strip()}\n"
            "Ese error es obligatorio de corregir antes de devolver el JSON."
        )
    artifact_rules = ""
    if artifact == "scenes":
        artifact_rules = (
            "\n\n## Reglas obligatorias para scenes\n"
            "- narration, caption y on_screen_text deben mantener el idioma del canal.\n"
            "- visual_prompt debe estar en inglés, preferiblemente ASCII, porque se usa para búsqueda en Pexels.\n"
            "- No uses palabras españolas dentro de visual_prompt."
        )
    rework_msg = (
        f"# Rework del artefacto `{artifact}`\n"
        f"Tu artefacto anterior recibió verdict NEEDS_REWORK del revisor "
        f"(Gemini). Reescribe el artefacto JSON corrigiendo SOLO los puntos "
        f"a continuación. Mantén el mismo esquema, idioma {expected_language}, job_id y "
        f"channel_id.\n\n"
        f"## Issues detectadas\n{issue_lines}\n\n"
        f"## Cambios requeridos\n{change_lines}\n\n"
        f"{validation_block}"
        f"{artifact_rules}\n\n"
        f"Devuelve UN SOLO objeto JSON válido del artefacto `{artifact}` "
        f"completo (no solo el diff). Sin markdown ni comentarios."
    )

    new_raw = await chatgpt_fn([rework_msg])
    if not isinstance(new_raw, str) or not new_raw.strip():
        raise StageInputMissingError(
            f"browser-worker returned an empty rework response for {artifact}"
        )

    # Late facade import: promoters (promote_script_stage etc.) live in other submodules
    from video_agent.orchestrator import stages as stages_pkg
    promoter = stages_pkg._ARTIFACT_PROMOTER[artifact]
    return promoter(job_dir, channel_path, new_raw)


def _max_retries_per_qa(channel_path: Path, default: int = 3) -> int:
    try:
        cfg = read_yaml(channel_path)
        return int(
            cfg.get("qa_rules", {}).get("thresholds", {}).get(
                "max_retry_per_qa", default
            )
        )
    except Exception:
        return default


async def auto_qa_with_rework(
    artifact: str,
    job_dir: Path,
    channel_path: Path,
    chatgpt_fn: SessionFn,
    qa_session_fn: SessionFn,
) -> Path:
    """Run ``<artifact>_qa``; if NEEDS_REWORK, rework via ChatGPT and retry.

    Honours ``channel.yaml -> qa_rules.thresholds.max_retry_per_qa``
    (default 3). After all retries are exhausted, re-raises the last
    ``StageInputMissingError`` so /run-all halts cleanly with the
    failed QA's issues in the response detail.
    """
    # Late facade import: _QA_STAGE_FN and _ARTIFACT_PROMOTER are populated at
    # facade level after all submodules are imported.
    from video_agent.orchestrator import stages as stages_pkg
    qa_fn = stages_pkg._QA_STAGE_FN[artifact]
    max_retries = _max_retries_per_qa(channel_path)
    last_exc: StageInputMissingError | None = None
    for attempt in range(max_retries + 1):
        try:
            state = load_job(job_dir)
            promote_stage = f"{artifact}_promote"
            if state.current_stage == promote_stage:
                raw_path = job_dir / _ARTIFACT_RAW_PATH[artifact]
                if not raw_path.exists():
                    raise StageInputMissingError(
                        f"Cannot promote {artifact}; missing raw response {raw_path}"
                    )
                stages_pkg._ARTIFACT_PROMOTER[artifact](
                    job_dir,
                    channel_path,
                    raw_path.read_text(encoding="utf-8"),
                )
            return await qa_fn(job_dir, channel_path, qa_session_fn)
        except StageInputMissingError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            # Only attempt rework if qa.json exists (QA ran but verdict=NEEDS_REWORK).
            # If qa.json is missing, the QA response itself was empty/invalid — skip
            # rework and retry qa_fn directly on the next iteration.
            qa_path = job_dir / "operator" / "gemini" / f"{artifact}_qa.json"
            if qa_path.exists():
                try:
                    await auto_rework_artifact(
                        artifact,
                        job_dir,
                        channel_path,
                        chatgpt_fn,
                        validation_feedback=str(exc),
                    )
                except StageInputMissingError:
                    # Rework failed (e.g. qa.json vanished) — retry qa_fn directly.
                    pass
    assert last_exc is not None
    raise last_exc


# Populate dicts after all functions are defined (avoids forward-ref issues).
# These are also re-populated by __init__.py after all submodule imports so
# both the local module and the facade dicts stay in sync.
_QA_STAGE_FN.update({
    "script": auto_script_qa_stage,
    "scenes": auto_scenes_qa_stage,
    "seo": auto_seo_qa_stage,
})
