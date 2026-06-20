from __future__ import annotations

from typing import Any

from video_agent.shorts.asset_schedule import (
    compile_asset_schedule,
    validate_compiled_asset_schedule,
)

FPS = 30


def _scene(sid: str, dur: float) -> dict[str, Any]:
    return {"id": sid, "layout": "short_tip", "duration_sec": dur}


def test_pr_d_trim_plan_overrides_continuous_track_trim_and_asset_ref() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.0)]
    trim_plan = {
        "schema_version": 1,
        "spans": [
            {
                "visual_span_id": "vs01",
                "scene_ids": ["s01", "s02"],
                "final_candidate_id": "pexels_video-final",
                "provider": "pexels_video",
                "provider_asset_id": "final",
                "asset_ref": "jobs/short-04/assets/final.mp4",
                "local_path": "/tmp/final.mp4",
                "source_duration_sec": 9.0,
                "selected_window_start_in_frames": 45,
                "selected_window_end_in_frames": 165,
                "trim_timebase_fps": FPS,
                "required_duration_in_frames": 120,
                "motion_band": "normal_motion",
                "crop_stability_score": 0.88,
                "crop_plan": {
                    "mode": "cover",
                    "anchor": "center",
                    "scale": 1.0,
                    "target": "person",
                },
                "qa": {"verdict": "CAPABILITY_REDUCED", "errors": [], "warnings": []},
            }
        ],
    }

    schedule = compile_asset_schedule(
        short_id="short-04",
        scene_doc={"scenes": scenes},
        visual_spans={
            "spans": [
                {"id": "vs01", "scene_ids": ["s01", "s02"], "planned_mode": "continuous_clip"}
            ]
        },
        resolved_visuals={"scenes": {}},
        fps=FPS,
        timing_source="tts_final",
        scene_version=9,
        trim_window_plan=trim_plan,
    )

    assert schedule["qa"]["verdict"] == "PASS", schedule["qa"]["errors"]
    assert len(schedule["tracks"]) == 1
    track = schedule["tracks"][0]
    assert track["asset_ref"] == "jobs/short-04/assets/final.mp4"
    assert track["trim_before_in_frames"] == 45
    assert track["trim_end_in_frames"] == 165
    assert track["duration_in_frames"] == 120
    assert track["selection_debug"]["mode"] == "visual_quality_flow_pr_d"
    assert track["playback_rate"] == 1.0
    assert track["loop_policy"] == "forbid"


def test_pr_d_trim_cannot_extend_past_source_duration() -> None:
    scenes = [_scene("s01", 2.0)]
    schedule = compile_asset_schedule(
        short_id="short-04",
        scene_doc={"scenes": scenes},
        visual_spans={
            "spans": [{"id": "vs01", "scene_ids": ["s01"], "planned_mode": "continuous_clip"}]
        },
        resolved_visuals={"scenes": {}},
        fps=FPS,
        timing_source="tts_final",
        scene_version=9,
        trim_window_plan={
            "spans": [
                {
                    "visual_span_id": "vs01",
                    "final_candidate_id": "pexels_video-final",
                    "asset_ref": "jobs/short-04/assets/final.mp4",
                    "source_duration_sec": 1.0,
                    "selected_window_start_in_frames": 15,
                    "selected_window_end_in_frames": 75,
                    "trim_timebase_fps": FPS,
                }
            ]
        },
    )

    qa = validate_compiled_asset_schedule(schedule, {"scenes": scenes})
    assert any("trim_end_exceeds_source_duration" in error for error in qa["errors"])
