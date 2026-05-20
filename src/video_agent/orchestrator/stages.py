from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from video_agent.contracts import EVENT_LOG
from video_agent.operator import (
    _chatgpt_scenes_prompt,
    _chatgpt_seo_prompt,
    _chatgpt_script_prompt,
    _gemini_qa_prompt,
    extract_json_object,
    promote_operator_artifact,
    promote_operator_qa,
    write_operator_review,
)
from video_agent.utils.json_io import write_json as _write_json
from video_agent.orchestrator.job_state import load_job, save_job
from video_agent.orchestrator.orchestrator import _now
from video_agent.pipeline import OperatorRenderOptions, render_operator_job
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.logging import EventLogger

IDEA_FILE = "idea.json"
SCRIPT_PROMPT_PATH = Path("operator/chatgpt/script_prompt.md")
SCRIPT_RAW_PATH = Path("operator/chatgpt/script.raw.txt")
SCENES_PROMPT_PATH = Path("operator/chatgpt/scenes_prompt.md")
SCENES_RAW_PATH = Path("operator/chatgpt/scenes.raw.txt")
SEO_PROMPT_PATH = Path("operator/chatgpt/seo_prompt.md")
SEO_RAW_PATH = Path("operator/chatgpt/seo.raw.txt")
SCRIPT_QA_RAW_PATH = Path("operator/gemini/script_qa.raw.txt")
SCENES_QA_RAW_PATH = Path("operator/gemini/scenes_qa.raw.txt")
SEO_QA_RAW_PATH = Path("operator/gemini/seo_qa.raw.txt")


class StageInputMissingError(Exception):
    pass


def _complete_stage(job_dir: Path, stage_name: str, output: Path) -> None:
    state = load_job(job_dir)
    ts = _now()
    stage = state.stage(stage_name)
    if stage.started_at is None:
        stage.started_at = ts
    stage.status = "completed"
    stage.completed_at = ts
    next_pending = next((s for s in state.stages if s.status == "pending"), None)
    if next_pending is not None:
        state.current_stage = next_pending.name
    state.updated_at = ts
    save_job(job_dir, state)

    logger = EventLogger(job_dir / EVENT_LOG)
    logger.log(
        "STAGE_COMPLETED",
        {
            "job_id": state.job_id,
            "stage": stage_name,
            "output": str(output.relative_to(job_dir)),
        },
    )
    if next_pending is None:
        logger.log("JOB_COMPLETED", {"job_id": state.job_id})


def run_script_stage(job_dir: Path, channel_path: Path) -> Path:
    """Produce the ChatGPT script prompt for the job.

    Reads ``job_dir/idea.json`` and the channel YAML, renders the prompt
    via the existing v2 helper, writes ``operator/chatgpt/script_prompt.md``,
    marks the ``script`` stage completed, and emits a ``STAGE_COMPLETED``
    event so consumers of ``events.jsonl`` see the same shape as v2.
    """
    idea_path = job_dir / IDEA_FILE
    if not idea_path.exists():
        raise StageInputMissingError(f"Missing {idea_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if state.current_stage != "script":
        raise StageInputMissingError(
            f"Cannot run script stage from current_stage={state.current_stage!r}"
        )

    idea = read_json(idea_path)
    channel_config = read_yaml(channel_path)
    prompt_text = _chatgpt_script_prompt(channel_config, idea)

    output_path = job_dir / SCRIPT_PROMPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt_text, encoding="utf-8")

    _complete_stage(job_dir, "script", output_path)

    return output_path


def promote_script_stage(job_dir: Path, channel_path: Path, raw_response: str) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "script_promote":
        raise StageInputMissingError(
            f"Cannot run script_promote stage from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError("Missing raw ChatGPT script response.")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    raw_path = job_dir / SCRIPT_RAW_PATH
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw_response, encoding="utf-8")

    try:
        result = promote_operator_artifact(
            job_dir,
            "script",
            raw_path,
            channel_path=channel_path,
        )
    except ValueError as exc:
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "script_promote", result.output_path)
    return result.output_path


def run_scenes_stage(job_dir: Path, channel_path: Path) -> Path:
    script_path = job_dir / "script.json"
    if not script_path.exists():
        raise StageInputMissingError(f"Missing {script_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if state.current_stage != "scenes":
        raise StageInputMissingError(
            f"Cannot run scenes stage from current_stage={state.current_stage!r}"
        )

    script = read_json(script_path)
    channel_config = read_yaml(channel_path)
    prompt_text = _chatgpt_scenes_prompt(channel_config, script)

    output_path = job_dir / SCENES_PROMPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt_text, encoding="utf-8")

    _complete_stage(job_dir, "scenes", output_path)
    return output_path


