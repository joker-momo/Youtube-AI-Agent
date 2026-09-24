"""Long-form semantic source-window selection (spec 2026-09-24).

The legacy long-form renderer (``ChannelVideo.tsx``) plays every native-video
background from its first frame unless the scene carries trim bounds. This
module chooses a bounded, evidence-backed interval instead:

* at most ``max_candidate_windows`` evenly spaced windows are sampled
  (``technical_samples_per_window`` frames each) - never a full-source scan;
* windows are hard-rejected on decode failure, black/fade, unstable motion or
  impossible center-cover crop;
* only the ``semantic_top_k`` technically best windows get local semantic
  evidence, one record set per frame, so a single lucky frame cannot carry an
  off-topic window;
* an eligible source with no acceptable window is rejected (fail closed) - the
  caller must not fall back to an unscored frame-zero render.

Sampling and semantic evidence are injected callables. The scoring core never
touches ffmpeg or model weights; :class:`FfmpegWindowSampler` and
:func:`probe_source_video` are the local-media implementations used in
production. Like the rest of ``video_agent.visual`` this module does not import
``video_agent.shorts``.
"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SELECTOR_VERSION = "1.0"

# ChannelVideo.tsx mounts every scene after the first SCENE_XFADE frames early to
# cross-dissolve, so a legacy background plays duration + SCENE_XFADE frames.
LEGACY_SCENE_XFADE_FRAMES = 15
# Keep each window's last sample far enough from the container end that the
# paired motion frame (MOTION_PAIR_SEC later) still decodes.
TAIL_GUARD_FRAMES = 8
MOTION_PAIR_SEC = 0.2
BLACK_LUMA_THRESHOLD = 8.0
MAX_BLACK_OR_FADE_RATIO = 0.05

SCENE_TRIM_KEYS = (
    "source_trim_before_in_frames",
    "source_trim_end_in_frames",
    "source_trim_timebase_fps",
)
SKIP_REASONS = ("disabled", "non_native_video", "decode_unavailable", "insufficient_headroom")

_MOTION_SCORES = {"normal": 100.0, "low": 65.0, "high": 40.0, "unstable": 0.0}
_POLICY_FIELD = "visual.source_window_selection"
_SUPPORTED_SEMANTIC_ADAPTERS = ("clip",)
_SUPPORTED_DEVICES = ("auto", "mps", "cpu")
DEFAULT_SIGLIP_MODEL = "google/siglip2-base-patch16-224"


class SourceWindowPolicyError(ValueError):
    """Invalid ``visual.source_window_selection`` config (raised before media work)."""


class LongSourceWindowSelectionError(RuntimeError):
    """An eligible long-form source has no acceptable window; do not render it."""

    def __init__(
        self,
        *,
        scene_id: str,
        asset_ref: str,
        rejected_window_counts: dict[str, int],
        rejected_scene_ids: list[str] | None = None,
    ) -> None:
        self.scene_id = scene_id
        self.asset_ref = asset_ref
        self.rejected_window_counts = dict(rejected_window_counts)
        self.rejected_scene_ids = list(rejected_scene_ids or [scene_id])
        counts = ", ".join(f"{k}={v}" for k, v in sorted(self.rejected_window_counts.items()))
        super().__init__(
            f"no acceptable long-form source window for {scene_id} ({asset_ref}); "
            f"rejected windows: {counts or 'none'}; "
            f"rejected scenes: {', '.join(self.rejected_scene_ids)}"
        )


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SourceWindowWeights:
    semantic: float = 0.60
    technical: float = 0.20
    motion: float = 0.10
    continuity: float = 0.10


@dataclass(frozen=True)
class SourceWindowSemanticConfig:
    adapter: str = "clip"
    device: str = "auto"
    model: str = DEFAULT_SIGLIP_MODEL
    enforce_age_band_45_plus: bool = True

    def as_local_qa_config(self) -> dict[str, Any]:
        """The ``local_qa`` block understood by the local semantic analyzer factory."""
        return {
            "semantic_adapter": self.adapter,
            "device": self.device,
            "semantic_models": {"siglip": self.model},
            "enforce_age_band_45_plus": self.enforce_age_band_45_plus,
        }


@dataclass(frozen=True)
class SourceWindowPolicy:
    enabled: bool = True
    min_surplus_sec: float = 6.0
    max_candidate_windows: int = 12
    technical_samples_per_window: int = 3
    semantic_top_k: int = 3
    semantic_samples_per_window: int = 3
    min_score: float = 70.0
    weights: SourceWindowWeights = field(default_factory=SourceWindowWeights)
    semantic: SourceWindowSemanticConfig = field(default_factory=SourceWindowSemanticConfig)


def _policy_error(name: str, message: str) -> SourceWindowPolicyError:
    return SourceWindowPolicyError(f"{_POLICY_FIELD}.{name} {message}")


def _positive_int(raw: Mapping[str, Any], name: str, default: int) -> int:
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _policy_error(name, f"must be a positive integer, got {value!r}")
    return value


def _finite_number(raw: Mapping[str, Any], name: str, default: float, *, label: str | None = None) -> float:
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _policy_error(label or name, f"must be a finite number, got {value!r}")
    return float(value)


def _bool(raw: Mapping[str, Any], name: str, default: bool, *, label: str | None = None) -> bool:
    value = raw.get(name, default)
    if not isinstance(value, bool):
        raise _policy_error(label or name, f"must be a boolean, got {value!r}")
    return value


def parse_source_window_policy(raw: Mapping[str, Any] | None) -> SourceWindowPolicy | None:
    """Validate the channel block; ``None`` (block absent) keeps legacy behavior."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise SourceWindowPolicyError(f"{_POLICY_FIELD} must be a mapping, got {type(raw).__name__}")
    enabled = _bool(raw, "enabled", True)
    min_surplus_sec = _finite_number(raw, "min_surplus_sec", 6.0)
    if min_surplus_sec < 0:
        raise _policy_error("min_surplus_sec", f"must be >= 0, got {min_surplus_sec!r}")
    max_windows = _positive_int(raw, "max_candidate_windows", 12)
    technical_samples = _positive_int(raw, "technical_samples_per_window", 3)
    top_k = _positive_int(raw, "semantic_top_k", 3)
    semantic_samples = _positive_int(raw, "semantic_samples_per_window", 3)
    if top_k > max_windows:
        raise _policy_error("semantic_top_k", f"must be <= max_candidate_windows ({max_windows}), got {top_k}")
    min_score = _finite_number(raw, "min_score", 70.0)
    if not 0.0 <= min_score <= 100.0:
        raise _policy_error("min_score", f"must be within 0..100, got {min_score!r}")

    raw_weights = raw.get("weights", {})
    if not isinstance(raw_weights, Mapping):
        raise _policy_error("weights", "must be a mapping")
    defaults = SourceWindowWeights()
    weight_values: dict[str, float] = {}
    for name in ("semantic", "technical", "motion", "continuity"):
        if name not in raw_weights and raw_weights:
            raise _policy_error(f"weights.{name}", "is required")
        value = _finite_number(raw_weights, name, getattr(defaults, name), label=f"weights.{name}")
        if value < 0:
            raise _policy_error(f"weights.{name}", f"must be >= 0, got {value!r}")
        weight_values[name] = value
    unknown_weights = sorted(set(raw_weights) - set(weight_values))
    if unknown_weights:
        raise _policy_error("weights", f"has unknown keys {unknown_weights}")
    total = sum(weight_values.values())
    if abs(total - 1.0) > 1e-6:
        raise _policy_error("weights", f"must sum to 1.0 (+/-1e-6), got {total!r}")

    raw_semantic = raw.get("semantic", {})
    if not isinstance(raw_semantic, Mapping):
        raise _policy_error("semantic", "must be a mapping")
    adapter = str(raw_semantic.get("adapter", "clip")).strip().lower()
    if adapter not in _SUPPORTED_SEMANTIC_ADAPTERS:
        raise _policy_error("semantic.adapter", f"must be one of {list(_SUPPORTED_SEMANTIC_ADAPTERS)}, got {adapter!r}")
    device = str(raw_semantic.get("device", "auto")).strip().lower()
    if device not in _SUPPORTED_DEVICES:
        raise _policy_error("semantic.device", f"must be one of {list(_SUPPORTED_DEVICES)}, got {device!r}")
    model = raw_semantic.get("model", DEFAULT_SIGLIP_MODEL)
    if not isinstance(model, str) or not model.strip():
        raise _policy_error("semantic.model", f"must be a non-empty string, got {model!r}")
    age_gate = _bool(raw_semantic, "enforce_age_band_45_plus", True, label="semantic.enforce_age_band_45_plus")

    return SourceWindowPolicy(
        enabled=enabled,
        min_surplus_sec=min_surplus_sec,
        max_candidate_windows=max_windows,
        technical_samples_per_window=technical_samples,
        semantic_top_k=top_k,
        semantic_samples_per_window=semantic_samples,
        min_score=min_score,
        weights=SourceWindowWeights(**weight_values),
        semantic=SourceWindowSemanticConfig(
            adapter=adapter, device=device, model=model.strip(), enforce_age_band_45_plus=age_gate
        ),
    )


