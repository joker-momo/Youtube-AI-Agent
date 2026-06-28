"""Regression guard: render_continuity_qa must sample the schedule the renderer
actually consumed.

Enforced renders recompile the asset schedule from the FINAL post-TTS scene
durations and embed it in ``render_props.json`` under ``visual_schedule``. The
on-disk ``compiled_asset_schedule.json`` is the ``visual_schedule`` stage output,
compiled *before* render-time duration sync, so its ``scene_boundaries`` are stale
by render time. QA reading the stale artifact samples luma at boundaries that no
longer match the rendered video -> false continuity verdicts. QA must prefer the
embedded (recompiled) schedule and fall back to the on-disk artifact only when no
embedded copy exists (report_only / legacy / older jobs).
"""

from __future__ import annotations

import json

from video_agent.orchestrator.job_state import JobState, StageStatus, save_job
from video_agent.orchestrator.stages import render_continuity_qa as rcq
from video_agent.utils.json_io import write_json


def _two_scene_schedule(boundary: int, total: int) -> dict:
    """A single continuous background track over two scenes -> one span-internal
    boundary at ``boundary``."""
    return {
        "schema_version": 2, "fps": 30, "total_duration_in_frames": total,
        "scene_boundaries": [
            {"scene_id": "s1", "from_frame": 0, "duration_in_frames": boundary,
             "end_frame_exclusive": boundary},
            {"scene_id": "s2", "from_frame": boundary,
             "duration_in_frames": total - boundary, "end_frame_exclusive": total},
        ],
        "tracks": [{"track_id": "vt01", "track_type": "background_media",
                    "from_frame": 0, "duration_in_frames": total,
                    "end_frame_exclusive": total, "scene_ids": ["s1", "s2"]}],
    }


def _job(job_dir, job_id: str) -> None:
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    save_job(job_dir, JobState(
        job_id=job_id, channel_id="ch", idea_path="i.json",
        created_at="t", updated_at="t", current_stage="render_continuity_qa",
        stages=[StageStatus(name="render_continuity_qa", status="in_progress")],
    ))
    write_json(job_dir / "json" / "scenes.json", {"scenes": [{"id": "s1"}, {"id": "s2"}]})
    (job_dir / "video.mp4").write_bytes(b"x")  # presence only


def _capture_sample(monkeypatch) -> dict:
    captured: dict = {}

    def _fake_sample(video, frames, total, *, frame_offset=0):
        captured["frames"] = list(frames)
        captured["total"] = total
        return [128.0] * (total + 1)  # non-black everywhere

    monkeypatch.setattr(rcq, "_sample_luma", _fake_sample)
    return captured


def test_qa_prefers_recompiled_schedule_in_render_props(tmp_path, monkeypatch):
    _job(tmp_path, "job-recompiled")
    # Stale on-disk artifact: internal boundary at frame 90.
    (tmp_path / "json" / "compiled_asset_schedule.json").write_text(
        json.dumps(_two_scene_schedule(90, 180)))
    # Recompiled schedule embedded in render_props: internal boundary at frame 120.
    write_json(tmp_path / "json" / "render_props.json", {
        "render": {"fps": 30}, "branding": {"intro_sec": 0.0, "outro_sec": 0.0},
        "visual_schedule": _two_scene_schedule(120, 240),
    })

    captured = _capture_sample(monkeypatch)
    rcq.run_render_continuity_qa_stage(tmp_path, None)

    # Samples the recompiled boundary (120), never the stale on-disk one (90).
    assert 120 in captured["frames"]
    assert 90 not in captured["frames"]
    assert captured["total"] == 240


def test_qa_falls_back_to_on_disk_when_no_embedded_schedule(tmp_path, monkeypatch):
    _job(tmp_path, "job-fallback")
    (tmp_path / "json" / "compiled_asset_schedule.json").write_text(
        json.dumps(_two_scene_schedule(90, 180)))
    # report_only / legacy: render_props carries no visual_schedule.
    write_json(tmp_path / "json" / "render_props.json", {
        "render": {"fps": 30}, "branding": {"intro_sec": 0.0, "outro_sec": 0.0},
    })

    captured = _capture_sample(monkeypatch)
    rcq.run_render_continuity_qa_stage(tmp_path, None)

    assert 90 in captured["frames"]
    assert captured["total"] == 180
