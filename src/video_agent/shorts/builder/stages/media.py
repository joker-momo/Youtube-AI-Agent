"""Asset, audio, SEO, render & lifecycle stages for the Short builder."""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import (
    anti_ai,
    call_budget,
    humanization,
    llm_history,
    performance_memory,
    paths,
    qa,
    retention_plan as retention_plan_builder,
    short_scene_builder,
    short_script_builder,
    short_seo_builder,
    source_map,
    validate_scenes,
    visual_rhythm,
)
from video_agent.shorts.idea_preservation import allowed_spoken_points_from_contract
from video_agent.shorts.manifest import write_short_status
from video_agent.storage.atomic import atomic_write_json
from video_agent.shorts.retry_memory import (
    ScenePipelineState,
    assert_latest_scenes_ready,
    RetryMemory,
    RetryIssue,
    add_or_update_issue,
    resolve_issue_by_id,
    suppress_issue_by_id,
    generate_cumulative_feedback,
    make_stable_issue_id,
    save_retry_memory,
    load_retry_memory,
)

# Backwards-compatible facade: tests and callers import/patch these via
# video_agent.shorts.short_builder.<name>.
from video_agent.shorts.builder.defaults import (
    _default_llm_fn, _default_background_fn, _default_tts_fn,
    _default_mix_fn, _default_render_fn, _default_cover_fn,
)
from video_agent.shorts.builder.qa_gate import (
    HARD_SCENE_VALIDATION_TYPES,
    _HARD_QA_ISSUE_MARKERS,
    _scene_qa_has_hard_fail, has_hard_fail, _qa_blocker_details,
    check_and_apply_auto_pass, should_fallback_to_gemini_scene_qa,
    build_script_compression_feedback,
)
from video_agent.shorts.builder.retry import (
    MAX_PROVIDER_RETRIES_PER_CALL,
    record_retry_event, wrap_llm_with_provider_retries,
)
from video_agent.shorts.builder.snapshots import (
    _parse, _cover_text, _normalized_script_hash, _normalized_scene_hash,
    _scene_duration_sum, _snapshot_scene_durations, _restore_scene_durations,
)
from video_agent.shorts.builder.status import _update_short_stage
from video_agent.shorts.builder.render_props import _write_render_props
from video_agent.shorts.builder.context import BuildContext

from video_agent.shorts.builder.types import (
    StageSignal,
    StageResult,
    _PROCEED,
    SCORE_AUTOPASS_AVERAGE,
    MAX_QA_RETRIES_PER_STAGE,
    MAX_SCENE_REGEN_ATTEMPTS,
    MAX_SCRIPT_REGEN_ATTEMPTS,
)


def _stage_retention_plan(ctx: BuildContext) -> dict[str, Any] | None:
    """Stage: retention_plan.

    Returns None to continue; returns a terminal failure dict to abort.
    On unexpected exception, re-raises (caller must propagate).
    """
    short_id = ctx.short_plan["short_id"]
    ctx.update_stage("retention_plan", "in_progress")
    try:
        ctx.check_stop()
        retention_plan = retention_plan_builder.build_retention_plan(
            ctx.long_job_dir,
            ctx.plan_for_prompt,
            ctx.channel_config,
            ctx.llm_fn,
            source_artifacts=ctx.source_artifacts,
        )
        ctx.update_stage(
            "retention_plan",
            "completed",
            generation_mode=retention_plan.get("generation_mode"),
            input_hash=retention_plan.get("input_hash"),
        )
        ctx.extras["retention_plan"] = retention_plan
        return None
    except Exception as exc:
        ctx.update_stage("retention_plan", "failed", error=str(exc))
        ctx.status["status"] = "failed"
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc


