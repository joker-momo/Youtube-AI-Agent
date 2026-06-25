"""Integration test for the long-form ``elena_plan`` pipeline stage."""

from __future__ import annotations

import json
from pathlib import Path

from video_agent.orchestrator.orchestrator import create_job
from video_agent.orchestrator.stages.elena_plan import run_elena_plan_stage


def _make_job(tmp_path: Path) -> Path:
    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    scenes = {
        "job_id": "j1",
        "scenes": [
            {"id": "scene-01", "layout": "hook", "duration_sec": 10.0},
            {"id": "scene-02", "layout": "subtitle", "duration_sec": 12.0},
            {"id": "scene-03", "layout": "checklist", "duration_sec": 10.0},
        ],
    }
    (job_dir / "json" / "scenes.json").write_text(json.dumps(scenes))
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=["elena_plan"])
    return job_dir


def test_stage_writes_elena_cues(tmp_path):
    job_dir = _make_job(tmp_path)
    out = run_elena_plan_stage(job_dir, None)
    assert out == job_dir / "json" / "elena_cues.json"
    doc = json.loads(out.read_text())
    assert doc["schema_version"] == 1
    assert doc["generation_mode"] == "report_only"
    assert doc["total_frames"] == 10 * 30 + 12 * 30 + 10 * 30
    # hook -> one talking cue; checklist hidden.
    assert all(c["mode"] == "talking" for c in doc["cues"])
    assert doc["cues"][0]["treatment"] == "large"
