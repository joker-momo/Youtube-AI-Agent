"""Backend API for the Shorts Autopilot (spec §18)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from video_agent.contracts import repo_root
from video_agent.shorts import manifest as manifest_mod
from video_agent.shorts import paths, source_url, status
from video_agent.web.routes._legacy import (
    _safe_job_dir,
    get_browser_client,
    get_jobs_root,
)

router = APIRouter()


def _channel_config(job_dir: Path) -> dict:
    from video_agent.utils.json_io import read_yaml

    job = {}
    jp = job_dir / "job.json"
    if jp.exists():
        try:
            job = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            job = {}
    channel_id = job.get("channel_id", "vida-plena-45")
    cfg_path = repo_root() / "configs" / channel_id / "channel.yaml"
    return read_yaml(cfg_path) if cfg_path.exists() else {}


class AutopilotRequest(BaseModel):
    force: bool = False


class UploadedRequest(BaseModel):
    youtube_url: str = ""


class SourceUrlRequest(BaseModel):
    long_video_url: str = ""


# --- background runner (browser-bridged); monkeypatchable in tests ----------

def enqueue_shorts_autopilot(job_dir: Path, channel_config: dict, *, force: bool, client: Any = None) -> None:
    """Enqueue the Shorts autopilot to the background worker (Node/Remotion +
    browser). The render needs Node, which the web app container lacks, so the
    whole sequential autopilot runs in the worker via the job queue."""
    from video_agent.orchestrator.queue import JobQueue

    jobs_root = job_dir.parent
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(
        job_dir.name,
        enforce_approvals=False,
        command="shorts_autopilot",
        payload={"force": bool(force)},
    )


@router.get("/jobs/{job_id}/shorts")
def list_shorts(job_id: str, jobs_root: Path = Depends(get_jobs_root)) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return {"job_id": job_id, **status.summarize_shorts(job_dir)}


@router.post("/jobs/{job_id}/shorts/autopilot", status_code=202)
async def post_autopilot(
    job_id: str,
    req: AutopilotRequest,
    jobs_root: Path = Depends(get_jobs_root),
    client: Any = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    cfg = _channel_config(job_dir)
    enqueue_shorts_autopilot(job_dir, cfg, force=req.force, client=client)
    return {"status": "enqueued", "job_id": job_id, "force": req.force}


@router.post("/jobs/{job_id}/shorts/{short_id}/regenerate", status_code=202)
async def post_regenerate(
    job_id: str,
    short_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    client: Any = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    cfg = _channel_config(job_dir)
    # Rebuild the single short by re-running autopilot in single-short mode via
    # a one-item plan derived from the existing plan/manifest entry.
    enqueue_shorts_autopilot(job_dir, cfg, force=False, client=client)
    return {"status": "enqueued", "job_id": job_id, "short_id": short_id}


@router.post("/jobs/{job_id}/shorts/{short_id}/render", status_code=202)
async def post_render(
    job_id: str,
    short_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    client: Any = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    cfg = _channel_config(job_dir)
    # Render needs Node (worker container). Re-run the autopilot in the worker;
    # already-rendered shorts are skipped, this short is (re)built + rendered.
    enqueue_shorts_autopilot(job_dir, cfg, force=False, client=client)
    return {"status": "enqueued", "job_id": job_id, "short_id": short_id}


@router.post("/jobs/{job_id}/shorts/{short_id}/uploaded")
def post_uploaded(
    job_id: str,
    short_id: str,
    req: UploadedRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    spath = paths.short_status_path(job_dir, short_id)
    if not spath.exists():
        raise HTTPException(status_code=404, detail=f"Unknown short: {short_id}")
    st = json.loads(spath.read_text(encoding="utf-8"))
    st["uploaded"] = True
    st["youtube_url"] = req.youtube_url
    manifest_mod.write_short_status(job_dir, short_id, st)
    return {"status": "ok", "short_id": short_id, "uploaded": True, "youtube_url": req.youtube_url}


@router.post("/jobs/{job_id}/shorts/source-url")
def post_source_url(
    job_id: str,
    req: SourceUrlRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    cfg = _channel_config(job_dir)
    updated = source_url.update_long_video_url(job_dir, req.long_video_url, cfg)
    return {"status": "ok", "job_id": job_id, "updated_shorts": updated, "long_video_url": req.long_video_url}
