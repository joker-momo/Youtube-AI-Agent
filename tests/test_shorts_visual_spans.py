"""Visual-span validation/repair tests (spec v3.2.3 §10, §11, §11A, §41.1)."""
from __future__ import annotations

from typing import Any

from video_agent.shorts.visual_spans import (
    assign_span_ids_to_scenes,
    build_visual_spans,
    compute_span_input_hash,
    detect_structured_span_conflicts,
    resolve_visual_span_config,
    validate_and_repair_visual_spans,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _scene(
    sid: str,
    *,
    layout: str = "short_tip",
    duration: float = 3.0,
    span: str | None = None,
    intent: str = "",
    visual_type: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    sc: dict[str, Any] = {"id": sid, "layout": layout, "duration_sec": duration}
    if span is not None:
        sc["visual_span_id"] = span
    if intent:
        sc["visual_span_intent"] = intent
    if visual_type is not None:
        sc["visual_type"] = visual_type
    sc.update(extra)
    return sc


def _doc(*scenes: dict[str, Any]) -> dict[str, Any]:
    return {"short_id": "short-test", "scenes": list(scenes)}


CFG: dict[str, Any] = {}  # empty channel config → all defaults


def _span_scene_ids(result: dict[str, Any]) -> list[list[str]]:
    return [s["scene_ids"] for s in result["spans"]]


# --------------------------------------------------------------------------- #
# §41.1 cases
# --------------------------------------------------------------------------- #
def test_case01_one_scene_spans_when_no_hints() -> None:
    doc = _doc(_scene("s01"), _scene("s02"), _scene("s03"))
    result = build_visual_spans(doc, CFG)
    assert _span_scene_ids(result) == [["s01"], ["s02"], ["s03"]]
    assert result["qa"]["verdict"] == "PASS"
    assert result["metrics"]["visual_span_count"] == 3


def test_case02_valid_multi_scene_span() -> None:
    doc = _doc(
        _scene("s01", layout="short_hook", span="vs01"),
        _scene("s02", span="vs02", intent="mature adult walking"),
        _scene("s03", span="vs02"),
    )
    result = build_visual_spans(doc, CFG)
    assert _span_scene_ids(result) == [["s01"], ["s02", "s03"]]
    vs = result["spans"][1]
    assert vs["planned_mode"] == "continuous_clip"
    assert vs["visual_intent"] == "mature adult walking"
    assert vs["source"] == "scene_planner"
    assert result["metrics"]["estimated_asset_call_reduction"] == 1


def test_case03_non_contiguous_ids_split() -> None:
    doc = _doc(
        _scene("s01", span="vsA"),
        _scene("s02", span="vsB"),
        _scene("s03", span="vsA"),
    )
    result = build_visual_spans(doc, CFG)
    # vsA reused non-contiguously → three distinct spans, fresh sequential ids.
    assert _span_scene_ids(result) == [["s01"], ["s02"], ["s03"]]
    assert [s["id"] for s in result["spans"]] == ["vs01", "vs02", "vs03"]


def test_case04_overlap_repaired() -> None:
    doc = _doc(_scene("s01"), _scene("s02"), _scene("s03"))
    proposed = {
        "spans": [
            {"id": "vs01", "scene_ids": ["s01", "s02"]},
            {"id": "vs02", "scene_ids": ["s02", "s03"]},  # s02 overlaps
        ]
    }
    result = validate_and_repair_visual_spans(doc, proposed, resolve_visual_span_config(CFG))
    # first-claim wins: s02 stays with vs01; vs02 keeps only s03.
    assert _span_scene_ids(result) == [["s01", "s02"], ["s03"]]
    covered = [sid for span in result["spans"] for sid in span["scene_ids"]]
    assert covered == ["s01", "s02", "s03"]  # complete, no duplicates


def test_case05_missing_scene_receives_implicit_span() -> None:
    doc = _doc(_scene("s01"), _scene("s02"), _scene("s03"))
    proposed = {"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]}  # s03 uncovered
    result = validate_and_repair_visual_spans(doc, proposed, resolve_visual_span_config(CFG))
    assert _span_scene_ids(result) == [["s01", "s02"], ["s03"]]
    assert result["spans"][1]["source"] == "implicit"


def test_case06_duplicate_id_repaired() -> None:
    doc = _doc(_scene("s01"), _scene("s02"), _scene("s03"), _scene("s04"))
    proposed = {
        "spans": [
            {"id": "vs01", "scene_ids": ["s01", "s02"]},
            {"id": "vs01", "scene_ids": ["s03", "s04"]},  # duplicate id
        ]
    }
    result = validate_and_repair_visual_spans(doc, proposed, resolve_visual_span_config(CFG))
    ids = [s["id"] for s in result["spans"]]
    assert ids == ["vs01", "vs02"]  # deterministically renamed
    assert len(set(ids)) == len(ids)


def test_case07_graphic_isolation() -> None:
    doc = _doc(
        _scene("s01", span="vs01"),
        _scene("s02", layout="graphic_definition", span="vs01"),  # planner grouped graphic
        _scene("s03", span="vs01"),
    )
    result = build_visual_spans(doc, CFG)
    # graphic scene split out; output stays fully covered and contiguous.
    assert _span_scene_ids(result) == [["s01"], ["s02"], ["s03"]]
    graphic_span = result["spans"][1]
    assert graphic_span["planned_mode"] == "graphic_led"
    assert result["metrics"]["graphic_span_count"] == 1
    assert result["metrics"]["repaired_span_count"] >= 1


def test_case08_cta_isolation() -> None:
    doc = _doc(
        _scene("s01", span="vs01"),
        _scene("s02", span="vs01"),
        _scene("s03", layout="short_cta", duration=2.4, span="vs01"),
    )
    result = build_visual_spans(doc, CFG)
    assert ["s03"] in _span_scene_ids(result)
    assert _span_scene_ids(result) == [["s01", "s02"], ["s03"]]


def test_case09_hook_isolation() -> None:
    doc = _doc(
        _scene("s01", layout="short_hook", span="vs01"),
        _scene("s02", span="vs01"),  # planner wrongly grouped hook with s02
        _scene("s03", span="vs01"),
    )
    result = build_visual_spans(doc, CFG)
    assert result["spans"][0]["scene_ids"] == ["s01"]
    assert result["spans"][0]["planning_reason"] == "hook isolated"


def test_case10_legacy_document_all_implicit() -> None:
    doc = _doc(_scene("s01"), _scene("s02"))  # no visual_span_id at all
    result = build_visual_spans(doc, CFG)
    assert _span_scene_ids(result) == [["s01"], ["s02"]]
    assert all(s["source"] == "implicit" for s in result["spans"])
    assert result["qa"]["verdict"] == "PASS"


def test_case11_structured_conflict_splits_nl_only_warns() -> None:
    # Hard structured conflict: required vs forbidden evidence intersect.
    hard = _doc(
        _scene("s01", span="vs01", required_evidence_tags=["dumbbell"]),
        _scene("s02", span="vs01", forbidden_evidence_tags=["dumbbell"]),
    )
    result = build_visual_spans(hard, CFG)
    assert _span_scene_ids(result) == [["s01"], ["s02"]]
    assert any("structured_conflict" in w for w in result["qa"]["warnings"])

    # Natural-language-only "contradiction" → no hard conflict, stays grouped.
    nl = _doc(
        _scene("s01", span="vs01", intent="show a calm bedroom"),
        _scene("s02", span="vs01", intent="absolutely no bedroom, only outdoors"),
    )
    nl_result = build_visual_spans(nl, CFG)
    assert _span_scene_ids(nl_result) == [["s01", "s02"]]
    assert nl_result["qa"]["verdict"] == "PASS"


def test_case12_cache_reuse_same_hash() -> None:
    doc = _doc(_scene("s01", span="vs01"), _scene("s02", span="vs01"))
    cfg = resolve_visual_span_config(CFG)
    assert compute_span_input_hash(doc, cfg) == compute_span_input_hash(doc, cfg)


def test_case13_hash_invalidation_on_change() -> None:
    cfg = resolve_visual_span_config(CFG)
    base = _doc(_scene("s01", span="vs01"), _scene("s02", span="vs01"))
    changed_layout = _doc(_scene("s01", span="vs01", layout="short_hook"), _scene("s02", span="vs01"))
    changed_group = _doc(_scene("s01", span="vs01"), _scene("s02", span="vs02"))
    h = compute_span_input_hash(base, cfg)
    assert compute_span_input_hash(changed_layout, cfg) != h
    assert compute_span_input_hash(changed_group, cfg) != h
    # Duration-only change does NOT invalidate the grouping hash (§40.1).
    dur_only = _doc(_scene("s01", span="vs01", duration=9.9), _scene("s02", span="vs01"))
    assert compute_span_input_hash(dur_only, cfg) == h


def test_case14_required_forbidden_intersection_splits() -> None:
    conflict = detect_structured_span_conflicts(
        [
            {"required_subject_tags": ["elderly woman"]},
            {"forbidden_subject_tags": ["elderly woman"]},
        ]
    )
    assert conflict["hard_conflicts"]
    assert conflict["hard_conflicts"][0]["type"] == "subject_tag_intersection"


def test_case15_missing_structured_fields_no_false_conflict() -> None:
    conflict = detect_structured_span_conflicts([{"id": "s01"}, {"id": "s02"}])
    assert conflict["hard_conflicts"] == []


def test_case16_free_form_ambiguity_never_hard_fails() -> None:
    conflict = detect_structured_span_conflicts(
        [
            {"visual_span_intent": "indoor only, never outdoors"},
            {"visual_span_intent": "outdoor walking in a park"},
        ]
    )
    assert conflict["hard_conflicts"] == []
    assert conflict["unresolved_semantic_warnings"] == []


# --------------------------------------------------------------------------- #
# additional invariants
# --------------------------------------------------------------------------- #
def test_max_scenes_per_span_splits() -> None:
    doc = _doc(*[_scene(f"s0{i}", span="vs01", duration=1.0) for i in range(1, 6)])
    result = build_visual_spans(doc, CFG)  # default max 3
    sizes = [len(s["scene_ids"]) for s in result["spans"]]
    assert max(sizes) <= 3
    assert sum(sizes) == 5


def test_max_span_sec_splits() -> None:
    # default max_span_sec=10.0; three 4s scenes (12s) cannot share one span.
    doc = _doc(
        _scene("s01", span="vs01", duration=4.0),
        _scene("s02", span="vs01", duration=4.0),
        _scene("s03", span="vs01", duration=4.0),
    )
    result = build_visual_spans(doc, CFG)
    assert _span_scene_ids(result) == [["s01", "s02"], ["s03"]]
    assert any("max_span_sec" in w for w in result["qa"]["warnings"])


def test_mode_constraint_conflict() -> None:
    conflict = detect_structured_span_conflicts(
        [{"graphic_only": True}, {"native_video_only": True}]
    )
    assert conflict["hard_conflicts"]
    assert conflict["hard_conflicts"][0]["type"] == "mode_constraint"


def test_assign_span_ids_to_scenes() -> None:
    doc = _doc(_scene("s01", span="vs99"), _scene("s02", span="vs99"))
    result = build_visual_spans(doc, CFG)
    assign_span_ids_to_scenes(doc, result)
    # repaired (authoritative) id, not the raw planner id.
    assert doc["scenes"][0]["visual_span_id"] == "vs01"
    assert doc["scenes"][1]["visual_span_id"] == "vs01"


def test_complete_coverage_in_order_always() -> None:
    doc = _doc(*[_scene(f"s0{i}", span="vs01" if i % 2 else "vs02") for i in range(1, 7)])
    result = build_visual_spans(doc, CFG)
    covered = [sid for s in result["spans"] for sid in s["scene_ids"]]
    assert covered == ["s01", "s02", "s03", "s04", "s05", "s06"]


def test_build_includes_mode_and_hash() -> None:
    doc = _doc(_scene("s01"))
    result = build_visual_spans(doc, CFG, short_id="short-04")
    assert result["short_id"] == "short-04"
    assert result["generation_mode"] == "report_only"
    assert len(result["input_hash"]) == 64  # sha256 hex


def test_config_defaults_and_override() -> None:
    assert resolve_visual_span_config({})["mode"] == "report_only"
    cfg = resolve_visual_span_config(
        {"shorts": {"visual_timeline": {"mode": "enforced", "span_planning": {"max_scenes_per_span": 2}}}}
    )
    assert cfg["mode"] == "enforced"
    assert cfg["max_scenes_per_span"] == 2
    # invalid mode clamps to report_only.
    assert resolve_visual_span_config({"shorts": {"visual_timeline": {"mode": "bogus"}}})["mode"] == "report_only"