# --------------------------------------------------------------------------- #
# Frame math
# --------------------------------------------------------------------------- #
def frames_from_seconds(seconds: float, fps: int) -> int:
    """JS ``Math.round`` parity (``floor(x + 0.5)``) - the renderer's rounding."""
    return int(math.floor(float(seconds) * fps + 0.5))


def required_window_frames(scene_duration_sec: float, fps: int) -> int:
    """Source frames a legacy scene background actually plays, crossfade lead included."""
    return frames_from_seconds(scene_duration_sec, fps) + LEGACY_SCENE_XFADE_FRAMES


def candidate_window_starts(*, max_start: int, count: int) -> list[int]:
    """At most ``count`` evenly spaced starts over ``0..max_start`` (endpoints included)."""
    if max_start <= 0:
        return [0]
    n = min(count, max_start + 1)
    if n == 1:
        return [0]
    return sorted({int(math.floor(i * max_start / (n - 1) + 0.5)) for i in range(n)})


def window_sample_frames(start: int, end: int, count: int) -> list[int]:
    """Evenly spaced frames across ``[start, end)``: start/middle/end for three."""
    length = end - start
    if count <= 1 or length <= 1:
        return [start + (length - 1) // 2]
    return [start + int(math.floor(i * (length - 1) / (count - 1) + 0.5)) for i in range(count)]


# --------------------------------------------------------------------------- #
# Evidence types
# --------------------------------------------------------------------------- #
@dataclass
class FrameSample:
    frame_in_frames: int
    decoded: bool
    mean_luma: float = 0.0
    sharpness: float = 0.0
    motion: float | None = None
    image: Any = field(default=None, repr=False, compare=False)


WindowSampler = Callable[[list[int]], list[FrameSample]]
SemanticEvaluator = Callable[[list[Any]], list[list[dict[str, Any]]]]


def _motion_band(value: float) -> str:
    if value < 6.0:
        return "low"
    if value < 25.0:
        return "normal"
    if value < 50.0:
        return "high"
    return "unstable"


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _r(value: float) -> float:
    return round(float(value), 3)


# --------------------------------------------------------------------------- #
# Semantic aggregation
# --------------------------------------------------------------------------- #
_SEMANTIC_SEVERITY = (
    "capability_unavailable",
    "forbidden_present",
    "age_contradicted",
    "contradicted",
    "unknown",
    "supported",
)


def _frame_semantics(records: list[dict[str, Any]]) -> tuple[str, float | None, bool]:
    """(worst status, numeric confidence floor or None, intent retained) for one frame."""
    worst = "supported"
    intent_seen = False
    intent_ok = True
    confidences: list[float] = []

    def worsen(status: str) -> None:
        nonlocal worst
        if _SEMANTIC_SEVERITY.index(status) < _SEMANTIC_SEVERITY.index(worst):
            worst = status

    for rec in records:
        requirement = str(rec.get("requirement") or "")
        status = str(rec.get("status") or "UNKNOWN").upper()
        if status in {"CAPABILITY_UNAVAILABLE", "CAPABILITY_REDUCED"}:
            worsen("capability_unavailable")
            if not requirement.startswith("forbidden"):
                intent_ok = False
            continue
        if requirement.startswith("forbidden"):
            if status in {"CONFIRMED_PRESENT", "CONTRADICTED", "SUPPORTED"}:
                worsen("forbidden_present")
            elif status != "CONFIRMED_ABSENT":
                worsen("unknown")
            continue
        if "age_band_45_plus" in requirement:
            if status == "CONTRADICTED":
                worsen("age_contradicted")
            elif status not in {"SUPPORTED", "CONFIRMED_PRESENT"}:
                worsen("unknown")
            continue
        intent_seen = True
        if status in {"SUPPORTED", "CONFIRMED_PRESENT"}:
            confidence = rec.get("confidence")
            if isinstance(confidence, (int, float)) and math.isfinite(confidence):
                confidences.append(float(confidence))
        elif status in {"CONTRADICTED", "CONFIRMED_ABSENT"}:
            worsen("contradicted")
            intent_ok = False
        else:
            worsen("unknown")
            intent_ok = False
    if not intent_seen:
        worsen("unknown")
        intent_ok = False
    return worst, (min(confidences) if confidences else None), intent_ok


def _frame_score(confidence: float | None) -> float:
    # SigLIP intent-vs-distractor logit margin -> 0..100; status-only support = 100.
    if confidence is None:
        return 100.0
    return 100.0 / (1.0 + math.exp(-confidence))


def _window_semantics(per_frame: list[list[dict[str, Any]]], expected_frames: int) -> dict[str, Any]:
    if len(per_frame) != expected_frames:
        return {"status": "capability_unavailable", "semantic": 0.0, "continuity": 0.0}
    statuses: list[str] = []
    scores: list[float] = []
    retained = 0
    for records in per_frame:
        status, confidence, intent_ok = _frame_semantics(list(records or []))
        statuses.append(status)
        scores.append(_frame_score(confidence))
        retained += int(intent_ok)
    worst = min(statuses, key=_SEMANTIC_SEVERITY.index) if statuses else "unknown"
    continuity = 100.0 * retained / max(1, len(per_frame))
    semantic = 0.0
    if worst == "supported":
        semantic = 0.70 * (sum(scores) / len(scores)) + 0.30 * min(scores)
    return {"status": worst, "semantic": _clamp(semantic), "continuity": _clamp(continuity)}


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def skipped_item(
    *,
    scene_id: str,
    asset_ref: str | None,
    reason: str,
    source_duration_sec: float | None = None,
    required_duration_in_frames: int | None = None,
) -> dict[str, Any]:
    if reason not in SKIP_REASONS:
        raise ValueError(f"unknown skip reason {reason!r}")
    return {
        "scene_id": scene_id,
        "asset_ref": asset_ref,
        "source_duration_sec": None if source_duration_sec is None else _r(source_duration_sec),
        "required_duration_in_frames": required_duration_in_frames,
        "status": "skipped",
        "reason": reason,
    }


def select_source_window(
    *,
    scene_id: str,
    asset_ref: str,
    source_duration_sec: float,
    scene_duration_sec: float,
    fps: int,
    crop_retained_fraction: float,
    policy: SourceWindowPolicy,
    sampler: WindowSampler,
    semantic_evaluator: SemanticEvaluator,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Return one provenance item: ``selected``, ``skipped`` or ``rejected``.

    Never raises for a rejected source; :func:`raise_for_rejections` does that
    after the report has been persisted.
    """
    required = required_window_frames(scene_duration_sec, fps)
    if not policy.enabled:
        return skipped_item(scene_id=scene_id, asset_ref=asset_ref, reason="disabled",
                            source_duration_sec=source_duration_sec, required_duration_in_frames=required)
    source_frames = int(math.floor(float(source_duration_sec) * fps))
    max_start = source_frames - required - TAIL_GUARD_FRAMES
    if source_duration_sec < scene_duration_sec + policy.min_surplus_sec or max_start < 0:
        return skipped_item(scene_id=scene_id, asset_ref=asset_ref, reason="insufficient_headroom",
                            source_duration_sec=source_duration_sec, required_duration_in_frames=required)

    started = clock()
    starts = candidate_window_starts(max_start=max_start, count=policy.max_candidate_windows)
    rejected: dict[str, int] = {}
    candidates: list[dict[str, Any]] = []
    passing: list[dict[str, Any]] = []

    def reject(candidate: dict[str, Any], reason: str) -> None:
        candidate["status"] = "rejected"
        candidate["rejection_reason"] = reason
        rejected[reason] = rejected.get(reason, 0) + 1

    for start in starts:
        end = start + required
        frames = window_sample_frames(start, end, policy.technical_samples_per_window)
        samples = sampler(frames)
        candidate: dict[str, Any] = {"start_in_frames": start, "end_in_frames": end, "_samples": samples}
        candidates.append(candidate)
        if len(samples) != len(frames) or not all(s.decoded for s in samples):
            reject(candidate, "decode_failure")
            continue
        black_ratio = sum(1 for s in samples if s.mean_luma < BLACK_LUMA_THRESHOLD) / len(samples)
        candidate["black_or_fade_ratio"] = _r(black_ratio)
        if black_ratio > MAX_BLACK_OR_FADE_RATIO:
            reject(candidate, "black_or_fade")
            continue
        motions = [s.motion for s in samples if s.motion is not None]
        if not motions:
            reject(candidate, "decode_failure")
            continue
        band = _motion_band(sum(motions) / len(motions))
        candidate["motion_band"] = band
        if band == "unstable":
            reject(candidate, "unstable_motion")
            continue
        if crop_retained_fraction <= 0.0:
            reject(candidate, "crop_impossible")
            continue
        candidate["_sharpness"] = _median([s.sharpness for s in samples])
        passing.append(candidate)

    max_sharpness = max((c["_sharpness"] for c in passing), default=0.0)
    crop = max(0.0, min(1.0, float(crop_retained_fraction)))
    for candidate in passing:
        sharp_norm = candidate["_sharpness"] / max_sharpness if max_sharpness > 0 else 0.0
        no_fade = 1.0 - candidate["black_or_fade_ratio"]
        candidate["technical"] = _clamp(100.0 * (0.45 * sharp_norm + 0.35 * crop + 0.20 * no_fade))
        candidate["motion"] = _MOTION_SCORES[candidate["motion_band"]]

    ranked = sorted(passing, key=lambda c: (-round(c["technical"], 6), c["start_in_frames"]))
    semantic_pool = ranked[: policy.semantic_top_k]
    for candidate in ranked[policy.semantic_top_k:]:
        candidate["status"] = "not_semantically_evaluated"

    weights = policy.weights
    accepted: list[dict[str, Any]] = []
    for candidate in semantic_pool:
        start, end = candidate["start_in_frames"], candidate["end_in_frames"]
        samples = candidate["_samples"]
        if policy.semantic_samples_per_window != policy.technical_samples_per_window:
            frames = window_sample_frames(start, end, policy.semantic_samples_per_window)
            samples = sampler(frames)
            if len(samples) != len(frames) or not all(s.decoded for s in samples):
                reject(candidate, "decode_failure")
                continue
        try:
            per_frame = semantic_evaluator([s.image for s in samples])
        except Exception as exc:  # noqa: BLE001 - analyzer failure must fail closed
            # Class name only: exception text can carry absolute model/media paths.
            candidate["semantic_error"] = exc.__class__.__name__
            per_frame = []
        semantics = _window_semantics(list(per_frame or []), len(samples))
        candidate["semantic_status"] = semantics["status"]
        candidate["continuity"] = _r(semantics["continuity"])
        if semantics["status"] != "supported":
            candidate["semantic"] = 0.0
            reject(candidate, f"semantic_{semantics['status']}")
            continue
        candidate["semantic"] = semantics["semantic"]
        total = (
            weights.semantic * candidate["semantic"]
            + weights.technical * candidate["technical"]
            + weights.motion * candidate["motion"]
            + weights.continuity * semantics["continuity"]
        )
        candidate["total"] = _clamp(total)
        if candidate["total"] < policy.min_score:
            reject(candidate, "below_min_score")
            continue
        candidate["status"] = "passed"
        accepted.append(candidate)

    winner = min(accepted, key=lambda c: (-round(c["total"], 6), c["start_in_frames"]), default=None)
    if winner is not None:
        winner["status"] = "selected"
    runtime_ms = max(0, int(round((clock() - started) * 1000)))

    item: dict[str, Any] = {
        "scene_id": scene_id,
        "asset_ref": asset_ref,
        "source_duration_sec": _r(source_duration_sec),
        "required_duration_in_frames": required,
        "status": "selected" if winner else "rejected",
    }
    if winner is not None:
        item.update({
            "selected_window_start_in_frames": winner["start_in_frames"],
            "selected_window_end_in_frames": winner["end_in_frames"],
            "trim_timebase_fps": fps,
        })
    else:
        item["reason"] = "no_passing_window"
    item.update({
        "technical_candidate_count": len(candidates),
        "semantic_candidate_count": len(semantic_pool),
    })
    if winner is not None:
        item["score"] = {
            "total": _r(winner["total"]),
            "semantic": _r(winner["semantic"]),
            "technical": _r(winner["technical"]),
            "motion": _r(winner["motion"]),
            "continuity": _r(winner["continuity"]),
        }
        item["semantic_status"] = "supported"
        reasons = ["semantic_match", "no_fade", "stable_crop" if crop >= 0.999 else "center_cover_crop",
                   f"motion_{winner['motion_band']}"]
        if winner["start_in_frames"] == 0:
            reasons.append("start_frame_zero_scored")
        item["selection_reasons"] = reasons
    item["rejected_window_counts"] = dict(sorted(rejected.items()))
    item["candidates"] = [_public_candidate(c) for c in candidates]
    item["analysis_runtime_ms"] = runtime_ms
    return item


_CANDIDATE_KEYS = (
    "start_in_frames", "end_in_frames", "status", "rejection_reason", "black_or_fade_ratio",
    "motion_band", "technical", "motion", "semantic_status", "semantic", "continuity", "total",
    "semantic_error",
)


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _CANDIDATE_KEYS:
        if key not in candidate:
            continue
        value = candidate[key]
        out[key] = _r(value) if isinstance(value, float) else value
    return out


# --------------------------------------------------------------------------- #
# Report + scene contract
# --------------------------------------------------------------------------- #
def build_selection_report(items: list[dict[str, Any]], *, fps: int) -> dict[str, Any]:
    selected = sum(1 for i in items if i.get("status") == "selected")
    rejected = sum(1 for i in items if i.get("status") == "rejected")
    skipped = sum(1 for i in items if i.get("status") == "skipped")
    return {
        "schema_version": SCHEMA_VERSION,
        "selector_version": SELECTOR_VERSION,
        "fps": fps,
        "aggregate": {
            "eligible_count": selected + rejected,
            "selected_count": selected,
            "skipped_count": skipped,
            "rejected_count": rejected,
            "total_analysis_runtime_ms": sum(int(i.get("analysis_runtime_ms") or 0) for i in items),
        },
        "items": list(items),
    }


def raise_for_rejections(report: Mapping[str, Any]) -> None:
    rejected = [i for i in report.get("items") or [] if i.get("status") == "rejected"]
    if not rejected:
        return
    first = rejected[0]
    raise LongSourceWindowSelectionError(
        scene_id=str(first.get("scene_id")),
        asset_ref=str(first.get("asset_ref")),
        rejected_window_counts=dict(first.get("rejected_window_counts") or {}),
        rejected_scene_ids=[str(i.get("scene_id")) for i in rejected],
    )


def apply_selection_to_scene(asset_refs: dict[str, Any], item: Mapping[str, Any]) -> None:
    """Mirror selected bounds into scene ``asset_refs``; clear stale trims otherwise."""
    for key in SCENE_TRIM_KEYS:
        asset_refs.pop(key, None)
    if item.get("status") != "selected":
        return
    asset_refs["source_trim_before_in_frames"] = int(item["selected_window_start_in_frames"])
    asset_refs["source_trim_end_in_frames"] = int(item["selected_window_end_in_frames"])
    asset_refs["source_trim_timebase_fps"] = int(item["trim_timebase_fps"])


def center_cover_retained_fraction(width: int, height: int, target_aspect: float) -> float:
    """Share of the source kept by a center-cover crop to ``target_aspect``; 0 = impossible."""
    if width <= 0 or height <= 0 or target_aspect <= 0:
        return 0.0
    source_aspect = width / height
    return min(source_aspect, target_aspect) / max(source_aspect, target_aspect)


# --------------------------------------------------------------------------- #
# Local media implementations (ffprobe / ffmpeg)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SourceProbe:
    duration_sec: float
    width: int
    height: int


def probe_source_video(path: Path) -> SourceProbe | None:
    """Local ffprobe; ``None`` when the job asset cannot be decoded."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,duration:format=duration", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    streams = data.get("streams") or []
    if not streams:
        return None
    stream = streams[0]
    try:
        duration = float(stream.get("duration") or (data.get("format") or {}).get("duration") or 0.0)
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if duration <= 0 or not math.isfinite(duration):
        return None
    return SourceProbe(duration_sec=duration, width=width, height=height)


class FfmpegWindowSampler:
    """Decode a motion pair (``t`` and ``t + MOTION_PAIR_SEC``) per sampled frame.

    Frames are downscaled for metrics and kept in memory for semantic reuse, so
    semantic evidence is computed on exactly the frames that passed technical QA.
    """

    def __init__(self, path: Path, fps: int, *, width: int = 480) -> None:
        self.path = Path(path)
        self.fps = fps
        self.width = width

    def __call__(self, frames: list[int]) -> list[FrameSample]:
        return [self._sample(frame) for frame in frames]

    def _sample(self, frame: int) -> FrameSample:
        import numpy as np
        from PIL import Image

        timestamp = frame / self.fps
        pair_fps = 1.0 / MOTION_PAIR_SEC
        with tempfile.TemporaryDirectory(prefix="source-window-") as td:
            pattern = Path(td) / "f%d.png"
            try:
                subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{timestamp:.3f}",
                     "-i", str(self.path), "-vf", f"fps={pair_fps:g},scale={self.width}:-2",
                     "-frames:v", "2", str(pattern)],
                    check=True, capture_output=True, timeout=60,
                )
            except (OSError, subprocess.SubprocessError):
                return FrameSample(frame_in_frames=frame, decoded=False)
            first, second = Path(td) / "f1.png", Path(td) / "f2.png"
            if not first.exists():
                return FrameSample(frame_in_frames=frame, decoded=False)
            with Image.open(first) as img:
                image = img.convert("RGB")
            arr = np.asarray(image, dtype=np.float32)
            luma = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
            motion: float | None = None
            if second.exists():
                with Image.open(second) as img2:
                    arr2 = np.asarray(img2.convert("RGB"), dtype=np.float32)
                if arr2.shape == arr.shape:
                    luma2 = 0.2126 * arr2[:, :, 0] + 0.7152 * arr2[:, :, 1] + 0.0722 * arr2[:, :, 2]
                    motion = float(np.mean(np.abs(luma2 - luma)))
        gx = np.diff(luma, axis=1)
        gy = np.diff(luma, axis=0)
        return FrameSample(
            frame_in_frames=frame,
            decoded=True,
            mean_luma=float(np.mean(luma)),
            sharpness=float(np.var(gx) + np.var(gy)),
            motion=motion,
            image=image,
        )
