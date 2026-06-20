"""Compiled visual-schedule stage (spec v3.2.3 §18, §21, §22).

Runs AFTER final audio-tail repair (so scene frame boundaries are final) and
before SEO. Compiles the schema-v2 ``compiled_asset_schedule.json`` from the
final scene timing + visual spans + adapted assets manifest, validates it, and
stashes it (plus its hash) in ``ctx.extras`` for the prepared-short final-props
path (PR B Step 3).

Mode behavior (§22):
- ``disabled`` → do not compile (legacy render).
- ``report_only`` (Phase 2 default) → compile + persist artifacts for diagnostics;
  the schedule is NOT activated in final render props and the build never fails on
  a schedule problem.
- ``enforced`` → a valid schedule is required; an invalid schedule fails the stage
  before Remotion.

This stage only produces artifacts + context. Renderer activation is decided
later by the final ``json/render_props.json`` (Step 3), never by this stage.
"""
from __future__ import annotations

from typing import Any

from video_agent.shorts import asset_schedule, paths
from video_agent.shorts import visual_spans as visual_spans_mod
from video_agent.shorts.builder.context import BuildContext
from video_agent.shorts.builder.types import _PROCEED, StageResult
from video_agent.shorts.manifest import write_short_status
from video_agent.storage.atomic import atomic_write_json


def _resolve_fps(channel_config: dict[str, Any]) -> int:
    render = channel_config.get("render") or {}
    shorts_render = (channel_config.get("shorts") or {}).get("render") or {}
    fps = shorts_render.get("fps") or render.get("fps") or 30
    try:
        return int(fps)
    except (TypeError, ValueError):
        return 30


def _resolve_timing_source(ctx: BuildContext) -> tuple[str, int]:
    """Return ``(timing_source, scene_version)`` honoring the §21 audio-tail
    invariant: only call it ``tts_final`` when audio-tail repair completed for the
    current scene version."""
    state = ctx.extras.get("scene_pipeline_state")
    scene_version = int(getattr(state, "current_scenes_version", 0) or 0) if state else 0
    audio_enabled = bool(ctx.tts_fn) and ctx.extras.get("narration_wav") is not None
    tail_ok = bool(getattr(state, "latest_audio_tail_ok", False)) if state else False
    tail_version = getattr(state, "latest_audio_tail_version", None) if state else None
    if audio_enabled and tail_ok and tail_version == scene_version:
        return "tts_final", scene_version
    return "scene_plan", scene_version


def _stage_visual_schedule(ctx: BuildContext) -> StageResult:
    """Compile the validated frame-based visual contract from final scene timing."""
    short_id = ctx.short_plan["short_id"]
    short_scenes = ctx.extras.get("short_scenes") or {}
    visual_spans = ctx.extras.get("visual_spans") or {}
    config = visual_spans_mod.resolve_visual_span_config(ctx.channel_config)
    mode = config["mode"]

    if mode == "disabled" or not config.get("enabled", True):
        return _PROCEED

    ctx.update_stage("visual_schedule", "in_progress")
    try:
        ctx.check_stop()
        fps = _resolve_fps(ctx.channel_config)
        timing_source, scene_version = _resolve_timing_source(ctx)

        manifest = ctx.extras.get("assets_manifest") or {}
        background_report = None
        bg_path = ctx.json_dir / "background_report.json"
        if bg_path.exists():
            from video_agent.utils.json_io import read_json

            background_report = read_json(bg_path)
        resolved = asset_schedule.adapt_assets_manifest(
            manifest, short_dir=ctx.short_dir, background_report=background_report
        )

        schedule = asset_schedule.compile_asset_schedule(
            short_id=short_id,
            scene_doc=short_scenes,
            visual_spans=visual_spans,
            resolved_visuals=resolved,
            fps=fps,
            timing_source=timing_source,
            scene_version=scene_version,
        )
        validation = asset_schedule.validate_compiled_asset_schedule(
            schedule, short_scenes, render_fps=fps, expected_scene_version=scene_version
        )
        schedule["qa"] = validation
        schedule_hash = asset_schedule.compute_schedule_hash(schedule)

        qa_artifact = {
            "short_id": short_id,
            "schema_version": schedule["schema_version"],
            "mode": mode,
            "timing_source": timing_source,
            "scene_version": scene_version,
            "schedule_hash": schedule_hash,
            "verdict": validation["verdict"],
            "errors": validation["errors"],
            "warnings": validation["warnings"],
            "manifest_adapter_warnings": resolved.get("warnings", []),
            "track_count": len(schedule["tracks"]),
            "continuous_clip_count": sum(
                1 for t in schedule["tracks"] if t.get("selection_debug", {}).get("mode") == "continuous_clip"
            ),
        }

        jd = ctx.json_dir
        atomic_write_json(jd / paths.SHORT_COMPILED_ASSET_SCHEDULE_FILE, schedule)
        atomic_write_json(jd / paths.SHORT_COMPILED_ASSET_SCHEDULE_QA_FILE, qa_artifact)

        ctx.extras["visual_schedule"] = schedule
        ctx.extras["visual_schedule_validation"] = validation
        ctx.extras["visual_schedule_hash"] = schedule_hash

        if mode == "enforced" and validation["verdict"] != "PASS":
            ctx.update_stage("visual_schedule", "failed", verdict="FAIL", errors=validation["errors"])
            ctx.status["status"] = "failed"
            write_short_status(ctx.long_job_dir, short_id, ctx.status)
            return StageResult(
                returns={
                    "status": "failed",
                    "failure_stage": "visual_schedule",
                    "failure_reason": f"invalid_schedule: {validation['errors'][:3]}",
                }
            )

        ctx.update_stage(
            "visual_schedule",
            "completed",
            verdict=validation["verdict"],
            mode=mode,
            timing_source=timing_source,
            track_count=qa_artifact["track_count"],
            continuous_clip_count=qa_artifact["continuous_clip_count"],
        )
        return _PROCEED
    except Exception as exc:  # noqa: BLE001 — report-only must not break the build
        if mode == "enforced":
            ctx.update_stage("visual_schedule", "failed", error=str(exc))
            ctx.status["status"] = "failed"
            write_short_status(ctx.long_job_dir, short_id, ctx.status)
            raise
        ctx.update_stage("visual_schedule", "skipped", error=str(exc), mode=mode)
        return _PROCEED
