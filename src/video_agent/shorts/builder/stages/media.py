"""Asset, audio, SEO, render & lifecycle stages for the Short builder."""

from __future__ import annotations

from typing import Any

from video_agent.shorts import (
    paths,
    performance_memory,
    short_seo_builder,
    validate_scenes,
)
from video_agent.shorts import (
    retention_plan as retention_plan_builder,
)
from video_agent.shorts.builder.context import BuildContext

# Backwards-compatible facade: tests and callers import/patch these via
# video_agent.shorts.short_builder.<name>.
from video_agent.shorts.builder.snapshots import (
    _scene_duration_sum,
)
from video_agent.shorts.builder.types import (
    _PROCEED,
    StageResult,
    StageSignal,
)
from video_agent.shorts.manifest import write_short_status
from video_agent.shorts.retry_memory import (
    assert_latest_scenes_ready,
)
from video_agent.storage.atomic import atomic_write_json


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


def _stage_render_continuity_qa(ctx: BuildContext) -> StageResult:
    """Stage: post-render production continuity QA (spec §34).

    The schedule validator proves the *plan* before render; this verifies the
    *rendered MP4* matches it — exact frame count, duration, resolution/fps, an
    audio stream, and no black/dropped frame at the scene-boundary cuts. Runs
    AFTER ``_stage_render`` and BEFORE the short is marked ``rendered``.

    Gated by ``shorts.visual_timeline.qa.production_boundary_check_enabled``: when
    enabled a FAIL blocks the ``rendered`` mark and routes the short to
    needs_review; otherwise the verdict is recorded report-only. The artifact is
    always written so ``_stage_visual_performance`` can consume it.
    """
    from pathlib import Path

    from video_agent.shorts.render_continuity_qa import build_render_continuity_qa
    from video_agent.utils.json_io import read_json

    short_id = ctx.short_plan["short_id"]
    qa_cfg = (
        (((ctx.channel_config or {}).get("shorts") or {}).get("visual_timeline") or {}).get("qa")
        or {}
    )
    enforced = bool(qa_cfg.get("production_boundary_check_enabled"))

    video_path = ctx.extras.get("video_path")
    if not video_path or not Path(video_path).exists():
        ctx.update_stage("render_continuity_qa", "skipped", reason="no_video")
        return _PROCEED

    visual_schedule = ctx.extras.get("visual_schedule")
    render_props_path = ctx.json_dir / "render_props.json"
    render_props = read_json(render_props_path) if render_props_path.exists() else {}
    fps = int(
        (visual_schedule or {}).get("fps")
        or (render_props.get("render") or {}).get("fps")
        or ((ctx.channel_config or {}).get("render") or {}).get("fps")
        or 30
    )

    ctx.update_stage("render_continuity_qa", "in_progress")
    try:
        qa = build_render_continuity_qa(
            video_path=Path(video_path),
            visual_schedule=visual_schedule,
            render_props=render_props,
            fps=fps,
            public_job_id=ctx.long_job_dir.name,
        )
    except Exception as exc:  # noqa: BLE001 - QA must not crash the build; record + gate
        qa = {
            "verdict": "FAIL",
            "errors": [f"qa_crashed:{type(exc).__name__}:{exc}"],
            "checks": {},
        }

    atomic_write_json(ctx.json_dir / paths.SHORT_RENDER_CONTINUITY_QA_FILE, qa)
    ctx.extras["render_continuity_qa"] = qa
    verdict = qa.get("verdict")
    ctx.update_stage("render_continuity_qa", "completed", verdict=verdict, enforced=enforced)

    if enforced and verdict == "FAIL":
        ctx.status.update(
            {
                "status": "needs_review",
                "rendered": False,
                "uploaded": False,
                "youtube_url": "",
                "requires_user_review": True,
                "qa_verdict": "FAIL",
                "failure_stage": "render_continuity_qa",
                "failure_reason": "post-render continuity QA FAILED: "
                + "; ".join(qa.get("errors") or []),
            }
        )
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        return StageResult(StageSignal.DONE, returns=ctx.status)
    return _PROCEED


def _stage_performance_memory(ctx: BuildContext) -> None:
    """Stage: performance_memory (final, after render).

    Updates the performance memory record to 'rendered' status.
    """
    short_id = ctx.short_plan["short_id"]
    anti_ai_regeneration_attempts = ctx.extras.get("anti_ai_regeneration_attempts", 0)
    short_script = ctx.extras.get("short_script", {})
    short_scenes = ctx.extras.get("short_scenes", {})
    retention_plan = ctx.extras.get("retention_plan", {})
    ctx.status.update(
        {
            "status": "rendered",
            "rendered": True,
            "uploaded": False,
            "youtube_url": "",
            "requires_user_review": False,
            "requires_render_confirmation": False,
            "video_path": f"shorts/{short_id}/{paths.SHORT_OUTPUTS_SUBDIR}/{paths.SHORT_VIDEO_FILE}",
            "cover_path": f"shorts/{short_id}/{paths.SHORT_OUTPUTS_SUBDIR}/{paths.SHORT_COVER_FILE}",
            "anti_ai_regeneration_attempts": anti_ai_regeneration_attempts,
        }
    )
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
                bg_sources.append(
                    {
                        "scene_id": info.get("scene_id"),
                        "background_source": info.get("background_source"),
                    }
                )
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
        from video_agent.shorts.audio import assert_no_unconverted_graphic_scenes

        assert_no_unconverted_graphic_scenes(short_scenes)
        # Load the resolved assets manifest into context for the schedule stage
        # (spec §20). Keep background_fn backward compatible — it need not return it.
        manifest_path = ctx.json_dir / "assets_manifest.json"
        if manifest_path.exists():
            from video_agent.utils.json_io import read_json

            ctx.extras["assets_manifest"] = read_json(manifest_path)
        ctx.update_stage("background", "completed", per_scene=bg_sources)
    except Exception as exc:
        ctx.update_stage("background", "failed", error=str(exc))
        ctx.status["status"] = "failed"
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc


