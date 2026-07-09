from __future__ import annotations

import asyncio
import logging
import os
import time
import threading
from pathlib import Path

from fastapi import HTTPException

from video_agent.orchestrator.browser_client import BrowserClient
from video_agent.orchestrator.queue import JobQueue
from video_agent.orchestrator.stages import (
    auto_thumbnail_image_stage,
    run_render_stage,
    run_whisper_timestamps_stage,
)
from video_agent.web.run_all_pipeline import execute_run_all, is_run_locked

logger = logging.getLogger("video_agent.worker")


def get_jobs_root() -> Path:
    env = os.environ.get("JOBS_DIR")
    if env:
        return Path(env)
    from video_agent.contracts import repo_root

    return repo_root() / "jobs"


def get_channel_path() -> Path:
    """Resolve the channel config path for the worker.

    Prefer ``CHANNEL_CONFIG`` when it points at an existing file; otherwise fall
    back to the **repo-relative** default — NOT the Docker ``/app`` path, which
    does not exist on local/Mac runs. A wrong/missing ``CHANNEL_CONFIG`` here
    silently drops opt-in flags (the channel reads as flagless → features never
    injected), which was bug-394. Logs the resolved path so a misconfigured env
    is visible instead of silent.
    """
    from video_agent.contracts import repo_root

    env = os.environ.get("CHANNEL_CONFIG")
    if env and Path(env).exists():
        logger.info("Worker channel config: %s (from CHANNEL_CONFIG)", env)
        return Path(env)
    if env:
        logger.warning("CHANNEL_CONFIG=%s missing; falling back to repo default", env)
    default = repo_root() / "configs" / "vida-plena-45" / "channel.yaml"
    logger.info("Worker channel config: %s (repo default)", default)
    return default


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
            browser_detail = detail.get("browser_worker_detail")
            if isinstance(browser_detail, dict) and browser_detail.get("quota_exhausted"):
                return False
        if 400 <= exc.status_code < 500 and exc.status_code not in {408, 429}:
            return False
    return True


def _maybe_trigger_shorts_autopilot(
    *, job_id: str, job_dir: Path, channel_path: Path, client: BrowserClient, command: str
) -> None:
    """Long-form completion must not enqueue Shorts automatically.

    Shorts generation remains available through the explicit Shorts routes and
    queue commands. Keeping long ``run_all`` completion as the end of the long
    pipeline prevents a finished long video from immediately starting another
    browser/render workload under the same job id.
    """
    return


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
    if command == "shorts_prepare_drafts":
        _run_shorts_prepare_drafts_job(job, job_dir=job_dir, channel_path=channel_path, client=client)
        return
    if command == "shorts_generate_ideas":
        _run_shorts_generate_ideas_job(job, job_dir=job_dir, channel_path=channel_path, client=client)
        return
    if command == "shorts_render_selected_ideas":
        _run_shorts_render_selected_ideas_job(job, job_dir=job_dir, channel_path=channel_path, client=client)
        return
    if command == "shorts_render_one":
        _run_short_render_job(job, job_dir=job_dir, channel_path=channel_path)
        return
    if command == "shorts_render_infographic":
        _run_short_infographic_job(job, job_dir=job_dir, channel_path=channel_path, client=client)
        return
    if command == "shorts_confirm_render":
        _run_shorts_confirm_render_job(job, job_dir=job_dir, channel_path=channel_path)
        return
    raise ValueError(f"Unknown queue command: {command}")


def _run_short_infographic_job(job: dict, *, job_dir: Path, channel_path: Path, client: BrowserClient) -> None:
    """Build infographic Shorts (one dense AI poster + voiceover) for selected ideas."""
    import json as _json

    from video_agent.shorts.infographic.build import render_selected_infographic_ideas
    from video_agent.shorts.infographic.render import make_infographic_render_fn
    from video_agent.shorts.infographic.voiceover import synthesize_infographic_voiceover
    from video_agent.utils.json_io import read_yaml

    payload: dict = {}
    raw = job.get("payload")
    if raw:
        try:
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            payload = {}
    idea_ids = list(payload.get("idea_ids") or [])
    force = bool(payload.get("force"))
    channel_config = read_yaml(channel_path)

    def chatgpt_fn(prompt: str) -> str:
        from video_agent.shorts.llm import chatgpt_send_with_recovery
        return asyncio.run(chatgpt_send_with_recovery(client, prompt))

    render_selected_infographic_ideas(
        job_dir,
        channel_config,
        idea_ids,
        image_fn=client.generate_image,  # async; awaited inside run_infographic_short
        llm_fn=chatgpt_fn,
        tts_fn=synthesize_infographic_voiceover,
        render_fn=make_infographic_render_fn(channel_config),
        read_text_fn=None,  # v1: QA disabled
        force=force,
    )


