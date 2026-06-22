"""PR E bounded visual-beat planner (spec v4.0.3).

The planner is intentionally deterministic and side-effect free. It does not
fetch media, analyze pixels, mutate selection weights, or export PR F analytics.
It only converts already validated PR D trim/assets plus scene boundaries into a
small set of editorial visual plans.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from video_agent.shorts import asset_schedule
from video_agent.shorts.visual_acquisition import CONTRACT_REVISION, stable_hash
from video_agent.shorts.visual_spans import _is_graphic_scene, _scene_duration, _scene_id

SCHEMA_VERSION = 1
PLANNER_VERSION = 1
SUPPORTED_NON_LEGACY_MODES = (
    "continuous_clip",
    "two_clip",
    "clip_plus_graphic",
    "generated_image_fallback",
)
ALLOWED_BOUNDARY_REASONS = {
    "required evidence changes",
    "required action changes",
    "problem-to-solution transition",
    "visible action completes",
    "clip becomes stale",
    "explicit pattern interrupt",
    "graphic explanation adds clarity",
    "source clip duration/crop constraint",
}

_SIMPLICITY_RANK = {
    "continuous_clip": 0,
    "clip_plus_graphic": 1,
    "two_clip": 2,
    "generated_image_fallback": 3,
}

# Editorial prior per mode. Evidence deltas (semantic, motion, crop, complexity,
# fallback penalty) move the final score off the prior so the planner is a quality
# OPTIMIZER, not a constant mode-selector: a weak native clip drops below margin so
# a graphic/fallback can win, while a strong native clip stays on top.
_MODE_BASE = {
    "continuous_clip": 80.0,
    "two_clip": 80.0,
    "clip_plus_graphic": 82.0,  # designed infographic adds clarity
    "generated_image_fallback": 66.0,  # no real footage
}
_SEMANTIC_DELTA = {"PASS": 4.0, "CAPABILITY_REDUCED": -4.0, "FAIL": -20.0}
_MOTION_DELTA = {
    "normal_motion": 4.0,
    "low_motion": 2.0,
    "high_motion": 1.0,
    "near_static": -2.0,
    "unstable": -8.0,
}


def _evidence_score(
    mode: str,
    *,
    trim: dict[str, Any] | None,
    span_qa: dict[str, Any] | None,
    beats: list[dict[str, Any]],
) -> tuple[float, dict[str, Any]]:
    """Deterministic, side-effect-free quality score from already-computed PR D
    evidence (semantic verdict, motion band, crop stability) plus structural
    factors (cut count, generated-fallback penalty). Returns (score, breakdown)."""
    base = _MODE_BASE.get(mode, 78.0)
    semantic = _SEMANTIC_DELTA.get(_qa_verdict(span_qa), 0.0)
    motion = _MOTION_DELTA.get(str((trim or {}).get("motion_band") or ""), 0.0)
    # crop_stability_score lives at the trim-span top level (PR D analysis); 0..1.
    crop_delta = 4.0 * float((trim or {}).get("crop_stability_score") or 0.0)
    n_generated = sum(1 for b in beats if b.get("type") == "generated_image")
    complexity = -1.5 * max(0, len(beats) - 1)
    # Penalize ONLY the fallback mode for using generated imagery; a graphic beat
    # inside clip_plus_graphic is a deliberate editorial element, not a fallback.
    fallback_penalty = -6.0 * n_generated if mode == "generated_image_fallback" else 0.0
    raw = base + semantic + motion + crop_delta + complexity + fallback_penalty
    score = round(max(0.0, min(100.0, raw)), 3)
    breakdown = {
        "base": base,
        "semantic_delta": semantic,
        "motion_delta": motion,
        "crop_delta": round(crop_delta, 3),
        "complexity_delta": complexity,
        "fallback_penalty": fallback_penalty,
        "motion_band": (trim or {}).get("motion_band"),
        "semantic_verdict": _qa_verdict(span_qa),
    }
    return score, breakdown


def resolve_visual_beat_planner_config(channel_config: dict[str, Any]) -> dict[str, Any]:
    """Resolve PR E beat-planner config and clamp the bounded search surface."""
    raw_vqf = ((channel_config or {}).get("shorts") or {}).get("visual_quality_flow") or {}
    raw = raw_vqf.get("beat_planner") or {}
    modes = [
        str(mode)
        for mode in (raw.get("modes") or SUPPORTED_NON_LEGACY_MODES)
        if str(mode) in SUPPORTED_NON_LEGACY_MODES
    ]
    if not modes:
        modes = list(SUPPORTED_NON_LEGACY_MODES)
    try:
        max_plans = int(raw.get("max_non_legacy_plans", 3))
    except (TypeError, ValueError):
        max_plans = 3
    try:
        margin = float(raw.get("simplicity_margin_points", 3.0))
    except (TypeError, ValueError):
        margin = 3.0
    return {
        "enabled": bool(raw_vqf.get("enabled", False)) and bool(raw.get("enabled", False)),
        "mode": str(raw_vqf.get("mode") or "disabled").strip().lower(),
        "max_non_legacy_plans": max(1, min(3, max_plans)),
        "simplicity_margin_points": max(0.0, margin),
        "allow_inside_scene_boundary": bool(raw.get("allow_inside_scene_boundary", False)),
        "modes": modes,
    }


def _span_id(span: dict[str, Any]) -> str:
    return str(span.get("id") or span.get("visual_span_id") or "")


def _trim_by_span(trim_window_plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(span.get("visual_span_id")): span
        for span in (trim_window_plan or {}).get("spans") or []
        if span.get("visual_span_id") and span.get("final_candidate_id")
    }


def _asset_qa_by_span(visual_span_asset_qa: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(span.get("visual_span_id")): span
        for span in (visual_span_asset_qa or {}).get("spans") or []
        if span.get("visual_span_id")
    }


def _qa_verdict(span_qa: dict[str, Any] | None) -> str:
    verdict = ((span_qa or {}).get("qa") or {}).get("verdict")
    return str(verdict or "PASS")


def _adapted_asset_ref(adapted: dict[str, Any] | None) -> str:
    return str((adapted or {}).get("public_ref") or (adapted or {}).get("local_path") or "")


def _resolved_native(adapted: dict[str, Any] | None) -> bool:
    return bool(
        adapted
        and adapted.get("source_media_kind") == asset_schedule.NATIVE_VIDEO
        and adapted.get("exists", True)
        and _adapted_asset_ref(adapted)
    )


def _resolved_generated_image(adapted: dict[str, Any] | None) -> bool:
    if not adapted or not adapted.get("exists", True) or not _adapted_asset_ref(adapted):
        return False
    return str(adapted.get("source_media_kind") or "") in {
        asset_schedule.IMAGE_BACKED_VIDEO,
        asset_schedule.NATIVE_IMAGE,
    }


def _base_native_beat(
    *,
    beat_id: str,
    scene_ids: list[str],
    adapted: dict[str, Any] | None,
    boundary_reason: str,
) -> dict[str, Any]:
    return {
        "beat_id": beat_id,
        "type": "native_video",
        "scene_ids": list(scene_ids),
        "asset_selection_ref": f"{scene_ids[0]}-selection" if scene_ids else None,
        "asset_ref": _adapted_asset_ref(adapted),
        "asset_id": (adapted or {}).get("asset_id"),
        "provider": (adapted or {}).get("provider"),
        "provider_asset_id": (adapted or {}).get("provider_asset_id"),
        "render_media_kind": (adapted or {}).get("render_media_kind") or "video",
        "source_media_kind": (adapted or {}).get("source_media_kind")
        or asset_schedule.NATIVE_VIDEO,
        "source_duration_sec": (adapted or {}).get("source_duration_sec"),
        "boundary_reason": boundary_reason,
        "inside_scene_boundary": False,
    }


def _base_generated_image_beat(
    *,
    beat_id: str,
    scene_ids: list[str],
    adapted: dict[str, Any] | None,
    boundary_reason: str,
) -> dict[str, Any]:
    return {
        "beat_id": beat_id,
        "type": "generated_image",
        "scene_ids": list(scene_ids),
        "asset_selection_ref": f"{scene_ids[0]}-generated-image" if scene_ids else None,
        "asset_ref": _adapted_asset_ref(adapted),
        "asset_id": (adapted or {}).get("asset_id"),
        "provider": (adapted or {}).get("provider"),
        "provider_asset_id": (adapted or {}).get("provider_asset_id"),
        "render_media_kind": (adapted or {}).get("render_media_kind") or "video",
        "source_media_kind": (adapted or {}).get("source_media_kind")
        or asset_schedule.IMAGE_BACKED_VIDEO,
        "source_duration_sec": (adapted or {}).get("source_duration_sec"),
        "boundary_reason": boundary_reason,
        "inside_scene_boundary": False,
    }


def _continuous_candidate(
    *,
    span_id: str,
    member_ids: list[str],
    member_scenes: list[dict[str, Any]],
    trim: dict[str, Any] | None,
    span_qa: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not trim or any(_is_graphic_scene(scene) for scene in member_scenes):
        return None
    if _qa_verdict(span_qa) == "FAIL":
        return None
    beat = {
        "beat_id": "vb01",
        "type": "native_video",
        "scene_ids": list(member_ids),
        "asset_selection_ref": f"{span_id}-selection",
        "trim_window_ref": f"{span_id}-trim",
        "source": "pr_d_trim_window",
        "asset_ref": trim.get("asset_ref")
        or trim.get("public_ref")
        or trim.get("local_path")
        or "",
        "asset_id": trim.get("final_candidate_id"),
        "provider": trim.get("provider"),
        "provider_asset_id": trim.get("provider_asset_id"),
        "render_media_kind": "video",
        "source_media_kind": asset_schedule.NATIVE_VIDEO,
        "source_duration_sec": trim.get("source_duration_sec"),
        "selected_window_start_in_frames": int(trim.get("selected_window_start_in_frames") or 0),
        "selected_window_end_in_frames": trim.get("selected_window_end_in_frames"),
        "trim_timebase_fps": trim.get("trim_timebase_fps"),
        "crop_plan": trim.get("crop_plan"),
        "motion_band": trim.get("motion_band"),
        "boundary_reason": "source clip duration/crop constraint",
        "inside_scene_boundary": False,
    }
    score, breakdown = _evidence_score(
        "continuous_clip", trim=trim, span_qa=span_qa, beats=[beat]
    )
    return {
        "plan_id": f"{span_id}-vp01",
        "mode": "continuous_clip",
        "score": score,
        "simplicity_rank": _SIMPLICITY_RANK["continuous_clip"],
        "beats": [beat],
        "candidate_debug": {
            "complexity_penalty": 0,
            "score_breakdown": breakdown,
            "staleness_policy": "single coherent validated trim; do not cut solely by timer",
        },
    }


def _two_clip_candidate(
    *,
    span_id: str,
    member_ids: list[str],
    member_scenes: list[dict[str, Any]],
    adapted_scenes: dict[str, dict[str, Any]],
    trim: dict[str, Any] | None = None,
    span_qa: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if len(member_ids) < 2 or any(_is_graphic_scene(scene) for scene in member_scenes):
        return None
    if not all(_resolved_native(adapted_scenes.get(sid)) for sid in member_ids):
        return None
    beats = [
        _base_native_beat(
            beat_id=f"vb{idx + 1:02d}",
            scene_ids=[sid],
            adapted=adapted_scenes.get(sid),
            boundary_reason="problem-to-solution transition"
            if idx == 0
            else "required evidence changes",
        )
        for idx, sid in enumerate(member_ids)
    ]
    score, breakdown = _evidence_score("two_clip", trim=trim, span_qa=span_qa, beats=beats)
    return {
        "plan_id": f"{span_id}-vp02",
        "mode": "two_clip",
        "score": score,
        "simplicity_rank": _SIMPLICITY_RANK["two_clip"],
        "beats": beats,
        "candidate_debug": {
            "complexity_penalty": 2,
            "score_breakdown": breakdown,
            "staleness_policy": "scene-boundary cut with explicit editorial reason",
        },
    }


def _clip_plus_graphic_candidate(
    *,
    span_id: str,
    member_ids: list[str],
    member_scenes: list[dict[str, Any]],
    adapted_scenes: dict[str, dict[str, Any]],
    trim: dict[str, Any] | None = None,
    span_qa: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not any(_is_graphic_scene(scene) for scene in member_scenes):
        return None
    beats: list[dict[str, Any]] = []
    for idx, (sid, scene) in enumerate(zip(member_ids, member_scenes, strict=True)):
        if _is_graphic_scene(scene):
            adapted = adapted_scenes.get(sid)
            if not _resolved_generated_image(adapted):
                return None
            beats.append(
                _base_generated_image_beat(
                    beat_id=f"vb{idx + 1:02d}",
                    scene_ids=[sid],
                    adapted=adapted,
                    boundary_reason="graphic explanation becomes generated image",
                )
            )
            continue
        adapted = adapted_scenes.get(sid)
        if not _resolved_native(adapted):
            return None
        beats.append(
            _base_native_beat(
                beat_id=f"vb{idx + 1:02d}",
                scene_ids=[sid],
                adapted=adapted,
                boundary_reason="graphic explanation adds clarity",
            )
        )
    score, breakdown = _evidence_score(
        "clip_plus_graphic", trim=trim, span_qa=span_qa, beats=beats
    )
    return {
        "plan_id": f"{span_id}-vp03",
        "mode": "clip_plus_graphic",
        "score": score,
        "simplicity_rank": _SIMPLICITY_RANK["clip_plus_graphic"],
        "beats": beats,
        "candidate_debug": {
            "complexity_penalty": 1,
            "score_breakdown": breakdown,
            "graphic_renderer": "existing_scene_graphic",
        },
    }


def _generated_image_fallback_candidate(
    *,
    span_id: str,
    member_ids: list[str],
    adapted_scenes: dict[str, dict[str, Any]],
    trim: dict[str, Any] | None = None,
    span_qa: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    beats: list[dict[str, Any]] = []
    for idx, sid in enumerate(member_ids):
        adapted = adapted_scenes.get(sid)
        if not _resolved_generated_image(adapted):
            return None
        beats.append(
            _base_generated_image_beat(
                beat_id=f"vb{idx + 1:02d}",
                scene_ids=[sid],
                adapted=adapted,
                boundary_reason="no validated Pexels finalist; use generated image fallback",
            )
        )
    score, breakdown = _evidence_score(
        "generated_image_fallback", trim=trim, span_qa=span_qa, beats=beats
    )
    return {
        "plan_id": f"{span_id}-vp04",
        "mode": "generated_image_fallback",
        "score": score,
        "simplicity_rank": _SIMPLICITY_RANK["generated_image_fallback"],
        "beats": beats,
        "candidate_debug": {
            "fallback_reason": "all Pexels finalists rejected or unavailable",
            "score_breakdown": breakdown,
            "quality_policy": "generated image is preferred over off-topic footage",
        },
    }


def _is_cta_scene(scene: dict[str, Any]) -> bool:
    """A call-to-action scene (end card) — by layout or retention function."""
    layout = str(scene.get("layout") or "")
    return layout in {"short_cta", "cta"} or str(scene.get("retention_function") or "") == "cta"


def _is_controlled_visual_scene(scene: dict[str, Any]) -> bool:
    """Scenes that need a controlled clean AI image instead of unreliable stock:
    hook (face/emotion first frame), CTA (on-brand end card), and short_tip
    (single precise human-pose actions — sit/feet, breathe, write, message — where
    stock topic-matches but misses the required action evidence). The montage
    (short_checklist) and graphics keep their own renderers."""
    layout = str(scene.get("layout") or "")
    rf = str(scene.get("retention_function") or "")
    return (
        layout in {"short_cta", "cta", "short_hook", "hook", "short_tip"}
        or rf in {"cta", "hook"}
    )


def _select_plan(
    candidates: list[dict[str, Any]], *, simplicity_margin_points: float
) -> tuple[dict[str, Any] | None, str]:
    if not candidates:
        return None, "legacy_fallback_no_non_legacy_candidate"
    best_score = max(float(candidate.get("score") or 0.0) for candidate in candidates)
    adequate = [
        candidate
        for candidate in candidates
        if float(candidate.get("score") or 0.0) >= best_score - simplicity_margin_points
    ]
    adequate.sort(key=lambda c: (int(c.get("simplicity_rank", 99)), -float(c.get("score") or 0.0)))
    selected = adequate[0]
    reason = (
        "highest_scoring"
        if float(selected.get("score") or 0.0) == best_score
        else "simplest_within_margin"
    )
    return selected, reason


def _span_duration_sec(member_scenes: list[dict[str, Any]]) -> float:
    return sum(_scene_duration(scene) for scene in member_scenes)


def _trim_candidate_count(plan: dict[str, Any]) -> int:
    return sum(1 for span in plan.get("spans") or [] if span.get("selected_plan"))


def build_visual_beat_plan(
    *,
    short_id: str,
    scene_doc: dict[str, Any],
    visual_spans: dict[str, Any],
    resolved_visuals: dict[str, Any],
    trim_window_plan: dict[str, Any] | None,
    visual_span_asset_qa: dict[str, Any] | None,
    channel_config: dict[str, Any],
    fps: int,
) -> dict[str, Any]:
    """Build canonical ``visual_beat_plan.json`` for PR E."""
    config = resolve_visual_beat_planner_config(channel_config)
    scenes = list((scene_doc or {}).get("scenes") or [])
    scene_by_id = {_scene_id(scene, idx): scene for idx, scene in enumerate(scenes)}
    boundaries = asset_schedule.build_scene_frame_timeline(scenes, fps)
    boundary_by_id = {b["scene_id"]: b for b in boundaries}
    adapted_scenes = (resolved_visuals or {}).get("scenes") or {}
    trims = _trim_by_span(trim_window_plan)
    span_qas = _asset_qa_by_span(visual_span_asset_qa)

    planned_spans: list[dict[str, Any]] = []
    distribution: Counter[str] = Counter()

    for raw_span in (visual_spans or {}).get("spans") or []:
        span_id = _span_id(raw_span)
        if not span_id:
            continue
        member_ids = [
            str(sid) for sid in (raw_span.get("scene_ids") or []) if str(sid) in scene_by_id
        ]
        member_scenes = [scene_by_id[sid] for sid in member_ids]
        if not member_scenes:
            continue

        candidates: list[dict[str, Any]] = []
        if "continuous_clip" in config["modes"]:
            candidate = _continuous_candidate(
                span_id=span_id,
                member_ids=member_ids,
                member_scenes=member_scenes,
                trim=trims.get(span_id),
                span_qa=span_qas.get(span_id),
            )
            if candidate:
                candidates.append(candidate)
        if "two_clip" in config["modes"]:
            candidate = _two_clip_candidate(
                span_id=span_id,
                member_ids=member_ids,
                member_scenes=member_scenes,
                adapted_scenes=adapted_scenes,
                trim=trims.get(span_id),
                span_qa=span_qas.get(span_id),
            )
            if candidate:
                candidates.append(candidate)
        if "clip_plus_graphic" in config["modes"]:
            candidate = _clip_plus_graphic_candidate(
                span_id=span_id,
                member_ids=member_ids,
                member_scenes=member_scenes,
                adapted_scenes=adapted_scenes,
                trim=trims.get(span_id),
                span_qa=span_qas.get(span_id),
            )
            if candidate:
                candidates.append(candidate)
        if "generated_image_fallback" in config["modes"]:
            candidate = _generated_image_fallback_candidate(
                span_id=span_id,
                member_ids=member_ids,
                adapted_scenes=adapted_scenes,
                trim=trims.get(span_id),
                span_qa=span_qas.get(span_id),
            )
            if candidate:
                candidates.append(candidate)

        # Hook + CTA scenes: prefer the clean generated-image fallback over stock.
        # The hook needs a human/emotional first frame (stock gives empty rooms);
        # CTA stock often carries foreign-language text / off-brand boards. A
        # controlled AI background reads far cleaner for both.
        if any(_is_controlled_visual_scene(s) for s in member_scenes):
            _fb = next(
                (c for c in candidates if c.get("mode") == "generated_image_fallback"), None
            )
            if _fb is not None:
                candidates = [_fb]

        candidates.sort(key=lambda c: (int(c.get("simplicity_rank", 99)), str(c.get("mode"))))
        candidates = candidates[: int(config["max_non_legacy_plans"])]
        selected, reason = _select_plan(
            candidates, simplicity_margin_points=float(config["simplicity_margin_points"])
        )
        if selected:
            distribution[str(selected["mode"])] += 1
        span_qa_verdict = _qa_verdict(span_qas.get(span_id))
        selected_mode = str((selected or {}).get("mode") or "")
        selected_verdict = (
            "PASS"
            if selected_mode == "generated_image_fallback"
            else span_qa_verdict
        )
        planned_spans.append(
            {
                "visual_span_id": span_id,
                "scene_ids": member_ids,
                "duration_sec": _span_duration_sec(member_scenes),
                "from_frame": boundary_by_id[member_ids[0]]["from_frame"],
                "end_frame_exclusive": boundary_by_id[member_ids[-1]]["end_frame_exclusive"],
                "candidate_plan_count": len(candidates),
                "candidate_plans": candidates,
                "selected_plan": selected,
                "selection_reason": reason,
                "qa": {
                    "verdict": "FAIL" if not selected else selected_verdict,
                    "errors": [] if selected else ["no_non_legacy_visual_plan"],
                    "warnings": []
                    if selected_verdict == "PASS"
                    else [f"span_asset_qa:{span_qa_verdict}"],
                },
            }
        )

    verdicts = [(span.get("qa") or {}).get("verdict") for span in planned_spans]
    overall = (
        "FAIL"
        if any(v == "FAIL" for v in verdicts)
        else ("CAPABILITY_REDUCED" if any(v == "CAPABILITY_REDUCED" for v in verdicts) else "PASS")
    )
    input_hash = stable_hash(
        {
            "scene_doc": scene_doc,
            "visual_spans": visual_spans,
            "trim_window_plan": trim_window_plan,
            "visual_span_asset_qa": visual_span_asset_qa,
            "config": config,
            "fps": fps,
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "contract_revision": CONTRACT_REVISION,
        "planner_version": PLANNER_VERSION,
        "short_id": short_id,
        "input_hash": input_hash,
        "created_by_stage": "visual_beats",
        "fps": fps,
        "config": {
            "max_non_legacy_plans": config["max_non_legacy_plans"],
            "simplicity_margin_points": config["simplicity_margin_points"],
            "allow_inside_scene_boundary": config["allow_inside_scene_boundary"],
            "modes": config["modes"],
        },
        "summary": {
            "span_count": len(planned_spans),
            "planned_span_count": _trim_candidate_count({"spans": planned_spans}),
            "candidate_plan_count": sum(s["candidate_plan_count"] for s in planned_spans),
            "plan_distribution": dict(sorted(distribution.items())),
        },
        "spans": planned_spans,
        "qa": {"verdict": overall, "errors": [], "warnings": []},
    }