def _stage_render(ctx: BuildContext) -> None:
    """Stage: render (video + cover).

    On success, writes video_path / cover_path to ctx.extras.
    On failure, updates status and re-raises.
    """
    short_id = ctx.short_plan["short_id"]
    sd = ctx.short_dir
    ctx.update_stage("render", "in_progress")
    try:
        ctx.check_stop()
        stop_file = ctx.long_job_dir / ".stop_requested"
        try:
            video_path = ctx.render_fn(sd, ctx.channel_config, stop_request_path=stop_file)
        except TypeError:
            # Injected render_fn without the kwarg (back-compat).
            video_path = ctx.render_fn(sd, ctx.channel_config)
        ctx.check_stop()
        cover_path = ctx.cover_fn(sd, ctx.channel_config)
        ctx.update_stage("render", "completed")
        ctx.extras["video_path"] = video_path
        ctx.extras["cover_path"] = cover_path
    except Exception as exc:
        ctx.update_stage("render", "failed", error=str(exc))
        ctx.status["status"] = "failed"
        ctx.status["error"] = str(exc)
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc


def _stage_performance_memory(ctx: BuildContext) -> None:
    """Stage: performance_memory (final, after render).

    Updates the performance memory record to 'rendered' status.
    """
    short_id = ctx.short_plan["short_id"]
    anti_ai_regeneration_attempts = ctx.extras.get("anti_ai_regeneration_attempts", 0)
    short_script = ctx.extras.get("short_script", {})
    short_scenes = ctx.extras.get("short_scenes", {})
    retention_plan = ctx.extras.get("retention_plan", {})
    ctx.status.update({
        "status": "rendered",
        "rendered": True,
        "uploaded": False,
        "youtube_url": "",
        "requires_user_review": False,
        "requires_render_confirmation": False,
        "video_path": f"shorts/{short_id}/{paths.SHORT_OUTPUTS_SUBDIR}/{paths.SHORT_VIDEO_FILE}",
        "cover_path": f"shorts/{short_id}/{paths.SHORT_OUTPUTS_SUBDIR}/{paths.SHORT_COVER_FILE}",
        "anti_ai_regeneration_attempts": anti_ai_regeneration_attempts,
    })
    write_short_status(ctx.long_job_dir, short_id, ctx.status)
    ctx.update_stage("performance_memory", "in_progress")
    performance_memory.write_performance_memory(
        ctx.long_job_dir,
        short_id,
        ctx.plan_for_prompt,
        short_script,
        short_scenes,
        retention_plan,
        thumbnail_meta={
            "status": "completed",
            "mode": "disabled_for_shorts_render",
            "image_generation_called": False,
            "thumbnail_path": None,
        },
        status="rendered",
    )
    ctx.update_stage("performance_memory", "completed", memory_status="rendered")


def _stage_background(ctx: BuildContext) -> None:
    """Stage: background assets — resolve each scene's background source.

    Reads short_scenes + scene_pipeline_state from ctx.extras. Raises on failure.
    """
    short_id = ctx.short_plan["short_id"]
    sd = ctx.short_dir
    short_scenes = ctx.extras["short_scenes"]
    state = ctx.extras["scene_pipeline_state"]
    ctx.update_stage("background", "in_progress")
    try:
        ctx.check_stop()
        assert_latest_scenes_ready(state)
        bg_sources: list[dict[str, Any]] = []

        def _on_scene_bg(info: dict[str, Any]) -> None:
            phase = info.get("phase")
            # Record the per-scene source only once acquisition resolved.
            if phase == "resolved":
                bg_sources.append({
                    "scene_id": info.get("scene_id"),
                    "background_source": info.get("background_source"),
                })
            ctx.update_stage(
                "background",
                "in_progress",
                current_scene=(int(info.get("index", 0)) + 1),
                total_scenes=info.get("total"),
                last_scene_id=info.get("scene_id"),
                # While fetching show "…", then the resolved source label.
                last_source=(info.get("background_source") if phase == "resolved" else "fetching…"),
                scene_phase=phase,
            )

        ctx.background_fn(sd, short_scenes, ctx.channel_config, on_scene_resolved=_on_scene_bg)
        ctx.update_stage("background", "completed", per_scene=bg_sources)
    except Exception as exc:
        ctx.update_stage("background", "failed", error=str(exc))
        ctx.status["status"] = "failed"
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc


