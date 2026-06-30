from __future__ import annotations

import asyncio
import errno
import fcntl
import logging
import time
from pathlib import Path

from fastapi import HTTPException

from video_agent.contracts import EVENT_LOG
from video_agent.notifications.telegram import (
    notify_job_done_with_files,
    notify_job_failed,
    notify_stage_done,
)
from video_agent.orchestrator import load_job
from video_agent.orchestrator.briefing import build_initial_briefing
from video_agent.orchestrator.browser_client import (
    BrowserClient,
    BrowserClientError,
    LoginRequiredFromWorker,
)
from video_agent.orchestrator.dag import DagScheduler
from video_agent.orchestrator.stages import (
    StageInputMissingError,
    auto_assets_chatgpt_stage,
    auto_idea_research_stage,
    auto_qa_with_rework,
    auto_scenes_stage,
    auto_script_stage,
    auto_seo_stage,
    auto_thumbnail_image_stage,
    run_graphic_images_stage,
    run_render_continuity_qa_stage,
    run_render_stage,
    run_review_stage,
    run_visual_schedule_stage,
    run_visual_spans_stage,
    run_whisper_timestamps_stage,
)
from video_agent.orchestrator.stages._shared import set_dag_mode

# Legacy auto_shorts_* stages are deprecated and intentionally NOT imported.
# Shorts are produced by the sequential Shorts Autopilot (video_agent.shorts).
from video_agent.utils.json_io import read_yaml
from video_agent.utils.logging import EventLogger
from video_agent.web.approval_flow import (
    APPROVAL_REQUIRED_STAGES,
    approval_block_for_current_stage,
    load_approvals,
    set_approval,
)

STOP_REQUEST_FILE = ".stop_requested"
BRIEFING_RESPONSE_TIMEOUT_MS = 60_000
MODEL_TASK_RESPONSE_TIMEOUT_MS = 300_000


def stop_request_path(job_dir: Path) -> Path:
    return job_dir / STOP_REQUEST_FILE


def is_run_locked(job_dir: Path) -> bool:
    lock_path = job_dir / ".run.lock"
    if not lock_path.exists():
        return False
    lock_fd = lock_path.open("a+")
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


def _browser_http_exception(exc: BrowserClientError) -> HTTPException:
    if isinstance(exc, LoginRequiredFromWorker):
        return HTTPException(
            status_code=409,
            detail={
                "error": str(exc),
                "browser_worker_detail": exc.detail,
                "login_required": True,
            },
        )
    return HTTPException(
        status_code=exc.status_code if exc.status_code == 429 else 502,
        detail={
            "error": str(exc),
            "browser_worker_status": exc.status_code,
            "browser_worker_detail": exc.detail,
        },
    )


