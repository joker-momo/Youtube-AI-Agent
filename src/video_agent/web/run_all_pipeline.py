from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import HTTPException

from video_agent.orchestrator import load_job
from video_agent.orchestrator.browser_client import (
    BrowserClient,
    BrowserClientError,
    LoginRequiredFromWorker,
)
from video_agent.orchestrator.briefing import build_initial_briefing
from video_agent.orchestrator.stages import (
    StageInputMissingError,
    auto_assets_chatgpt_stage,
    auto_idea_research_stage,
    auto_qa_with_rework,
    auto_scenes_stage,
    auto_script_stage,
    auto_seo_stage,
    auto_seo_vidiq_stage,
    auto_thumbnail_image_stage,
    run_render_stage,
    run_review_stage,
    run_whisper_timestamps_stage,
)
from video_agent.notifications.telegram import notify_job_done_with_files
from video_agent.utils.json_io import read_yaml
from video_agent.web.approval_flow import (
    APPROVAL_REQUIRED_STAGES,
    approval_block_for_current_stage,
    load_approvals,
    set_approval,
)


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
        status_code=502,
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
    enforce_approvals: bool = True,
) -> dict:
    """End-to-end pipeline: script -> scenes -> seo -> render -> review."""

    completed: list[dict] = []
    _start_time = time.monotonic()

    async def _record(stage_label: str, output_path: Path) -> None:
        completed.append(
            {"stage": stage_label, "output": str(output_path.relative_to(job_dir))}
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

    # idea_research uses run_vidiq_scores (not the session tab API) so
    # run it BEFORE opening persistent tabs. This way a browser-worker
    # error on the ChatGPT/Claude briefing doesn't hide a gate block.
    start_idx = stage_order.index(pending_stage)
    remaining = set(stage_order[start_idx:])

    if "idea_research" in remaining:
        try:
            await _record_gate_and_stop(
                "idea_research",
                await auto_idea_research_stage(
                    job_dir, channel_path, client.run_vidiq_scores
                ),
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
        # Reload remaining from updated state.
        state = load_job(job_dir)
        new_pending = next((s.name for s in state.stages if s.status != "completed"), None)
        if new_pending is None:
            return {"completed": completed, "state": state.to_dict()}
        remaining = set(stage_order[stage_order.index(new_pending):])
        approvals = load_approvals(job_dir)
        blocked_by = approval_block_for_current_stage(state.current_stage, approvals)
        if enforce_approvals and blocked_by:
            raise HTTPException(
                status_code=409,
                detail=_approval_stop_payload(blocked_by, state),
            )

    # Open ONE ChatGPT temp chat for the whole writing pipeline
    # (script_promote, scenes_promote, seo_promote) and ONE Claude
    # temp chat for the whole QA pipeline (script_qa, scenes_qa,
    # seo_qa). The tabs stay open across stages so the model carries
    # context, and we only close them when /run-all finishes.
    # Send the role+context+constraints briefing ONCE per tab; each
    # stage then only sends its short task message.
    channel_config = read_yaml(channel_path)

    async def _noop_close() -> None:
        return None

    chatgpt_sender = None
    qa_sender = None
    chatgpt_close = _noop_close
    qa_close = _noop_close

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

    async def _send_with_retry(sender, messages, attempts: int = 3) -> str:
        last_exc: BrowserClientError | None = None
        for idx in range(attempts):
            try:
                return await sender(list(messages))
            except BrowserClientError as exc:
                last_exc = exc
                if exc.status_code < 500 or idx == attempts - 1:
                    raise
                await asyncio.sleep(1.0 + idx * 0.5)
        assert last_exc is not None
        raise last_exc

    need_writing_tab = any(
        s in remaining
        for s in ("script", "script_promote", "scenes", "scenes_promote", "seo", "seo_promote")
    )
    need_qa_tab = any(s in remaining for s in ("script_qa", "scenes_qa", "seo_qa"))

    try:
        if need_writing_tab:
            chatgpt_sender, chatgpt_close = await _open_with_retry("chatgpt")
        if need_qa_tab:
            qa_sender, qa_close = await _open_with_retry("claude")

        async def chatgpt_fn(msgs):
            if chatgpt_sender is None:
                raise StageInputMissingError(
                    "ChatGPT session not available for writing stage."
                )
            return await _send_with_retry(chatgpt_sender, msgs)

        async def qa_fn(msgs):
            if qa_sender is None:
                raise StageInputMissingError(
                    "Claude session not available for QA stage."
                )
            return await _send_with_retry(qa_sender, msgs)

        # Brief each tab once before any task message.
        if need_writing_tab:
            await _send_with_retry(
                chatgpt_sender,
                [
                    build_initial_briefing(
                        channel_config,
                        kind="writing",
                        job_id=state.job_id,
                        channel_id=state.channel_id,
                    )
                ],
            )
        if need_qa_tab:
            await _send_with_retry(
                qa_sender,
                [
                    build_initial_briefing(
                        channel_config,
                        kind="qa",
                        job_id=state.job_id,
                        channel_id=state.channel_id,
                    )
                ],
            )

        if "script" in remaining or "script_promote" in remaining:
            await _record_gate_and_stop(
                "script_promote",
                await auto_script_stage(job_dir, channel_path, chatgpt_fn),
            )
        if "script_qa" in remaining:
            await _record(
                "script_qa",
                await auto_qa_with_rework(
                    "script", job_dir, channel_path, chatgpt_fn, qa_fn
                ),
            )
        if "scenes" in remaining or "scenes_promote" in remaining:
            await _record_gate_and_stop(
                "scenes_promote",
                await auto_scenes_stage(job_dir, channel_path, chatgpt_fn),
            )
        if "scenes_qa" in remaining:
            await _record(
                "scenes_qa",
                await auto_qa_with_rework(
                    "scenes", job_dir, channel_path, chatgpt_fn, qa_fn
                ),
            )
        if "seo" in remaining or "seo_promote" in remaining:
            await _record_gate_and_stop(
                "seo_promote",
                await auto_seo_stage(job_dir, channel_path, chatgpt_fn),
            )
        if "seo_qa" in remaining:
            await _record(
                "seo_qa",
                await auto_qa_with_rework(
                    "seo", job_dir, channel_path, chatgpt_fn, qa_fn
                ),
            )
        if "seo_vidiq" in remaining:
            await _record(
                "seo_vidiq",
                await auto_seo_vidiq_stage(
                    job_dir, channel_path, client.run_vidiq_scores
                ),
            )
        if "thumbnail_image" in remaining:
            await _record_gate_and_stop(
                "thumbnail_image",
                await auto_thumbnail_image_stage(
                    job_dir, channel_path, client.generate_image
                ),
            )
        if "assets_chatgpt" in remaining:
            await _record(
                "assets_chatgpt",
                await auto_assets_chatgpt_stage(
                    job_dir, channel_path, client.generate_image
                ),
            )
        if "whisper_timestamps" in remaining:
            await _record("whisper_timestamps", run_whisper_timestamps_stage(job_dir))
        if "render" in remaining:
            await _record("render", run_render_stage(job_dir, channel_path))
        if "review" in remaining:
            await _record("review", run_review_stage(job_dir))
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
    except BrowserClientError as exc:
        state = load_job(job_dir)
        http_exc = _browser_http_exception(exc)
        # Re-pack the detail to include progress.
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
        try:
            await chatgpt_close()
        except Exception:
            pass
        try:
            await qa_close()
        except Exception:
            pass

    state = load_job(job_dir)
    wall = time.monotonic() - _start_time
    await notify_job_done_with_files(
        job_dir.name,
        job_dir=job_dir,
        stages_done=[c["stage"] for c in completed],
        wall_seconds=wall,
    )
    return {"completed": completed, "state": state.to_dict()}
