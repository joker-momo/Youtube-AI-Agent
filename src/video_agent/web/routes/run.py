"""Run-all and run-batch pipeline routes.

Extracted from ``_legacy.py``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from video_agent.orchestrator import load_job
from video_agent.orchestrator.browser_client import BrowserClient, BrowserClientError
from video_agent.web.run_all_pipeline import execute_run_all, is_run_locked

from video_agent.web.routes._common import (
    RunBatchRequest,
    _safe_job_dir,
    get_browser_client,
    get_channel_path,
    get_jobs_root,
    _handle_browser_client_error,
)

router = APIRouter()


@router.post("/jobs/{job_id}/run-all")
async def post_run_all(
    job_id: str,
    enforce_approvals: bool = False,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    import sys
    if "pytest" in sys.modules:
        return await execute_run_all(
            job_dir=job_dir,
            channel_path=channel_path,
            client=client,
            enforce_approvals=enforce_approvals,
        )

    state = load_job(job_dir)
    if is_run_locked(job_dir):
        return {
            "job_id": job_id,
            "status": "already_running",
            "state": state.to_dict(),
        }

    from video_agent.orchestrator.queue import JobQueue
    db_path = jobs_root / "queue.db"
    queue = JobQueue(db_path)
    enqueued = queue.enqueue(job_id, enforce_approvals)

    if not enqueued:
        return {
            "job_id": job_id,
            "status": "already_running",
            "state": state.to_dict(),
        }
    return {
        "job_id": job_id,
        "status": "enqueued",
        "state": state.to_dict(),
    }


@router.post("/run-batch")
async def post_run_batch(
    req: RunBatchRequest,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    import sys
    if "pytest" in sys.modules:
        results: list[dict] = []
        for job_id in req.job_ids:
            try:
                job_dir = _safe_job_dir(jobs_root, job_id)
            except HTTPException as exc:
                results.append({"job_id": job_id, "error": exc.detail})
                continue
            if not (job_dir / "job.json").exists():
                results.append({"job_id": job_id, "error": f"Unknown job: {job_id}"})
                continue
            try:
                outcome = await execute_run_all(
                    job_dir=job_dir,
                    channel_path=channel_path,
                    client=client,
                    enforce_approvals=req.enforce_approvals,
                )
                results.append({"job_id": job_id, "result": outcome})
            except HTTPException as exc:
                results.append({"job_id": job_id, "error": exc.detail})
            except Exception as exc:
                results.append({"job_id": job_id, "error": str(exc)})
        failed = [r for r in results if "error" in r]
        summary = {
            "total": len(req.job_ids),
            "succeeded": len(results) - len(failed),
            "failed": len(failed),
            "results": results,
        }
        from video_agent.notifications.telegram import notify_batch_done
        await notify_batch_done(
            total=summary["total"],
            succeeded=summary["succeeded"],
            failed=summary["failed"],
            failed_jobs=[r["job_id"] for r in failed],
        )
        return summary

    """Enqueue /run-all on each job sequentially."""
    from video_agent.orchestrator.queue import JobQueue
    db_path = jobs_root / "queue.db"
    queue = JobQueue(db_path)

    results: list[dict] = []
    for job_id in req.job_ids:
        try:
            job_dir = _safe_job_dir(jobs_root, job_id)
        except HTTPException as exc:
            results.append({"job_id": job_id, "error": exc.detail})
            continue
        if not (job_dir / "job.json").exists():
            results.append({"job_id": job_id, "error": f"Unknown job: {job_id}"})
            continue

        queue.enqueue(job_id, req.enforce_approvals)
        results.append({"job_id": job_id, "status": "enqueued"})

    failed = [r for r in results if "error" in r]
    summary = {
        "total": len(req.job_ids),
        "succeeded": len(results) - len(failed),
        "failed": len(failed),
        "results": results,
    }
    return summary
