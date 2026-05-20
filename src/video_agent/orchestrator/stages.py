from __future__ import annotations

from pathlib import Path

from video_agent.contracts import EVENT_LOG
from video_agent.operator import (
    _chatgpt_scenes_prompt,
    _chatgpt_seo_prompt,
    _chatgpt_script_prompt,
    promote_operator_artifact,
    write_operator_review,
)
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
