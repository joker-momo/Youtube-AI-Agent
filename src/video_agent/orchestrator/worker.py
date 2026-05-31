from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from fastapi import HTTPException

from video_agent.orchestrator.browser_client import BrowserClient
from video_agent.orchestrator.queue import JobQueue
from video_agent.orchestrator.stages import (
    auto_thumbnail_image_stage,
    run_render_stage,
    run_whisper_timestamps_stage,
)
from video_agent.web.run_all_pipeline import execute_run_all

logger = logging.getLogger("video_agent.worker")


def get_jobs_root() -> Path:
    return Path(os.environ.get("JOBS_DIR", "/app/jobs"))


def get_channel_path() -> Path:
    return Path(
        os.environ.get(
            "CHANNEL_CONFIG",
            "/app/configs/vida-plena-45/channel.yaml",
        )
    )


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            if detail.get("stop_requested"):
                return False
            if detail.get("approval_required"):
                return False
            if detail.get("login_required"):
                return False
        if 400 <= exc.status_code < 500 and exc.status_code not in {408, 429}:
            return False
    return True


def _dispatch_queue_job(
    job: dict,
    *,
    jobs_root: Path,
    channel_path: Path,
    client: BrowserClient,
) -> None:
    job_id = job["job_id"]
    job_dir = jobs_root / job_id
    command = job.get("command") or "run_all"
    enforce_approvals = bool(job.get("enforce_approvals"))
    if command == "run_all":
        asyncio.run(execute_run_all(
            job_dir=job_dir,
            channel_path=channel_path,
            client=client,
            enforce_approvals=enforce_approvals,
        ))
        return
    if command == "stage_render":
        run_render_stage(job_dir, channel_path)
        return
    if command == "stage_whisper_timestamps":
        run_whisper_timestamps_stage(job_dir)
        return
    if command == "stage_thumbnail_image_auto":
        asyncio.run(auto_thumbnail_image_stage(job_dir, channel_path, client.generate_image))
        return
    if command == "shorts_autopilot":
        _run_shorts_autopilot_job(job, job_dir=job_dir, channel_path=channel_path, client=client)
        return
    if command == "shorts_render_one":
        _run_short_render_job(job, job_dir=job_dir, channel_path=channel_path)
        return
    raise ValueError(f"Unknown queue command: {command}")


def _run_shorts_autopilot_job(job: dict, *, job_dir: Path, channel_path: Path, client: BrowserClient) -> None:
    """Run the sequential Shorts autopilot in the worker (Node/Remotion + browser).

    The worker container has Node, so the Short render runs here, not in the web
    app. Each LLM call is a fresh browser ChatGPT send."""
    import json as _json

    from video_agent.shorts.autopilot import run_shorts_autopilot
    from video_agent.shorts.short_builder import build_short
    from video_agent.utils.json_io import read_yaml

    payload: dict = {}
    raw = job.get("payload")
    if raw:
        try:
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            payload = {}
    force = bool(payload.get("force"))
    target_short_id = payload.get("short_id") or None
    channel_config = read_yaml(channel_path)

    def llm_fn(kind: str, prompt: str) -> str:
        return asyncio.run(client.chatgpt_send(prompt))

    def build_short_fn(long_job_dir, short_plan, cfg):
        return build_short(long_job_dir, short_plan, cfg, llm_fn=llm_fn)

    run_shorts_autopilot(
        job_dir,
        channel_config,
        force=force,
        target_short_id=target_short_id,
        build_short_fn=build_short_fn,
    )


def _run_short_render_job(job: dict, *, job_dir: Path, channel_path: Path) -> None:
    """Render one existing Short again without regenerating script/scenes/SEO."""
    import json as _json

    from video_agent.shorts import manifest as manifest_mod
    from video_agent.shorts import paths
    from video_agent.shorts.renderer import render_short_cover, render_short_video
    from video_agent.utils.json_io import read_yaml

    payload: dict = {}
    raw = job.get("payload")
    if raw:
        try:
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            payload = {}
    short_id = payload.get("short_id")
    if not short_id:
        raise ValueError("shorts_render_one requires payload.short_id")

    short_dir = paths.short_dir(job_dir, short_id)
    if not short_dir.exists():
        raise FileNotFoundError(f"Unknown short: {short_id}")

    channel_config = read_yaml(channel_path)
    video_path = render_short_video(short_dir, channel_config)
    cover_path = render_short_cover(short_dir, channel_config)

    status_path = paths.short_status_path(job_dir, short_id)
    status = {}
    if status_path.exists():
        status = _json.loads(status_path.read_text(encoding="utf-8"))
    status.update(
        {
            "short_id": short_id,
            "status": "rendered",
            "rendered": True,
            "video_path": f"shorts/{short_id}/{paths.SHORT_VIDEO_FILE}",
            "cover_path": f"shorts/{short_id}/{paths.SHORT_COVER_FILE}",
        }
    )
    manifest_mod.write_short_status(job_dir, short_id, status)

    manifest_path = paths.manifest_path(job_dir)
    if manifest_path.exists():
        manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("shorts") or []:
            if entry.get("short_id") == short_id:
                entry["status"] = "rendered"
                entry["video_path"] = f"shorts/{short_id}/{paths.SHORT_VIDEO_FILE}"
                entry["cover_path"] = f"shorts/{short_id}/{paths.SHORT_COVER_FILE}"
                break
        manifest_mod.write_manifest(job_dir, manifest)


def run_worker_loop(db_path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting Youtube-AI-Agent Background Worker...")

    queue = JobQueue(db_path)
    recovered = queue.requeue_running_jobs()
    if recovered:
        logger.warning(
            "Recovered %s job(s) stuck in 'running' state after restart; moved back to 'pending'.",
            recovered,
        )

    browser_worker_url = os.environ.get("BROWSER_WORKER_URL", "http://browser-worker:8001")
    client = BrowserClient(browser_worker_url)

    logger.info(f"Worker connected to browser-worker at {browser_worker_url}")
    logger.info("Polling queue.db for jobs...")

    while True:
        try:
            job = queue.get_next_job()
            if job:
                job_id = job["job_id"]
                enforce_approvals = bool(job["enforce_approvals"])
                logger.info(f"Picked up job {job_id} from queue.")
                queue.mark_running(job_id)

                job_dir = get_jobs_root() / job_id
                channel_path = get_channel_path()

                try:
                    logger.info(
                        "Executing queue command %s for job %s...",
                        job.get("command") or "run_all",
                        job_id,
                    )
                    _dispatch_queue_job(
                        job,
                        jobs_root=get_jobs_root(),
                        channel_path=channel_path,
                        client=client,
                    )
                    logger.info(f"Successfully finished job {job_id}.")
                    queue.mark_completed(job_id)
                except Exception as e:
                    logger.error(f"Job {job_id} failed with exception: {e}", exc_info=True)
                    if _is_retryable_exception(e) and queue.mark_retry(job_id, str(e)):
                        next_job = queue.get_job(job_id) or {}
                        logger.warning(
                            "Requeued job %s after retryable failure; attempt %s/%s.",
                            job_id,
                            next_job.get("attempts", "?"),
                            queue.max_attempts,
                        )
                    else:
                        queue.mark_failed(job_id, str(e))
            else:
                time.sleep(2)
        except Exception as e:
            logger.error(f"Exception in worker loop: {e}", exc_info=True)
            time.sleep(5)
