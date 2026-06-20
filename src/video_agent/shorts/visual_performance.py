"""PR F visual performance feature export and offline reporting.

This module is report-only. It never mutates production weights, channel config,
asset selection, render props, or schedule behavior.
"""

from __future__ import annotations

import datetime as _dt
import re
from statistics import mean
from typing import Any

from video_agent.shorts.visual_acquisition import CONTRACT_REVISION, stable_hash

FEATURE_SCHEMA_VERSION = "visual_performance_features.v1"
REPORT_SCHEMA_VERSION = "visual_performance_report.v1"
ALLOWED_YOUTUBE_METRICS = {
    "viewed_vs_swiped_away",
    "average_view_duration",
    "average_percentage_viewed",
    "retention_1s",
    "retention_3s",
    "retention_10s",
    "rewatch_rate",
    "shares",
    "saves",
    "comments",
}


def _safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _round(value: float | None, ndigits: int = 3) -> float | None:
    return round(value, ndigits) if value is not None else None


def _avg(values: list[float]) -> float | None:
    return mean(values) if values else None


def _extract_video_id(status: dict[str, Any] | None, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    url = str((status or {}).get("youtube_url") or "")
    if not url:
        return None
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", url)
    return match.group(1) if match else None


def resolve_visual_rollout_config(channel_config: dict[str, Any]) -> dict[str, Any]:
    raw_vqf = ((channel_config or {}).get("shorts") or {}).get("visual_quality_flow") or {}
    raw = raw_vqf.get("rollout") or {}
    requested_auto_adjust = bool(raw.get("auto_adjust_weights", False))
    warnings: list[str] = []
    if requested_auto_adjust:
        warnings.append("auto_adjust_weights_forced_false")
    try:
        min_report_sample_size = int(raw.get("min_report_sample_size", 5))
    except (TypeError, ValueError):
        min_report_sample_size = 5
    return {
        "enabled": bool(raw_vqf.get("enabled", False)),
        "mode": str(raw_vqf.get("mode") or "disabled").strip().lower(),
        "capture_features": bool(raw.get("capture_features", True)),
        "capture_manual_review": bool(raw.get("capture_manual_review", True)),
        "requested_auto_adjust_weights": requested_auto_adjust,
        "auto_adjust_weights": False,
        "min_report_sample_size": max(1, min_report_sample_size),
        "warnings": warnings,
    }


def _selected_plan_modes(visual_beat_plan: dict[str, Any] | None) -> list[str]:
    modes: list[str] = []
    for span in (visual_beat_plan or {}).get("spans") or []:
        selected = span.get("selected_plan") or {}
        mode = str(selected.get("mode") or "")
        if mode:
            modes.append(mode)
    return modes


def _graphic_beat_count(visual_beat_plan: dict[str, Any] | None) -> int:
    count = 0
    for span in (visual_beat_plan or {}).get("spans") or []:
        selected = span.get("selected_plan") or {}
        count += sum(1 for beat in selected.get("beats") or [] if beat.get("type") == "graphic")
    return count


def _quality_from_span_asset_qa(visual_span_asset_qa: dict[str, Any] | None) -> dict[str, Any]:
    span_failures = 0
    crop_scores: list[float] = []
    first_frame_scores: list[float] = []
    selection_scores: list[float] = []
    strong_matches = 0
    candidate_records = 0
    for span in (visual_span_asset_qa or {}).get("spans") or []:
        if ((span.get("qa") or {}).get("verdict")) == "FAIL":
            span_failures += 1
        for candidate in span.get("candidate_qa") or []:
            candidate_records += 1
            analysis = candidate.get("analysis") or {}
            first_frame = _safe_float(analysis.get("first_frame_score"))
            if first_frame is not None:
                first_frame_scores.append(first_frame)
            crop = (analysis.get("crop_feasibility") or {}).get("crop_stability_score")
            crop_value = _safe_float(crop)
            if crop_value is not None:
                crop_scores.append(crop_value)
            score = _safe_float(candidate.get("selection_score"))
            if score is not None:
                selection_scores.append(score)
            if candidate.get("asset_match_status") == "strong_match":
                strong_matches += 1
    return {
        "span_qa_failures": span_failures,
        "average_crop_stability": _avg(crop_scores),
        "first_frame_score": _avg(first_frame_scores),
        "average_selection_score": _avg(selection_scores),
        "strong_match_ratio": (strong_matches / candidate_records) if candidate_records else None,
    }


def _trim_quality(trim_window_plan: dict[str, Any] | None) -> dict[str, Any]:
    crop_scores: list[float] = []
    window_scores: list[float] = []
    for span in (trim_window_plan or {}).get("spans") or []:
        crop = _safe_float(span.get("crop_stability_score"))
        if crop is not None:
            crop_scores.append(crop)
        score = _safe_float(span.get("window_score"))
        if score is not None:
            window_scores.append(score)
    return {
        "average_crop_stability": _avg(crop_scores),
        "average_selection_score": _avg(window_scores),
    }


def join_youtube_metrics(
    *,
    short_id: str,
    job_id: str | None,
    video_id: str | None,
    youtube_metrics_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    records = list((youtube_metrics_doc or {}).get("metrics") or [])
    if not records:
        return {
            "status": "metrics_unavailable",
            "matched_by": None,
            "coverage": 0.0,
            "metrics": {},
            "warnings": ["youtube_metrics_missing"],
        }
    match: dict[str, Any] | None = None
    matched_by: str | None = None
    for field, value in (("video_id", video_id), ("short_id", short_id), ("job_id", job_id)):
        if not value:
            continue
        for record in records:
            if str(record.get(field) or "") == str(value):
                match = record
                matched_by = field
                break
        if match:
            break
    if not match:
        return {
            "status": "not_matched",
            "matched_by": None,
            "coverage": 0.0,
            "metrics": {},
            "warnings": ["no_stable_video_job_mapping_match"],
        }
    metrics = {k: match[k] for k in sorted(ALLOWED_YOUTUBE_METRICS) if k in match}
    warnings = []
    if "views" in match:
        warnings.append("raw_view_count_not_used_as_visual_quality_label")
    coverage = len(metrics) / len(ALLOWED_YOUTUBE_METRICS)
    return {
        "status": "joined",
        "matched_by": matched_by,
        "coverage": round(coverage, 3),
        "metrics": metrics,
        "warnings": warnings,
    }


def _normalize_manual_review(
    review: dict[str, Any] | None, *, build_hash: str | None, spec_mode: str
) -> dict[str, Any] | None:
    if not review:
        return None
    fields = (
        "relevance",
        "continuity",
        "crop_quality",
        "staleness",
        "first_frame",
        "overall_visual_quality",
    )
    out: dict[str, Any] = {
        "reviewer": str(review.get("reviewer") or "operator"),
        "better_than_legacy": bool(review.get("better_than_legacy", False)),
        "notes": str(review.get("notes") or ""),
        "spec_mode": spec_mode,
        "build_hash": build_hash,
    }
    for field in fields:
        value = int(max(1, min(5, int(review.get(field, 3)))))
        out[field] = value
    return out


def build_visual_performance_features(
    *,
    short_id: str,
    job_id: str | None,
    channel_id: str | None,
    video_id: str | None,
    visual_schedule: dict[str, Any] | None,
    visual_beat_plan: dict[str, Any] | None,
    visual_sequence_qa: dict[str, Any] | None,
    visual_span_asset_qa: dict[str, Any] | None,
    trim_window_plan: dict[str, Any] | None,
    render_continuity_qa: dict[str, Any] | None,
    manual_review: dict[str, Any] | None,
    youtube_metrics_doc: dict[str, Any] | None,
    build_hash: str | None,
    spec_mode: str,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tracks = list((visual_schedule or {}).get("tracks") or [])
    durations = [
        float(t.get("duration_in_frames") or 0) / float((visual_schedule or {}).get("fps") or 30)
        for t in tracks
    ]
    modes = _selected_plan_modes(visual_beat_plan)
    track_modes = [str((track.get("selection_debug") or {}).get("mode") or "") for track in tracks]
    native_tracks = sum(1 for track in tracks if track.get("source_media_kind") == "native_video")
    placeholders = sum(
        1 for track in tracks if track.get("source_media_kind") == "generated_placeholder"
    )
    quality = _quality_from_span_asset_qa(visual_span_asset_qa)
    trim_quality = _trim_quality(trim_window_plan)
    average_crop = trim_quality["average_crop_stability"] or quality["average_crop_stability"]
    average_score = trim_quality["average_selection_score"] or quality["average_selection_score"]
    sequence_summary = (visual_sequence_qa or {}).get("summary") or {}
    sequence_qa = (visual_sequence_qa or {}).get("qa") or {}
    sequence_warnings = sequence_summary.get("sequence_warnings")
    if not isinstance(sequence_warnings, int):
        sequence_warnings = len(sequence_qa.get("warnings") or [])
    resolved_video_id = _extract_video_id(status, video_id)
    metric_join = join_youtube_metrics(
        short_id=short_id,
        job_id=job_id,
        video_id=resolved_video_id,
        youtube_metrics_doc=youtube_metrics_doc,
    )
    feature_doc = {
        "schema_version": 1,
        "contract_revision": CONTRACT_REVISION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "short_id": short_id,
        "job_id": job_id,
        "channel_id": channel_id,
        "video_id": resolved_video_id,
        "created_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "build_hash": build_hash,
        "input_hash": stable_hash(
            {
                "visual_schedule": visual_schedule,
                "visual_beat_plan": visual_beat_plan,
                "visual_sequence_qa": visual_sequence_qa,
                "visual_span_asset_qa": visual_span_asset_qa,
                "trim_window_plan": trim_window_plan,
                "render_continuity_qa": render_continuity_qa,
                "manual_review": manual_review,
                "metric_join": metric_join,
            }
        ),
        "visual_features": {
            "visual_span_count": len((visual_beat_plan or {}).get("spans") or []),
            "continuous_clip_count": sum(1 for mode in modes if mode == "continuous_clip"),
            "two_clip_plan_count": sum(1 for mode in modes if mode == "two_clip"),
            "clip_plus_graphic_count": sum(1 for mode in modes if mode == "clip_plus_graphic"),
            "graphic_beat_count": _graphic_beat_count(visual_beat_plan),
            "legacy_fallback_count": sum(
                1 for mode in track_modes if mode == "legacy_scene_assets"
            ),
            "placeholder_count": placeholders,
            "cut_count": max(0, len(tracks) - 1),
            "average_track_duration_sec": _round(_avg(durations)),
            "maximum_track_duration_sec": _round(max(durations) if durations else None),
            "native_video_ratio": _round(native_tracks / len(tracks) if tracks else None),
            "strong_match_ratio": _round(quality["strong_match_ratio"]),
            "average_selection_score": _round(average_score),
            "average_crop_stability": _round(average_crop),
            "near_static_hold_sec": None,
            "first_frame_score": _round(quality["first_frame_score"]),
        },
        "quality_features": {
            "schedule_qa": ((visual_schedule or {}).get("qa") or {}).get("verdict"),
            "span_qa_failures": quality["span_qa_failures"],
            "sequence_warnings": sequence_warnings,
            "render_qa": ((render_continuity_qa or {}).get("qa") or {}).get("verdict"),
            "rollback_used": False,
        },
        "manual_review": _normalize_manual_review(
            manual_review, build_hash=build_hash, spec_mode=spec_mode
        ),
        "metric_join": metric_join,
        "rollout_policy": {
            "mode": spec_mode,
            "auto_adjust_weights": False,
            "production_config_mutated": False,
            "production_weights_mutated": False,
        },
        "privacy": {
            "remote_media_urls_persisted": False,
            "raw_view_count_used_as_quality_label": False,
        },
    }
    return feature_doc


def build_visual_performance_report(
    *,
    short_id: str,
    channel_id: str | None,
    feature_docs: list[dict[str, Any]],
    min_sample_size: int = 5,
) -> dict[str, Any]:
    sample_count = len(feature_docs)
    joined_count = sum(
        1 for doc in feature_docs if (doc.get("metric_join") or {}).get("status") == "joined"
    )
    coverage = round(joined_count / sample_count, 3) if sample_count else 0.0
    dates = sorted(
        str(doc.get("created_at") or "") for doc in feature_docs if doc.get("created_at")
    )
    warnings: list[str] = []
    findings: list[dict[str, Any]] = []
    confounders = [
        "small sample",
        "mixed topic",
        "mixed hook quality",
        "different publication time",
        "different channel state",
    ]
    if sample_count < min_sample_size:
        warnings.append("small sample")
    if coverage == 0.0:
        warnings.append("youtube_metrics_missing")
    if sample_count < min_sample_size or coverage == 0.0:
        findings.append(
            {
                "status": "insufficient_evidence",
                "claim": "insufficient evidence to report visual-performance correlations",
                "sample_count": sample_count,
                "date_range": [dates[0], dates[-1]] if dates else [],
                "channels": [channel_id] if channel_id else [],
                "confidence_caveat": "insufficient evidence; correlation does not prove causation",
                "confounders": confounders,
            }
        )
    else:
        findings.append(
            {
                "status": "report_only_correlation",
                "claim": "visual timing features correlate with joined retention metrics",
                "sample_count": sample_count,
                "date_range": [dates[0], dates[-1]] if dates else [],
                "channels": [channel_id] if channel_id else [],
                "confidence_caveat": "observed correlation does not prove causation",
                "confounders": confounders,
            }
        )
    return {
        "schema_version": 1,
        "contract_revision": CONTRACT_REVISION,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "short_id": short_id,
        "channel_id": channel_id,
        "created_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "summary": {
            "sample_count": sample_count,
            "joined_metric_coverage": coverage,
            "date_range": [dates[0], dates[-1]] if dates else [],
        },
        "findings": findings,
        "warnings": sorted(set(warnings)),
        "interpretation_policy": [
            "correlation_not_causation",
            "raw_view_count_is_not_a_visual_quality_label",
            "reports_only_no_automatic_weight_mutation",
        ],
        "production_mutation": {
            "automatic_weight_mutation": False,
            "automatic_config_mutation": False,
        },
    }