class _JobHeartbeat:
    def __init__(self, queue: JobQueue, job_id: str, interval_seconds: float = 15.0):
        self.queue = queue
        self.job_id = job_id
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"job-heartbeat-{job_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.queue.touch_running(self.job_id)
            except Exception:
                logger.warning("Failed to update heartbeat for job %s.", self.job_id, exc_info=True)


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

    # Spec v6 §2 + §3: ChatGPT for planner/script/scenes, Gemini for QA.
    # Every call opens a fresh temporary conversation via _send().
    def chatgpt_fn(prompt: str) -> str:
        from video_agent.shorts.llm import chatgpt_send_with_recovery
        return asyncio.run(chatgpt_send_with_recovery(client, prompt))

    def gemini_fn(prompt: str) -> str:
        return asyncio.run(client.gemini_send(prompt))

    def plan_fn(long_job_dir, channel_config, requested_count=None):
        from video_agent.shorts.planner import plan_shorts_from_long_video
        return plan_shorts_from_long_video(
            long_job_dir, channel_config, requested_count, llm_fn=chatgpt_fn,
        )

    def build_short_fn(long_job_dir, short_plan, cfg):
        # Shorts have no thumbnail/cover deliverable (YouTube Shorts use an auto
        # frame), so build_short takes no thumbnail_fn.
        return build_short(
            long_job_dir, short_plan, cfg,
            llm_fn=chatgpt_fn, gemini_fn=gemini_fn,
        )

    run_shorts_autopilot(
        job_dir,
        channel_config,
        force=force,
        target_short_id=target_short_id,
        plan_fn=plan_fn,
        build_short_fn=build_short_fn,
    )


def _run_shorts_prepare_drafts_job(job: dict, *, job_dir: Path, channel_path: Path, client: BrowserClient) -> None:
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
    channel_config = read_yaml(channel_path)

    def chatgpt_fn(prompt: str) -> str:
        from video_agent.shorts.llm import chatgpt_send_with_recovery
        return asyncio.run(chatgpt_send_with_recovery(client, prompt))

    def gemini_fn(prompt: str) -> str:
        return asyncio.run(client.gemini_send(prompt))

    def plan_fn(long_job_dir, channel_config, requested_count=None):
        from video_agent.shorts.planner import plan_shorts_from_long_video
        return plan_shorts_from_long_video(
            long_job_dir, channel_config, requested_count, llm_fn=chatgpt_fn,
        )

    def build_short_fn(long_job_dir, short_plan, cfg, **kwargs):
        # Shorts have no thumbnail/cover deliverable — no thumbnail_fn.
        return build_short(
            long_job_dir, short_plan, cfg,
            llm_fn=chatgpt_fn, gemini_fn=gemini_fn,
            **kwargs,
        )

    run_shorts_autopilot(
        job_dir,
        channel_config,
        force=force,
        trigger="shorts_studio_prepare",
        plan_fn=plan_fn,
        build_short_fn=build_short_fn,
        require_render_confirmation=True,
    )


def _run_shorts_generate_ideas_job(job: dict, *, job_dir: Path, channel_path: Path, client: BrowserClient) -> None:
    import json as _json

    from video_agent.shorts.idea_generator import generate_short_ideas
    from video_agent.utils.json_io import read_yaml

    payload: dict = {}
    raw = job.get("payload")
    if raw:
        try:
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            payload = {}
    target_count = int(payload.get("target_count") or 10)
    channel_config = read_yaml(channel_path)

    def chatgpt_fn(prompt: str) -> str:
        from video_agent.shorts.llm import chatgpt_send_with_recovery
        return asyncio.run(chatgpt_send_with_recovery(client, prompt))

    generate_short_ideas(job_dir, channel_config, llm_fn=chatgpt_fn, target_count=target_count)


