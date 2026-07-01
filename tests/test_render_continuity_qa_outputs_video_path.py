"""Regression: render_continuity_qa must find the rendered video at its real
location, outputs/video.mp4 (bridge P1 report, job auto-1782904564).

_find_rendered_video previously checked only job_dir/video.mp4 (legacy root)
and a non-recursive job_dir.glob("*.mp4") — missing ARTIFACT_VIDEO
("outputs/video.mp4"), which is where the renderer actually writes the final
file (see render_review.py's _resolve_artifact(job_dir, ARTIFACT_VIDEO) usage).
The stage silently wrote {"verdict": "PASS", "skipped": true, "reason": "no
compiled schedule or rendered video"} on a job that had BOTH a real
compiled_asset_schedule.json and a real 2GB outputs/video.mp4 -- the continuity
gate never actually inspected the video it was supposed to check.
"""

from __future__ import annotations

import json

from video_agent.orchestrator.job_state import JobState, StageStatus, save_job
from video_agent.orchestrator.stages import render_continuity_qa as rcq
from video_agent.utils.json_io import write_json


def _job(tmp_path):
    save_job(tmp_path, JobState(
        job_id="job-1", channel_id="ch", idea_path="i.json",
        created_at="t", updated_at="t", current_stage="render_continuity_qa",
        stages=[StageStatus(name="render_continuity_qa", status="in_progress")],
    ))


def _schedule_with_one_internal_boundary():
    return {
        "schema_version": 2, "fps": 30, "total_duration_in_frames": 180,
        "scene_boundaries": [
            {"scene_id": "s1", "from_frame": 0, "duration_in_frames": 90, "end_frame_exclusive": 90},
            {"scene_id": "s2", "from_frame": 90, "duration_in_frames": 90, "end_frame_exclusive": 180},
        ],
        "tracks": [{"track_id": "vt01", "track_type": "background_media",
                    "from_frame": 0, "duration_in_frames": 180, "end_frame_exclusive": 180,
                    "scene_ids": ["s1", "s2"]}],
    }


def test_find_rendered_video_locates_outputs_video_mp4(tmp_path):
    (tmp_path / "outputs").mkdir()
    video = tmp_path / "outputs" / "video.mp4"
    video.write_bytes(b"fake-render")
    found = rcq._find_rendered_video(tmp_path)
    assert found == video


def test_find_rendered_video_prefers_outputs_over_legacy_glob(tmp_path):
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "video.mp4").write_bytes(b"real")
    (tmp_path / "scene-01.mp4").write_bytes(b"unrelated asset")
    found = rcq._find_rendered_video(tmp_path)
    assert found == tmp_path / "outputs" / "video.mp4"


def test_stage_does_not_falsely_skip_when_video_is_in_outputs_dir(tmp_path, monkeypatch):
    _job(tmp_path)
    (tmp_path / "json").mkdir()
    write_json(tmp_path / "json" / "compiled_asset_schedule.json", _schedule_with_one_internal_boundary())
    write_json(tmp_path / "json" / "scenes.json", {"scenes": [{"id": "s1"}, {"id": "s2"}]})
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "video.mp4").write_bytes(b"fake-render")

    monkeypatch.setattr(rcq, "_sample_luma", lambda *a, **k: [128.0] * 181)

    out_path = rcq.run_render_continuity_qa_stage(tmp_path, None)
    result = json.loads(out_path.read_text())

    # Before the fix: skipped=True, reason "no compiled schedule or rendered
    # video" -- even though both existed. The continuity gate must actually run.
    assert result.get("skipped") is not True
    assert result.get("reason") != "no compiled schedule or rendered video"
    assert result["verdict"] == "PASS"
