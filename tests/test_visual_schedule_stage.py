"""Integration test for the long-form ``visual_schedule`` pipeline stage.

Runs after ``whisper_timestamps`` (frame-accurate timing). Reads scenes.json +
visual_spans.json and writes compiled_asset_schedule.json (schema v2). It must not
touch render artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

from video_agent.orchestrator.orchestrator import create_job
from video_agent.orchestrator.stages.visual_schedule import run_visual_schedule_stage
from video_agent.visual import build_visual_spans


def _make_job(tmp_path: Path) -> Path:
    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    scenes = {
        "job_id": "j1",
        "scenes": [
            {"id": "scene-01", "layout": "hook", "duration_sec": 12.0,
             "asset_refs": {"background": "assets/scene-01.mp4"}},
            {"id": "scene-02", "layout": "subtitle", "duration_sec": 12.0,
             "asset_refs": {"background": "assets/scene-02.mp4"}},
            {"id": "scene-03", "layout": "subtitle", "duration_sec": 12.0,
             "asset_refs": {"background": "assets/scene-03.mp4"}},
            {"id": "scene-04", "layout": "checklist", "duration_sec": 10.0,
             "asset_refs": {"background": "assets/scene-04.mp4"}},
        ],
    }
    (job_dir / "json" / "scenes.json").write_text(json.dumps(scenes))
    spans = build_visual_spans(scenes, {}, job_id="j1")
    (job_dir / "json" / "visual_spans.json").write_text(json.dumps(spans))
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=["visual_schedule"])
    return job_dir


def test_stage_writes_compiled_asset_schedule(tmp_path):
    job_dir = _make_job(tmp_path)
    out = run_visual_schedule_stage(job_dir, None)
    assert out == job_dir / "json" / "compiled_asset_schedule.json"
    sched = json.loads(out.read_text())
    assert sched["schema_version"] == 2
    assert sched["fps"] == 30
    assert sched["timing_source"] == "tts_final"
    # 12+12+12+10 s @30fps = 360+360+360+300 = 1380 frames
    assert sched["total_duration_in_frames"] == 1380
    # hook | [s2,s3] | checklist == 3 spans -> 3 tracks
    assert len(sched["tracks"]) == 3


def test_stage_does_not_touch_render_props(tmp_path):
    job_dir = _make_job(tmp_path)
    run_visual_schedule_stage(job_dir, None)
    assert not (job_dir / "json" / "render_props.json").exists()
