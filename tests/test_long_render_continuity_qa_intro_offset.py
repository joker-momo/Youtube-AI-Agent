"""Regression guard for render_continuity_qa intro offset (B2).

Schedule scene_boundaries are scene-layer 0-based, but the rendered long video
shifts the scene layer by ``introFrames`` (ChannelVideo.tsx). The continuity QA
must sample the rendered video at ``boundary + introFrames``, not at the raw
boundary, or it inspects intro frames instead of the real scene cut (false
PASS/FAIL). Offset is read from the already-written render_props.json.
"""

from __future__ import annotations

import json

from video_agent.orchestrator.job_state import JobState, StageStatus, save_job
from video_agent.orchestrator.stages import render_continuity_qa as rcq
from video_agent.utils.json_io import write_json


def _write_render_props(job_dir, *, intro_sec, fps):
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    write_json(
        job_dir / "json" / "render_props.json",
        {"render": {"fps": fps}, "branding": {"intro_sec": intro_sec, "outro_sec": 1.0}},
    )


def test_intro_offset_frames_from_render_props(tmp_path):
    _write_render_props(tmp_path, intro_sec=2.0, fps=30)
    assert rcq._intro_offset_frames(tmp_path) == 60


def test_intro_offset_zero_when_no_branding(tmp_path):
    (tmp_path / "json").mkdir(parents=True, exist_ok=True)
    write_json(tmp_path / "json" / "render_props.json", {"render": {"fps": 30}})
    assert rcq._intro_offset_frames(tmp_path) == 0


def _job(tmp_path):
    save_job(tmp_path, JobState(
        job_id="job-1", channel_id="ch", idea_path="i.json",
        created_at="t", updated_at="t", current_stage="render_continuity_qa",
        stages=[StageStatus(name="render_continuity_qa", status="in_progress")],
    ))


def test_stage_samples_video_at_intro_shifted_frames(tmp_path, monkeypatch):
    _job(tmp_path)
    _write_render_props(tmp_path, intro_sec=2.0, fps=30)
    # One continuous track over two scenes -> one span-internal boundary at 90.
    schedule = {
        "schema_version": 2, "fps": 30, "total_duration_in_frames": 180,
        "scene_boundaries": [
            {"scene_id": "s1", "from_frame": 0, "duration_in_frames": 90, "end_frame_exclusive": 90},
            {"scene_id": "s2", "from_frame": 90, "duration_in_frames": 90, "end_frame_exclusive": 180},
        ],
        "tracks": [{"track_id": "vt01", "track_type": "background_media",
                    "from_frame": 0, "duration_in_frames": 180, "end_frame_exclusive": 180,
                    "scene_ids": ["s1", "s2"]}],
    }
    (tmp_path / "json" / "compiled_asset_schedule.json").write_text(json.dumps(schedule))
    write_json(tmp_path / "json" / "scenes.json", {"scenes": [{"id": "s1"}, {"id": "s2"}]})
    (tmp_path / "video.mp4").write_bytes(b"x")  # presence only

    captured = {}

    def _fake_sample(video, frames, total, *, frame_offset=0):
        captured["frames"] = list(frames)
        captured["frame_offset"] = frame_offset
        # luma indexed by unshifted scene-layer frame; non-black everywhere.
        return [128.0] * (total + 1)

    monkeypatch.setattr(rcq, "_sample_luma", _fake_sample)
    rcq.run_render_continuity_qa_stage(tmp_path, None)

    # Boundary 90 sampled with a +60 intro offset (analyze still sees frame 90).
    assert captured["frame_offset"] == 60
    assert 90 in captured["frames"]
