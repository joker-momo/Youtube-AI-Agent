from __future__ import annotations

from typing import Any

from video_agent.shorts.asset_schedule import (
    compile_asset_schedule,
    validate_compiled_asset_schedule,
)

FPS = 30


def _scene(sid: str, dur: float, *, graphic: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"id": sid, "layout": "short_tip", "duration_sec": dur}
    if graphic:
        out["layout"] = "graphic_definition"
        out["visual_type"] = "graphic"
    return out


def _resolved(scene_id: str) -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "asset_id": f"asset-{scene_id}",
        "provider": "pexels_video",
        "provider_asset_id": f"provider-{scene_id}",
        "public_ref": f"jobs/short-04/assets/{scene_id}.mp4",
        "render_media_kind": "video",
        "source_media_kind": "native_video",
        "source_duration_sec": 12.0,
        "selection_score": 80.0,
        "asset_match_status": "strong_match",
        "exists": True,
    }


def _beat_plan(mode: str, beats: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "spans": [
            {
                "visual_span_id": "vs01",
                "selected_plan": {
                    "plan_id": f"vp01-{mode}",
                    "mode": mode,
                    "beats": beats,
                },
                "qa": {"verdict": "PASS", "errors": [], "warnings": []},
            }
        ],
    }


def test_two_clip_visual_plan_compiles_to_two_non_overlapping_tracks() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.0)]
    schedule = compile_asset_schedule(
        short_id="short-04",
        scene_doc={"scenes": scenes},
        visual_spans={"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]},
        resolved_visuals={"scenes": {"s01": _resolved("s01"), "s02": _resolved("s02")}},
        fps=FPS,
        timing_source="tts_final",
        scene_version=3,
        visual_beat_plan=_beat_plan(
            "two_clip",
            [
                {
                    "beat_id": "vb01",
                    "type": "native_video",
                    "scene_ids": ["s01"],
                    "asset_selection_ref": "s01-selection",
                    "asset_ref": "jobs/short-04/assets/s01.mp4",
                    "boundary_reason": "problem-to-solution transition",
                },
                {
                    "beat_id": "vb02",
                    "type": "native_video",
                    "scene_ids": ["s02"],
                    "asset_selection_ref": "s02-selection",
                    "asset_ref": "jobs/short-04/assets/s02.mp4",
                    "boundary_reason": "required evidence changes",
                },
            ],
        ),
    )

    assert schedule["qa"]["verdict"] == "PASS", schedule["qa"]["errors"]
    assert len(schedule["tracks"]) == 2
    assert [track["visual_beat_id"] for track in schedule["tracks"]] == ["vb01", "vb02"]
    assert [
        (track["from_frame"], track["end_frame_exclusive"]) for track in schedule["tracks"]
    ] == [
        (0, 60),
        (60, 120),
    ]
    assert all(track["playback_rate"] == 1.0 for track in schedule["tracks"])
    assert all(track["loop_policy"] == "forbid" for track in schedule["tracks"])
    assert all(
        track["selection_debug"]["mode"] == "visual_plan:two_clip" for track in schedule["tracks"]
    )


def test_clip_plus_graphic_visual_plan_uses_generated_image_track() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.0)]
    scenes[1]["generated_image_source_layout"] = "graphic_definition"
    generated = {
        **_resolved("s02"),
        "provider": "ai_generated",
        "source_media_kind": "image_backed_video",
    }
    schedule = compile_asset_schedule(
        short_id="short-04",
        scene_doc={"scenes": scenes},
        visual_spans={"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]},
        resolved_visuals={"scenes": {"s01": _resolved("s01"), "s02": generated}},
        fps=FPS,
        timing_source="tts_final",
        scene_version=3,
        visual_beat_plan=_beat_plan(
            "clip_plus_graphic",
            [
                {
                    "beat_id": "vb01",
                    "type": "native_video",
                    "scene_ids": ["s01"],
                    "asset_ref": "jobs/short-04/assets/s01.mp4",
                    "boundary_reason": "graphic explanation adds clarity",
                },
                {
                    "beat_id": "vb02",
                    "type": "generated_image",
                    "scene_ids": ["s02"],
                    "asset_ref": "jobs/short-04/assets/s02.mp4",
                    "source_media_kind": "image_backed_video",
                    "boundary_reason": "graphic explanation adds clarity",
                },
            ],
        ),
    )

    assert schedule["qa"]["verdict"] == "PASS", schedule["qa"]["errors"]
    assert len(schedule["tracks"]) == 2
    assert [track["scene_ids"] for track in schedule["tracks"]] == [["s01"], ["s02"]]
    assert schedule["tracks"][1]["source_media_kind"] == "image_backed_video"
    assert all(
        track["selection_debug"]["mode"] == "visual_plan:clip_plus_graphic"
        for track in schedule["tracks"]
    )


def test_legacy_fallback_remains_valid_without_visual_beat_plan() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.0)]
    schedule = compile_asset_schedule(
        short_id="short-04",
        scene_doc={"scenes": scenes},
        visual_spans={"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]},
        resolved_visuals={"scenes": {"s01": _resolved("s01"), "s02": _resolved("s02")}},
        fps=FPS,
        timing_source="tts_final",
        scene_version=3,
        visual_beat_plan=None,
    )

    qa = validate_compiled_asset_schedule(schedule, {"scenes": scenes})
    assert qa["verdict"] == "PASS"
    assert schedule["tracks"][0]["selection_debug"]["mode"] in {
        "continuous_clip",
        "legacy_scene_assets",
    }
