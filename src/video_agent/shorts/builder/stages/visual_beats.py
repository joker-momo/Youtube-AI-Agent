"""PR E visual beat-planning stage."""

from __future__ import annotations

from video_agent.shorts import asset_schedule, paths
from video_agent.shorts.builder.context import BuildContext
from video_agent.shorts.builder.stages.visual_schedule import _resolve_fps
from video_agent.shorts.builder.types import _PROCEED, StageResult
from video_agent.shorts.manifest import write_short_status
from video_agent.shorts.visual_beat_planner import (
    build_visual_beat_plan,
    resolve_visual_beat_planner_config,
)
from video_agent.shorts.visual_sequence_qa import build_visual_sequence_qa
from video_agent.storage.atomic import atomic_write_json


def _stage_visual_beats(ctx: BuildContext) -> StageResult:
    """Build bounded visual beat plans and sequence QA artifacts for PR E."""
    short_id = ctx.short_plan["short_id"]
    config = resolve_visual_beat_planner_config(ctx.channel_config)
    mode = config["mode"]
    if not config.get("enabled") or mode == "disabled":
        ctx.update_stage("visual_beats", "skipped", mode=mode)
        return _PROCEED

    ctx.update_stage("visual_beats", "in_progress")
    try:
        ctx.check_stop()
        fps = _resolve_fps(ctx.channel_config)
        manifest = ctx.extras.get("assets_manifest") or {}
        background_report = None
        bg_path = ctx.json_dir / "background_report.json"
        if bg_path.exists():
            from video_agent.utils.json_io import read_json

            background_report = read_json(bg_path)
        resolved = asset_schedule.adapt_assets_manifest(
            manifest, short_dir=ctx.short_dir, background_report=background_report
        )
        beat_plan = build_visual_beat_plan(
            short_id=short_id,
            scene_doc=ctx.extras.get("short_scenes") or {},
            visual_spans=ctx.extras.get("visual_spans") or {},
            resolved_visuals=resolved,
            trim_window_plan=ctx.extras.get("trim_window_plan"),
            visual_span_asset_qa=ctx.extras.get("visual_span_asset_qa"),
            channel_config=ctx.channel_config,
            fps=fps,
        )
        sequence_qa = build_visual_sequence_qa(short_id=short_id, visual_beat_plan=beat_plan)

        atomic_write_json(ctx.json_dir / paths.SHORT_VISUAL_BEAT_PLAN_FILE, beat_plan)
        atomic_write_json(ctx.json_dir / paths.SHORT_VISUAL_SEQUENCE_QA_FILE, sequence_qa)
        ctx.extras["visual_beat_plan"] = beat_plan
        ctx.extras["visual_sequence_qa"] = sequence_qa

        verdict = (sequence_qa.get("qa") or {}).get("verdict")
        if mode == "enforced" and verdict == "FAIL":
            errors = (sequence_qa.get("qa") or {}).get("errors") or []
            ctx.update_stage("visual_beats", "failed", verdict="FAIL", errors=errors[:3])
            ctx.status["status"] = "failed"
            write_short_status(ctx.long_job_dir, short_id, ctx.status)
            return StageResult(
                returns={
                    "status": "failed",
                    "failure_stage": "visual_beats",
                    "failure_reason": f"invalid_visual_sequence: {errors[:3]}",
                }
            )

        ctx.update_stage(
            "visual_beats",
            "completed",
            verdict=verdict,
            mode=mode,
            beat_count=(sequence_qa.get("summary") or {}).get("beat_count"),
            track_count=(sequence_qa.get("summary") or {}).get("track_count"),
        )
        return _PROCEED
    except Exception as exc:  # noqa: BLE001 - report-only must preserve legacy rendering.
        if mode == "enforced":
            ctx.update_stage("visual_beats", "failed", error=str(exc))
            ctx.status["status"] = "failed"
            write_short_status(ctx.long_job_dir, short_id, ctx.status)
            raise
        ctx.update_stage("visual_beats", "skipped", error=str(exc), mode=mode)
        return _PROCEED