def _run_shorts_render_selected_ideas_job(job: dict, *, job_dir: Path, channel_path: Path, client: BrowserClient) -> None:
    import json as _json

    from video_agent.shorts.short_builder import build_short
    from video_agent.shorts.synthesis import render_selected_short_ideas
    from video_agent.utils.json_io import read_yaml

    payload: dict = {}
    raw = job.get("payload")
    if raw:
        try:
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            payload = {}
    idea_ids = list(payload.get("idea_ids") or [])
    force = bool(payload.get("force"))
    channel_config = read_yaml(channel_path)

    def chatgpt_fn(prompt: str) -> str:
        from video_agent.shorts.llm import chatgpt_send_with_recovery
        return asyncio.run(chatgpt_send_with_recovery(client, prompt))

    def gemini_fn(prompt: str) -> str:
        return asyncio.run(client.gemini_send(prompt))

    def build_short_fn(long_job_dir, short_plan, cfg, **kwargs):
        # Shorts have no thumbnail/cover deliverable — no thumbnail_fn.
        return build_short(
            long_job_dir,
            short_plan,
            cfg,
            llm_fn=chatgpt_fn,
            gemini_fn=gemini_fn,
            **kwargs,
        )

    render_selected_short_ideas(
        job_dir,
        channel_config,
        idea_ids,
        build_short_fn=build_short_fn,
        force=force,
    )