def promote_scenes_stage(job_dir: Path, channel_path: Path, raw_response: str) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "scenes_promote":
        raise StageInputMissingError(
            f"Cannot run scenes_promote stage from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError("Missing raw ChatGPT scenes response.")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    raw_path = job_dir / SCENES_RAW_PATH
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw_response, encoding="utf-8")

    try:
        result = promote_operator_artifact(
            job_dir,
            "scenes",
            raw_path,
            channel_path=channel_path,
        )
    except ValueError as exc:
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "scenes_promote", result.output_path)
    return result.output_path


def run_seo_stage(job_dir: Path, channel_path: Path) -> Path:
    script_path = job_dir / "script.json"
    scenes_path = job_dir / "scenes.json"
    if not script_path.exists():
        raise StageInputMissingError(f"Missing {script_path}")
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if state.current_stage != "seo":
        raise StageInputMissingError(
            f"Cannot run seo stage from current_stage={state.current_stage!r}"
        )

    script = read_json(script_path)
    scenes = read_json(scenes_path)
    channel_config = read_yaml(channel_path)
    prompt_text = _chatgpt_seo_prompt(channel_config, script, scenes)

    output_path = job_dir / SEO_PROMPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(prompt_text, encoding="utf-8")

    _complete_stage(job_dir, "seo", output_path)
    return output_path


def promote_seo_stage(job_dir: Path, channel_path: Path, raw_response: str) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "seo_promote":
        raise StageInputMissingError(
            f"Cannot run seo_promote stage from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError("Missing raw ChatGPT SEO response.")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    raw_path = job_dir / SEO_RAW_PATH
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw_response, encoding="utf-8")

    try:
        result = promote_operator_artifact(
            job_dir,
            "seo",
            raw_path,
            channel_path=channel_path,
        )
    except ValueError as exc:
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "seo_promote", result.output_path)
    return result.output_path


def run_render_stage(job_dir: Path, channel_path: Path) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "render":
        raise StageInputMissingError(
            f"Cannot run render stage from current_stage={state.current_stage!r}"
        )
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    try:
        render_operator_job(
            OperatorRenderOptions(
                channel_path=channel_path,
                job_dir=job_dir,
                render=True,
                require_operator_qa=False,
            )
        )
    except (FileNotFoundError, ValueError) as exc:
        raise StageInputMissingError(str(exc)) from exc

    output_path = job_dir / "video.mp4"
    _complete_stage(job_dir, "render", output_path)
    return output_path


def run_review_stage(job_dir: Path) -> Path:
    state = load_job(job_dir)
    if state.current_stage != "review":
        raise StageInputMissingError(
            f"Cannot run review stage from current_stage={state.current_stage!r}"
        )

    try:
        output_path = write_operator_review(job_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise StageInputMissingError(str(exc)) from exc

    _complete_stage(job_dir, "review", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Gemini QA stages (script_qa, scenes_qa, seo_qa).
# ---------------------------------------------------------------------------

_QA_ARTIFACT_FILE = {
    "script": "script.json",
    "scenes": "scenes.json",
    "seo": "seo.json",
}
_QA_RAW_PATH = {
    "script": SCRIPT_QA_RAW_PATH,
    "scenes": SCENES_QA_RAW_PATH,
    "seo": SEO_QA_RAW_PATH,
}


def promote_qa_stage(job_dir: Path, artifact: str, raw_response: str) -> Path:
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
    raw_path.write_text(raw_response, encoding="utf-8")

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
            raise StageInputMissingError(str(exc)) from exc
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
    verdict = str(qa_payload.get("verdict", "")).upper()
    if verdict != "PASS":
        issues = qa_payload.get("issues") or qa_payload.get("required_changes") or []
        raise StageInputMissingError(
            f"Gemini QA verdict for {artifact} is {verdict or 'MISSING'}: {issues}"
        )

    _complete_stage(job_dir, stage_name, result.output_path)
    return result.output_path


# ---------------------------------------------------------------------------
# Auto-driven stages: orchestrator -> browser-worker -> ChatGPT.
# ---------------------------------------------------------------------------

PromptFn = Callable[[str], Awaitable[str]]
"""Async callable: takes a prompt string, returns the raw model response."""

SessionFn = Callable[[Sequence[str]], Awaitable[str]]
"""Async callable: takes a list of messages to send in one temp chat,
returns the last assistant response."""


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

    task = build_task_prompt(briefing_stage_name, prompt_text)

    raw_response = await session_fn([task])
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise StageInputMissingError(
            "browser-worker returned an empty response for "
            f"{promote_stage_name}"
        )
    try:
        return promoter(job_dir, channel_path, raw_response)
    except ValueError as exc:
        raise StageInputMissingError(
            f"Promotion failed for {promote_stage_name}: {exc}"
        ) from exc


async def auto_script_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    return await _auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SCRIPT_PROMPT_PATH,
        runner=run_script_stage,
        promoter=promote_script_stage,
        session_fn=session_fn,
        run_stage_name="script",
        promote_stage_name="script_promote",
        briefing_stage_name="script",
    )


async def auto_scenes_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    return await _auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SCENES_PROMPT_PATH,
        runner=run_scenes_stage,
        promoter=promote_scenes_stage,
        session_fn=session_fn,
        run_stage_name="scenes",
        promote_stage_name="scenes_promote",
        briefing_stage_name="scenes",
    )


