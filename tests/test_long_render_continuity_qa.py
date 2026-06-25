"""Unit tests for the long-form render-continuity analyzer.

Checks that, where the compiled schedule mounts ONE continuous clip across several
scenes, the rendered luma does not reset/black-out at a span-internal scene
boundary (a reset would show as a black frame). Track boundaries (real cuts) are
not flagged.
"""

from __future__ import annotations

from video_agent.orchestrator.stages.render_continuity_qa import analyze_span_continuity


def _schedule(tracks, boundaries, total):
    return {"schema_version": 2, "fps": 30, "total_duration_in_frames": total,
            "scene_boundaries": boundaries, "tracks": tracks}


# One continuous track over 3 scenes: internal boundaries at 60 and 135.
_BOUNDARIES = [
    {"scene_id": "s1", "from_frame": 0, "duration_in_frames": 60, "end_frame_exclusive": 60},
    {"scene_id": "s2", "from_frame": 60, "duration_in_frames": 75, "end_frame_exclusive": 135},
    {"scene_id": "s3", "from_frame": 135, "duration_in_frames": 45, "end_frame_exclusive": 180},
]
_ONE_TRACK = [{"track_id": "vt01", "track_type": "background_media", "from_frame": 0,
               "duration_in_frames": 180, "end_frame_exclusive": 180, "scene_ids": ["s1", "s2", "s3"]}]


def test_continuous_clip_no_black_passes():
    luma = [128.0] * 180
    res = analyze_span_continuity(luma, _schedule(_ONE_TRACK, _BOUNDARIES, 180))
    assert res["verdict"] == "PASS"
    assert res["checked_boundaries"] == [60, 135]
    assert res["flagged"] == []


def test_black_frame_at_internal_boundary_flagged():
    luma = [128.0] * 180
    luma[60] = 2.0  # reset/black at the s1->s2 span-internal boundary
    res = analyze_span_continuity(luma, _schedule(_ONE_TRACK, _BOUNDARIES, 180))
    assert res["verdict"] == "FAIL"
    assert 60 in res["flagged"]


def test_track_boundary_is_not_flagged():
    # Two tracks (real cut at 90): a dark frame exactly at the track boundary is a
    # legitimate cut, not a span-internal reset -> not flagged.
    tracks = [
        {"track_id": "vt01", "track_type": "background_media", "from_frame": 0,
         "duration_in_frames": 90, "end_frame_exclusive": 90, "scene_ids": ["s1"]},
        {"track_id": "vt02", "track_type": "background_media", "from_frame": 90,
         "duration_in_frames": 90, "end_frame_exclusive": 180, "scene_ids": ["s2"]},
    ]
    boundaries = [
        {"scene_id": "s1", "from_frame": 0, "duration_in_frames": 90, "end_frame_exclusive": 90},
        {"scene_id": "s2", "from_frame": 90, "duration_in_frames": 90, "end_frame_exclusive": 180},
    ]
    luma = [128.0] * 180
    luma[90] = 1.0
    res = analyze_span_continuity(luma, _schedule(tracks, boundaries, 180))
    assert res["checked_boundaries"] == []  # no span-internal boundaries
    assert res["verdict"] == "PASS"


def test_no_schedule_is_pass():
    res = analyze_span_continuity([128.0] * 10, {"tracks": [], "scene_boundaries": [], "total_duration_in_frames": 10})
    assert res["verdict"] == "PASS"
    assert res["checked_boundaries"] == []


# --------------------------------------------------------------------------- #
# Stage-level (long-form run_render_continuity_qa_stage)
# --------------------------------------------------------------------------- #
def test_stage_pass_skips_without_schedule_or_video(tmp_path):
    import json

    from video_agent.orchestrator.orchestrator import create_job
    from video_agent.orchestrator.stages.render_continuity_qa import (
        run_render_continuity_qa_stage,
    )

    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "json" / "scenes.json").write_text(json.dumps({"scenes": []}))
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=["render_continuity_qa"])

    out = run_render_continuity_qa_stage(job_dir, None)
    assert out == job_dir / "json" / "render_continuity_qa.json"
    doc = json.loads(out.read_text())
    # No compiled schedule + no rendered video -> PASS-skip (never blocks the pipeline).
    assert doc["verdict"] == "PASS"
    assert doc["skipped"] is True
