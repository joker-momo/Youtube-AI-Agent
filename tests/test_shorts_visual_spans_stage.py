"""Stage-level tests for `_stage_visual_spans` (spec v3.2.3 §19, §41.3).

Exercises the stage with a lightweight fake context (the stage only touches a
handful of attributes), proving artifact persistence, span-id attachment, extras
publication, and report-only graceful degradation — without standing up the full
Short builder.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.builder.stages.visual_spans import _stage_visual_spans
from video_agent.shorts.builder.types import StageSignal


def _ctx(tmp_path: Path, short_scenes: dict[str, Any], channel_config: dict[str, Any]) -> SimpleNamespace:
    json_dir = tmp_path / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    stage_calls: list[tuple[str, str]] = []

    def update_stage(name: str, status: str, **kwargs: Any) -> None:
        stage_calls.append((name, status))

    ctx = SimpleNamespace(
        short_plan={"short_id": "short-04"},
        short_dir=tmp_path,
        json_dir=json_dir,
        long_job_dir=tmp_path.parent,
        channel_config=channel_config,
        status={"status": "generating"},
        extras={"short_scenes": short_scenes},
        update_stage=update_stage,
        check_stop=lambda: None,
    )
    ctx.stage_calls = stage_calls  # type: ignore[attr-defined]
    return ctx


def _scenes() -> dict[str, Any]:
    return {
        "short_id": "short-04",
        "scenes": [
            {"id": "s01", "layout": "short_hook", "duration_sec": 2.0, "visual_span_id": "vs01"},
            {"id": "s02", "layout": "short_tip", "duration_sec": 3.0, "visual_span_id": "vs02"},
            {"id": "s03", "layout": "short_tip", "duration_sec": 3.0, "visual_span_id": "vs02"},
        ],
    }


def test_stage_persists_artifacts_and_attaches_span_ids(tmp_path: Path) -> None:
    scenes = _scenes()
    ctx = _ctx(tmp_path, scenes, {})  # empty config → report_only default
    result = _stage_visual_spans(ctx)

    assert result.signal is StageSignal.PROCEED
    jd = tmp_path / "json"
    spans_doc = json.loads((jd / paths.SHORT_VISUAL_SPANS_FILE).read_text())
    qa_doc = json.loads((jd / paths.SHORT_VISUAL_SPAN_QA_FILE).read_text())

    assert spans_doc["generation_mode"] == "report_only"
    assert [s["scene_ids"] for s in spans_doc["spans"]] == [["s01"], ["s02", "s03"]]
    assert qa_doc["qa"]["verdict"] == "PASS"
    assert qa_doc["metrics"]["estimated_asset_call_reduction"] == 1

    # span ids attached onto the in-memory + persisted scene doc.
    assert scenes["scenes"][1]["visual_span_id"] == "vs02"
    persisted_scenes = json.loads((jd / paths.SHORT_SCENES_FILE).read_text())
    assert persisted_scenes["scenes"][2]["visual_span_id"] == "vs02"

    assert ctx.extras["visual_spans"]["spans"]
    assert ctx.extras["visual_span_validation"]["verdict"] == "PASS"
    assert ("visual_spans", "completed") in ctx.stage_calls


def test_stage_does_not_change_durations(tmp_path: Path) -> None:
    scenes = _scenes()
    before = [s["duration_sec"] for s in scenes["scenes"]]
    ctx = _ctx(tmp_path, scenes, {})
    _stage_visual_spans(ctx)
    after = [s["duration_sec"] for s in scenes["scenes"]]
    assert before == after


def test_report_only_is_non_fatal_on_bad_scenes(tmp_path: Path) -> None:
    # scenes is not a dict-shaped doc → build raises internally; report_only must
    # swallow it, mark the stage skipped, and PROCEED (legacy render preserved).
    ctx = _ctx(tmp_path, {"scenes": "not-a-list"}, {})
    result = _stage_visual_spans(ctx)
    assert result.signal is StageSignal.PROCEED
    assert ("visual_spans", "skipped") in ctx.stage_calls
    assert ctx.status["status"] == "generating"  # build not failed


def test_disabled_mode_still_reports(tmp_path: Path) -> None:
    cfg = {"shorts": {"visual_timeline": {"mode": "disabled"}}}
    ctx = _ctx(tmp_path, _scenes(), cfg)
    result = _stage_visual_spans(ctx)
    assert result.signal is StageSignal.PROCEED
    spans_doc = json.loads((tmp_path / "json" / paths.SHORT_VISUAL_SPANS_FILE).read_text())
    assert spans_doc["generation_mode"] == "disabled"
