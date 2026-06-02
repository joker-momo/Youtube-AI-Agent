from __future__ import annotations

import errno
import fcntl
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from video_agent.orchestrator.queue import JobQueue
from video_agent.shorts import paths as shorts_paths
from video_agent.shorts import status as shorts_status
from video_agent.web.routes._legacy import _job_idea_title, _safe_job_dir, get_jobs_root
from video_agent.web.run_all_pipeline import is_run_locked

router = APIRouter()

_REQUIRED_LONG_ARTIFACTS = ("video.mp4", "script.json", "scenes.json", "seo.json")


class PrepareDraftsRequest(BaseModel):
    force: bool = False


class ConfirmRenderRequest(BaseModel):
    short_ids: list[str]


def _is_file_locked(path: Path) -> bool:
    if not path.exists():
        return False
    lock_fd = path.open("a+")
    try:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return True
            raise
        finally:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        lock_fd.close()
    return False


def _resolve_artifact(job_dir: Path, name: str) -> Path | None:
    candidates = [job_dir / name]
    if name.endswith(".json"):
        candidates.append(job_dir / "json" / name)
    elif name.endswith(".mp4"):
        candidates.append(job_dir / "outputs" / name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _job_missing_artifacts(job_dir: Path) -> list[str]:
    missing: list[str] = []
    for name in _REQUIRED_LONG_ARTIFACTS:
        if _resolve_artifact(job_dir, name) is None:
            missing.append(name)
    return missing


def system_has_active_job(jobs_root: Path) -> tuple[bool, list[dict[str, Any]]]:
    active_jobs: list[dict[str, Any]] = []
    queue = JobQueue(jobs_root / "queue.db")
    for row in queue.active_jobs():
        active_jobs.append(
            {
                "kind": "queue",
                "job_id": row.get("job_id"),
                "status": row.get("status"),
                "command": row.get("command") or "run_all",
            }
        )

    for job_dir in sorted(jobs_root.iterdir()) if jobs_root.exists() else []:
        if not job_dir.is_dir() or not (job_dir / "job.json").exists():
            continue
        if is_run_locked(job_dir):
            active_jobs.append({"kind": "run_lock", "job_id": job_dir.name, "status": "running"})
        if _is_file_locked(shorts_paths.autopilot_lock_path(job_dir)):
            active_jobs.append({"kind": "shorts_autopilot_lock", "job_id": job_dir.name, "status": "running"})
        shorts_root = shorts_paths.shorts_dir(job_dir)
        if not shorts_root.exists():
            continue
        for short_dir in sorted(shorts_root.iterdir()):
            if not short_dir.is_dir():
                continue
            if _is_file_locked(short_dir / shorts_paths.SHORT_LOCK_FILE):
                active_jobs.append(
                    {
                        "kind": "short_lock",
                        "job_id": job_dir.name,
                        "short_id": short_dir.name,
                        "status": "running",
                    }
                )
    return bool(active_jobs), active_jobs


def _studio_job_state(job_dir: Path) -> dict[str, Any]:
    summary = shorts_status.summarize_shorts(job_dir)
    missing = _job_missing_artifacts(job_dir)
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    return {
        "job_id": job.get("job_id") or job_dir.name,
        "title": _job_idea_title(job_dir),
        "channel_id": job.get("channel_id", "vida-plena-45"),
        "created_at": job.get("created_at"),
        "eligible": not missing,
        "missing": missing,
        "video_exists": _resolve_artifact(job_dir, "video.mp4") is not None,
        "shorts_status": summary.get("state") or "none",
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.get("/shorts-studio/state")
def get_shorts_studio_state(jobs_root: Path = Depends(get_jobs_root)) -> dict[str, Any]:
    busy, active_jobs = system_has_active_job(jobs_root)
    jobs: list[dict[str, Any]] = []
    for job_dir in sorted(jobs_root.iterdir(), reverse=True) if jobs_root.exists() else []:
        if not job_dir.is_dir() or not (job_dir / "job.json").exists():
            continue
        jobs.append(_studio_job_state(job_dir))
    return {
        "can_start": not busy,
        "active_jobs": active_jobs,
        "jobs": jobs,
        "eligible_jobs": [job for job in jobs if job["eligible"]],
    }


@router.get("/shorts-studio/jobs/{job_id}/drafts")
def get_shorts_studio_drafts(job_id: str, jobs_root: Path = Depends(get_jobs_root)) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    manifest = _read_json(shorts_paths.manifest_path(job_dir))
    drafts: list[dict[str, Any]] = []
    for entry in manifest.get("shorts") or []:
        short_id = entry.get("short_id")
        if not short_id:
            continue
        short_dir = shorts_paths.short_dir(job_dir, short_id)
        status_doc = _read_json(shorts_paths.short_status_path(job_dir, short_id))
        qa_doc = _read_json(short_dir / shorts_paths.SHORT_QA_FILE)
        source_map = _read_json(short_dir / shorts_paths.SHORT_SOURCE_MAP_FILE)
        merged = dict(entry)
        merged.update(status_doc)
        if "qa_verdict" not in merged and qa_doc.get("qa_verdict"):
            merged["qa_verdict"] = qa_doc.get("qa_verdict")
        merged["source_scene_ids"] = (
            merged.get("source_scene_ids")
            or source_map.get("source_scene_ids")
            or source_map.get("scene_ids")
            or []
        )
        drafts.append(merged)
    return {"job_id": job_id, "drafts": drafts}


@router.post("/shorts-studio/jobs/{job_id}/prepare", status_code=202)
def post_shorts_studio_prepare(
    job_id: str,
    req: PrepareDraftsRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    missing = _job_missing_artifacts(job_dir)
    if missing:
        raise HTTPException(status_code=400, detail={"error": "missing_artifacts", "missing": missing})
    busy, _active = system_has_active_job(jobs_root)
    if busy:
        raise HTTPException(status_code=409, detail={"error": "system_busy"})
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(
        job_id,
        enforce_approvals=False,
        command="shorts_prepare_drafts",
        payload={"force": bool(req.force)},
    )
    return {"status": "enqueued", "command": "shorts_prepare_drafts", "job_id": job_id}


@router.post("/shorts-studio/jobs/{job_id}/confirm-render", status_code=202)
def post_shorts_studio_confirm_render(
    job_id: str,
    req: ConfirmRenderRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    busy, _active = system_has_active_job(jobs_root)
    if busy:
        raise HTTPException(status_code=409, detail={"error": "system_busy"})
    short_ids = [short_id for short_id in req.short_ids if short_id]
    if not short_ids:
        raise HTTPException(status_code=400, detail={"error": "missing_short_ids"})
    for short_id in short_ids:
        short_dir = shorts_paths.short_dir(job_dir, short_id)
        status_doc = _read_json(shorts_paths.short_status_path(job_dir, short_id))
        if status_doc.get("status") != "ready_for_render" or status_doc.get("qa_verdict") != "PASS":
            raise HTTPException(status_code=400, detail={"error": "short_not_ready", "short_id": short_id})
        for required in (
            shorts_paths.SHORT_SCRIPT_FILE,
            shorts_paths.SHORT_SCENES_FILE,
            shorts_paths.SHORT_SEO_FILE,
            shorts_paths.SHORT_QA_FILE,
        ):
            if not (short_dir / required).exists():
                raise HTTPException(
                    status_code=400,
                    detail={"error": "missing_short_artifacts", "short_id": short_id, "missing": required},
                )
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(
        job_id,
        enforce_approvals=False,
        command="shorts_confirm_render",
        payload={"short_ids": short_ids},
    )
    return {
        "status": "enqueued",
        "command": "shorts_confirm_render",
        "job_id": job_id,
        "short_ids": short_ids,
    }