def _run_short_render_job(job: dict, *, job_dir: Path, channel_path: Path) -> None:
    """Render one existing Short again without regenerating script/scenes/SEO."""
    import json as _json
    import datetime

    from video_agent.shorts import manifest as manifest_mod
    from video_agent.shorts import paths
    from video_agent.shorts.renderer import render_short_video
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

    # Load status document or initialize
    status_path = paths.short_status_path(job_dir, short_id)
    status = {}
    if status_path.exists():
        status = _json.loads(status_path.read_text(encoding="utf-8"))

    # Initialize default stages if missing (backward compatibility)
    if "stages" not in status:
        script_ok = (short_dir / paths.SHORT_SCRIPT_FILE).exists()
        qa_script_ok = (short_dir / paths.SHORT_SCRIPT_QA_FILE).exists()
        scenes_ok = (short_dir / paths.SHORT_SCENES_FILE).exists()
        qa_scenes_ok = (short_dir / paths.SHORT_SCENES_QA_FILE).exists()
        seo_ok = (short_dir / paths.SHORT_SEO_FILE).exists()

        status["stages"] = [
            {"name": "script", "label": "Short Script", "status": "completed" if script_ok else "failed", "started_at": None, "completed_at": None, "actual_seconds": None},
            {"name": "qa_script", "label": "QA Script", "status": "completed" if qa_script_ok else "failed", "started_at": None, "completed_at": None, "actual_seconds": None},
            {"name": "scenes", "label": "Short Scenes", "status": "completed" if scenes_ok else "failed", "started_at": None, "completed_at": None, "actual_seconds": None},
            {"name": "qa_scenes", "label": "QA Scenes", "status": "completed" if qa_scenes_ok else "failed", "started_at": None, "completed_at": None, "actual_seconds": None},
            {"name": "seo", "label": "Short SEO", "status": "completed" if seo_ok else "failed", "started_at": None, "completed_at": None, "actual_seconds": None},
            {"name": "audio", "label": "Audio TTS & Mix", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
            {"name": "render", "label": "Video & Cover Render", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        ]

    def update_stage(stage_name: str, new_status: str, **kwargs):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for s in status["stages"]:
            if s["name"] == stage_name:
                s["status"] = new_status
                if new_status == "in_progress" and not s.get("started_at"):
                    s["started_at"] = now_str
                elif new_status in ("completed", "failed", "skipped"):
                    if not s.get("started_at"):
                        s["started_at"] = now_str
                    s["completed_at"] = now_str
                    try:
                        from datetime import datetime as dt
                        t_start = dt.fromisoformat(s["started_at"].replace("Z", "+00:00"))
                        t_end = dt.fromisoformat(now_str.replace("Z", "+00:00"))
                        s["actual_seconds"] = max(0, int((t_end - t_start).total_seconds()))
                    except Exception:
                        s["actual_seconds"] = 1
                for k, v in kwargs.items():
                    s[k] = v
                break
        status["updated_at"] = now_str
        manifest_mod.write_short_status(job_dir, short_id, status)

    def check_stop():
        if (job_dir / ".stop_requested").exists() or (short_dir / ".stop_requested").exists():
            from fastapi import HTTPException
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Stop requested by operator.",
                    "stop_requested": True,
                }
            )

    # Ensure pre-render files exist
    missing_files = []
    for s in status.get("stages") or []:
        if s["name"] == "script" and not paths.resolve_short_json(short_dir, paths.SHORT_SCRIPT_FILE).exists():
            s["status"] = "failed"
            missing_files.append("Short Script")
        elif s["name"] == "qa_script" and not paths.resolve_short_json(short_dir, paths.SHORT_SCRIPT_QA_FILE).exists():
            s["status"] = "failed"
            missing_files.append("QA Script")
        elif s["name"] == "scenes" and not paths.resolve_short_json(short_dir, paths.SHORT_SCENES_FILE).exists():
            s["status"] = "failed"
            missing_files.append("Short Scenes")
        elif s["name"] == "qa_scenes" and not paths.resolve_short_json(short_dir, paths.SHORT_SCENES_QA_FILE).exists():
            s["status"] = "failed"
            missing_files.append("QA Scenes")
        elif s["name"] == "seo" and not paths.resolve_short_json(short_dir, paths.SHORT_SEO_FILE).exists():
            s["status"] = "failed"
            missing_files.append("Short SEO")

            
    if missing_files:
        status["status"] = "failed"
        manifest_mod.write_short_status(job_dir, short_id, status)
        raise FileNotFoundError(
            f"Cannot run render-only job for short {short_id}. Missing required files: {', '.join(missing_files)}. "
            f"Please regenerate this Short instead of running render/resume."
        )

    # Set status to rendering
    status["status"] = "rendering"
    manifest_mod.write_short_status(job_dir, short_id, status)
    
    manifest_path = paths.manifest_path(job_dir)
    if manifest_path.exists():
        try:
            manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest.get("shorts") or []:
                if entry.get("short_id") == short_id:
                    entry["status"] = "rendering"
            manifest_mod.write_manifest(job_dir, manifest)
        except Exception:
            pass

    # 1. Audio TTS & Mix
    update_stage("audio", "in_progress")
    try:
        check_stop()
        # Remotion materialize will generate cached audio if not present, so we mark completed
        update_stage("audio", "completed")
    except Exception as exc:
        update_stage("audio", "failed")
        status.update({"status": "failed"})
        manifest_mod.write_short_status(job_dir, short_id, status)
        raise exc

    # 2. Video & Cover Render
    update_stage("render", "in_progress")
    try:
        check_stop()
        render_short_video(short_dir, channel_config)
        update_stage("render", "completed")
        status.update(
            {
                "short_id": short_id,
                "status": "rendered",
                "rendered": True,
                "video_path": f"shorts/{short_id}/{paths.SHORT_VIDEO_FILE}",
            }
        )
    except Exception as exc:
        update_stage("render", "failed")
        status.update({"status": "failed"})
        manifest_mod.write_short_status(job_dir, short_id, status)
        raise exc
    manifest_mod.write_short_status(job_dir, short_id, status)

    # Phone-publishing handoff: ship short.mp4 + cover + copy-paste SEO to
    # Telegram so the operator can post from mobile. Best-effort, never raises.
    from video_agent.notifications.telegram import notify_short_rendered_sync

    notify_short_rendered_sync(job_dir, short_id)

    manifest_path = paths.manifest_path(job_dir)
    if manifest_path.exists():
        manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("shorts") or []:
            if entry.get("short_id") == short_id:
                entry["status"] = "rendered"
                entry["rendered"] = True
                entry["qa_verdict"] = status.get("qa_verdict", "PASS")
                entry["requires_render_confirmation"] = False
                entry["video_path"] = f"shorts/{short_id}/{paths.SHORT_VIDEO_FILE}"
                break
        rendered_count = sum(1 for entry in manifest.get("shorts") or [] if entry.get("status") == "rendered")
        ready_count = sum(1 for entry in manifest.get("shorts") or [] if entry.get("status") == "ready_for_render")
        review_count = sum(1 for entry in manifest.get("shorts") or [] if entry.get("status") == "needs_review")
        failed_count = sum(1 for entry in manifest.get("shorts") or [] if entry.get("status") == "failed")
        if rendered_count and ready_count == 0 and review_count == 0 and failed_count == 0:
            manifest["status"] = "rendered"
        elif ready_count:
            manifest["status"] = "completed_with_warnings" if (review_count or failed_count) else "drafts_ready"
        elif review_count or failed_count:
            manifest["status"] = "completed_with_warnings"
        else:
            manifest["status"] = "rendered"
        manifest_mod.write_manifest(job_dir, manifest)
        run_path = paths.autopilot_run_path(job_dir)
        if run_path.exists():
            run = _json.loads(run_path.read_text(encoding="utf-8"))
            run["rendered_count"] = rendered_count
            run["ready_for_render_count"] = ready_count
            run["needs_review_count"] = review_count
            run["failed_count"] = failed_count
            run["status"] = manifest.get("status") or run.get("status")
            manifest_mod.write_autopilot_run(job_dir, run)


