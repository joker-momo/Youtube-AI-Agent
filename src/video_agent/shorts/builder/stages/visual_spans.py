"""Visual-span grouping stage (spec v3.2.3 §18, §19).

Runs after anti-AI review and before background acquisition, on the final
approved scene plan. Builds + validates report-only visual spans, persists
``visual_spans.json`` + ``visual_span_qa.json``, attaches the repaired
``visual_span_id`` compatibility field onto the scene document, and stashes the
result in ``ctx.extras`` for later stages / PR B.

It acquires no media and changes no scene duration. In ``report_only`` mode
(the Phase 1 default) it never fails the build — a span computation problem is
recorded as a skipped stage and the build proceeds on the legacy render path.
Only ``enforced`` mode (PR B) re-raises.
"""
from __future__ import annotations

from video_agent.shorts import paths
from video_agent.shorts import visual_spans as visual_spans_mod
from video_agent.shorts.builder.context import BuildContext
from video_agent.shorts.builder.types import _PROCEED, StageResult
from video_agent.shorts.manifest import write_short_status
from video_agent.storage.atomic import atomic_write_json


def _stage_visual_spans(ctx: BuildContext) -> StageResult:
    """Build and validate visual grouping after the final approved scene plan."""
    short_id = ctx.short_plan["short_id"]
    short_scenes = ctx.extras.get("short_scenes") or {}
    config = visual_spans_mod.resolve_visual_span_config(ctx.channel_config)
    mode = config["mode"]

    ctx.update_stage("visual_spans", "in_progress")
    try:
        ctx.check_stop()
        result = visual_spans_mod.build_visual_spans(
            short_scenes, ctx.channel_config, short_id=short_id
        )
        # Attach the repaired (authoritative) span id onto the in-memory scenes
        # so later stages and PR B see the grouping. Durations are untouched.
        visual_spans_mod.assign_span_ids_to_scenes(short_scenes, result)

        qa = {
            "short_id": short_id,
            "schema_version": result["schema_version"],
            "generation_mode": result["generation_mode"],
            "input_hash": result["input_hash"],
            "metrics": result["metrics"],
            "qa": result["qa"],
        }

        jd = ctx.json_dir
        atomic_write_json(jd / paths.SHORT_VISUAL_SPANS_FILE, result)
        atomic_write_json(jd / paths.SHORT_VISUAL_SPAN_QA_FILE, qa)
        # Persist the scene doc with the compatibility span ids (render-neutral:
        # the renderer ignores visual_span_id until VisualTimeline lands in PR B).
        atomic_write_json(jd / paths.SHORT_SCENES_FILE, short_scenes)

        ctx.extras["short_scenes"] = short_scenes
        ctx.extras["visual_spans"] = result
        ctx.extras["visual_span_validation"] = result["qa"]

        ctx.update_stage(
            "visual_spans",
            "completed",
            verdict=result["qa"]["verdict"],
            mode=mode,
            visual_span_count=result["metrics"]["visual_span_count"],
            estimated_asset_call_reduction=result["metrics"]["estimated_asset_call_reduction"],
            repaired_span_count=result["metrics"]["repaired_span_count"],
        )
        return _PROCEED
    except Exception as exc:  # noqa: BLE001 — report-only must not break the build
        if mode == "enforced":
            ctx.update_stage("visual_spans", "failed", error=str(exc))
            ctx.status["status"] = "failed"
            write_short_status(ctx.long_job_dir, short_id, ctx.status)
            raise
        # report_only / disabled: degrade gracefully, keep legacy render path.
        ctx.update_stage("visual_spans", "skipped", error=str(exc), mode=mode)
        return _PROCEED
