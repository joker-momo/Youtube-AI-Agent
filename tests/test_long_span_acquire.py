"""Unit tests for per-span continuous-clip acquisition orchestration (pure).

The heavy provider/quality steps are injected, so these run without Pexels/SigLIP.
"""

from __future__ import annotations

from pathlib import Path

from video_agent.visual import build_visual_spans
from video_agent.visual.span_acquire import (
    acquire_span_source_clips,
    build_span_acquisition_context,
    build_span_select_and_download,
    mirror_clip_to_public,
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


# --------------------------------------------------------------------------- #
# Live adapter (fake service — reuses the cascade via get_scene_asset)
# --------------------------------------------------------------------------- #
class _FakeService:
    def __init__(self, status, source_path):
        self._status = status
        self._source = source_path

    def get_scene_asset(self, scene, channel_id, job_id):
        if self._status is None:
            return None
        return {
            "asset_selection": {"asset_match_status": self._status},
            "source_path": self._source,
            "source_duration_sec": 18.0,
        }


def test_adapter_accepts_strong_match_and_mirrors(tmp_path):
    src = tmp_path / "pexels_orig.mp4"
    src.write_bytes(b"video")
    public = tmp_path / "public"
    fn = build_span_select_and_download(
        _FakeService("strong_match", str(src)), channel_id="vida-plena-45",
        job_id="j1", public_root=public,
    )
    out = fn({"span_id": "vs06", "visual_prompt": "p", "planned_duration_sec": 24.0}, [])
    assert out["path"] == "jobs/j1/assets/visual_spans/vs06.mp4"
    assert out["duration_sec"] == 18.0
    assert (public / "jobs/j1/assets/visual_spans/vs06.mp4").exists()  # mirrored


def test_adapter_rejects_weak_match(tmp_path):
    src = tmp_path / "x.mp4"; src.write_bytes(b"v")
    fn = build_span_select_and_download(
        _FakeService("weak_match", str(src)), channel_id="c", job_id="j1", public_root=tmp_path / "pub",
    )
    assert fn({"span_id": "vs1", "visual_prompt": "p", "planned_duration_sec": 24.0}, []) is None


def test_adapter_rejects_no_asset(tmp_path):
    fn = build_span_select_and_download(
        _FakeService(None, None), channel_id="c", job_id="j1", public_root=tmp_path / "pub",
    )
    assert fn({"span_id": "vs1", "visual_prompt": "p", "planned_duration_sec": 24.0}, []) is None


def test_end_to_end_with_adapter_and_no_candidate_gate(tmp_path):
    src = tmp_path / "orig.mp4"; src.write_bytes(b"v"); public = tmp_path / "pub"
    doc = _doc(_scene("s1"), _scene("s2"))
    spans = build_visual_spans(doc, {}, job_id="j1")
    fn = build_span_select_and_download(
        _FakeService("strong_match", str(src)), channel_id="c", job_id="j1", public_root=public,
    )
    sc = acquire_span_source_clips(spans, doc, select_and_download_fn=fn)  # no candidate_fn
    assert sc["s1"]["path"] == sc["s2"]["path"] == "jobs/j1/assets/visual_spans/vs01.mp4"


def test_mirror_returns_render_relative_path(tmp_path):
    src = tmp_path / "a.mp4"; src.write_bytes(b"v")
    rel = mirror_clip_to_public(str(src), "vs03", job_id="job-z", public_root=tmp_path / "pub")
    assert rel == "jobs/job-z/assets/visual_spans/vs03.mp4"
    assert (tmp_path / "pub" / rel).exists()


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
