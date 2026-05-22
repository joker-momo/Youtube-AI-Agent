from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from video_agent.contracts import EVENT_LOG, repo_root
from video_agent.operator import (
    _chatgpt_scenes_prompt,
    _chatgpt_seo_prompt,
    _chatgpt_script_prompt,
    _claude_qa_prompt,
    extract_json_object,
    extract_json_objects,
    get_scenes_qa_feedback,
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
SCRIPT_QA_RAW_PATH = Path("operator/claude/script_qa.raw.txt")
SCENES_QA_RAW_PATH = Path("operator/claude/scenes_qa.raw.txt")
SEO_QA_RAW_PATH = Path("operator/claude/seo_qa.raw.txt")


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
    qa_feedback = get_scenes_qa_feedback(job_dir)
    prompt_text = _chatgpt_scenes_prompt(channel_config, script, qa_feedback=qa_feedback)

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


def run_whisper_timestamps_stage(job_dir: Path) -> Path:
    """Run Whisper on narration.wav and write per-scene word segments.

    Reads ``jobs/<id>/assets/narration.wav`` (produced by assets_chatgpt),
    runs Whisper tiny model with word_timestamps=True, groups words into
    ~7-word chunks, maps chunks to scenes by cumulative duration offset,
    and writes ``jobs/<id>/whisper_timestamps.json``.
    """
    state = load_job(job_dir)
    if state.current_stage != "whisper_timestamps":
        raise StageInputMissingError(
            f"Cannot run whisper_timestamps stage from current_stage={state.current_stage!r}"
        )

    logger = EventLogger(job_dir / EVENT_LOG)
    logger.log("WHISPER_STAGE_PROGRESS", {"job_id": state.job_id, "step": "start"})

    narration_path = job_dir / "assets" / "narration.wav"
    if not narration_path.exists():
        import os
        from video_agent.stages.assets import prepare_assets

        channel_config_path = Path(
            os.environ.get(
                "CHANNEL_CONFIG",
                "/app/configs/vida-plena-45/channel.yaml",
            )
        )
        if not channel_config_path.exists():
            channel_config_path = repo_root() / "configs/vida-plena-45/channel.yaml"

        if not channel_config_path.exists():
            raise StageInputMissingError(
                f"Missing narration audio: {narration_path} and cannot auto-synthesize because channel config was not found."
            )

        channel_config = read_yaml(channel_config_path)
        style = read_json(repo_root() / channel_config["style_dna"]["path"])
        scenes_path = job_dir / "scenes.json"
        if not scenes_path.exists():
            raise StageInputMissingError(f"Missing {scenes_path}")
        scene_doc = read_json(scenes_path)

        logger.log(
            "WHISPER_STAGE_PROGRESS",
            {"job_id": state.job_id, "step": "synthesizing_narration_audio"},
        )
        prepare_assets(
            job_dir,
            style,
            scene_doc,
            visual_config=channel_config.get("visuals"),
            tts_config=channel_config.get("tts"),
            channel_id=channel_config["channel"]["id"],
        )
        logger.log(
            "WHISPER_STAGE_PROGRESS",
            {"job_id": state.job_id, "step": "narration_audio_ready"},
        )

    if not narration_path.exists():
        raise StageInputMissingError(f"Missing narration audio: {narration_path}")

    scenes_path = job_dir / "scenes.json"
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")

    import whisper  # lazy import — heavy dep

    scene_doc = read_json(scenes_path)
    scenes = scene_doc["scenes"]

    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {"job_id": state.job_id, "step": "loading_whisper_model_tiny"},
    )
    model = whisper.load_model("tiny")
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {"job_id": state.job_id, "step": "transcribing_audio"},
    )
    result = model.transcribe(
        str(narration_path),
        word_timestamps=True,
        language="es",
        fp16=False,
    )
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {"job_id": state.job_id, "step": "transcription_complete"},
    )

    # Flatten all words from all segments (cast np.float64 → float for JSON)
    all_words: list[dict] = []
    for seg in result.get("segments") or []:
        for w in seg.get("words") or []:
            all_words.append({"word": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"])})

    # Group words into ~7-word chunks with text+start+end
    CHUNK_SIZE = 7

    def _group_chunks(words: list[dict]) -> list[dict]:
        chunks = []
        for i in range(0, len(words), CHUNK_SIZE):
            group = words[i : i + CHUNK_SIZE]
            text = " ".join(w["word"] for w in group).strip()
            if text:
                chunks.append({
                    "text": text,
                    "start": group[0]["start"],
                    "end": group[-1]["end"],
                })
        return chunks

    all_chunks = _group_chunks(all_words)

    # Map chunks to scenes by cumulative audio offset
    scene_data = []
    offset = 0.0
    for scene in scenes:
        dur = float(scene.get("duration_sec") or 15)
        scene_end = offset + dur
        # Chunks whose midpoint falls within [offset, scene_end)
        scene_chunks = [
            c for c in all_chunks
            if offset <= (c["start"] + c["end"]) / 2 < scene_end
        ]
        # Rebase timestamps relative to this scene's audio offset
        rebased = [
            {
                "text": c["text"],
                "start": round(c["start"] - offset, 4),
                "end": round(c["end"] - offset, 4),
            }
            for c in scene_chunks
        ]
        scene_data.append({
            "scene_id": scene["id"],
            "audio_offset_sec": round(offset, 4),
            "word_segments": rebased,
        })
        offset = scene_end

    output = {"scenes": scene_data}
    output_path = job_dir / "whisper_timestamps.json"
    from video_agent.utils.json_io import write_json
    write_json(output_path, output)
    logger.log(
        "WHISPER_STAGE_PROGRESS",
        {"job_id": state.job_id, "step": "timestamps_written"},
    )

    _complete_stage(job_dir, "whisper_timestamps", output_path)
    return output_path


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
                stop_request_path=job_dir / ".stop_requested",
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
# Claude QA stages (script_qa, scenes_qa, seo_qa).
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
    """Promote a raw Claude QA response into ``operator/claude/<art>_qa.json``.

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
        raise StageInputMissingError(f"Missing raw Claude QA response for {artifact}")

    raw_path = job_dir / _QA_RAW_PATH[artifact]
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw_response, encoding="utf-8")

    qa_output = job_dir / "operator" / "claude" / f"{artifact}_qa.json"

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
            # extract_json_object failed (e.g. truncated prefix from Claude UI).
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
            f"Claude QA verdict for {artifact} is "
            f"{qa_payload['verdict']}: {qa_payload['issues']}"
        ) from exc

    qa_payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    verdict = str(qa_payload.get("verdict", "")).upper()
    if verdict != "PASS":
        issues = qa_payload.get("issues") or qa_payload.get("required_changes") or []
        raise StageInputMissingError(
            f"Claude QA verdict for {artifact} is {verdict or 'MISSING'}: {issues}"
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

    # Continuation loop: if the model truncated mid-JSON, send "Continúa"
    # and append until the JSON parses or we give up (max 4 continuations).
    import json as _json

    _CONTINUE_MSG = (
        "Continúa exactamente desde donde te quedaste, "
        "sin repetir nada de lo anterior."
    )
    _max_continuations = 4
    for _attempt in range(_max_continuations):
        try:
            _json.loads(raw_response.strip())
            break  # valid JSON — proceed to promote
        except _json.JSONDecodeError:
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
# Auto Claude QA stages: orchestrator -> browser-worker -> Claude.
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
    base_prompt = _claude_qa_prompt(artifact, artifact_payload)

    from video_agent.orchestrator.briefing import build_task_prompt

    task = build_task_prompt(stage_name, base_prompt)

    raw_response = await session_fn([task])
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise StageInputMissingError(
            f"browser-worker returned an empty Claude QA response for {artifact}"
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
# idea_research stage: vidIQ keyword gate before script.
# ---------------------------------------------------------------------------

RESEARCH_FILE = "research.json"
_DEFAULT_MIN_SCORE = 40
_DEFAULT_MAX_COMPETITION = "High"  # vidIQ labels: Low / Medium / High / Very High
_COMPETITION_RANK = {"low": 0, "medium": 1, "high": 2, "very high": 3}


def _competition_rank(label: str | None) -> int:
    return _COMPETITION_RANK.get((label or "").strip().lower(), 99)


def _research_gate_config(channel_path: Path) -> dict:
    try:
        cfg = read_yaml(channel_path)
        gate = cfg.get("research_gate") or {}
        return {
            "min_score": int(gate.get("min_score", _DEFAULT_MIN_SCORE)),
            "max_competition": gate.get("max_competition", _DEFAULT_MAX_COMPETITION),
        }
    except Exception:
        return {"min_score": _DEFAULT_MIN_SCORE, "max_competition": _DEFAULT_MAX_COMPETITION}


def _idea_keywords(idea: dict) -> list[str]:
    """Build 3-5 keyword variants from idea.topic + title_seed."""
    base = idea.get("topic", "").strip()
    seed = idea.get("title_seed", "").strip()
    keywords = []
    if base:
        keywords.append(base)
    if seed and seed.lower() != base.lower():
        keywords.append(seed)
    # short variant (first 6 words)
    words = base.split()
    if len(words) > 4:
        short = " ".join(words[:5])
        if short not in keywords:
            keywords.append(short)
    return keywords[:5]


async def auto_idea_research_stage(
    job_dir: Path,
    channel_path: Path,
    vidiq_fn,
) -> Path:
    """Score idea keywords via vidIQ and gate low-potential topics.

    ``vidiq_fn(keywords: list[str]) -> list[dict]`` is typically
    ``BrowserClient.run_vidiq_scores``. Returns ``research.json``.
    Raises ``StageInputMissingError`` when the best keyword score is
    below ``channel.yaml -> research_gate.min_score`` (default 40),
    so ``/run-all`` halts and the operator can choose a better idea.
    """
    stage_name = "idea_research"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )
    idea_path = job_dir / IDEA_FILE
    if not idea_path.exists():
        raise StageInputMissingError(f"Missing {idea_path}")

    idea = json.loads(idea_path.read_text(encoding="utf-8"))
    keywords = _idea_keywords(idea)
    if not keywords:
        raise StageInputMissingError("idea.json has no topic or title_seed to score")

    gate = _research_gate_config(channel_path)

    try:
        scores = await vidiq_fn(keywords)
    except Exception as exc:
        # vidIQ unavailable: skip gate, log warning, advance.
        EventLogger(job_dir / EVENT_LOG).log(
            "RESEARCH_VIDIQ_UNAVAILABLE",
            {"job_id": state.job_id, "error": str(exc)},
        )
        research = {
            "keywords": keywords,
            "scores": [],
            "gate": gate,
            "best_score": None,
            "verdict": "skipped",
            "note": f"vidIQ unavailable: {exc}",
        }
        output_path = job_dir / RESEARCH_FILE
        _write_json(output_path, research)
        _complete_stage(job_dir, stage_name, output_path)
        return output_path

    valid_scores = [s for s in scores if isinstance(s.get("score"), int)]
    best = max((s["score"] for s in valid_scores), default=None)

    verdict = "pass"
    block_reason = None
    if best is not None and best < gate["min_score"]:
        verdict = "blocked_low_score"
        block_reason = (
            f"Best keyword score {best} < min_score {gate['min_score']}. "
            "Choose a higher-demand topic."
        )

    research = {
        "keywords": keywords,
        "scores": scores,
        "gate": gate,
        "best_score": best,
        "verdict": verdict,
        "block_reason": block_reason,
    }
    output_path = job_dir / RESEARCH_FILE
    _write_json(output_path, research)

    EventLogger(job_dir / EVENT_LOG).log(
        "IDEA_RESEARCH_COMPLETE",
        {"job_id": state.job_id, "best_score": best, "verdict": verdict},
    )

    if verdict != "pass":
        raise StageInputMissingError(block_reason or "idea_research gate failed")

    _complete_stage(job_dir, stage_name, output_path)
    return output_path


# ---------------------------------------------------------------------------
# seo_vidiq stage: score + swap weak tags after seo_qa.
# ---------------------------------------------------------------------------

SEO_VIDIQ_REPORT_FILE = "seo_vidiq_report.json"
_DEFAULT_MIN_TAG_SCORE = 30


def _seo_vidiq_min_score(channel_path: Path) -> int:
    try:
        cfg = read_yaml(channel_path)
        return int(
            cfg.get("research_gate", {}).get("min_tag_score", _DEFAULT_MIN_TAG_SCORE)
        )
    except Exception:
        return _DEFAULT_MIN_TAG_SCORE


async def auto_seo_vidiq_stage(
    job_dir: Path,
    channel_path: Path,
    vidiq_fn,
) -> Path:
    """Score SEO tags via vidIQ and swap low-scoring ones for related suggestions.

    Reads ``seo.json``, scores every tag, replaces tags whose score is
    below ``channel.yaml -> research_gate.min_tag_score`` (default 30)
    with the highest-scoring related keyword from that tag's vidIQ panel.
    Writes the updated ``seo.json`` and a ``seo_vidiq_report.json``.

    vidIQ failures are soft: the stage always completes; a ``note``
    field records any errors so the operator can review.
    """
    stage_name = "seo_vidiq"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )
    seo_path = job_dir / "seo.json"
    if not seo_path.exists():
        raise StageInputMissingError(f"Missing {seo_path}")

    seo = json.loads(seo_path.read_text(encoding="utf-8"))
    original_tags: list[str] = list(seo.get("tags") or [])
    min_score = _seo_vidiq_min_score(channel_path)

    tag_scores: list[dict] = []
    vidiq_error: str | None = None
    try:
        tag_scores = await vidiq_fn(original_tags)
    except Exception as exc:
        vidiq_error = str(exc)

    report: dict = {
        "original_tags": original_tags,
        "min_score": min_score,
        "tag_scores": tag_scores,
        "swaps": [],
        "final_tags": list(original_tags),
        "vidiq_error": vidiq_error,
    }

    if not vidiq_error and tag_scores:
        new_tags = list(original_tags)
        for entry in tag_scores:
            kw = entry.get("keyword", "")
            score = entry.get("score")
            if score is None or score >= min_score:
                continue
            # Find the best related keyword not already in the tag list.
            related = sorted(
                entry.get("related") or [],
                key=lambda r: r.get("score", 0),
                reverse=True,
            )
            replacement = None
            for r in related:
                candidate = r.get("keyword", "").strip()
                if candidate and candidate.lower() not in {t.lower() for t in new_tags}:
                    replacement = candidate
                    break
            if replacement:
                idx = next(
                    (i for i, t in enumerate(new_tags) if t.lower() == kw.lower()), None
                )
                if idx is not None:
                    new_tags[idx] = replacement
                    report["swaps"].append(
                        {
                            "original": kw,
                            "score": score,
                            "replacement": replacement,
                            "replacement_score": related[0].get("score") if related else None,
                        }
                    )
        report["final_tags"] = new_tags
        seo["tags"] = new_tags
        _write_json(seo_path, seo)

    report_path = job_dir / SEO_VIDIQ_REPORT_FILE
    _write_json(report_path, report)

    EventLogger(job_dir / EVENT_LOG).log(
        "SEO_VIDIQ_COMPLETE",
        {
            "job_id": state.job_id,
            "swaps": len(report["swaps"]),
            "vidiq_error": vidiq_error,
        },
    )

    _complete_stage(job_dir, stage_name, report_path)
    return report_path


# ---------------------------------------------------------------------------
# Per-scene image generation via ChatGPT projects.
# ---------------------------------------------------------------------------


_ASSET_GEN_PROMPT_PREFIX = (
    "Photorealistic, 16:9 cinematic, soft natural light, no text overlay, "
    "no watermark, no logos. Audience: adultos 45+. Scene visual: "
)


def _scene_project_name(job_id: str, scene_id: str) -> str:
    return f"{job_id[:35]}-{scene_id}"[:45]


def _build_thumbnail_prompt(
    title: str,
    thumbnail_text: str,
    accent_color: str,
    channel_description: str,
) -> str:
    return (
        f"Photorealistic YouTube thumbnail background image. "
        f"No text, no watermarks, no captions, no overlays. "
        f"16:9 aspect ratio, high resolution. "
        f"Topic: '{title}'. "
        f"Mood/emotion reference: '{thumbnail_text}'. "
        f"Subject: Hispanic or Latina woman aged 45-55 years old. "
        f"Her expression conveys the emotion of the hook: concern, relief, or urgency — matching '{thumbnail_text}'. "
        f"Composition: subject positioned in the left third of the frame, "
        f"face clearly visible and sharp, looking slightly toward the right (center of frame). "
        f"Right third of frame is intentionally empty for text overlay. "
        f"Background: simple, warm-toned, uncluttered. "
        f"Accent color to complement: {accent_color}. "
        f"Lighting: soft natural light, professional photography, bokeh background. "
        f"Channel context: {channel_description}."
    )


async def generate_scene_asset(
    job_dir: Path,
    channel_path: Path,
    scene_id: str,
    image_fn,
) -> dict:
    """Generate a ChatGPT image for ``scene_id`` and update scenes.json.

    Looks up the scene in ``scenes.json`` by id, builds an image prompt
    from its ``visual_prompt`` (plus a brand-consistent style prefix),
    calls ``image_fn(prompt, project_name, out_path)`` (typically
    ``BrowserClient.generate_image``), saves the bytes under
    ``jobs/<id>/assets/<scene_id>.png``, and patches the scene's
    ``asset_refs.primary`` to the relative path so the v2 render
    stage picks it up.

    Returns the image_fn payload plus the scene id.
    """
    scenes_path = job_dir / "scenes.json"
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", scene_id or ""):
        raise StageInputMissingError(f"Invalid scene_id: {scene_id!r}")
    scenes_doc = json.loads(scenes_path.read_text(encoding="utf-8"))
    target = None
    for s in scenes_doc.get("scenes", []):
        if s.get("id") == scene_id:
            target = s
            break
    if target is None:
        raise StageInputMissingError(
            f"Scene {scene_id!r} not found in {scenes_path}"
        )
    visual_prompt = target.get("visual_prompt") or target.get("caption") or ""
    if not visual_prompt:
        raise StageInputMissingError(
            f"Scene {scene_id} has no visual_prompt to feed image gen."
        )

    state = load_job(job_dir)
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_path = assets_dir / f"{scene_id}.png"
    project_name = _scene_project_name(state.job_id, scene_id)
    prompt = _ASSET_GEN_PROMPT_PREFIX + visual_prompt

    result = await image_fn(
        prompt=prompt,
        project_name=project_name,
        out_path=str(out_path),
    )

    # Update scenes.json -> asset_refs.primary with the job-relative path.
    rel = str(out_path.relative_to(job_dir))
    refs = target.get("asset_refs")
    if not isinstance(refs, dict):
        refs = {}
    refs["primary"] = rel
    refs["primary_source"] = "chatgpt_image"
    refs["primary_url"] = result.get("src", "")
    refs["primary_project"] = project_name
    target["asset_refs"] = refs
    _write_json(scenes_path, scenes_doc)

    EventLogger(job_dir / EVENT_LOG).log(
        "SCENE_ASSET_GENERATED",
        {
            "job_id": state.job_id,
            "scene_id": scene_id,
            "local_path": rel,
            "bytes": result.get("bytes"),
        },
    )
    return {"scene_id": scene_id, **result, "asset_refs_primary": rel}


# ---------------------------------------------------------------------------
# Batch image generation stage: assets_chatgpt.
# ---------------------------------------------------------------------------


async def auto_assets_chatgpt_stage(
    job_dir: Path,
    channel_path: Path,
    image_fn,
    *,
    throttle_sec: float = 8.0,
) -> Path:
    """Generate ChatGPT images for every scene, with per-scene throttle + fallback.

    Iterates all scenes in scenes.json and calls ``generate_scene_asset()``
    for each. A failed scene logs a ``SCENE_ASSET_FAILED`` event and
    continues — the render stage falls back to stock/placeholder for that
    scene so the pipeline never halts on a single image failure.

    Returns ``scenes.json`` (updated in-place with ``asset_refs.primary``
    for each successfully generated scene).
    """
    stage_name = "assets_chatgpt"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )

    scenes_path = job_dir / "scenes.json"
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")

    scenes_doc = json.loads(scenes_path.read_text(encoding="utf-8"))
    logger = EventLogger(job_dir / EVENT_LOG)
    scene_ids: list[str] = []
    for s in scenes_doc.get("scenes", []):
        sid = s.get("id")
        if sid:
            scene_ids.append(sid)
        else:
            logger.log(
                "SCENE_ASSET_FAILED",
                {"job_id": state.job_id, "scene_id": None, "error": "scene missing id"},
            )

    for idx, scene_id in enumerate(scene_ids):
        if idx > 0:
            await asyncio.sleep(throttle_sec)
        try:
            await generate_scene_asset(job_dir, channel_path, scene_id, image_fn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.log(
                "SCENE_ASSET_FAILED",
                {"job_id": state.job_id, "scene_id": scene_id, "error": str(exc)},
            )

    _complete_stage(job_dir, stage_name, scenes_path)
    return scenes_path


# ---------------------------------------------------------------------------
# Thumbnail background image generation stage.
# ---------------------------------------------------------------------------


async def auto_thumbnail_image_stage(
    job_dir: Path,
    channel_path: Path,
    image_fn,
) -> Path:
    stage_name = "thumbnail_image"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} from current_stage={state.current_stage!r}"
        )

    seo_path = job_dir / "seo.json"
    if not seo_path.exists():
        raise StageInputMissingError(f"Missing {seo_path}")
    seo = json.loads(seo_path.read_text(encoding="utf-8"))

    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")
    channel_config = read_yaml(channel_path)

    variants = seo.get("title_variants") or []
    thumbnail_text = (variants[0].get("thumbnail_text") or "") if variants else ""
    if not thumbnail_text:
        thumbnail_text = seo.get("thumbnail_text") or ""
    title = seo.get("title") or ""
    palette = (channel_config.get("style") or {}).get("palette") or {}
    accent_color = palette.get("accent", "#F2C94C")
    channel_description = (channel_config.get("channel") or {}).get("description", "Wellness channel for adults 45+")

    prompt = _build_thumbnail_prompt(title, thumbnail_text, accent_color, channel_description)

    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_path = assets_dir / "thumbnail_bg.png"
    project_name = f"{state.job_id[:35]}-thumbnail"[:45]

    await image_fn(
        prompt=prompt,
        project_name=project_name,
        out_path=str(out_path),
    )

    public_assets_dir = repo_root() / "remotion/public/jobs" / job_dir.name / "assets"
    public_assets_dir.mkdir(parents=True, exist_ok=True)
    import shutil as _shutil
    _shutil.copy2(out_path, public_assets_dir / "thumbnail_bg.png")

    public_ref = f"jobs/{job_dir.name}/assets/thumbnail_bg.png"
    seo["thumbnail_path"] = public_ref
    _write_json(seo_path, seo)

    EventLogger(job_dir / EVENT_LOG).log(
        "THUMBNAIL_IMAGE_GENERATED",
        {"job_id": state.job_id, "public_path": public_ref},
    )
    _complete_stage(job_dir, stage_name, seo_path)
    return seo_path


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

    Reads ``operator/claude/<artifact>_qa.json`` to extract issues and
    required_changes, resets the ``<artifact>_promote`` + ``<artifact>_qa``
    stages to pending, sends a rework message into the persistent
    ChatGPT tab, and re-runs the promoter with the new response.
    """
    qa_path = job_dir / "operator" / "claude" / f"{artifact}_qa.json"
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
        f"(Claude). Reescribe el artefacto JSON corrigiendo SOLO los puntos "
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
    qa_session_fn: SessionFn,
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
            return await qa_fn(job_dir, channel_path, qa_session_fn)
        except StageInputMissingError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            # Only attempt rework if qa.json exists (QA ran but verdict=NEEDS_REWORK).
            # If qa.json is missing, the QA response itself was empty/invalid — skip
            # rework and retry qa_fn directly on the next iteration.
            qa_path = job_dir / "operator" / "claude" / f"{artifact}_qa.json"
            if qa_path.exists():
                try:
                    await auto_rework_artifact(
                        artifact, job_dir, channel_path, chatgpt_fn
                    )
                except StageInputMissingError:
                    # Rework failed (e.g. qa.json vanished) — retry qa_fn directly.
                    pass
    assert last_exc is not None
    raise last_exc
