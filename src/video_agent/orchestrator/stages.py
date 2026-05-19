from __future__ import annotations

from pathlib import Path

from video_agent.contracts import EVENT_LOG
from video_agent.operator import _chatgpt_script_prompt, promote_operator_artifact
from video_agent.orchestrator.job_state import load_job, save_job
from video_agent.orchestrator.orchestrator import _now
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.utils.logging import EventLogger

IDEA_FILE = "idea.json"
SCRIPT_PROMPT_PATH = Path("operator/chatgpt/script_prompt.md")
SCRIPT_RAW_PATH = Path("operator/chatgpt/script.raw.txt")


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
