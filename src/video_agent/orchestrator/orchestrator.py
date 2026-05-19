from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from video_agent.contracts import EVENT_LOG
from video_agent.orchestrator.job_state import (
    DEFAULT_STAGES,
    JobState,
    StageStatus,
    load_job,
    save_job,
)
from video_agent.utils.logging import EventLogger


class JobAlreadyExistsError(Exception):
    pass


class JobNotFoundError(Exception):
    pass


class StageError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_logger(job_dir: Path) -> EventLogger:
    return EventLogger(job_dir / EVENT_LOG)


def create_job(
    job_dir: Path,
    job_id: str,
    channel_id: str,
    idea_path: str,
    stages: Iterable[str] = DEFAULT_STAGES,
) -> JobState:
    if (job_dir / "job.json").exists():
        raise JobAlreadyExistsError(f"job.json already exists in {job_dir}")
    stage_list = [StageStatus(name=name) for name in stages]
    if not stage_list:
        raise ValueError("stages must not be empty")
    now = _now()
    state = JobState(
        job_id=job_id,
        channel_id=channel_id,
        idea_path=idea_path,
        created_at=now,
        updated_at=now,
        current_stage=stage_list[0].name,
        stages=stage_list,
    )
    save_job(job_dir, state)
    _event_logger(job_dir).log(
        "JOB_CREATED",
        {
            "job_id": job_id,
            "channel_id": channel_id,
            "idea_path": idea_path,
            "stages": [s.name for s in stage_list],
        },
    )
    return state


def _set_stage_status(state: JobState, name: str, status: str, ts: str) -> None:
    stage = state.stage(name)
    stage.status = status
    if status == "in_progress":
        stage.started_at = ts
    elif status in {"completed", "failed"}:
        stage.completed_at = ts


def advance(job_dir: Path) -> JobState:
    if not (job_dir / "job.json").exists():
        raise JobNotFoundError(f"job.json missing in {job_dir}")
    state = load_job(job_dir)
    logger = _event_logger(job_dir)
    ts = _now()

    current = state.stage(state.current_stage)

    if current.status == "pending":
        _set_stage_status(state, current.name, "in_progress", ts)
        logger.log("STAGE_STARTED", {"job_id": state.job_id, "stage": current.name})
    elif current.status == "in_progress":
        _set_stage_status(state, current.name, "completed", ts)
        logger.log("STAGE_COMPLETED", {"job_id": state.job_id, "stage": current.name})
        next_stage = _next_pending(state)
        if next_stage is not None:
            state.current_stage = next_stage.name
        else:
            logger.log("JOB_COMPLETED", {"job_id": state.job_id})
    elif current.status == "completed":
        next_stage = _next_pending(state)
        if next_stage is None:
            raise StageError("All stages already completed")
        state.current_stage = next_stage.name
        _set_stage_status(state, next_stage.name, "in_progress", ts)
        logger.log("STAGE_STARTED", {"job_id": state.job_id, "stage": next_stage.name})
    else:
        raise StageError(f"Cannot advance from status={current.status!r}")

    state.updated_at = ts
    save_job(job_dir, state)
    return state


def _next_pending(state: JobState) -> StageStatus | None:
    for stage in state.stages:
        if stage.status == "pending":
            return stage
    return None
