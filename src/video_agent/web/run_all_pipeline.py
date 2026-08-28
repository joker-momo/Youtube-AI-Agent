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
from video_agent.orchestrator import load_job, mark_stage_failed
from video_agent.orchestrator.briefing import build_initial_briefing
from video_agent.orchestrator.browser_client import (
    BrowserClient,
    BrowserClientError,
    LoginRequiredFromWorker,
)
from video_agent.orchestrator.dag import STAGE_DEPS, DagScheduler
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
from video_agent.utils.json_io import read_json, read_yaml
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


def _actual_failed_stage(
    state, exc: StageInputMissingError | None = None
) -> str:
    """The stage that actually failed, for failure bookkeeping.

    ``current_stage`` is a linear pointer that FREEZES once the parallel DAG
    takes over (set_dag_mode) — so a render failure used to be recorded on
    whatever stage the pointer froze at (observed: 'visual_spans' marked
    failed with a render error while 'render' stayed in_progress forever;
    bug-446/bug-451). The stage genuinely running is the LAST in_progress one
    in pipeline order; fall back to the pointer for linear-mode runs where
    nothing is in_progress."""
    # Parallel lanes can have several in-progress stages.  The exception's
    # explicit origin is authoritative; pipeline order is only a fallback for
    # legacy/linear call sites that do not attach stage context.
    attributed = getattr(exc, "pipeline_stage", None)
    if isinstance(attributed, str) and any(
        stage.name == attributed for stage in state.stages
    ):
        return attributed

    in_progress = [s.name for s in state.stages if s.status == "in_progress"]
    return in_progress[-1] if in_progress else state.current_stage


async def _run_with_stage_attribution(stage_name: str, operation):
    """Preserve the originating stage on errors crossing ``asyncio.gather``."""
    try:
        return await operation
    except StageInputMissingError as exc:
        exc.pipeline_stage = stage_name
        raise


def _whisper_timestamps_artifact_invalid_reason(job_dir: Path) -> str | None:
    """None when ``whisper_timestamps.json`` exists and carries real, nonempty
    word_segments for at least one scene; otherwise a human-readable reason.

    A stage can be marked ``completed`` in job.json without its output actually
    being usable (e.g. a future regression writes an empty/stub artifact, or
    the file is deleted after completion). Status alone isn't proof the
    subtitle-timing data the renderer needs actually exists — Codex's
    verification of the first bug-462 fix caught exactly this gap: a synthetic
    job with every dep marked 'completed' but no real whisper artifact still
    passed the (status-only) gate."""
    for candidate in (job_dir / "json" / "whisper_timestamps.json", job_dir / "whisper_timestamps.json"):
        if not candidate.exists():
            continue
        try:
            data = read_json(candidate)
        except Exception as exc:
            return f"whisper_timestamps.json unreadable ({exc})"
        scenes = (data or {}).get("scenes") or []
        if not scenes:
            return "whisper_timestamps.json has no scenes"
        if not any(s.get("word_segments") for s in scenes if isinstance(s, dict)):
            return "whisper_timestamps.json exists but every scene has empty word_segments"
        return None
    return "whisper_timestamps.json is missing"