def _run_shorts_confirm_render_job(job: dict, *, job_dir: Path, channel_path: Path) -> None:
    import json as _json

    payload: dict = {}
    raw = job.get("payload")
    if raw:
        try:
            payload = _json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            payload = {}
    short_ids = list(payload.get("short_ids") or [])
    for short_id in short_ids:
        _run_short_render_job(
            {"job_id": job.get("job_id"), "payload": _json.dumps({"short_id": short_id})},
            job_dir=job_dir,
            channel_path=channel_path,
        )


def run_worker_loop(db_path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting Youtube-AI-Agent Background Worker...")

    queue = JobQueue(db_path)
    recovered = queue.requeue_stale_running_jobs()
    if recovered:
        logger.warning(
            "Recovered %s stale running job(s) after restart; moved back to 'pending'.",
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
                job_dir = get_jobs_root() / job_id
                channel_path = get_channel_path()
                command = job.get("command") or "run_all"
                if command == "run_all" and is_run_locked(job_dir):
                    logger.warning(
                        "Job %s already has a live /run-all lock; marking queue running and skipping duplicate dispatch.",
                        job_id,
                    )
                    queue.mark_running(job_id)
                    continue

                queue.mark_running(job_id)
                heartbeat = _JobHeartbeat(queue, job_id)
                heartbeat.start()

                try:
                    logger.info(
                        "Executing queue command %s for job %s...",
                        command,
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
                    _maybe_trigger_shorts_autopilot(
                        job_id=job_id, job_dir=job_dir, channel_path=channel_path,
                        client=client, command=command,
                    )
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
                        # FINAL failure: notify Telegram/dashboard now (not
                        # during execute_run_all, which would fire on every
                        # transient 502 before the worker decides to retry).
                        try:
                            from video_agent.notifications.telegram import notify_job_failed
                            stopped_at = ""
                            try:
                                from video_agent.orchestrator import load_job
                                stopped_at = load_job(get_jobs_root() / job_id).current_stage or ""
                            except Exception:
                                pass
                            asyncio.run(notify_job_failed(
                                job_id, stopped_at=stopped_at, error=str(e),
                            ))
                        except Exception:
                            logger.exception("notify_job_failed crashed for %s", job_id)
                finally:
                    heartbeat.stop()
            else:
                stale = queue.requeue_stale_running_jobs()
                if stale:
                    logger.warning(
                        "Recovered %s stale running job(s); moved back to 'pending'.",
                        stale,
                    )
                time.sleep(2)
        except Exception as e:
            logger.error(f"Exception in worker loop: {e}", exc_info=True)
            time.sleep(5)
