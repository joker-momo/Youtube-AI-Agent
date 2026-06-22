"""Local semantic vision cascade tests (spec v4.0.3 §12A, §38).

Uses fake adapters — never loads real models / downloads weights — so it is fully
deterministic and CI-safe. Real-model paths are exercised only when the operator
enables an adapter with weights present (out of scope for unit tests).
"""
from __future__ import annotations

from typing import Any

from video_agent.shorts import visual_semantic as vs
from video_agent.shorts.builder.stages.visual_local_qa import (
    _placeholder_records,
    _qa_verdict,
    _semantic_records,
)


class _FakeAdapter:
    def __init__(self, name: str, records: list[dict[str, Any]]) -> None:
        self.name = name
        self._records = records
        self.calls = 0

    def available(self) -> bool:
        return True

    def evaluate(self, images, **kw) -> list[dict[str, Any]]:
        self.calls += 1
        return self._records


def _ev(req: str, status: str, *, confidence: float | None = None) -> dict[str, Any]:
    return {"requirement": req, "status": status, "capability_source": "optional_semantic_model",
            "model": "fake", "model_version": "fake", "asset_id": "c1", "confidence": confidence, "reason": "x"}


# --------------------------------------------------------------------------- #
# factory + config
# --------------------------------------------------------------------------- #
def test_factory_none_returns_none_baseline_safe() -> None:
    assert vs.build_semantic_analyzer({"semantic_adapter": "none"}) is None
    assert vs.build_semantic_analyzer({}) is None


def test_factory_tiers() -> None:
    clip = vs.build_semantic_analyzer({"semantic_adapter": "clip"})
    assert [a.name for a in clip.adapters] == ["siglip"]
    clip_vlm = vs.build_semantic_analyzer({"semantic_adapter": "clip_vlm"})
    assert [a.name for a in clip_vlm.adapters] == ["siglip", "vlm"]
    full = vs.build_semantic_analyzer({"semantic_adapter": "full"})
    assert [a.name for a in full.adapters] == ["siglip", "vlm", "detector"]


def test_detector_adapter_flag_forces_detector() -> None:
    a = vs.build_semantic_analyzer({"semantic_adapter": "clip", "detector_adapter": "grounding_dino"})
    assert "detector" in [ad.name for ad in a.adapters]


def test_resolve_config_models_and_thresholds() -> None:
    cfg = vs.resolve_semantic_config({
        "semantic_adapter": "full",
        "semantic_models": {"vlm": "custom/vlm"},
        "semantic_thresholds": {"detector_box": 0.5, "siglip_reject": 0.05},
    })
    assert cfg.enabled and cfg.use_siglip and cfg.use_vlm and cfg.use_detector
    assert cfg.vlm_model == "custom/vlm"
    assert cfg.detector_box_threshold == 0.5
    assert cfg.siglip_reject_margin == 0.05


# --------------------------------------------------------------------------- #
# cascade behavior (fake adapters)
# --------------------------------------------------------------------------- #
def _cascade(*adapters) -> vs.CascadeSemanticAnalyzer:
    return vs.CascadeSemanticAnalyzer(cfg=vs.SemanticConfig(enabled=True), adapters=list(adapters))


def test_cascade_no_file_is_capability_unavailable() -> None:
    casc = _cascade(_FakeAdapter("siglip", [_ev("topic:visual_intent", "SUPPORTED")]))
    recs = casc.analyze_span(video_path=None, duration_sec=5.0, required_tags={}, forbidden_tags={},
                             visual_intent="x", asset_id="c1")
    assert recs[0]["status"] == "CAPABILITY_UNAVAILABLE"


def test_cascade_topic_contradicted_skips_vlm(monkeypatch, tmp_path) -> None:
    # avoid real ffmpeg: stub extract_frames to return a dummy non-empty list
    monkeypatch.setattr(vs, "extract_frames", lambda *a, **k: ["img"])
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    siglip = _FakeAdapter("siglip", [_ev("topic:visual_intent", "CONTRADICTED")])
    vlm = _FakeAdapter("vlm", [_ev("scene:brand_intent", "SUPPORTED")])
    detector = _FakeAdapter("detector", [_ev("forbidden_evidence:dog", "CONFIRMED_PRESENT")])
    casc = _cascade(siglip, vlm, detector)
    recs = casc.analyze_span(video_path=str(f), duration_sec=5.0, required_tags={}, forbidden_tags={},
                             visual_intent="x", asset_id="c1")
    assert vlm.calls == 0, "VLM must be skipped after cheap topic contradiction (cost gate §14/§38)"
    assert detector.calls == 1, "detector still runs for forbidden grounding"
    assert any(r["status"] == "CONTRADICTED" for r in recs)


