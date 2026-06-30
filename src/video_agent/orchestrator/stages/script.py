from __future__ import annotations

from pathlib import Path

from video_agent.operator import (
    _chatgpt_script_prompt,
    promote_operator_artifact,
)
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    SCRIPT_PROMPT_PATH,
    SCRIPT_RAW_PATH,
    StageInputMissingError,
    _complete_stage,
    _resolve_idea_path,
    dag_mode,
)
from video_agent.storage.atomic import atomic_write_text
from video_agent.utils.json_io import read_json, read_yaml

__all__ = [
    "run_script_stage",
    "promote_script_stage",
    "auto_script_stage",
]


def run_script_stage(job_dir: Path, channel_path: Path) -> Path:
    """Produce the ChatGPT script prompt for the job.

    Reads ``job_dir/idea.json`` and the channel YAML, renders the prompt
    via the existing v2 helper, writes ``operator/chatgpt/script_prompt.md``,
    marks the ``script`` stage completed, and emits a ``STAGE_COMPLETED``
    event so consumers of ``events.jsonl`` see the same shape as v2.
    """
    idea_path = _resolve_idea_path(job_dir)
    if not idea_path.exists():
        raise StageInputMissingError(f"Missing {idea_path}")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != "script":
        raise StageInputMissingError(
            f"Cannot run script stage from current_stage={state.current_stage!r}"
        )

    idea = read_json(idea_path)
    channel_config = read_yaml(channel_path)
    prompt_text = _chatgpt_script_prompt(channel_config, idea)

    output_path = job_dir / SCRIPT_PROMPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, prompt_text, encoding="utf-8")

    _complete_stage(job_dir, "script", output_path)

    return output_path


def promote_script_stage(job_dir: Path, channel_path: Path, raw_response: str) -> Path:
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != "script_promote":
        raise StageInputMissingError(
            f"Cannot run script_promote stage from current_stage={state.current_stage!r}"
        )
    if not raw_response.strip():
        raise StageInputMissingError("Missing raw ChatGPT script response.")
    if not channel_path.exists():
        raise StageInputMissingError(f"Missing channel config {channel_path}")

    raw_path = job_dir / SCRIPT_RAW_PATH
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(raw_path, raw_response, encoding="utf-8")

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


async def auto_script_stage(
    job_dir: Path,
    channel_path: Path,
    session_fn,
) -> Path:
    from video_agent.orchestrator import stages as stages_pkg
    return await stages_pkg._auto_run_then_promote(
        job_dir=job_dir,
        channel_path=channel_path,
        prompt_path=job_dir / SCRIPT_PROMPT_PATH,
        runner=stages_pkg.run_script_stage,
        promoter=stages_pkg.promote_script_stage,
        session_fn=session_fn,
        run_stage_name="script",
        promote_stage_name="script_promote",
        briefing_stage_name="script",
    )