async def execute_run_all(
    *,
    job_dir: Path,
    channel_path: Path,
    client: BrowserClient,
    enforce_approvals: bool = False,
) -> dict:
    """End-to-end pipeline: script -> scenes -> seo -> render -> review.

    Holds a non-blocking ``fcntl.flock`` on ``<job_dir>/.run.lock`` for the
    duration so two concurrent ``/run-all`` calls on the same job don't
    stomp each other's state writes.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    # Clear stale stop marker from older runs.
    try:
        stop_request_path(job_dir).unlink()
    except FileNotFoundError:
        pass
    lock_path = job_dir / ".run.lock"
    lock_fd = lock_path.open("w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_fd.close()
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise HTTPException(
                status_code=409,
                detail=f"Job {job_dir.name} already has a /run-all in progress.",
            ) from exc
        raise

    try:
        return await _execute_run_all_locked(
            job_dir=job_dir,
            channel_path=channel_path,
            client=client,
            enforce_approvals=enforce_approvals,
        )
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            lock_fd.close()


async def _execute_run_all_locked(
    *,
    job_dir: Path,
    channel_path: Path,
    client: BrowserClient,
    enforce_approvals: bool = False,
) -> dict:
    completed: list[dict] = []
    _start_time = time.monotonic()

    async def _record(stage_label: str, output_path: Path) -> None:
        completed.append(
            {"stage": stage_label, "output": str(output_path.relative_to(job_dir))}
        )

    def _check_stop_requested() -> None:
        if not stop_request_path(job_dir).exists():
            return
        state_now = load_job(job_dir)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Stop requested by operator.",
                "stop_requested": True,
                "completed": completed,
                "stopped_at": state_now.current_stage,
                "state": state_now.to_dict(),
            },
        )

    def _approval_stop_payload(stage_name: str, state) -> dict:
        approvals = load_approvals(job_dir)
        return {
            "error": f"Please confirm {stage_name} before running next stages.",
            "approval_required": stage_name,
            "completed": completed,
            "stopped_at": state.current_stage,
            "state": state.to_dict(),
            "approvals": approvals,
        }

    async def _record_gate_and_stop(stage_name: str, output_path: Path) -> None:
        await _record(stage_name, output_path)
        if stage_name in APPROVAL_REQUIRED_STAGES:
            set_approval(job_dir, stage_name, False)
            state_after = load_job(job_dir)
            if enforce_approvals:
                raise HTTPException(
                    status_code=409,
                    detail=_approval_stop_payload(stage_name, state_after),
                )

    # Resume from the current stage instead of always restarting from
    # script/script_promote. This lets callers continue a partially
    # completed pipeline by re-hitting /run-all.
    state = load_job(job_dir)
    stage_order = [s.name for s in state.stages]
    pending_stage = next((s.name for s in state.stages if s.status != "completed"), None)
    if pending_stage is None:
        return {"completed": completed, "state": state.to_dict()}
    if pending_stage not in stage_order:
        raise HTTPException(
            status_code=409,
            detail={"error": f"Unknown pending stage: {pending_stage}"},
        )
    _check_stop_requested()
    approvals = load_approvals(job_dir)
    blocked_by = approval_block_for_current_stage(state.current_stage, approvals)
    if enforce_approvals and blocked_by:
        raise HTTPException(
            status_code=409,
            detail={
                "error": f"Approval required for {blocked_by} before continuing.",
                "approval_required": blocked_by,
                "completed": completed,
                "stopped_at": state.current_stage,
                "state": state.to_dict(),
                "approvals": approvals,
            },
        )

    # Run lightweight idea research before opening persistent model tabs.
    # ``remaining`` = ONLY the non-completed stages. A contiguous slice from the
    # first pending stage would re-include already-completed LATER stages on a
    # partial resume (e.g. graphic_images reset to pending while thumbnail_image
    # stays completed) and re-run them, tripping their current_stage guard with a
    # spurious 409. Membership is order-independent; the sequential if-blocks below
    # still enforce execution order.
    remaining = {s.name for s in state.stages if s.status != "completed"}

    if "idea_research" in remaining:
        _check_stop_requested()
        try:
            await _record_gate_and_stop(
                "idea_research",
                await auto_idea_research_stage(job_dir, channel_path),
            )
        except StageInputMissingError as exc:
            state = load_job(job_dir)
            raise HTTPException(
                status_code=409,
                detail={
                    "error": str(exc),
                    "completed": completed,
                    "stopped_at": state.current_stage,
                    "state": state.to_dict(),
                },
            ) from exc
        # Notify Telegram that idea research is done; pipeline continues automatically.
        await notify_stage_done(load_job(job_dir).job_id, "idea_research")
        # Reload remaining from updated state.
        state = load_job(job_dir)
        new_pending = next((s.name for s in state.stages if s.status != "completed"), None)
        if new_pending is None:
            return {"completed": completed, "state": state.to_dict()}
        remaining = {s.name for s in state.stages if s.status != "completed"}
        approvals = load_approvals(job_dir)
        blocked_by = approval_block_for_current_stage(state.current_stage, approvals)
        if enforce_approvals and blocked_by:
            raise HTTPException(
                status_code=409,
                detail=_approval_stop_payload(blocked_by, state),
            )

    # Open ONE ChatGPT temp chat for the whole writing pipeline
    # (script_promote, scenes_promote, seo_promote) and ONE Gemini
    # temp chat for the whole QA pipeline (script_qa, scenes_qa,
    # seo_qa). The tabs stay open across stages so the model carries
    # context, and we only close them when /run-all finishes.
    # Send the role+context+constraints briefing ONCE per tab; each
    # stage then only sends its short task message.
    channel_config = read_yaml(channel_path)
    # Opt-in parallel DAG: run the post-scenes stages as concurrent resource lanes
    # (ChatGPT browser ‖ local MPS ‖ cpu). Default off = today's linear path.
    parallel_dag = bool((channel_config.get("pipeline") or {}).get("parallel_dag"))
    set_dag_mode(False)  # spine (script/scenes) runs linear; post-scenes re-enables

    async def _noop_close() -> None:
        return None

    chatgpt_sender = None
    qa_sender = None
    chatgpt_close = _noop_close
    qa_close = _noop_close
    model_sessions_closed = False

    async def _open_with_retry(site: str, attempts: int = 3):
        last_exc: BrowserClientError | None = None
        for idx in range(attempts):
            try:
                return await client.open_persistent_session(site)
            except BrowserClientError as exc:
                last_exc = exc
                # Retry only for transient 5xx worker failures.
                if exc.status_code < 500 or idx == attempts - 1:
                    raise
                await asyncio.sleep(1.0 + idx * 0.5)
        assert last_exc is not None
        raise last_exc

    model_event_logger = EventLogger(job_dir / EVENT_LOG)

    async def _send_with_retry(
        sender,
        messages,
        attempts: int = 3,
        *,
        site: str,
        phase: str,
        response_timeout_ms: int = MODEL_TASK_RESPONSE_TIMEOUT_MS,
    ) -> str:
        last_exc: BrowserClientError | None = None
        for idx in range(attempts):
            try:
                return await sender(
                    list(messages),
                    response_timeout_ms=response_timeout_ms,
                )
            except BrowserClientError as exc:
                last_exc = exc
                if exc.status_code < 500 or idx == attempts - 1:
                    raise
                model_event_logger.log(
                    "MODEL_SEND_RETRY",
                    {
                        "job_id": state.job_id,
                        "site": site,
                        "phase": phase,
                        "attempt": idx + 1,
                        "attempts": attempts,
                        "status_code": exc.status_code,
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(1.0 + idx * 0.5)
        assert last_exc is not None
        raise last_exc

    async def _close_model_sessions() -> None:
        nonlocal chatgpt_sender, qa_sender, chatgpt_close, qa_close, model_sessions_closed
        if model_sessions_closed:
            return
        model_sessions_closed = True
        try:
            await chatgpt_close()
        except Exception:
            pass
        try:
            await qa_close()
        except Exception:
            pass
        chatgpt_sender = None
        qa_sender = None
        chatgpt_close = _noop_close
        qa_close = _noop_close

    need_writing_tab = any(
        s in remaining
        for s in (
            "script",
            "script_promote",
            "script_qa",
            "scenes",
            "scenes_promote",
            "scenes_qa",
            "seo",
            "seo_promote",
            "seo_qa",
        )
    )
    need_qa_tab = any(s in remaining for s in ("script_qa", "scenes_qa", "seo_qa"))

    try:
        _check_stop_requested()
        if need_writing_tab:
            chatgpt_sender, chatgpt_close = await _open_with_retry("chatgpt")
        if need_qa_tab:
            qa_sender, qa_close = await _open_with_retry("gemini")

        writing_briefing = build_initial_briefing(
            channel_config,
            kind="writing",
            job_id=state.job_id,
            channel_id=state.channel_id,
        )
        qa_briefing = build_initial_briefing(
            channel_config,
            kind="qa",
            job_id=state.job_id,
            channel_id=state.channel_id,
        )

        async def _reopen_chatgpt_session() -> None:
            nonlocal chatgpt_sender, chatgpt_close
            try:
                await chatgpt_close()
            except Exception:
                pass
            chatgpt_sender, chatgpt_close = await _open_with_retry("chatgpt")
            await _send_with_retry(
                chatgpt_sender,
                [writing_briefing],
                site="chatgpt",
                phase="briefing",
                response_timeout_ms=BRIEFING_RESPONSE_TIMEOUT_MS,
            )

        async def _reopen_qa_session() -> None:
            nonlocal qa_sender, qa_close
            try:
                await qa_close()
            except Exception:
                pass
            qa_sender, qa_close = await _open_with_retry("gemini")
            await _send_with_retry(
                qa_sender,
                [qa_briefing],
                site="gemini",
                phase="briefing",
                response_timeout_ms=BRIEFING_RESPONSE_TIMEOUT_MS,
            )

        async def chatgpt_fn(msgs):
            nonlocal chatgpt_sender
            if chatgpt_sender is None:
                raise StageInputMissingError(
                    "ChatGPT session not available for writing stage."
                )
            try:
                return await _send_with_retry(
                    chatgpt_sender,
                    msgs,
                    site="chatgpt",
                    phase="task",
                )
            except BrowserClientError as exc:
                if exc.status_code < 500:
                    raise
                await _reopen_chatgpt_session()
                if chatgpt_sender is None:
                    raise
                return await _send_with_retry(
                    chatgpt_sender,
                    msgs,
                    site="chatgpt",
                    phase="task",
                )

        async def qa_fn(msgs):
            nonlocal qa_sender
            if qa_sender is None:
                raise StageInputMissingError(
                    "Gemini session not available for QA stage."
                )
            try:
                return await _send_with_retry(
                    qa_sender,
                    msgs,
                    site="gemini",
                    phase="task",
                )
            except BrowserClientError as exc:
                if exc.status_code < 500:
                    raise
                await _reopen_qa_session()
                if qa_sender is None:
                    raise
                return await _send_with_retry(
                    qa_sender,
                    msgs,
                    site="gemini",
                    phase="task",
                )

        # Brief each tab once before any task message.
        if need_writing_tab:
            try:
                await _send_with_retry(
                    chatgpt_sender,
                    [writing_briefing],
                    site="chatgpt",
                    phase="briefing",
                    response_timeout_ms=BRIEFING_RESPONSE_TIMEOUT_MS,
                )
            except BrowserClientError as exc:
                if exc.status_code < 500:
                    raise
                await _reopen_chatgpt_session()
        if need_qa_tab:
            try:
                await _send_with_retry(
                    qa_sender,
                    [qa_briefing],
                    site="gemini",
                    phase="briefing",
                    response_timeout_ms=BRIEFING_RESPONSE_TIMEOUT_MS,
                )
            except BrowserClientError as exc:
                if exc.status_code < 500:
                    raise
                await _reopen_qa_session()

        if "script" in remaining or "script_promote" in remaining:
            _check_stop_requested()
            await _record_gate_and_stop(
                "script_promote",
                await auto_script_stage(job_dir, channel_path, chatgpt_fn),
            )
        if "script_qa" in remaining:
            _check_stop_requested()
            await _record(
                "script_qa",
                await auto_qa_with_rework(
                    "script", job_dir, channel_path, chatgpt_fn, qa_fn
                ),
            )
        if "scenes" in remaining or "scenes_promote" in remaining:
            _check_stop_requested()
            await _record_gate_and_stop(
                "scenes_promote",
                await auto_scenes_stage(job_dir, channel_path, chatgpt_fn),
            )
        if "scenes_qa" in remaining:
            _check_stop_requested()
            await _record(
                "scenes_qa",
                await auto_qa_with_rework(
                    "scenes", job_dir, channel_path, chatgpt_fn, qa_fn
                ),
            )
        if parallel_dag:
            # Parallel DAG: ChatGPT browser lane ‖ local-MPS/cpu lane run together.
            # set_dag_mode relaxes the single-current_stage guards (this gather +
            # the scheduler own ordering). render/qa below run under the same flag.
            set_dag_mode(True)

            async def _chatgpt_lane() -> None:
                if "seo" in remaining or "seo_promote" in remaining:
                    _check_stop_requested()
                    await _record_gate_and_stop(
                        "seo_promote",
                        await auto_seo_stage(job_dir, channel_path, chatgpt_fn),
                    )
                if "seo_qa" in remaining:
                    _check_stop_requested()
                    await _record(
                        "seo_qa",
                        await auto_qa_with_rework("seo", job_dir, channel_path, chatgpt_fn, qa_fn),
                    )
                await _close_model_sessions()
                if "graphic_images" in remaining:
                    _check_stop_requested()
                    await _record(
                        "graphic_images",
                        await run_graphic_images_stage(job_dir, channel_path, client.generate_image),
                    )
                if "thumbnail_image" in remaining:
                    _check_stop_requested()
                    await _record_gate_and_stop(
                        "thumbnail_image",
                        await auto_thumbnail_image_stage(job_dir, channel_path, client.generate_image),
                    )
                if "assets_chatgpt" in remaining:
                    _check_stop_requested()
                    await _record(
                        "assets_chatgpt",
                        await auto_assets_chatgpt_stage(job_dir, channel_path, client.generate_image),
                    )

            _local_fns = {
                "visual_spans": lambda: run_visual_spans_stage(job_dir, channel_path),
                "whisper_timestamps": lambda: run_whisper_timestamps_stage(job_dir),
                "visual_schedule": lambda: run_visual_schedule_stage(job_dir, channel_path),
            }

            async def _run_local(name: str) -> None:
                _check_stop_requested()
                out = await asyncio.to_thread(_local_fns[name])
                await _record(name, out)

            _local_subset = [
                s
                for s in (
                    "visual_spans", "whisper_timestamps", "visual_schedule",
                )
                if s in remaining
            ]
            await asyncio.gather(
                _chatgpt_lane(),
                DagScheduler().run(_local_subset, _run_local),
            )
        else:
            if "visual_spans" in remaining:
                _check_stop_requested()
                # Report-only long-form visual-span planning; never touches render.
                await _record(
                    "visual_spans",
                    run_visual_spans_stage(job_dir, channel_path),
                )
            if "seo" in remaining or "seo_promote" in remaining:
                _check_stop_requested()
                await _record_gate_and_stop(
                    "seo_promote",
                    await auto_seo_stage(job_dir, channel_path, chatgpt_fn),
                )
            if "seo_qa" in remaining:
                _check_stop_requested()
                await _record(
                    "seo_qa",
                    await auto_qa_with_rework(
                        "seo", job_dir, channel_path, chatgpt_fn, qa_fn
                    ),
                )
            if any(
                s in remaining
                for s in (
                    "graphic_images",
                    "thumbnail_image",
                    "assets_chatgpt",
                    "whisper_timestamps",
                    "render",
                    "review",
                )
            ):
                await _close_model_sessions()
            if "graphic_images" in remaining:
                _check_stop_requested()
                # Generate ChatGPT images for graphic-layout scenes (checklist/warning/
                # quote/cta). Per-scene failures are non-fatal.
                await _record(
                    "graphic_images",
                    await run_graphic_images_stage(job_dir, channel_path, client.generate_image),
                )
            if "thumbnail_image" in remaining:
                _check_stop_requested()
                await _record_gate_and_stop(
                    "thumbnail_image",
                    await auto_thumbnail_image_stage(
                        job_dir, channel_path, client.generate_image
                    ),
                )
            if "assets_chatgpt" in remaining:
                _check_stop_requested()
                await _record(
                    "assets_chatgpt",
                    await auto_assets_chatgpt_stage(
                        job_dir, channel_path, client.generate_image
                    ),
                )
            if "whisper_timestamps" in remaining:
                _check_stop_requested()
                await _record("whisper_timestamps", run_whisper_timestamps_stage(job_dir))
            if "visual_schedule" in remaining:
                _check_stop_requested()
                # Compile the schema-v2 asset schedule; render consumes it only when
                # injected into render_props (gated by visual.span_planning.mode).
                await _record(
                    "visual_schedule",
                    run_visual_schedule_stage(job_dir, channel_path),
                )
        if "render" in remaining:
            _check_stop_requested()
            # Full pipeline ends with notify_job_done_with_files; skip per-render notify to avoid duplicates.
            await _record("render", run_render_stage(job_dir, channel_path, notify_telegram=False))
        if "render_continuity_qa" in remaining:
            _check_stop_requested()
            # Verify span continuity in the rendered video (PASS-skips when there is
            # no compiled schedule / no rendered video).
            await _record(
                "render_continuity_qa",
                run_render_continuity_qa_stage(job_dir, channel_path),
            )
        if "review" in remaining:
            _check_stop_requested()
            await _record("review", run_review_stage(job_dir))
            # Emit a machine-readable long-form review verdict so the Shorts
            # autopilot trigger (after_review_passed) has a stable PASS signal.
            try:
                from video_agent.shorts.review_verdict import write_review_verdict

                write_review_verdict(job_dir)
            except Exception:
                logging.getLogger(__name__).warning(
                    "write_review_verdict failed (non-fatal)", exc_info=True
                )
            # Shorts autopilot auto-trigger (new jobs): only on long Review PASS
            # and when enabled. Fire-and-forget so the long pipeline returns.
            try:
                from video_agent.shorts.trigger import should_run_autopilot_after_review

                if should_run_autopilot_after_review(job_dir, channel_config):
                    from video_agent.web.routes.shorts import enqueue_shorts_autopilot

                    enqueue_shorts_autopilot(job_dir, channel_config, force=False, client=client)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Shorts autopilot auto-trigger failed (non-fatal)", exc_info=True
                )
    except StageInputMissingError as exc:
        state = load_job(job_dir)
        await notify_job_failed(
            state.job_id,
            stopped_at=state.current_stage,
            error=str(exc),
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": str(exc),
                "completed": completed,
                "stopped_at": state.current_stage,
                "state": state.to_dict(),
            },
        ) from exc
    except BrowserClientError as exc:
        # Browser-worker hiccups (502/504/HTTP 5xx) are usually transient.
        # Do NOT notify Telegram / dashboard here — that flips the job to
        # "❌ Failed" even when the queue layer is about to retry. The
        # worker (orchestrator/worker.py) is the only place that fires the
        # user-visible failure alert, and only after retries are exhausted.
        state = load_job(job_dir)
        http_exc = _browser_http_exception(exc)
        detail = (
            http_exc.detail
            if isinstance(http_exc.detail, dict)
            else {"error": http_exc.detail}
        )
        detail["completed"] = completed
        detail["stopped_at"] = state.current_stage
        detail["state"] = state.to_dict()
        raise HTTPException(status_code=http_exc.status_code, detail=detail) from exc
    finally:
        # Always close the persistent tabs so a failure never leaks
        # browser-runtime pages.
        await _close_model_sessions()
        set_dag_mode(False)  # clear DAG flag so a later single-stage run isn't relaxed

    state = load_job(job_dir)
    wall = time.monotonic() - _start_time
    await notify_job_done_with_files(
        job_dir.name,
        job_dir=job_dir,
        stages_done=[c["stage"] for c in completed],
        wall_seconds=wall,
    )
    return {"completed": completed, "state": state.to_dict()}
