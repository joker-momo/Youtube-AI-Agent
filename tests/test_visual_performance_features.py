from __future__ import annotations

from typing import Any

from video_agent.shorts.visual_performance import (
    build_visual_performance_features,
    join_youtube_metrics,
)


def _schedule() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "contract_revision": "4.0.3",
        "short_id": "short-04",
        "schedule_hash": "abc123",
        "fps": 30,
        "qa": {"verdict": "PASS", "errors": [], "warnings": []},
        "tracks": [
            {
                "track_id": "vt01",
                "visual_span_id": "vs01",
                "visual_beat_id": "vb01",
                "scene_ids": ["s01", "s02"],
                "asset_ref": "jobs/x/assets/final.mp4",
                "asset_id": "final-01",
                "source_media_kind": "native_video",
                "duration_in_frames": 120,
                "source_duration_sec": 12.0,
                "selection_debug": {"mode": "visual_plan:continuous_clip"},
            },
            {
                "track_id": "vt02",
                "visual_span_id": "vs02",
                "visual_beat_id": None,
                "scene_ids": ["s03"],
                "asset_ref": "jobs/x/assets/placeholder.mp4",
                "asset_id": "placeholder",
                "source_media_kind": "generated_placeholder",
                "duration_in_frames": 60,
                "selection_debug": {"mode": "legacy_scene_assets"},
            },
        ],
    }


def _beat_plan() -> dict[str, Any]:
    return {
        "spans": [
            {
                "visual_span_id": "vs01",
                "selected_plan": {
                    "plan_id": "vp01",
                    "mode": "continuous_clip",
                    "beats": [{"beat_id": "vb01", "type": "native_video"}],
                },
            },
            {
                "visual_span_id": "vs02",
                "selected_plan": {
                    "plan_id": "vp02",
                    "mode": "clip_plus_graphic",
                    "beats": [
                        {"beat_id": "vb02", "type": "native_video"},
                        {"beat_id": "vb03", "type": "graphic"},
                    ],
                },
            },
        ]
    }


def _span_asset_qa() -> dict[str, Any]:
    return {
        "spans": [
            {
                "visual_span_id": "vs01",
                "qa": {"verdict": "PASS"},
                "candidate_qa": [
                    {
                        "candidate_id": "final-01",
                        "analysis": {
                            "first_frame_score": 0.9,
                            "crop_feasibility": {"crop_stability_score": 0.8},
                        },
                    }
                ],
            },
            {"visual_span_id": "vs02", "qa": {"verdict": "FAIL"}, "candidate_qa": []},
        ]
    }


def test_visual_performance_features_are_versioned_and_stable() -> None:
    features = build_visual_performance_features(
        short_id="short-04",
        job_id="job-1",
        channel_id="vida-plena-45",
        video_id=None,
        visual_schedule=_schedule(),
        visual_beat_plan=_beat_plan(),
        visual_sequence_qa={
            "summary": {"sequence_warnings": 1},
            "qa": {"verdict": "CAPABILITY_REDUCED", "warnings": ["capability_reduced:vs02"]},
        },
        visual_span_asset_qa=_span_asset_qa(),
        trim_window_plan={
            "spans": [
                {
                    "visual_span_id": "vs01",
                    "window_score": 86.0,
                    "crop_stability_score": 0.8,
                }
            ]
        },
        render_continuity_qa={"qa": {"verdict": "PASS"}},
        manual_review={
            "reviewer": "operator",
            "relevance": 4,
            "continuity": 5,
            "crop_quality": 4,
            "staleness": 2,
            "first_frame": 4,
            "overall_visual_quality": 4,
            "better_than_legacy": True,
            "notes": "",
        },
        youtube_metrics_doc=None,
        build_hash="hash-001",
        spec_mode="report_only",
    )

    assert features["schema_version"] == 1
    assert features["feature_schema_version"] == "visual_performance_features.v1"
    assert features["visual_features"]["visual_span_count"] == 2
    assert features["visual_features"]["continuous_clip_count"] == 1
    assert features["visual_features"]["native_continuous_track_count"] == 1
    assert features["visual_features"]["image_backed_track_count"] == 0
    assert features["visual_features"]["graphic_fallback_track_count"] == 1
    assert features["visual_features"]["graphic_beat_count"] == 1
    assert features["visual_features"]["legacy_fallback_count"] == 1
    assert features["visual_features"]["placeholder_count"] == 1
    assert features["visual_features"]["cut_count"] == 1
    assert features["visual_features"]["native_video_ratio"] == 0.5
    assert features["quality_features"]["schedule_qa"] == "PASS"
    assert features["quality_features"]["span_qa_failures"] == 1
    assert features["quality_features"]["sequence_warnings"] == 1
    assert features["manual_review"]["spec_mode"] == "report_only"
    assert features["manual_review"]["build_hash"] == "hash-001"
    assert features["metric_join"]["status"] == "metrics_unavailable"
    assert features["rollout_policy"]["auto_adjust_weights"] is False
    assert features["rollout_policy"]["production_config_mutated"] is False


def test_youtube_metrics_join_is_reproducible_and_handles_missing_metrics() -> None:
    no_metrics = join_youtube_metrics(
        short_id="short-04", job_id="job-1", video_id=None, youtube_metrics_doc=None
    )
    assert no_metrics["status"] == "metrics_unavailable"
    assert no_metrics["coverage"] == 0.0

    joined = join_youtube_metrics(
        short_id="short-04",
        job_id="job-1",
        video_id="yt-123",
        youtube_metrics_doc={
            "metrics": [
                {"video_id": "yt-999", "average_view_duration": 7.0},
                {"video_id": "yt-123", "average_view_duration": 8.0, "views": 1000000},
            ]
        },
    )

    assert joined["status"] == "joined"
    assert joined["matched_by"] == "video_id"
    assert joined["metrics"]["average_view_duration"] == 8.0
    assert "views" not in joined["metrics"]
    assert joined["warnings"] == ["raw_view_count_not_used_as_visual_quality_label"]
