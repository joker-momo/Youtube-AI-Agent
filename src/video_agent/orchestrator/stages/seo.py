from __future__ import annotations

import json
from pathlib import Path

from video_agent.contracts import ARTIFACT_SCENES, ARTIFACT_SCRIPT, ARTIFACT_SEO
from video_agent.operator import (
    _chatgpt_seo_prompt,
    promote_operator_artifact,
)
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    SEO_PROMPT_PATH,
    SEO_RAW_PATH,
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    dag_mode,
)
from video_agent.storage.atomic import atomic_write_text
from video_agent.style_dna import load_style_dna
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.json_io import write_json as _write_json

__all__ = [
    "run_seo_stage",
    "promote_seo_stage",
    "auto_seo_stage",
    "_enforce_seo_language_qa",
]


def run_seo_stage(job_dir: Path, channel_path: Path) -> Path:
    script_path = _resolve_artifact(job_dir, ARTIFACT_SCRIPT)
    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES)
    if not script_path.exists():
        raise StageInputMissingError(f"Missing {script_path}")
    if not scenes_path.exists():
        raise StageInputMissingError(f"Missing {scenes_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != "seo":
        raise StageInputMissingError(
            f"Cannot run seo stage from current_stage={state.current_stage!r}"
        )

    script = read_json(script_path)
    scenes = read_json(scenes_path)
    channel_config = read_yaml(channel_path)
    brand_palette = load_style_dna(channel_path)
    prompt_text = _chatgpt_seo_prompt(channel_config, script, scenes, brand_palette)

    output_path = job_dir / SEO_PROMPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, prompt_text, encoding="utf-8")

    _complete_stage(job_dir, "seo", output_path)
    return output_path


def promote_seo_stage(job_dir: Path, channel_path: Path, raw_response: str) -> Path:
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != "seo_promote":
        raise StageInputMissingError(
            f"Cannot run seo_promote stage from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError("Missing raw ChatGPT SEO response.")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    raw_path = job_dir / SEO_RAW_PATH
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(raw_path, raw_response, encoding="utf-8")

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


async def auto_seo_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn,
) -> Path:
    from video_agent.orchestrator import stages as stages_pkg
    return await stages_pkg._auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SEO_PROMPT_PATH,
        runner=stages_pkg.run_seo_stage,
        promoter=stages_pkg.promote_seo_stage,
        session_fn=session_fn,
        run_stage_name="seo",
        promote_stage_name="seo_promote",
        briefing_stage_name="seo",
    )


def _enforce_seo_language_qa(
    job_dir: Path,
    qa_output: Path,
    qa_payload: dict,
    *,
    channel_path: Path | None = None,
) -> None:
    """Force SEO rework if Gemini misses the configured language contract."""
    seo_path = _resolve_artifact(job_dir, ARTIFACT_SEO)
    if not seo_path.exists():
        return
    try:
        seo_payload = json.loads(seo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    if channel_path is None:
        try:
            state = load_job(job_dir)
        except Exception:
            # If job.json is missing or corrupted we cannot know which channel this
            # belongs to. Skip the enforcement layer rather than aborting the QA
            # promotion entirely; the artifact has already been written to disk.
            return
        from video_agent.orchestrator import stages as stages_pkg
        channel_config_path = stages_pkg.repo_root() / "configs" / state.channel_id / "channel.yaml"
    else:
        channel_config_path = channel_path
    channel_config = read_yaml(channel_config_path) if channel_config_path.exists() else {}
    expected_language = (
        (channel_config.get("seo") or {}).get("language")
        or (channel_config.get("audience") or {}).get("language")
        or "es-ES"
    )
    actual_language = seo_payload.get("language")
    if actual_language == expected_language:
        return

    issue = (
        f"SEO language must be exactly {expected_language}; got {actual_language}. "
        "ChatGPT must regenerate the SEO artifact with the configured language."
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
        current_channel_fit = int(scores.get("channel_fit") or 5)
    except (TypeError, ValueError):
        current_channel_fit = 5
    scores["channel_fit"] = min(current_channel_fit, 3)
    updated["scores"] = scores
    issues = list(updated.get("issues") or [])
    if issue not in issues:
        issues.append(issue)
    updated["issues"] = issues
    required = (
        f"Set seo.language to exactly {expected_language} and use "
        f"{expected_language} consistently in SEO text."
    )
    changes = list(updated.get("required_changes") or [])
    if required not in changes:
        changes.append(required)
    updated["required_changes"] = changes
    _write_json(qa_output, updated)
