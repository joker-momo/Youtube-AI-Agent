from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from video_agent.orchestrator.browser_client import BrowserClient
from video_agent.orchestrator.queue import JobQueue
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
                    logger.info(f"Executing pipeline for job {job_id}...")
                    asyncio.run(execute_run_all(
                        job_dir=job_dir,
                        channel_path=channel_path,
                        client=client,
                        enforce_approvals=enforce_approvals,
                    ))
                    logger.info(f"Successfully finished job {job_id}.")
                    queue.mark_completed(job_id)
                except Exception as e:
                    logger.error(f"Job {job_id} failed with exception: {e}", exc_info=True)
                    queue.mark_failed(job_id, str(e))
            else:
                time.sleep(2)
        except Exception as e:
            logger.error(f"Exception in worker loop: {e}", exc_info=True)
            time.sleep(5)
