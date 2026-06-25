"""Unit tests for the long-form compiled asset schedule (``video_agent.visual.schedule``).

The schedule is the single source of truth for the renderer's background layer. It
must emit the exact ``CompiledAssetSchedule`` shape defined in
``remotion/src/render-props.ts`` (schema_version 2, scene_boundaries +
total_duration_in_frames + end_frame_exclusive, timing_source tts_final|scene_plan),
with frame math matching the renderer's ``Math.round(duration_sec*fps)`` per scene.
"""

from __future__ import annotations

import math

from video_agent.visual import build_visual_spans
from video_agent.visual.schedule import compile_asset_schedule


def _scene(sid, layout="subtitle", dur=12.0, bg=None):
    return {
        "id": sid,
        "layout": layout,
        "duration_sec": dur,
        "asset_refs": {"background": bg or f"assets/{sid}.mp4"},
    }


def _doc(*scenes):
    return {"job_id": "j1", "scenes": list(scenes)}


def _f(dur, fps=30):
    # JS Math.round parity (round half up), NOT Python banker's rounding.
    return math.floor(dur * fps + 0.5)


def _compile(doc, fps=30):
    spans = build_visual_spans(doc, {}, job_id="j1")
    return compile_asset_schedule(scene_doc=doc, visual_spans=spans, fps=fps)


# --------------------------------------------------------------------------- #
# Schema shape
# --------------------------------------------------------------------------- #
def test_schema_version_and_required_top_level_fields():
    sched = _compile(_doc(_scene("scene-01")))
    assert sched["schema_version"] == 2
    assert sched["fps"] == 30
    assert sched["timing_source"] == "tts_final"
    assert "scene_boundaries" in sched and "tracks" in sched
    assert "total_duration_in_frames" in sched


def test_timing_source_override():
    doc = _doc(_scene("scene-01"))
    spans = build_visual_spans(doc, {}, job_id="j1")
    sched = compile_asset_schedule(
        scene_doc=doc, visual_spans=spans, fps=30, timing_source="scene_plan"
    )
    assert sched["timing_source"] == "scene_plan"


# --------------------------------------------------------------------------- #
# Scene boundaries — frame math matches the renderer
# --------------------------------------------------------------------------- #
def test_scene_boundaries_cumulative_frames_match_js_round():
    doc = _doc(
        _scene("scene-01", "hook", 12.28),
        _scene("scene-02", "subtitle", 9.34),
        _scene("scene-03", "subtitle", 21.8),
    )
    sched = _compile(doc)
    b = sched["scene_boundaries"]
    d0, d1, d2 = _f(12.28), _f(9.34), _f(21.8)
    assert [x["scene_id"] for x in b] == ["scene-01", "scene-02", "scene-03"]
    assert b[0]["from_frame"] == 0 and b[0]["duration_in_frames"] == d0
    assert b[0]["end_frame_exclusive"] == d0
    assert b[1]["from_frame"] == d0 and b[1]["end_frame_exclusive"] == d0 + d1
    assert b[2]["from_frame"] == d0 + d1
    assert b[2]["end_frame_exclusive"] == d0 + d1 + d2
    assert sched["total_duration_in_frames"] == d0 + d1 + d2


def test_scene_boundary_is_graphic_flag():
    doc = _doc(_scene("scene-01", "subtitle"), _scene("scene-02", "checklist"))
    sched = _compile(doc)
    by_id = {b["scene_id"]: b for b in sched["scene_boundaries"]}
    assert by_id["scene-01"]["is_graphic"] is False
    assert by_id["scene-02"]["is_graphic"] is True


# --------------------------------------------------------------------------- #
# Tracks — one per span, continuous across member scenes
# --------------------------------------------------------------------------- #
def test_continuous_span_emits_one_track_spanning_members():
    doc = _doc(
        _scene("scene-01", "hook", 12.0),
        _scene("scene-02", "subtitle", 12.0),
        _scene("scene-03", "subtitle", 12.0),
        _scene("scene-04", "checklist", 10.0),
    )
    sched = _compile(doc)
    tracks = sched["tracks"]
    # vs01 hook | vs02 [s2,s3] | vs03 graphic
    assert len(tracks) == 3
    sub = next(t for t in tracks if t["scene_ids"] == ["scene-02", "scene-03"])
    assert sub["track_type"] == "background_media"
    assert sub["from_frame"] == _f(12.0)
    assert sub["duration_in_frames"] == _f(12.0) * 2
    assert sub["end_frame_exclusive"] == _f(12.0) * 3
    assert sub["render_media_kind"] == "video"
    assert sub["loop_policy"] == "forbid"
    assert sub["playback_rate"] == 1.0


def test_graphic_span_track_is_image_kind():
    doc = _doc(_scene("scene-01", "warning", 10.0))
    sched = _compile(doc)
    t = sched["tracks"][0]
    assert t["render_media_kind"] == "image"
    assert t["source_media_kind"] == "native_image"


def test_phase2_asset_ref_is_first_member_scene_background():
    doc = _doc(
        _scene("scene-01", "subtitle", 12.0, bg="assets/scene-01.mp4"),
        _scene("scene-02", "subtitle", 12.0, bg="assets/scene-02.mp4"),
    )
    sched = _compile(doc)
    t = sched["tracks"][0]
    assert t["scene_ids"] == ["scene-01", "scene-02"]
    assert t["asset_ref"] == "assets/scene-01.mp4"  # Phase 2: first scene's clip


def test_tracks_cover_full_timeline_without_gap_or_overlap():
    doc = _doc(
        _scene("scene-01", "hook", 12.0),
        _scene("scene-02", "subtitle", 14.0),
        _scene("scene-03", "subtitle", 13.0),
        _scene("scene-04", "cta", 8.0),
    )
    sched = _compile(doc)
    tracks = sorted(sched["tracks"], key=lambda t: t["from_frame"])
    cursor = 0
    for t in tracks:
        assert t["from_frame"] == cursor  # contiguous, no gap/overlap
        cursor = t["end_frame_exclusive"]
    assert cursor == sched["total_duration_in_frames"]


def test_required_track_fields_present():
    doc = _doc(_scene("scene-01", "subtitle", 12.0))
    t = _compile(doc)["tracks"][0]
    for field in (
        "track_id", "track_type", "visual_span_id", "scene_ids", "asset_ref",
        "render_media_kind", "source_media_kind", "from_frame",
        "duration_in_frames", "end_frame_exclusive", "trim_before_in_frames",
        "trim_timebase_fps", "playback_rate", "loop_policy",
    ):
        assert field in t, f"missing track field: {field}"
