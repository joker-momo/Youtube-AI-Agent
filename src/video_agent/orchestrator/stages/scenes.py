from __future__ import annotations

from pathlib import Path

from video_agent.contracts import ARTIFACT_SCENES, ARTIFACT_SCRIPT
from video_agent.operator import (
    _chatgpt_scenes_prompt,
    get_scenes_qa_feedback,
    promote_operator_artifact,
)
from video_agent.operator_validators import _looks_like_spanish_visual_prompt
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    SCENES_PROMPT_PATH,
    SCENES_RAW_PATH,
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    dag_mode,
)
from video_agent.storage.atomic import atomic_write_text
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.json_io import write_json as _write_json

__all__ = [
    "run_scenes_stage",
    "promote_scenes_stage",
    "auto_scenes_stage",
    "_enforce_scenes_visual_prompt_english",
]


def run_scenes_stage(job_dir: Path, channel_path: Path) -> Path:
    script_path = _resolve_artifact(job_dir, ARTIFACT_SCRIPT)
    if not script_path.exists():
        raise StageInputMissingError(f"Missing {script_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != "scenes":
        raise StageInputMissingError(
            f"Cannot run scenes stage from current_stage={state.current_stage!r}"
        )

    script = read_json(script_path)
    channel_config = read_yaml(channel_path)
    qa_feedback = get_scenes_qa_feedback(job_dir)
    prompt_text = _chatgpt_scenes_prompt(channel_config, script, qa_feedback=qa_feedback)

    output_path = job_dir / SCENES_PROMPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, prompt_text, encoding="utf-8")

    _complete_stage(job_dir, "scenes", output_path)
    return output_path


def promote_scenes_stage(job_dir: Path, channel_path: Path, raw_response: str) -> Path:
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != "scenes_promote":
        raise StageInputMissingError(
            f"Cannot run scenes_promote stage from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError("Missing raw ChatGPT scenes response.")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    raw_path = job_dir / SCENES_RAW_PATH
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(raw_path, raw_response, encoding="utf-8")

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


async def auto_scenes_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn,
) -> Path:
    import os

    from video_agent.orchestrator import stages as stages_pkg
    if os.environ.get("SCENES_SHARDED_GENERATION", "").strip() == "1":
        return await stages_pkg.auto_scenes_stage_sharded(job_dir, channel_path, session_fn)
    return await stages_pkg._auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SCENES_PROMPT_PATH,
        runner=stages_pkg.run_scenes_stage,
        promoter=stages_pkg.promote_scenes_stage,
        session_fn=session_fn,
        run_stage_name="scenes",
        promote_stage_name="scenes_promote",
        briefing_stage_name="scenes",
    )


def _enforce_scenes_visual_prompt_english(
    job_dir: Path,
    qa_output: Path,
    qa_payload: dict,
) -> None:
    """Force scenes rework if any visual_prompt is Spanish.

    Pexels stock search is English-keyword based, so Spanish visual_prompts
    produce off-topic backgrounds (Bellagio fountains for sleep scenes, etc.).
    This QA layer flips the verdict to NEEDS_REWORK with a per-scene list of
    offending visual_prompts so ChatGPT regenerates them in English.
    """
    import json
    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES)
    if not scenes_path.exists():
        return
    try:
        scenes_payload = json.loads(scenes_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    offenders: list[tuple[str, str]] = []  # (scene_id, reason)
    for scene in scenes_payload.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        prompt = str(scene.get("visual_prompt") or "")
        is_spanish, reason = _looks_like_spanish_visual_prompt(prompt)
        if is_spanish:
            offenders.append((str(scene.get("id", "?")), reason or "Spanish detected"))

    if not offenders:
        return

    summary = ", ".join(f"{sid} ({reason})" for sid, reason in offenders[:5])
    if len(offenders) > 5:
        summary += f", and {len(offenders) - 5} more"
    issue = (
        f"{len(offenders)} scene visual_prompt fields are Spanish. "
        "Pexels stock search is English-keyword based; Spanish prompts produce "
        f"off-topic backgrounds. Offenders: {summary}."
    )

    updated = dict(qa_payload)
    updated["verdict"] = "NEEDS_REWORK"
    youtube_policy = dict(updated.get("youtube_policy") or {})
    youtube_policy.setdefault("compliant", True)
    youtube_policy.setdefault("risk_level", "none")
    youtube_policy.setdefault("violations", [])
    updated["youtube_policy"] = youtube_policy
    scores = dict(updated.get("scores") or {})
    try:
        current_clarity = int(scores.get("clarity") or 5)
    except (TypeError, ValueError):
        current_clarity = 5
    scores["clarity"] = min(current_clarity, 3)
    updated["scores"] = scores
    issues = list(updated.get("issues") or [])
    if issue not in issues:
        issues.append(issue)
    updated["issues"] = issues
    required = (
        "Rewrite every visual_prompt in ENGLISH for Pexels stock search. "
        "Format: person + setting + action + lighting + camera framing. "
        "Example: 'Mature woman in her 50s drinking herbal tea on a sofa "
        "at night, warm tungsten light, medium shot'. "
        f"Scenes to fix: {', '.join(sid for sid, _ in offenders)}."
    )
    changes = list(updated.get("required_changes") or [])
    if required not in changes:
        changes.append(required)
    updated["required_changes"] = changes
    _write_json(qa_output, updated)