def _assert_stage_deps_satisfied(job_dir: Path, stage_name: str) -> None:
    """Raise if any of ``stage_name``'s declared STAGE_DEPS isn't 'completed',
    or (for ``whisper_timestamps`` specifically) isn't backed by a real,
    nonempty artifact.

    ``render`` depends on ``whisper_timestamps`` (among others) per
    ``dag.STAGE_DEPS``, but render/render_continuity_qa/review run OUTSIDE
    DagScheduler (only the parallel DAG lane's own subset enforces deps) —
    they were previously gated only on "not yet completed" membership in
    ``remaining``, with no check that their real dependencies actually
    succeeded. A dependency that silently failed inside the DAG lane (bug-461)
    let render proceed on stale/incomplete data (e.g. render_props.json with
    zero word_segments because whisper_timestamps never got to write them).
    Status alone is not sufficient proof of that (bug-462 verification gap) —
    whisper_timestamps additionally needs its artifact content checked, since
    ``pipeline.render_operator_job`` silently no-ops the word_segments merge
    when the file is missing/empty rather than raising. Call this right before
    dispatching a stage that has cross-lane deps."""
    state = load_job(job_dir)
    deps = STAGE_DEPS.get(stage_name, [])
    unmet: list[str] = []
    for dep in deps:
        dep_state = next((s for s in state.stages if s.name == dep), None)
        if dep_state is None:
            continue
        if dep_state.status != "completed":
            unmet.append(f"{dep} (status={dep_state.status}, error={dep_state.error!r})")
        elif dep == "whisper_timestamps":
            reason = _whisper_timestamps_artifact_invalid_reason(job_dir)
            if reason is not None:
                unmet.append(f"{dep} (status=completed, but {reason})")
    if unmet:
        raise StageInputMissingError(
            f"Cannot run {stage_name}: dependency not completed -- " + "; ".join(unmet)
        )


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
            mark_stage_failed(job_dir, _actual_failed_stage(state, exc), str(exc))
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
                await auto_scenes_stage(
                    job_dir,
                    channel_path,
                    chatgpt_fn,
                    # Prewarm Gemini QA per saved batch while ChatGPT keeps
                    # generating; scenes_qa then reuses the fresh verdicts.
                    qa_session_fn=qa_fn if "scenes_qa" in remaining else None,
                ),
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
                        await _run_with_stage_attribution(
                            "seo_promote",
                            auto_seo_stage(job_dir, channel_path, chatgpt_fn),
                        ),
                    )
                if "seo_qa" in remaining:
                    _check_stop_requested()
                    await _record(
                        "seo_qa",
                        await _run_with_stage_attribution(
                            "seo_qa",
                            auto_qa_with_rework(
                                "seo", job_dir, channel_path, chatgpt_fn, qa_fn
                            ),
                        ),
                    )
                await _close_model_sessions()
                if "graphic_images" in remaining:
                    _check_stop_requested()
                    await _record(
                        "graphic_images",
                        await _run_with_stage_attribution(
                            "graphic_images",
                            run_graphic_images_stage(
                                job_dir, channel_path, client.generate_image
                            ),
                        ),
                    )
                if "thumbnail_image" in remaining:
                    _check_stop_requested()
                    await _record_gate_and_stop(
                        "thumbnail_image",
                        await _run_with_stage_attribution(
                            "thumbnail_image",
                            auto_thumbnail_image_stage(
                                job_dir, channel_path, client.generate_image
                            ),
                        ),
                    )
                if "assets_chatgpt" in remaining:
                    _check_stop_requested()
                    await _record(
                        "assets_chatgpt",
                        await _run_with_stage_attribution(
                            "assets_chatgpt",
                            auto_assets_chatgpt_stage(
                                job_dir, channel_path, client.generate_image
                            ),
                        ),
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
            _, _dag_results = await asyncio.gather(
                _chatgpt_lane(),
                DagScheduler().run(_local_subset, _run_local),
            )
            # DagScheduler catches per-stage exceptions internally so ONE lane's
            # failure never cancels an independent lane, but its result dict used
            # to be discarded here -- a failed stage (e.g. whisper_timestamps
            # timing out) stayed job.json-"pending" forever with no error
            # recorded, and nothing downstream checked it before rendering
            # anyway (bug-461). Persist the real outcome now.
            for _stage_name, _outcome in _dag_results.items():
                if _outcome == "failed":
                    mark_stage_failed(
                        job_dir, _stage_name,
                        f"{_stage_name} failed inside the parallel DAG lane "
                        "(see worker log 'DAG stage failed' for the traceback).",
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
            _assert_stage_deps_satisfied(job_dir, "render")
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
            # Emit a machine-readable long-form review verdict for explicit
            # Shorts workflows without auto-enqueuing Shorts from run_all.
            try:
                from video_agent.shorts.review_verdict import write_review_verdict

                write_review_verdict(job_dir)
            except Exception:
                logging.getLogger(__name__).warning(
                    "write_review_verdict failed (non-fatal)", exc_info=True
                )
    except StageInputMissingError as exc:
        state = load_job(job_dir)
        # Persist the failure so job.json reflects the real halt — otherwise the
        # stage stays 'pending' and dashboard/timeline show a stale in-progress
        # job forever (bug-421), and status derivation misreports it as an
        # approval block (bug-424).
        mark_stage_failed(job_dir, _actual_failed_stage(state, exc), str(exc))
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