async def auto_seo_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    return await _auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SEO_PROMPT_PATH,
        runner=run_seo_stage,
        promoter=promote_seo_stage,
        session_fn=session_fn,
        run_stage_name="seo",
        promote_stage_name="seo_promote",
        briefing_stage_name="seo",
    )


# ---------------------------------------------------------------------------
# Auto Gemini QA stages: orchestrator -> browser-worker -> Gemini.
# ---------------------------------------------------------------------------


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
    artifact_path = job_dir / _QA_ARTIFACT_FILE[artifact]
    if not artifact_path.exists():
        raise StageInputMissingError(f"Missing {artifact_path}")

    artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    base_prompt = _gemini_qa_prompt(artifact, artifact_payload)

    from video_agent.orchestrator.briefing import build_task_prompt

    task = build_task_prompt(stage_name, base_prompt)

    raw_response = await session_fn([task])
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise StageInputMissingError(
            f"browser-worker returned an empty Gemini QA response for {artifact}"
        )
    return promote_qa_stage(job_dir, artifact, raw_response)


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


# ---------------------------------------------------------------------------
# Rework loop: QA NEEDS_REWORK -> feed issues back to ChatGPT -> re-promote.
# ---------------------------------------------------------------------------


_QA_STAGE_FN = {
    "script": auto_script_qa_stage,
    "scenes": auto_scenes_qa_stage,
    "seo": auto_seo_qa_stage,
}

_ARTIFACT_PROMOTER = {
    "script": promote_script_stage,
    "scenes": promote_scenes_stage,
    "seo": promote_seo_stage,
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

    _reset_promote_and_qa(job_dir, artifact)

    issue_lines = "\n".join(f"- {i}" for i in issues) or "- (sin issues listadas)"
    change_lines = (
        "\n".join(f"- {c}" for c in required_changes)
        or "- (sin required_changes listadas)"
    )
    rework_msg = (
        f"# Rework del artefacto `{artifact}`\n"
        f"Tu artefacto anterior recibió verdict NEEDS_REWORK del revisor "
        f"(Gemini). Reescribe el artefacto JSON corrigiendo SOLO los puntos "
        f"a continuación. Mantén el mismo esquema, idioma es-419, job_id y "
        f"channel_id.\n\n"
        f"## Issues detectadas\n{issue_lines}\n\n"
        f"## Cambios requeridos\n{change_lines}\n\n"
        f"Devuelve UN SOLO objeto JSON válido del artefacto `{artifact}` "
        f"completo (no solo el diff). Sin markdown ni comentarios."
    )

    new_raw = await chatgpt_fn([rework_msg])
    if not isinstance(new_raw, str) or not new_raw.strip():
        raise StageInputMissingError(
            f"browser-worker returned an empty rework response for {artifact}"
        )

    promoter = _ARTIFACT_PROMOTER[artifact]
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
    gemini_fn: SessionFn,
) -> Path:
    """Run ``<artifact>_qa``; if NEEDS_REWORK, rework via ChatGPT and retry.

    Honours ``channel.yaml -> qa_rules.thresholds.max_retry_per_qa``
    (default 3). After all retries are exhausted, re-raises the last
    ``StageInputMissingError`` so /run-all halts cleanly with the
    failed QA's issues in the response detail.
    """
    qa_fn = _QA_STAGE_FN[artifact]
    max_retries = _max_retries_per_qa(channel_path)
    last_exc: StageInputMissingError | None = None
    for attempt in range(max_retries + 1):
        try:
            return await qa_fn(job_dir, channel_path, gemini_fn)
        except StageInputMissingError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            await auto_rework_artifact(
                artifact, job_dir, channel_path, chatgpt_fn
            )
    assert last_exc is not None
    raise last_exc
