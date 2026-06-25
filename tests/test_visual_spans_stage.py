"""Integration test for the long-form ``visual_spans`` pipeline stage.

The stage is report-only: it reads ``json/scenes.json``, groups scenes into
visual spans, and writes ``json/visual_spans.json``. It must NOT touch assets or
render artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

from video_agent.orchestrator.orchestrator import create_job
from video_agent.orchestrator.stages.visual_spans import run_visual_spans_stage


def _make_job(tmp_path: Path) -> Path:
    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    scenes = {
        "job_id": "j1",
        "scenes": [
            {"id": "scene-01", "layout": "hook", "duration_sec": 12},
            {"id": "scene-02", "layout": "subtitle", "duration_sec": 12},
            {"id": "scene-03", "layout": "subtitle", "duration_sec": 12},
            {"id": "scene-04", "layout": "cta", "duration_sec": 10},
        ],
    }
    (job_dir / "json" / "scenes.json").write_text(json.dumps(scenes))
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=["visual_spans"])
    return job_dir


def test_stage_writes_report_only_visual_spans(tmp_path):
    job_dir = _make_job(tmp_path)
    out = run_visual_spans_stage(job_dir, None)
    assert out == job_dir / "json" / "visual_spans.json"
    assert out.exists()
    doc = json.loads(out.read_text())
    assert doc["generation_mode"] == "report_only"
    assert doc["qa"]["verdict"] == "PASS"
    # hook isolated | scene-02+03 subtitle merged | cta isolated == 3 spans
    assert doc["metrics"]["visual_span_count"] == 3
    cta = next(s for s in doc["spans"] if s["scene_ids"] == ["scene-04"])
    assert cta["planned_mode"] == "graphic_image"


def test_stage_does_not_touch_assets_or_render(tmp_path):
    job_dir = _make_job(tmp_path)
    run_visual_spans_stage(job_dir, None)
    assert not (job_dir / "assets").exists()
    assert not (job_dir / "json" / "render_props.json").exists()
    assert not (job_dir / "json" / "assets_manifest.json").exists()


def test_stage_assigns_span_ids_back_onto_scenes_without_touching_other_fields(tmp_path):
    job_dir = job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    scenes = {
        "job_id": "j1",
        "scenes": [
            {"id": "scene-01", "layout": "hook", "duration_sec": 12, "narration": "a"},
            {"id": "scene-02", "layout": "subtitle", "duration_sec": 12, "narration": "b"},
            {"id": "scene-03", "layout": "subtitle", "duration_sec": 12, "narration": "c"},
        ],
    }
    (job_dir / "json" / "scenes.json").write_text(json.dumps(scenes))
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=["visual_spans"])

    run_visual_spans_stage(job_dir, None)

    updated = json.loads((job_dir / "json" / "scenes.json").read_text())["scenes"]
    # hook isolated -> vs01; the two subtitles merge -> vs02
    assert updated[0]["visual_span_id"] == "vs01"
    assert updated[1]["visual_span_id"] == "vs02"
    assert updated[2]["visual_span_id"] == "vs02"
    # other fields untouched
    assert [s["narration"] for s in updated] == ["a", "b", "c"]
    assert [s["duration_sec"] for s in updated] == [12, 12, 12]
