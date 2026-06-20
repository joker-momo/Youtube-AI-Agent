from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.builder.stages.visual_performance import _stage_visual_performance
from video_agent.shorts.visual_performance import resolve_visual_rollout_config


def _ctx(tmp_path: Path, *, capture_features: bool = True) -> SimpleNamespace:
    json_dir = tmp_path / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[str, str]] = []
    ctx = SimpleNamespace(
        short_plan={"short_id": "short-04"},
        long_job_dir=tmp_path.parent,
        short_dir=tmp_path,
        json_dir=json_dir,
        channel_config={
            "channel": {"id": "vida-plena-45"},
            "shorts": {
                "visual_quality_flow": {
                    "enabled": True,
                    "mode": "report_only",
                    "rollout": {
                        "capture_features": capture_features,
                        "capture_manual_review": True,
                        "auto_adjust_weights": True,
                        "min_report_sample_size": 5,
                    },
                }
            },
        },
        status={"status": "rendered", "rendered": True, "youtube_url": ""},
        extras={
            "visual_schedule": {
                "schema_version": 2,
                "contract_revision": "4.0.3",
                "short_id": "short-04",
                "qa": {"verdict": "PASS"},
                "tracks": [
                    {
                        "track_id": "vt01",
                        "visual_span_id": "vs01",
                        "visual_beat_id": "vb01",
                        "asset_ref": "jobs/x/assets/final.mp4",
                        "source_media_kind": "native_video",
                        "duration_in_frames": 90,
                        "selection_debug": {"mode": "visual_plan:continuous_clip"},
                    }
                ],
            },
            "visual_schedule_hash": "hash-001",
            "visual_beat_plan": {
                "spans": [
                    {
                        "visual_span_id": "vs01",
                        "selected_plan": {
                            "mode": "continuous_clip",
                            "beats": [{"beat_id": "vb01", "type": "native_video"}],
                        },
                    }
                ]
            },
            "visual_sequence_qa": {"summary": {"beat_count": 1}, "qa": {"verdict": "PASS"}},
            "visual_span_asset_qa": {
                "spans": [{"visual_span_id": "vs01", "qa": {"verdict": "PASS"}}]
            },
            "trim_window_plan": {"spans": []},
        },
        update_stage=lambda name, status, **kw: calls.append((name, status)),
        check_stop=lambda: None,
    )
    ctx.calls = calls  # type: ignore[attr-defined]
    return ctx


def test_rollout_config_never_allows_automatic_weight_mutation() -> None:
    cfg = resolve_visual_rollout_config(
        {
            "shorts": {
                "visual_quality_flow": {
                    "enabled": True,
                    "mode": "report_only",
                    "rollout": {"capture_features": True, "auto_adjust_weights": True},
                }
            }
        }
    )

    assert cfg["capture_features"] is True
    assert cfg["requested_auto_adjust_weights"] is True
    assert cfg["auto_adjust_weights"] is False
    assert "auto_adjust_weights_forced_false" in cfg["warnings"]


def test_visual_performance_stage_writes_features_and_report_without_blocking(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)

    result = _stage_visual_performance(ctx)

    assert result.returns is None
    feature_path = ctx.json_dir / paths.SHORT_VISUAL_PERFORMANCE_FEATURES_FILE
    report_path = ctx.json_dir / paths.SHORT_VISUAL_PERFORMANCE_REPORT_FILE
    assert feature_path.exists()
    assert report_path.exists()
    feature_doc = json.loads(feature_path.read_text())
    report_doc = json.loads(report_path.read_text())
    assert feature_doc["rollout_policy"]["auto_adjust_weights"] is False
    assert report_doc["production_mutation"]["automatic_weight_mutation"] is False
    assert ("visual_performance", "completed") in ctx.calls


def test_visual_performance_stage_is_non_blocking_on_collection_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        "video_agent.shorts.builder.stages.visual_performance.build_visual_performance_features",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced collection failure")),
    )

    result = _stage_visual_performance(ctx)

    assert result.returns is None
    assert ctx.status["status"] == "rendered"
    assert ("visual_performance", "skipped") in ctx.calls


def test_visual_performance_stage_skips_when_capture_disabled(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, capture_features=False)

    _stage_visual_performance(ctx)

    assert not (ctx.json_dir / paths.SHORT_VISUAL_PERFORMANCE_FEATURES_FILE).exists()
    assert ("visual_performance", "skipped") in ctx.calls
