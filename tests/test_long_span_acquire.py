"""Unit tests for per-span continuous-clip acquisition orchestration (pure).

The heavy provider/quality steps are injected, so these run without Pexels/SigLIP.
"""

from __future__ import annotations

from video_agent.visual import build_visual_spans
from video_agent.visual.span_acquire import (
    acquire_span_source_clips,
    build_span_acquisition_context,
)


def _scene(sid, layout="subtitle", dur=12.0, prompt="elderly woman stretching"):
    return {"id": sid, "layout": layout, "duration_sec": dur, "visual_prompt": prompt}


def _doc(*scenes):
    return {"job_id": "j1", "scenes": list(scenes)}


class _Budget:
    max_queries = 2
    metadata_candidates_per_query = 8
    max_unique_metadata_candidates = 12


def test_context_combines_member_prompts_and_duration():
    doc = _doc(_scene("s1", prompt="a"), _scene("s2", prompt="b"))
    spans = build_visual_spans(doc, {}, job_id="j1")
    span = spans["spans"][0]
    ctx = build_span_acquisition_context(span, {s["id"]: s for s in doc["scenes"]})
    assert ctx["scene_ids"] == ["s1", "s2"]
    assert "a" in ctx["visual_prompt"] and "b" in ctx["visual_prompt"]
    assert ctx["planned_duration_sec"] == 24.0
    assert ctx["locale"] == "es-ES"


def test_multi_scene_span_gets_one_clip_for_all_members():
    doc = _doc(_scene("s1"), _scene("s2"))  # one 2-scene subtitle span
    spans = build_visual_spans(doc, {}, job_id="j1")
    calls = {"cand": 0, "sel": 0}

    def candidate_fn(*, acquisition_context, budget):
        calls["cand"] += 1
        return [{"provider_asset_id": "px1"}]

    def select_and_download_fn(context, candidates):
        calls["sel"] += 1
        return {"path": "jobs/j1/assets/visual_spans/vs01.mp4", "duration_sec": 30.0}

    sc = acquire_span_source_clips(spans, doc, candidate_fn=candidate_fn,
                                   select_and_download_fn=select_and_download_fn, budget=_Budget())
    assert sc["s1"] == sc["s2"] == {"path": "jobs/j1/assets/visual_spans/vs01.mp4", "duration_sec": 30.0}
    assert calls == {"cand": 1, "sel": 1}


def test_fail_closed_when_nothing_passes_quality():
    doc = _doc(_scene("s1"), _scene("s2"))
    spans = build_visual_spans(doc, {}, job_id="j1")
    sc = acquire_span_source_clips(
        spans, doc,
        candidate_fn=lambda *, acquisition_context, budget: [{"provider_asset_id": "px1"}],
        select_and_download_fn=lambda c, cands: None,  # cascade rejected all
        budget=_Budget(),
    )
    assert sc == {}  # omitted -> schedule fails closed to per-scene


def test_no_candidates_omits_span():
    doc = _doc(_scene("s1"), _scene("s2"))
    spans = build_visual_spans(doc, {}, job_id="j1")
    sc = acquire_span_source_clips(
        spans, doc,
        candidate_fn=lambda *, acquisition_context, budget: [],
        select_and_download_fn=lambda c, cands: {"path": "x.mp4"},
        budget=_Budget(),
    )
    assert sc == {}


def test_single_scene_and_graphic_spans_are_skipped():
    doc = _doc(_scene("s1", "hook"), _scene("s2", "checklist"), _scene("s3", "cta"))
    spans = build_visual_spans(doc, {}, job_id="j1")
    seen = []

    def cand(*, acquisition_context, budget):
        seen.append(acquisition_context["span_id"])
        return [{"id": 1}]

    sc = acquire_span_source_clips(spans, doc, candidate_fn=cand,
                                   select_and_download_fn=lambda c, k: {"path": "x.mp4", "duration_sec": 9},
                                   budget=_Budget())
    # hook=single continuous, checklist+cta=graphic_image → none are multi-scene continuous
    assert sc == {}
    assert seen == []  # candidate search never even called
