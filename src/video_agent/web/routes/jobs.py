"""Job CRUD and control routes.

Extracted from ``_legacy.py``.  All route handlers are defined here;
``_legacy.py`` re-exports these symbols for backward-compatible imports.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from video_agent.contracts import EVENT_LOG, repo_root
from video_agent.orchestrator import (
    JobAlreadyExistsError,
    JobNotFoundError,
    advance,
    create_job,
    load_job,
)
from video_agent.orchestrator.orchestrator import StageError
from video_agent.orchestrator.stages import IDEA_FILE
from video_agent.utils.logging import EventLogger
from video_agent.web.run_all_pipeline import stop_request_path
from video_agent.web.timeline_helpers import (
    effective_stage_status,
    job_has_in_progress_stage,
)
from video_agent.storage.atomic import atomic_write_json, atomic_write_text

from video_agent.web.routes._common import (
    CreateJobRequest,
    _queue_status,
    _safe_job_dir,
    get_jobs_root,
)
from video_agent.web.services.video_job_creator import (
    create_job_from_full_idea,
)

router = APIRouter()

# Track live /run-all request tasks by job_id so /stop can cancel promptly.
_RUN_ALL_TASKS: dict[str, asyncio.Task[Any]] = {}


def _kill_job_subprocesses(job_dir: Path) -> list[int]:
    """Best-effort hard-stop for known long-running subprocesses."""
    killed: list[int] = []
    for name in (".render.pid", ".thumbnail.pid"):
        pid_path = job_dir / name
        if not pid_path.exists():
            continue
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
        except OSError:
            pass
        try:
            pid_path.unlink()
        except OSError:
            pass
    return killed


def _job_idea_title(job_dir: Path) -> str:
    idea_path = job_dir / IDEA_FILE
    if not idea_path.exists():
        idea_path = job_dir / "idea.json"
        if not idea_path.exists():
            return ""
    try:
        idea = json.loads(idea_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for key in ("title_seed", "recommended_angle", "topic", "target_keyword"):
        value = idea.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class CreateJobFromIdeaRequest(BaseModel):
    channel_id: str = "vida-plena-45"
    idea: dict
    job_id: str | None = None
    run_now: bool = True
    enforce_approvals: bool = False
    check_duplicates: bool = True
    duplicate_policy: str = "block"
    max_existing_videos: int = 100


@router.post("/jobs/from-idea", status_code=201)
async def post_job_from_idea(
    payload: CreateJobFromIdeaRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    return await create_job_from_full_idea(
        channel_id=payload.channel_id,
        idea=payload.idea,
        job_id=payload.job_id,
        run_now=payload.run_now,
        enforce_approvals=payload.enforce_approvals,
        duplicate_policy=payload.duplicate_policy,
        check_duplicates=payload.check_duplicates,
        max_existing_videos=payload.max_existing_videos,
        jobs_root=jobs_root,
    )


@router.get("/jobs")
def list_jobs(jobs_root: Path = Depends(get_jobs_root)) -> dict:
    """List every job folder under JOBS_DIR that has a ``job.json``.

    Returns a summary view per job (stage progress + duration) suitable
    for the dashboard. Heavy fields (stages array) are included so the
    dashboard does not need a second round-trip per row.
    """
    items = []
    if jobs_root.exists():
        for entry in sorted(jobs_root.iterdir(), reverse=True):
            job_file = entry / "job.json"
            if not job_file.exists():
                continue
            try:
                payload = json.loads(job_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            current_stage = payload.get("current_stage")
            stop_requested = stop_request_path(entry).exists()
            queue_status = _queue_status(
                jobs_root,
                str(payload.get("job_id") or entry.name),
                entry,
            )
            current_stage_active = queue_status == "running" or job_has_in_progress_stage(payload)
            stages = []
            for raw_stage in payload.get("stages", []):
                stage = dict(raw_stage)
                stage["status"] = effective_stage_status(
                    stage,
                    current_stage,
                    stop_requested=stop_requested,
                    current_stage_active=current_stage_active,
                )
                stages.append(stage)
            done = sum(1 for s in stages if s.get("status") == "completed")
            total = len(stages)
            in_progress = [
                s["name"] for s in stages if s.get("status") == "in_progress"
            ]
            items.append(
                {
                    "job_id": payload.get("job_id"),
                    "idea_title": _job_idea_title(entry),
                    "channel_id": payload.get("channel_id"),
                    "current_stage": current_stage,
                    "created_at": payload.get("created_at"),
                    "updated_at": payload.get("updated_at"),
                    "queue_status": queue_status,
                    "stages_done": done,
                    "stages_total": total,
                    "in_progress": in_progress,
                    "stages": stages,
                }
            )
    return {"count": len(items), "jobs": items}


@router.post("/jobs", status_code=201)
def post_job(
    payload: CreateJobRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, payload.job_id)
    try:
        state = create_job(
            job_dir,
            job_id=payload.job_id,
            channel_id=payload.channel_id,
            idea_path=payload.idea_path,
        )
    except JobAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state.to_dict()


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return load_job(job_dir).to_dict()


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> None:
    job_dir = _safe_job_dir(jobs_root, job_id)
    job_file = job_dir / "job.json"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    payload = json.loads(job_file.read_text(encoding="utf-8"))
    if job_has_in_progress_stage(payload):
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a running job. Stop it first, then delete.",
        )
    shutil.rmtree(job_dir)
    # Also delete from remotion/public/jobs/<job_id>
    public_job_dir = repo_root() / "remotion" / "public" / "jobs" / job_id
    if public_job_dir.exists():
        shutil.rmtree(public_job_dir, ignore_errors=True)


@router.post("/jobs/{job_id}/stop")
def stop_job(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    """Request graceful stop for the current /run-all execution."""
    job_dir = _safe_job_dir(jobs_root, job_id)
    job_file = job_dir / "job.json"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    atomic_write_text(stop_request_path(job_dir), "1\n", encoding="utf-8")
    try:
        state = load_job(job_dir)
        EventLogger(job_dir / EVENT_LOG).log(
            "STOP_REQUESTED",
            {"job_id": state.job_id, "current_stage": state.current_stage},
        )
    except Exception:
        pass
    killed_pids = _kill_job_subprocesses(job_dir)
    cancelled = False
    task = _RUN_ALL_TASKS.get(job_id)
    if task is not None and not task.done():
        cancelled = task.cancel("stop requested by operator")

    # AC8 (shorts render batch): the stop must be visible IMMEDIATELY, even
    # while an expensive in-flight item (poster/LLM/render) is still running —
    # cancel the durable batch atomically here; the loops tolerate the current
    # call returning afterwards (late completion is a no-op on cancelled items).
    batch_cancelled = False
    try:
        from video_agent.shorts.render_batch import RenderBatchStore

        batch_store = RenderBatchStore(job_dir)
        if batch_store.is_active():
            batch_store.cancel(error="operator requested stop")
            batch_cancelled = True
    except Exception:
        pass

    # If a synchronous Short build is orphaned (no live worker owns it), the
    # .stop_requested flag has no consumer — its check_stop only aborts an
    # in-process build. Force any ownerless active short to a terminal status so
    # the UI releases it instead of showing a Short stuck at "generating".
    recovered_shorts: list[str] = []
    try:
        from video_agent.orchestrator.queue import JobQueue
        from video_agent.shorts import status as shorts_status

        try:
            queue = JobQueue(jobs_root / "queue.db")
        except Exception:
            queue = None
        owner_alive = shorts_status.short_owner_is_alive(job_dir, queue=queue)
        recovered_shorts = shorts_status.force_terminate_orphaned_shorts(
            job_dir, owner_alive=owner_alive
        )
    except Exception:
        pass

    return {
        "job_id": job_id,
        "stop_requested": True,
        "cancelled": bool(cancelled),
        "batch_cancelled": batch_cancelled,
        "killed_pids": killed_pids,
        "recovered_shorts": recovered_shorts,
    }


@router.post("/jobs/{job_id}/advance")
def post_advance(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    try:
        state = advance(job_dir)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state.to_dict()


@router.get("/jobs/{job_id}/events")
def get_events(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    events_path = job_dir / EVENT_LOG
    if not events_path.exists():
        return {"job_id": job_id, "events": []}
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {"job_id": job_id, "events": events}


@router.post("/jobs/{job_id}/idea", status_code=201)
def post_idea(
    job_id: str,
    idea: dict,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    from video_agent.utils.validation import validate_json
    try:
        validate_json(idea, repo_root() / "schemas" / "manual-idea.schema.json")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid idea payload: {exc}") from exc
    idea_path = job_dir / IDEA_FILE
    atomic_write_json(idea_path, idea)
    return {"job_id": job_id, "idea_path": str(idea_path)}
