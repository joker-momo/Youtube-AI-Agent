"""PR F visual performance feature/report stage."""

from __future__ import annotations

from video_agent.shorts import paths
from video_agent.shorts.builder.context import BuildContext
from video_agent.shorts.builder.types import _PROCEED, StageResult
from video_agent.shorts.visual_performance import (
    build_visual_performance_features,
    build_visual_performance_report,
    resolve_visual_rollout_config,
)
from video_agent.storage.atomic import atomic_write_json


def _read_optional_json(path):
    if not path.exists():
        return None
    from video_agent.utils.json_io import read_json

    return read_json(path)


def _stage_visual_performance(ctx: BuildContext) -> StageResult:
    """Export PR F report-only visual performance artifacts after render."""
    short_id = ctx.short_plan["short_id"]
    cfg = resolve_visual_rollout_config(ctx.channel_config)
    mode = cfg["mode"]
    if not cfg.get("enabled") or not cfg.get("capture_features") or mode == "disabled":
        ctx.update_stage("visual_performance", "skipped", mode=mode)
        return _PROCEED
    if not ctx.status.get("rendered"):
        ctx.update_stage("visual_performance", "skipped", mode=mode, reason="short_not_rendered")
        return _PROCEED

    ctx.update_stage("visual_performance", "in_progress")
    try:
        ctx.check_stop()
        channel_id = str(((ctx.channel_config or {}).get("channel") or {}).get("id") or "")
        manual_review = (
            _read_optional_json(ctx.json_dir / paths.SHORT_VISUAL_MANUAL_REVIEW_FILE)
            if cfg.get("capture_manual_review")
            else None
        )
        youtube_metrics = _read_optional_json(ctx.json_dir / paths.SHORT_YOUTUBE_METRICS_FILE)
        render_qa = _read_optional_json(ctx.json_dir / paths.SHORT_RENDER_CONTINUITY_QA_FILE)
        features = build_visual_performance_features(
            short_id=short_id,
            job_id=ctx.long_job_dir.name,
            channel_id=channel_id,
            video_id=None,
            visual_schedule=ctx.extras.get("visual_schedule"),
            visual_beat_plan=ctx.extras.get("visual_beat_plan"),
            visual_sequence_qa=ctx.extras.get("visual_sequence_qa"),
            visual_span_asset_qa=ctx.extras.get("visual_span_asset_qa"),
            trim_window_plan=ctx.extras.get("trim_window_plan"),
            render_continuity_qa=render_qa,
            manual_review=manual_review,
            youtube_metrics_doc=youtube_metrics,
            build_hash=ctx.extras.get("visual_schedule_hash"),
            spec_mode=mode,
            status=ctx.status,
        )
        report = build_visual_performance_report(
            short_id=short_id,
            channel_id=channel_id,
            feature_docs=[features],
            min_sample_size=int(cfg.get("min_report_sample_size") or 5),
        )
        atomic_write_json(ctx.json_dir / paths.SHORT_VISUAL_PERFORMANCE_FEATURES_FILE, features)
        atomic_write_json(ctx.json_dir / paths.SHORT_VISUAL_PERFORMANCE_REPORT_FILE, report)
        ctx.extras["visual_performance_features"] = features
        ctx.extras["visual_performance_report"] = report
        ctx.update_stage(
            "visual_performance",
            "completed",
            mode=mode,
            feature_schema_version=features["feature_schema_version"],
            joined_metric_coverage=(report.get("summary") or {}).get("joined_metric_coverage"),
            auto_adjust_weights=False,
        )
        return _PROCEED
    except Exception as exc:  # noqa: BLE001 - PR F collection must not block rendered output.
        ctx.update_stage("visual_performance", "skipped", mode=mode, error=str(exc))
        return _PROCEED
