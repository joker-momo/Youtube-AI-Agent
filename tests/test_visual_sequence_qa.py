from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from video_agent.shorts import paths
from video_agent.shorts.builder.stages.visual_beats import _stage_visual_beats


def _ctx(
    tmp_path: Path, *, mode: str = "report_only", beat_enabled: bool = True
) -> SimpleNamespace:
    json_dir = tmp_path / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[str, str]] = []
    scenes = {
        "scenes": [
            {"id": "s01", "layout": "short_tip", "duration_sec": 2.0},
            {"id": "s02", "layout": "short_tip", "duration_sec": 2.0},
        ]
    }
    ctx = SimpleNamespace(
        short_plan={"short_id": "short-04"},
        short_dir=tmp_path,
        json_dir=json_dir,
        long_job_dir=tmp_path.parent,
        channel_config={
            "shorts": {
                "render": {"fps": 30},
                "visual_quality_flow": {
                    "enabled": True,
                    "mode": mode,
                    "beat_planner": {"enabled": beat_enabled, "max_non_legacy_plans": 3},
                },
            }
        },
        status={"status": "generating"},
        extras={
            "short_scenes": scenes,
            "visual_spans": {"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]},
            "assets_manifest": {
                "scenes": [
                    {
                        "scene_id": "s01",
                        "public_background": "jobs/x/assets/s01.mp4",
                        "asset_tier": "pexels_video",
                        "provider": "pexels_video",
                        "source_duration_sec": 12.0,
                        "asset_selection": {
                            "score": 80,
                            "asset_match_status": "strong_match",
                        },
                    },
                    {
                        "scene_id": "s02",
                        "public_background": "jobs/x/assets/s02.mp4",
                        "asset_tier": "pexels_video",
                        "provider": "pexels_video",
                        "source_duration_sec": 12.0,
                        "asset_selection": {
                            "score": 76,
                            "asset_match_status": "strong_match",
                        },
                    },
                ]
            },
            "visual_span_asset_qa": {
                "spans": [
                    {
                        "visual_span_id": "vs01",
                        "final_candidate_id": "final-01",
                        "render_eligible": True,
                        "qa": {"verdict": "CAPABILITY_REDUCED", "errors": [], "warnings": []},
                    }
                ]
            },
            "trim_window_plan": {
                "spans": [
                    {
                        "visual_span_id": "vs01",
                        "scene_ids": ["s01", "s02"],
                        "final_candidate_id": "final-01",
                        "provider": "pexels_video",
                        "provider_asset_id": "provider-final",
                        "asset_ref": "jobs/x/assets/final.mp4",
                        "selected_window_start_in_frames": 0,
                        "selected_window_end_in_frames": 120,
                        "trim_timebase_fps": 30,
                        "source_duration_sec": 12.0,
                        "window_score": 90.0,
                        "motion_band": "normal_motion",
                        "crop_stability_score": 0.9,
                    }
                ]
            },
        },
        update_stage=lambda name, status, **kw: calls.append((name, status)),
        check_stop=lambda: None,
    )
    ctx.calls = calls  # type: ignore[attr-defined]
    return ctx


def test_visual_beats_stage_writes_beat_plan_and_sequence_qa(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    result = _stage_visual_beats(ctx)

    assert result.returns is None
    beat_path = ctx.json_dir / paths.SHORT_VISUAL_BEAT_PLAN_FILE
    qa_path = ctx.json_dir / paths.SHORT_VISUAL_SEQUENCE_QA_FILE
    assert beat_path.exists()
    assert qa_path.exists()
    assert not (ctx.json_dir / "visual_performance_features.json").exists()
    beat_plan = json.loads(beat_path.read_text())
    sequence_qa = json.loads(qa_path.read_text())
    assert beat_plan["spans"][0]["selected_plan"]["mode"] == "continuous_clip"
    assert sequence_qa["qa"]["verdict"] == "CAPABILITY_REDUCED"
    assert sequence_qa["summary"]["beat_count"] == 1
    assert sequence_qa["summary"]["track_count"] == 1
    assert ctx.extras["visual_beat_plan"] == beat_plan
    assert ctx.extras["visual_sequence_qa"] == sequence_qa
    assert ("visual_beats", "completed") in ctx.calls


def test_visual_beats_report_only_capability_reduced_does_not_block_render(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, mode="report_only")

    result = _stage_visual_beats(ctx)

    assert result.returns is None
    assert ctx.status["status"] == "generating"


def test_visual_beats_disabled_skips_without_artifacts(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, beat_enabled=False)

    result = _stage_visual_beats(ctx)

    assert result.returns is None
    assert not (ctx.json_dir / paths.SHORT_VISUAL_BEAT_PLAN_FILE).exists()
    assert ("visual_beats", "skipped") in ctx.calls