def test_cascade_runs_vlm_when_topic_ok(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(vs, "extract_frames", lambda *a, **k: ["img"])
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    vlm = _FakeAdapter("vlm", [_ev("scene:brand_intent", "SUPPORTED")])
    casc = _cascade(_FakeAdapter("siglip", [_ev("topic:visual_intent", "SUPPORTED")]), vlm)
    casc.analyze_span(video_path=str(f), duration_sec=5.0, required_tags={}, forbidden_tags={},
                      visual_intent="x", asset_id="c1")
    assert vlm.calls == 1


def test_default_timestamps_first_frame_biased() -> None:
    ts = vs.default_timestamps(10.0, 4)
    assert ts[0] == 0.0
    assert any(t <= 0.5 for t in ts)  # samples the first half-second
    assert all(0 <= t <= 10.0 for t in ts)
    assert len(ts) <= 4


def test_default_timestamps_three_frames_cover_start_mid_end() -> None:
    ts = vs.default_timestamps(10.0, 3)
    assert ts[0] == 0.0  # trim start
    assert any(4.5 <= t <= 5.5 for t in ts)  # midpoint
    assert ts[-1] >= 9.5  # trim end (action must persist to the end)
    assert len(ts) == 3


# --------------------------------------------------------------------------- #
# stage verdict + records integration
# --------------------------------------------------------------------------- #
def test_qa_verdict_subject_contradicted_is_fail() -> None:
    assert _qa_verdict([_ev("required_subject:adult_45_plus", "CONTRADICTED")], "PASS") == "FAIL"


def test_qa_verdict_topic_contradicted_is_fail() -> None:
    # SigLIP off-topic rejection (dog / feet-POV) is a hard gate.
    assert _qa_verdict([_ev("topic:visual_intent", "CONTRADICTED")], "PASS") == "FAIL"


def test_qa_verdict_advisory_contradiction_passes() -> None:
    # action / environment / brand contradictions are advisory — a snowy-vs-sunny
    # detail must NOT reject an otherwise on-subject clip (over-rejection fix).
    recs = [
        _ev("required_subject:age_band_45_plus", "SUPPORTED"),
        _ev("required_environment:intended_environment", "CONTRADICTED"),
        _ev("scene:brand_intent", "CONTRADICTED"),
    ]
    assert _qa_verdict(recs, "PASS") == "PASS"


def test_qa_verdict_action_contradicted_is_advisory_when_not_critical() -> None:
    # On a normal span a contradicted action must not reject on-subject footage.
    assert _qa_verdict([_ev("required_action:sit_down", "CONTRADICTED")], "PASS") == "PASS"


def test_qa_verdict_action_contradicted_is_fail_when_critical() -> None:
    # On a CRITICAL span the action IS the point (feet on floor / breathe / write):
    # topic-matching cooking footage that doesn't show it is wrong footage there.
    assert (
        _qa_verdict([_ev("required_action:feet_on_floor", "CONTRADICTED")], "PASS", critical=True)
        == "FAIL"
    )


def test_qa_verdict_forbidden_present_is_fail() -> None:
    assert _qa_verdict([_ev("forbidden_evidence:dog", "CONFIRMED_PRESENT")], "PASS") == "FAIL"


def test_qa_verdict_all_supported_is_pass() -> None:
    assert _qa_verdict([_ev("required_subject:adult_45_plus", "SUPPORTED", confidence=2.0)], "PASS") == "PASS"


def test_qa_verdict_weak_required_semantic_support_is_capability_reduced() -> None:
    # A model that ran but only weakly supports the visual intent is not enough
    # evidence to publish a critical span in enforced mode.
    recs = [
        _ev("topic:visual_intent", "SUPPORTED", confidence=0.18),
        _ev("required_subject:age_band_45_plus", "SUPPORTED", confidence=2.4),
    ]
    assert _qa_verdict(recs, "PASS") == "CAPABILITY_REDUCED"


def test_qa_verdict_unknown_is_capability_reduced() -> None:
    assert _qa_verdict([_ev("required_subject:x", "CAPABILITY_UNAVAILABLE")], "PASS") == "CAPABILITY_REDUCED"


def test_qa_verdict_candidate_fail_short_circuits() -> None:
    assert _qa_verdict([_ev("required_subject:x", "SUPPORTED")], "FAIL") == "FAIL"


def test_semantic_records_baseline_placeholder_when_no_analyzer() -> None:
    span = {"required_subject_tags": ["adult_45_plus"], "forbidden_evidence_tags": ["visible_injury"]}
    recs = _semantic_records(span, candidate_id="c1", local_qa={"semantic_adapter": "none"})
    assert {r["status"] for r in recs} <= {"CAPABILITY_UNAVAILABLE", "UNKNOWN"}
    assert all(r["status"] != "PASS" for r in recs)


def test_semantic_records_uses_analyzer_when_present(monkeypatch) -> None:
    class _A:
        def analyze_span(self, **kw):
            return [_ev("required_subject:adult_45_plus", "SUPPORTED")]

    span = {"required_subject_tags": ["adult_45_plus"]}
    recs = _semantic_records(span, candidate_id="c1", local_qa={"semantic_adapter": "clip"},
                             semantic_analyzer=_A(), video_path="/tmp/x.mp4", duration_sec=5.0)
    assert recs == [_ev("required_subject:adult_45_plus", "SUPPORTED")]


def test_semantic_records_analyzer_error_degrades_to_placeholder() -> None:
    class _Broken:
        def analyze_span(self, **kw):
            raise RuntimeError("boom")

    span = {"required_subject_tags": ["adult_45_plus"]}
    recs = _semantic_records(span, candidate_id="c1", local_qa={"semantic_adapter": "clip"},
                             semantic_analyzer=_Broken(), video_path="/tmp/x.mp4", duration_sec=5.0)
    assert any(r["status"] == "CAPABILITY_UNAVAILABLE" for r in recs)  # never crashes


def test_placeholder_records_never_pass() -> None:
    span = {"required_action_tags": ["gentle_walking"], "forbidden_evidence_tags": ["dog"]}
    for r in _placeholder_records(span, "c1"):
        assert r["status"] in ("CAPABILITY_UNAVAILABLE", "UNKNOWN")
