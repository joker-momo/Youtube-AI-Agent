"""Long-form semantic source-window selection (spec 2026-09-24).

Pure selector tests: the sampler, semantic evaluator and clock are injected, so
no ffmpeg decode or SigLIP weights are touched here.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

import pytest

from video_agent.visual.source_window_selection import (
    LEGACY_SCENE_XFADE_FRAMES,
    SCENE_TRIM_KEYS,
    FrameSample,
    LongSourceWindowSelectionError,
    SourceWindowPolicy,
    SourceWindowPolicyError,
    apply_selection_to_scene,
    build_selection_report,
    candidate_window_starts,
    parse_source_window_policy,
    raise_for_rejections,
    required_window_frames,
    select_source_window,
    skipped_item,
)

FPS = 30
SPEC_POLICY = {
    "enabled": True,
    "min_surplus_sec": 6.0,
    "max_candidate_windows": 12,
    "technical_samples_per_window": 3,
    "semantic_top_k": 3,
    "semantic_samples_per_window": 3,
    "min_score": 70.0,
    "weights": {"semantic": 0.60, "technical": 0.20, "motion": 0.10, "continuity": 0.10},
    "semantic": {
        "adapter": "clip",
        "device": "auto",
        "model": "google/siglip2-base-patch16-224",
        "enforce_age_band_45_plus": True,
    },
}


def policy(**overrides: Any) -> SourceWindowPolicy:
    raw = json.loads(json.dumps(SPEC_POLICY))
    raw.update(overrides)
    parsed = parse_source_window_policy(raw)
    assert parsed is not None
    return parsed


class FakeSampler:
    """Deterministic frame sampler: ``metrics(frame) -> (luma, sharpness, motion)``."""

    def __init__(self, metrics: Callable[[int], tuple[float, float, float | None]] | None = None,
                 *, fail_frames: set[int] | None = None) -> None:
        self.metrics = metrics or (lambda frame: (120.0, 50.0, 10.0))
        self.fail_frames = fail_frames or set()
        self.calls: list[list[int]] = []

    def __call__(self, frames: list[int]) -> list[FrameSample]:
        self.calls.append(list(frames))
        out: list[FrameSample] = []
        for frame in frames:
            if frame in self.fail_frames:
                out.append(FrameSample(frame_in_frames=frame, decoded=False))
                continue
            luma, sharp, motion = self.metrics(frame)
            out.append(FrameSample(frame_in_frames=frame, decoded=True, mean_luma=luma,
                                   sharpness=sharp, motion=motion, image=("img", frame)))
        return out

    @property
    def sampled_frame_count(self) -> int:
        return sum(len(c) for c in self.calls)


def record(requirement: str, status: str, confidence: float | None = 2.0) -> dict[str, Any]:
    return {"requirement": requirement, "status": status, "confidence": confidence}


class FakeEvaluator:
    """``per_frame(frame) -> list of evidence records`` for each sampled image."""

    def __init__(self, per_frame: Callable[[int], list[dict[str, Any]]] | None = None) -> None:
        self.per_frame = per_frame or (lambda frame: [record("topic:visual_intent", "SUPPORTED")])
        self.calls: list[list[int]] = []

    def __call__(self, images: list[Any]) -> list[list[dict[str, Any]]]:
        frames = [img[1] for img in images]
        self.calls.append(frames)
        return [self.per_frame(f) for f in frames]

    @property
    def evaluated_window_count(self) -> int:
        return len(self.calls)


def fake_clock() -> Callable[[], float]:
    ticks = iter(float(i) * 0.001 for i in range(10_000))
    return lambda: next(ticks)


def run(
    *,
    source_sec: float = 60.0,
    scene_sec: float = 10.0,
    sampler: FakeSampler | None = None,
    evaluator: FakeEvaluator | None = None,
    pol: SourceWindowPolicy | None = None,
    crop: float = 1.0,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    return select_source_window(
        scene_id="scene-07",
        asset_ref="jobs/example/assets/scene-07.mp4",
        source_duration_sec=source_sec,
        scene_duration_sec=scene_sec,
        fps=FPS,
        crop_retained_fraction=crop,
        policy=pol or policy(),
        sampler=sampler or FakeSampler(),
        semantic_evaluator=evaluator or FakeEvaluator(),
        clock=clock or fake_clock(),
    )


# --------------------------------------------------------------------------- #
# Frame math and candidate construction
# --------------------------------------------------------------------------- #
def test_required_window_covers_scene_frames_plus_legacy_crossfade_lead() -> None:
    # ChannelVideo mounts each scene SCENE_XFADE frames early, so the clip plays
    # round(duration*fps) + 15 frames; the window must contain real footage for all.
    assert LEGACY_SCENE_XFADE_FRAMES == 15
    assert required_window_frames(15.0, 30) == 450 + 15
    assert required_window_frames(2.5, 30) == 75 + 15
    # JS Math.round parity (floor(x + 0.5)), not banker's rounding.
    assert required_window_frames(0.05, 30) == 2 + 15


def test_candidate_starts_are_evenly_distributed_with_endpoints_and_capped() -> None:
    starts = candidate_window_starts(max_start=8500, count=12)
    assert len(starts) == 12
    assert starts[0] == 0 and starts[-1] == 8500
    assert starts == sorted(set(starts))
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert max(gaps) - min(gaps) <= 1
    assert candidate_window_starts(max_start=5, count=12) == [0, 1, 2, 3, 4, 5]
    assert candidate_window_starts(max_start=0, count=12) == [0]


def test_crossfade_constant_matches_channel_video() -> None:
    from pathlib import Path

    tsx = (Path(__file__).resolve().parents[1] / "remotion/src/ChannelVideo.tsx").read_text()
    assert f"const SCENE_XFADE = {LEGACY_SCENE_XFADE_FRAMES};" in tsx


# --------------------------------------------------------------------------- #
# Selection semantics
# --------------------------------------------------------------------------- #
def test_semantic_supported_window_beats_sharper_contradicted_window() -> None:
    # Early windows are much sharper but off-topic; later windows are on-topic.
    sampler = FakeSampler(lambda f: (120.0, 400.0 if f < 900 else 60.0, 10.0))
    evaluator = FakeEvaluator(
        lambda f: [record("topic:visual_intent", "CONTRADICTED" if f < 900 else "SUPPORTED",
                          -3.0 if f < 900 else 2.0)]
    )
    item = run(source_sec=60.0, scene_sec=10.0, sampler=sampler, evaluator=evaluator,
               pol=policy(semantic_top_k=12))

    assert item["status"] == "selected"
    assert item["selected_window_start_in_frames"] >= 900
    assert item["rejected_window_counts"].get("semantic_contradicted", 0) >= 1
    assert item["semantic_status"] == "supported"


def test_candidate_and_semantic_work_are_capped_for_five_minute_source() -> None:
    sampler = FakeSampler()
    evaluator = FakeEvaluator()
    item = run(source_sec=300.0, scene_sec=15.0, sampler=sampler, evaluator=evaluator)

    assert item["technical_candidate_count"] == 12
    assert item["semantic_candidate_count"] == 3
    assert sampler.sampled_frame_count == 12 * 3  # semantic frames reuse technical samples
    assert evaluator.evaluated_window_count == 3
    assert all(len(frames) == 3 for frames in evaluator.calls)
    assert len(item["candidates"]) == 12


def test_black_lead_selects_later_interval_with_consistent_bounds() -> None:
    sampler = FakeSampler(lambda f: (0.0 if f < 90 else 120.0, 50.0, 10.0))
    item = run(source_sec=30.0, scene_sec=10.0, sampler=sampler)

    assert item["status"] == "selected"
    start = item["selected_window_start_in_frames"]
    end = item["selected_window_end_in_frames"]
    assert start >= 90
    assert end - start == item["required_duration_in_frames"] == 315
    assert item["trim_timebase_fps"] == FPS
    assert item["rejected_window_counts"]["black_or_fade"] >= 1


def test_one_relevant_frame_cannot_carry_an_off_topic_window() -> None:
    def per_frame(frame: int) -> list[dict[str, Any]]:
        # Only each window's first sample is on topic.
        first_sample = frame in first_frames
        return [record("topic:visual_intent", "SUPPORTED" if first_sample else "CONTRADICTED",
                       3.0 if first_sample else -2.0)]

    first_frames = set(candidate_window_starts(max_start=1800 - 315 - 8, count=12))
    item = run(source_sec=60.0, scene_sec=10.0, evaluator=FakeEvaluator(per_frame))

    assert item["status"] == "rejected"
    assert item["rejected_window_counts"]["semantic_contradicted"] == 3
    evaluated = [c for c in item["candidates"] if c.get("semantic_status")]
    assert evaluated and all(math.isclose(c["continuity"], 100.0 / 3.0, abs_tol=0.01) for c in evaluated)
    assert "selected_window_start_in_frames" not in item


def test_insufficient_headroom_is_an_explicit_skip_without_trim_fields() -> None:
    sampler = FakeSampler()
    evaluator = FakeEvaluator()
    item = run(source_sec=15.0, scene_sec=10.0, sampler=sampler, evaluator=evaluator)

    assert item["status"] == "skipped"
    assert item["reason"] == "insufficient_headroom"
    assert item["source_duration_sec"] == 15.0
    assert not any(k.startswith("selected_window") for k in item)
    assert sampler.calls == [] and evaluator.calls == []
    refs = {"background": "jobs/x/assets/scene-07.mp4", "source_trim_before_in_frames": 99}
    apply_selection_to_scene(refs, item)
    assert not any(k in refs for k in SCENE_TRIM_KEYS)


def _unavailable(frame: int) -> list[dict[str, Any]]:
    return [record("topic:visual_intent", "CAPABILITY_UNAVAILABLE", None)]


def _unknown(frame: int) -> list[dict[str, Any]]:
    return [record("topic:visual_intent", "UNKNOWN", -0.4)]


def _forbidden(frame: int) -> list[dict[str, Any]]:
    return [record("topic:visual_intent", "SUPPORTED"), record("forbidden_evidence:dog", "CONFIRMED_PRESENT", 0.9)]


def _age(frame: int) -> list[dict[str, Any]]:
    return [record("topic:visual_intent", "SUPPORTED"),
            record("required_subject:age_band_45_plus", "CONTRADICTED", -1.2)]


def _weak(frame: int) -> list[dict[str, Any]]:
    return [record("topic:visual_intent", "SUPPORTED", 0.0)]


@pytest.mark.parametrize(
    ("label", "sampler", "evaluator", "reason"),
    [
        ("semantic unavailable", FakeSampler(), FakeEvaluator(_unavailable), "semantic_capability_unavailable"),
        ("semantic unknown", FakeSampler(), FakeEvaluator(_unknown), "semantic_unknown"),
        ("forbidden object", FakeSampler(), FakeEvaluator(_forbidden), "semantic_forbidden_present"),
        ("age contradiction", FakeSampler(), FakeEvaluator(_age), "semantic_age_contradicted"),
        ("all black", FakeSampler(lambda f: (2.0, 0.0, 0.0)), FakeEvaluator(), "black_or_fade"),
        ("decode failure", FakeSampler(fail_frames=set(range(0, 20_000))), FakeEvaluator(), "decode_failure"),
        ("no score >= 70", FakeSampler(lambda f: (120.0, 0.0, 1.0)), FakeEvaluator(_weak), "below_min_score"),
    ],
)
def test_eligible_source_without_acceptable_window_fails_closed(label, sampler, evaluator, reason) -> None:
    item = run(source_sec=60.0, scene_sec=10.0, sampler=sampler, evaluator=evaluator)

    assert item["status"] == "rejected", label
    assert item["reason"] == "no_passing_window"
    assert item["rejected_window_counts"].get(reason, 0) >= 1, item["rejected_window_counts"]
    report = build_selection_report([item], fps=FPS)
    with pytest.raises(LongSourceWindowSelectionError, match="scene-07") as excinfo:
        raise_for_rejections(report)
    assert excinfo.value.scene_id == "scene-07"
    assert excinfo.value.asset_ref == "jobs/example/assets/scene-07.mp4"
    assert excinfo.value.rejected_window_counts == item["rejected_window_counts"]


def test_unstable_motion_is_rejected() -> None:
    item = run(sampler=FakeSampler(lambda f: (120.0, 50.0, 80.0)))
    assert item["status"] == "rejected"
    assert item["rejected_window_counts"]["unstable_motion"] == 12


def test_impossible_center_cover_crop_is_rejected() -> None:
    item = run(crop=0.0)
    assert item["status"] == "rejected"
    assert item["rejected_window_counts"]["crop_impossible"] == 12


def test_score_contract_matches_spec_formula() -> None:
    # Uniform windows: sharpness_norm 1, crop 1, no fade, normal motion,
    # numeric semantic confidence 2.0 on every frame.
    item = run(sampler=FakeSampler(lambda f: (120.0, 50.0, 10.0)))
    score = item["score"]
    semantic = 100.0 / (1.0 + math.exp(-2.0))
    assert score["technical"] == pytest.approx(100.0)
    assert score["motion"] == pytest.approx(100.0)
    assert score["continuity"] == pytest.approx(100.0)
    assert score["semantic"] == pytest.approx(round(semantic, 3))
    expected = 0.60 * semantic + 0.20 * 100 + 0.10 * 100 + 0.10 * 100
    assert score["total"] == pytest.approx(round(expected, 3))


def test_semantic_score_weights_worst_frame() -> None:
    confidences = {0: 3.0, 1: 3.0, 2: 0.2}

    def per_frame(frame: int) -> list[dict[str, Any]]:
        idx = sample_index[frame]
        return [record("topic:visual_intent", "SUPPORTED", confidences[idx])]

    sample_index: dict[int, int] = {}
    starts = candidate_window_starts(max_start=1800 - 315 - 8, count=12)
    for start in starts:
        for i, f in enumerate((start, start + 157, start + 314)):
            sample_index[f] = i
    item = run(evaluator=FakeEvaluator(per_frame), pol=policy(min_score=0.0))

    frame_scores = [100.0 / (1.0 + math.exp(-c)) for c in (3.0, 3.0, 0.2)]
    expected = 0.70 * (sum(frame_scores) / 3) + 0.30 * min(frame_scores)
    assert item["score"]["semantic"] == pytest.approx(round(expected, 3))


def test_start_frame_zero_wins_only_as_an_explicitly_scored_tie() -> None:
    item = run()  # every window identical -> deterministic lower-start tie break
    assert item["selected_window_start_in_frames"] == 0
    assert "start_frame_zero_scored" in item["selection_reasons"]
    zero = [c for c in item["candidates"] if c["start_in_frames"] == 0][0]
    assert zero["total"] == item["score"]["total"]
    assert zero["status"] == "selected"


# --------------------------------------------------------------------------- #
# Policy validation
# --------------------------------------------------------------------------- #
def test_missing_policy_block_is_backwards_compatible() -> None:
    assert parse_source_window_policy(None) is None


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"max_candidate_windows": 0}, "max_candidate_windows"),
        ({"technical_samples_per_window": 2.5}, "technical_samples_per_window"),
        ({"semantic_top_k": True}, "semantic_top_k"),
        ({"semantic_samples_per_window": -1}, "semantic_samples_per_window"),
        ({"semantic_top_k": 13}, "semantic_top_k"),
        ({"min_surplus_sec": -0.5}, "min_surplus_sec"),
        ({"min_score": 101}, "min_score"),
        ({"weights": {"semantic": 0.7, "technical": 0.2, "motion": 0.1, "continuity": 0.1}}, "weights"),
        ({"weights": {"semantic": float("nan"), "technical": 0.2, "motion": 0.1, "continuity": 0.1}}, "weights.semantic"),
        ({"weights": {"semantic": 0.8, "technical": -0.1, "motion": 0.2, "continuity": 0.1}}, "weights.technical"),
        ({"weights": {"semantic": 0.6, "technical": 0.2, "motion": 0.2}}, "weights.continuity"),
        ({"enabled": "yes"}, "enabled"),
        ({"semantic": {"adapter": "full"}}, "semantic.adapter"),
    ],
)
def test_invalid_policy_raises_field_specific_error(overrides, field) -> None:
    raw = json.loads(json.dumps(SPEC_POLICY))
    raw.update(overrides)
    with pytest.raises(SourceWindowPolicyError, match=rf"visual\.source_window_selection\.{field}\b"):
        parse_source_window_policy(raw)


def test_disabled_policy_skips_before_any_sampling() -> None:
    sampler = FakeSampler()
    item = run(pol=policy(enabled=False), sampler=sampler)
    assert item["status"] == "skipped" and item["reason"] == "disabled"
    assert sampler.calls == []


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_equivalent_inputs_yield_byte_identical_provenance() -> None:
    def build() -> str:
        items = [
            run(clock=fake_clock()),
            skipped_item(scene_id="scene-08", asset_ref="jobs/example/assets/scene-08.mp4",
                         reason="non_native_video"),
        ]
        return json.dumps(build_selection_report(items, fps=FPS), indent=2)

    assert build() == build()


def test_report_includes_bounded_work_and_runtime_totals() -> None:
    selected = run(source_sec=300.0, scene_sec=15.0)
    rejected = run(evaluator=FakeEvaluator(_unknown))
    skipped = skipped_item(scene_id="scene-09", asset_ref="jobs/example/assets/scene-09.mp4",
                           reason="insufficient_headroom", source_duration_sec=12.0,
                           required_duration_in_frames=315)
    report = build_selection_report([selected, rejected, skipped], fps=FPS)

    assert report["schema_version"] == 1
    assert report["selector_version"] == "1.0"
    assert report["fps"] == FPS
    agg = report["aggregate"]
    assert agg == {
        "eligible_count": 2,
        "selected_count": 1,
        "skipped_count": 1,
        "rejected_count": 1,
        "total_analysis_runtime_ms": selected["analysis_runtime_ms"] + rejected["analysis_runtime_ms"],
    }
    assert agg["total_analysis_runtime_ms"] >= 0
    assert max(i.get("technical_candidate_count", 0) for i in report["items"]) <= 12
    assert max(i.get("semantic_candidate_count", 0) for i in report["items"]) <= 3


def test_provenance_has_no_images_or_absolute_paths() -> None:
    report = build_selection_report([run()], fps=FPS)
    text = json.dumps(report)
    assert "img" not in text
    assert "/Users" not in text and "/Volumes" not in text


def test_selected_bounds_are_mirrored_exactly_into_scene_asset_refs() -> None:
    item = run(sampler=FakeSampler(lambda f: (0.0 if f < 90 else 120.0, 50.0, 10.0)))
    refs = {"background": "jobs/example/assets/scene-07.mp4", "background_media_kind": "video"}
    apply_selection_to_scene(refs, item)
    assert refs["source_trim_before_in_frames"] == item["selected_window_start_in_frames"]
    assert refs["source_trim_end_in_frames"] == item["selected_window_end_in_frames"]
    assert refs["source_trim_timebase_fps"] == FPS
