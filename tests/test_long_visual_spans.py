"""Unit tests for the long-form visual-span core (``video_agent.visual``).

Pure module — no IO/LLM. These tests lock the Phase 1 (Gate 0) grouping rule:
only contiguous ``subtitle`` scenes merge; hook/cta/graphic scenes are isolated;
caps split spans; planner hints are respected; coverage stays complete + ordered.
"""

from __future__ import annotations

from video_agent.visual import (
    assign_span_ids_to_scenes,
    build_visual_spans,
    resolve_visual_span_config,
    validate_and_repair_visual_spans,
)
from video_agent.visual.spans import verify_span_invariants


def _scene(sid: str, layout: str = "subtitle", dur: float = 12.0, **extra):
    s = {"id": sid, "layout": layout, "duration_sec": dur}
    s.update(extra)
    return s


def _doc(*scenes):
    return {"scenes": list(scenes)}


def _cfg(**overrides):
    base = {
        "mode": "report_only",
        "isolate_hook": True,
        "isolate_cta": True,
        "isolate_graphic_scenes": True,
        "groupable_layouts": ("subtitle",),
        "max_scenes_per_span": 3,
        "max_span_sec": 40.0,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #
def test_contiguous_subtitles_merge_into_one_span():
    doc = _doc(_scene("scene-01"), _scene("scene-02"), _scene("scene-03"))
    res = validate_and_repair_visual_spans(doc, _cfg())
    assert len(res["spans"]) == 1
    assert res["spans"][0]["scene_ids"] == ["scene-01", "scene-02", "scene-03"]
    assert res["spans"][0]["planned_mode"] == "continuous_clip"


def test_max_scenes_cap_splits_span():
    doc = _doc(*[_scene(f"scene-{i:02d}") for i in range(1, 5)])  # 4 subtitle scenes
    res = validate_and_repair_visual_spans(doc, _cfg(max_scenes_per_span=3))
    assert [s["scene_ids"] for s in res["spans"]] == [
        ["scene-01", "scene-02", "scene-03"],
        ["scene-04"],
    ]
    assert res["metrics"]["cap_split_count"] == 1


def test_max_span_sec_cap_splits_span():
    # 3 x 18s = 54s > 40s cap → must split before exceeding.
    doc = _doc(_scene("scene-01", dur=18), _scene("scene-02", dur=18), _scene("scene-03", dur=18))
    res = validate_and_repair_visual_spans(doc, _cfg(max_span_sec=40.0))
    assert [s["scene_ids"] for s in res["spans"]] == [
        ["scene-01", "scene-02"],
        ["scene-03"],
    ]


def test_hook_cta_and_graphic_are_isolated():
    doc = _doc(
        _scene("scene-01", "hook"),
        _scene("scene-02", "subtitle"),
        _scene("scene-03", "subtitle"),
        _scene("scene-04", "checklist"),
        _scene("scene-05", "subtitle"),
        _scene("scene-06", "cta"),
    )
    res = validate_and_repair_visual_spans(doc, _cfg())
    groups = [s["scene_ids"] for s in res["spans"]]
    assert groups == [
        ["scene-01"],              # hook isolated
        ["scene-02", "scene-03"],  # subtitle run merged
        ["scene-04"],              # graphic isolated
        ["scene-05"],              # lone subtitle
        ["scene-06"],              # cta isolated
    ]


def test_graphic_span_tagged_graphic_image():
    doc = _doc(_scene("scene-01", "warning"))
    res = validate_and_repair_visual_spans(doc, _cfg())
    assert res["spans"][0]["planned_mode"] == "graphic_image"


def test_cta_tagged_graphic_image():
    # MASTER-PLAN G1 (canonical): checklist/warning/quote/cta -> ChatGPT image.
    doc = _doc(_scene("scene-01", "cta"))
    res = validate_and_repair_visual_spans(doc, _cfg())
    assert res["spans"][0]["planned_mode"] == "graphic_image"


def test_planner_hint_splits_into_separate_spans():
    doc = _doc(
        _scene("scene-01", visual_span_id="a"),
        _scene("scene-02", visual_span_id="a"),
        _scene("scene-03", visual_span_id="b"),
    )
    res = validate_and_repair_visual_spans(doc, _cfg())
    assert [s["scene_ids"] for s in res["spans"]] == [
        ["scene-01", "scene-02"],
        ["scene-03"],
    ]
    assert res["spans"][0]["source"] == "scene_planner"


# --------------------------------------------------------------------------- #
# Coverage / QA
# --------------------------------------------------------------------------- #
def test_coverage_complete_and_ordered():
    doc = _doc(*[_scene(f"scene-{i:02d}", "subtitle") for i in range(1, 9)])
    res = validate_and_repair_visual_spans(doc, _cfg())
    covered = [sid for s in res["spans"] for sid in s["scene_ids"]]
    assert covered == [f"scene-{i:02d}" for i in range(1, 9)]
    assert res["qa"]["verdict"] == "PASS"


def test_reduction_metric():
    # 3 subtitle scenes -> 1 continuous clip span -> reduction of 2 Pexels clips.
    doc = _doc(_scene("scene-01"), _scene("scene-02"), _scene("scene-03"))
    res = validate_and_repair_visual_spans(doc, _cfg())
    assert res["metrics"]["estimated_background_clip_reduction"] == 2


# --------------------------------------------------------------------------- #
# Build envelope + config
# --------------------------------------------------------------------------- #
def test_build_visual_spans_envelope_and_hash_stable():
    doc = _doc(_scene("scene-01"), _scene("scene-02"))
    a = build_visual_spans(doc, {}, job_id="job-x")
    b = build_visual_spans(doc, {}, job_id="job-x")
    assert a["schema_version"] == 1
    assert a["job_id"] == "job-x"
    assert a["generation_mode"] == "report_only"
    assert a["input_hash"] == b["input_hash"]  # deterministic


def test_resolve_config_reads_visual_namespace_and_clamps_mode():
    channel = {
        "visual": {
            "span_planning": {
                "mode": "bogus",
                "max_span_sec": 30,
                "max_scenes_per_span": 2,
            }
        }
    }
    cfg = resolve_visual_span_config(channel)
    assert cfg["mode"] == "report_only"  # invalid clamped
    assert cfg["max_span_sec"] == 30.0
    assert cfg["max_scenes_per_span"] == 2


def test_assign_span_ids_back_onto_scenes():
    doc = _doc(_scene("scene-01"), _scene("scene-02"), _scene("scene-03", "checklist"))
    res = validate_and_repair_visual_spans(doc, _cfg())
    assign_span_ids_to_scenes(doc, res)
    assert doc["scenes"][0]["visual_span_id"] == "vs01"
    assert doc["scenes"][1]["visual_span_id"] == "vs01"
    assert doc["scenes"][2]["visual_span_id"] == "vs02"


def test_enforced_mode_passes_on_clean_coverage():
    doc = _doc(_scene("scene-01"), _scene("scene-02"))
    res = validate_and_repair_visual_spans(doc, _cfg(mode="enforced"))
    assert res["qa"]["verdict"] == "PASS"


# --------------------------------------------------------------------------- #
# Invariant self-audit (gives `enforced` real teeth — regression guard)
# --------------------------------------------------------------------------- #
def test_verify_invariants_clean_grouping_has_no_errors():
    doc = _doc(_scene("scene-01"), _scene("scene-02"), _scene("scene-03", "checklist"))
    cfg = _cfg()
    res = validate_and_repair_visual_spans(doc, cfg)
    errs = verify_span_invariants(res["spans"], doc["scenes"], cfg)
    assert errs == []


def test_verify_invariants_flags_scene_cap_violation():
    scenes = [_scene(f"scene-{i:02d}") for i in range(1, 5)]  # 4 subtitle scenes
    bad_span = {
        "id": "vs01",
        "scene_ids": [s["id"] for s in scenes],  # 4 > max_scenes_per_span=3
        "start_scene_index": 0,
        "end_scene_index": 3,
        "planned_mode": "continuous_clip",
    }
    errs = verify_span_invariants([bad_span], scenes, _cfg(max_scenes_per_span=3))
    assert any("max_scenes" in e for e in errs)


def test_verify_invariants_flags_span_seconds_violation():
    scenes = [_scene("scene-01", dur=30), _scene("scene-02", dur=30)]  # 60 > 40
    bad_span = {
        "id": "vs01",
        "scene_ids": ["scene-01", "scene-02"],
        "start_scene_index": 0,
        "end_scene_index": 1,
        "planned_mode": "continuous_clip",
    }
    errs = verify_span_invariants([bad_span], scenes, _cfg(max_span_sec=40.0))
    assert any("max_span_sec" in e for e in errs)


def test_verify_invariants_flags_graphic_merged_with_subtitle():
    scenes = [_scene("scene-01", "subtitle"), _scene("scene-02", "checklist")]
    bad_span = {
        "id": "vs01",
        "scene_ids": ["scene-01", "scene-02"],  # graphic must be isolated
        "start_scene_index": 0,
        "end_scene_index": 1,
        "planned_mode": "continuous_clip",
    }
    errs = verify_span_invariants([bad_span], scenes, _cfg())
    assert any("isolation" in e or "graphic" in e for e in errs)


def test_verify_invariants_flags_non_contiguous_span():
    scenes = [_scene("scene-01"), _scene("scene-02"), _scene("scene-03")]
    bad_span = {
        "id": "vs01",
        "scene_ids": ["scene-01", "scene-03"],  # skips scene-02
        "start_scene_index": 0,
        "end_scene_index": 2,
        "planned_mode": "continuous_clip",
    }
    errs = verify_span_invariants([bad_span], scenes, _cfg())
    assert any("contiguous" in e for e in errs)