def _stage_audio(ctx: BuildContext) -> StageResult:
    """Stage: audio TTS + audio_fit check.

    Reads from ctx.extras: short_scenes, scene_pipeline_state, short_script, and
    the counters total_regeneration_attempts / scenes_attempts /
    structural_attempts / product_attempts (for the failure payload). Writes
    narration_wav + duration_sec back to ctx.extras. Returns a terminal status
    payload when audio_fit fails; otherwise PROCEED. Raises on unexpected errors.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    sd = ctx.short_dir
    _jd = ctx.json_dir
    status = ctx.status
    channel_config = ctx.channel_config
    update_stage = ctx.update_stage
    check_stop = ctx.check_stop
    _recorder = ctx.recorder
    tts_fn = ctx.tts_fn

    short_scenes = ctx.extras["short_scenes"]
    short_script = ctx.extras["short_script"]
    state = ctx.extras["scene_pipeline_state"]
    total_regeneration_attempts = ctx.extras["total_regeneration_attempts"]
    scenes_attempts = ctx.extras["scenes_attempts"]
    structural_attempts = ctx.extras["structural_attempts"]
    product_attempts = ctx.extras["product_attempts"]

    update_stage("audio", "in_progress")
    try:
        check_stop()
        assert_latest_scenes_ready(state)
        # Shorts TTS runs with dynamic_sync=False (see shorts.audio): each
        # scene's audio is padded to its planned duration_sec, so the single
        # narration track stays aligned with the per-scene visual sequences.
        # Keep the planned scene durations the renderer already uses — do NOT
        # overwrite them with raw speech lengths, which desyncs audio/video.
        narration_wav = tts_fn(sd, short_scenes, channel_config)
        duration_sec = float(_scene_duration_sum(short_scenes) or short_scenes.get("total_duration_sec") or 0.0)
        short_scenes["total_duration_sec"] = round(duration_sec, 1)
        narration_audio_sec = validate_scenes.probe_audio_duration_sec(narration_wav)
        tail_added_total = 0.0
        tail_distribution: list[dict[str, Any]] = []
        if narration_audio_sec is not None:
            tail_repair = validate_scenes.extend_scene_durations_for_audio_tail(
                short_scenes,
                narration_audio_sec
            )
            tail_added_total = float(tail_repair.get("added_sec") or 0.0)
            tail_distribution = list(tail_repair.get("tail_repair_distribution") or [])
            if tail_repair.get("changed"):
                duration_sec = float(_scene_duration_sum(short_scenes) or short_scenes.get("total_duration_sec") or duration_sec)
                short_scenes["total_duration_sec"] = round(duration_sec, 1)

                state.current_scenes_version += 1
                state.latest_scene_validation_ok = False
                state.latest_scene_validation_version = None

                re_issues = validate_scenes.validate_scene_structure(
                    short_scenes.get("scenes") or [],
                    scenes_doc=short_scenes,
                    script=short_script,
                )
                if not validate_scenes.has_blocking_or_repairable(re_issues):
                    state.latest_scene_validation_ok = True
                    state.latest_scene_validation_version = state.current_scenes_version
                    if state.latest_scene_qa_ok:
                        state.latest_scene_qa_version = state.current_scenes_version
                duration_sec = float(_scene_duration_sum(short_scenes) or short_scenes.get("total_duration_sec") or duration_sec)
                short_scenes["total_duration_sec"] = round(duration_sec, 1)

                atomic_write_json(_jd / paths.SHORT_SCENES_FILE, short_scenes)
                _recorder.record_event(
                    "deterministic",
                    "audio_tail_repair",
                    {
                        "verdict": "PASS",
                        "render_duration_sec": duration_sec,
                        "narration_audio_sec": round(narration_audio_sec, 3),
                        **tail_repair,
                    },
                    ok=True,
                )

        if narration_audio_sec is not None:
            sync_summary = validate_scenes.audio_sync_summary(
                render_duration_sec=duration_sec,
                narration_audio_sec=narration_audio_sec,
                tail_added_sec=tail_added_total,
                tail_repair_distribution=tail_distribution,
            )
            atomic_write_json(_jd / paths.SHORT_AUDIO_SYNC_SUMMARY_FILE, sync_summary)
            _recorder.record_event(
                "deterministic",
                "audio_sync_summary",
                sync_summary,
                ok=sync_summary["verdict"] != "FAIL",
            )

        audio_fit_passed = True
        audio_issue = None
        if narration_audio_sec is not None:
            audio_issue = validate_scenes.validate_audio_fit(duration_sec, narration_audio_sec)
            if audio_issue:
                audio_fit_passed = False

        if audio_fit_passed:
            state.latest_audio_tail_ok = True
            state.latest_audio_tail_version = state.current_scenes_version
        else:
            state.latest_audio_tail_ok = False
            state.latest_audio_tail_version = None

        if not audio_fit_passed:
            repair_plan = validate_scenes.build_scene_repair_plan(
                short_scenes.get("scenes") or [],
                [audio_issue],
                script=short_script,
            )
            _recorder.record_event(
                "deterministic",
                "audio_fit",
                {
                    "verdict": "FAIL",
                    "issue": audio_issue.to_dict(),
                    "render_duration_sec": duration_sec,
                    "narration_audio_sec": round(narration_audio_sec, 3),
                    "repair_plan": repair_plan,
                },
                ok=False,
            )
            update_stage("audio", "failed", error=audio_issue.detail)
            atomic_write_json(_jd / paths.SHORT_FAILURE_REPORT_FILE, {
                "stage": "audio",
                "issues": [audio_issue.to_dict()],
                "render_duration_sec": duration_sec,
                "narration_audio_sec": round(narration_audio_sec, 3),
                "repair_plan": repair_plan,
            })
            status.update({
                "status": "needs_review",
                "rendered": False,
                "uploaded": False,
                "youtube_url": "",
                "requires_user_review": True,
                "qa_verdict": "FAIL",
                "duration_sec": round(duration_sec, 1),
                "audio_fit_issue": audio_issue.to_dict(),
                "regeneration_attempts": total_regeneration_attempts,
                "qa_scenes_attempts": scenes_attempts,
                "qa_scenes_structural_attempts": structural_attempts,
                "qa_scenes_product_attempts": product_attempts,
            })
            write_short_status(long_job_dir, short_id, status)
            ctx.extras["narration_wav"] = narration_wav
            ctx.extras["duration_sec"] = duration_sec
            return StageResult(StageSignal.PROCEED, returns=status)
        else:
            audio_fit_passed = True
            update_stage(
                "audio",
                "completed",
                qa_verdict="PASS",
                render_duration_sec=round(duration_sec, 1),
                narration_audio_sec=round(narration_audio_sec, 3) if narration_audio_sec is not None else None,
            )
    except Exception as exc:
        update_stage("audio", "failed")
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    ctx.extras["narration_wav"] = narration_wav
    ctx.extras["duration_sec"] = duration_sec
    return _PROCEED


def _stage_seo(ctx: BuildContext) -> None:
    """Stage: SEO. Runs only after audio_fit passes. Raises on failure."""
    short_id = ctx.short_plan["short_id"]
    short_script = ctx.extras["short_script"]
    retention_plan = ctx.extras.get("retention_plan", {})
    ctx.update_stage("seo", "in_progress")
    try:
        ctx.check_stop()
        short_seo_builder.build_short_seo(
            ctx.long_job_dir, short_id, ctx.plan_for_prompt, short_script,
            ctx.channel_config, ctx.llm_fn, ctx.long_video_url,
            retention_plan=retention_plan,
            history_recorder=ctx.recorder,
        )
        ctx.update_stage("seo", "completed")
    except Exception as exc:
        ctx.update_stage("seo", "failed")
        ctx.status["status"] = "failed"
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc
