from __future__ import annotations

from video_agent.shorts.visual_performance import build_visual_performance_report


def test_visual_performance_report_says_insufficient_evidence_for_small_sample() -> None:
    report = build_visual_performance_report(
        short_id="short-04",
        channel_id="vida-plena-45",
        feature_docs=[
            {
                "short_id": "short-04",
                "channel_id": "vida-plena-45",
                "created_at": "2026-06-20T12:00:00+00:00",
                "metric_join": {"status": "metrics_unavailable", "coverage": 0.0},
                "visual_features": {"maximum_track_duration_sec": 7.0},
            }
        ],
        min_sample_size=5,
    )

    assert report["schema_version"] == 1
    assert report["report_schema_version"] == "visual_performance_report.v1"
    assert report["summary"]["sample_count"] == 1
    assert report["summary"]["joined_metric_coverage"] == 0.0
    assert report["findings"][0]["status"] == "insufficient_evidence"
    assert "correlation_not_causation" in report["interpretation_policy"]
    assert "small sample" in report["warnings"]
    assert report["production_mutation"]["automatic_weight_mutation"] is False
    assert report["production_mutation"]["automatic_config_mutation"] is False


def test_visual_performance_report_uses_joined_metric_coverage_without_causal_claims() -> None:
    docs = [
        {
            "short_id": f"short-{idx}",
            "channel_id": "vida-plena-45",
            "created_at": f"2026-06-2{idx}T12:00:00+00:00",
            "metric_join": {"status": "joined", "coverage": 1.0},
            "visual_features": {"maximum_track_duration_sec": 4.0 + idx},
        }
        for idx in range(5)
    ]

    report = build_visual_performance_report(
        short_id="short-04",
        channel_id="vida-plena-45",
        feature_docs=docs,
        min_sample_size=5,
    )

    assert report["summary"]["sample_count"] == 5
    assert report["summary"]["joined_metric_coverage"] == 1.0
    assert all("correlate" in finding["claim"] for finding in report["findings"])
    assert all(
        "does not prove causation" in finding["confidence_caveat"] for finding in report["findings"]
    )
    assert all(finding["sample_count"] == 5 for finding in report["findings"])