def _stage_fallback_image_gen(ctx: BuildContext) -> None:
    """Lazy AI fallback: generate ChatGPT images ONLY for scenes that actually
    need a clean controlled background. No-op when nothing qualifies.

    Quality-first selection (do NOT override good native footage — that costs
    motion, character continuity, and the "real footage" feel):

    * (a) spans whose native candidate was rejected / failed local QA / has no
      final candidate — they have no usable native, so they need a fallback;
    * (b) hook + short_tip scenes whose span is NOT a confident PASS. A confident
      PASS (render_eligible native that cleared action evidence) is kept as native;
      only a non-PASS (FAIL / CAPABILITY_REDUCED / no candidate) hook/tip — where
      stock topic-matched but failed to actually show the required single-human
      action — gets a controlled AI image;
    * (c) CTA end-card scenes — deliberate channel brand policy: an on-brand AI
      end card beats off-brand stock (corkboards / foreign-language text). This is
      the one layout we still force, and intentionally so.

    The montage (short_checklist) and any confidently-passing native footage keep
    real stock motion.
    """
    asset_qa = ctx.extras.get("visual_span_asset_qa") or {}
    short_scenes = ctx.extras.get("short_scenes") or {}
    rejected_scene_ids: set[str] = set()

    # (a) Spans with no usable native -> map their scenes to fallback, and record
    # which scenes had a confident PASS so we never override good native below.
    confident_native_scene_ids: set[str] = set()
    for span in asset_qa.get("spans") or []:
        status = span.get("final_selection_status")
        verdict = (span.get("qa") or {}).get("verdict")
        scene_ids = [str(s) for s in (span.get("scene_ids") or [])]
        if status == "rejected" or verdict == "FAIL" or not span.get("final_candidate_id"):
            rejected_scene_ids.update(scene_ids)
        elif verdict == "PASS" and span.get("render_eligible"):
            confident_native_scene_ids.update(scene_ids)

    # (b)/(c) Layout-driven controlled backgrounds.
    for scene in short_scenes.get("scenes") or []:
        layout = str(scene.get("layout") or "")
        rf = str(scene.get("retention_function") or "")
        sid = str(scene.get("id"))
        is_cta = layout in {"short_cta", "cta"} or rf == "cta"
        is_controlled_action = layout in {"short_hook", "hook", "short_tip"} or rf == "hook"
        if is_cta:
            rejected_scene_ids.add(sid)  # (c) brand end-card: always AI
        elif is_controlled_action and sid not in confident_native_scene_ids:
            rejected_scene_ids.add(sid)  # (b) only when native did not clearly pass

    if not rejected_scene_ids:
        return
    ctx.update_stage("background", "in_progress", fallback_regen=sorted(rejected_scene_ids))
    try:
        from video_agent.shorts.audio import regen_fallback_backgrounds

        regen_fallback_backgrounds(
            ctx.short_dir, short_scenes, ctx.channel_config, rejected_scene_ids
        )
        manifest_path = ctx.json_dir / "assets_manifest.json"
        if manifest_path.exists():
            from video_agent.utils.json_io import read_json

            ctx.extras["assets_manifest"] = read_json(manifest_path)
        ctx.update_stage(
            "background", "completed", fallback_regen_count=len(rejected_scene_ids)
        )
    except Exception as exc:  # noqa: BLE001 - lazy gen failure degrades to placeholder
        ctx.update_stage("background", "completed", fallback_regen_error=str(exc))


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
        duration_sec = float(
            _scene_duration_sum(short_scenes) or short_scenes.get("total_duration_sec") or 0.0
        )
        short_scenes["total_duration_sec"] = round(duration_sec, 1)
        narration_audio_sec = validate_scenes.probe_audio_duration_sec(narration_wav)
        tail_added_total = 0.0
        tail_distribution: list[dict[str, Any]] = []
        if narration_audio_sec is not None:
            tail_repair = validate_scenes.extend_scene_durations_for_audio_tail(
                short_scenes, narration_audio_sec
            )
            tail_added_total = float(tail_repair.get("added_sec") or 0.0)
            tail_distribution = list(tail_repair.get("tail_repair_distribution") or [])
            if tail_repair.get("changed"):
                duration_sec = float(
                    _scene_duration_sum(short_scenes)
                    or short_scenes.get("total_duration_sec")
                    or duration_sec
                )
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
                duration_sec = float(
                    _scene_duration_sum(short_scenes)
                    or short_scenes.get("total_duration_sec")
                    or duration_sec
                )
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
            atomic_write_json(
                _jd / paths.SHORT_FAILURE_REPORT_FILE,
                {
                    "stage": "audio",
                    "issues": [audio_issue.to_dict()],
                    "render_duration_sec": duration_sec,
                    "narration_audio_sec": round(narration_audio_sec, 3),
                    "repair_plan": repair_plan,
                },
            )
            status.update(
                {
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
                }
            )
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
                narration_audio_sec=round(narration_audio_sec, 3)
                if narration_audio_sec is not None
                else None,
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
            ctx.long_job_dir,
            short_id,
            ctx.plan_for_prompt,
            short_script,
            ctx.channel_config,
            ctx.llm_fn,
            ctx.long_video_url,
            retention_plan=retention_plan,
            history_recorder=ctx.recorder,
        )
        ctx.update_stage("seo", "completed")
    except Exception as exc:
        ctx.update_stage("seo", "failed")
        ctx.status["status"] = "failed"
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc
