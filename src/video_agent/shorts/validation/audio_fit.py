"""Audio-fit & sync validation for scene durations."""

from __future__ import annotations

import math
import wave

from video_agent.shorts.validation._constants import *  # noqa: F401,F403
from video_agent.shorts.validation._helpers import _duration
from video_agent.shorts.validation.issues import *  # noqa: F401,F403


def validate_audio_fit(
    render_duration_sec: float,
    narration_audio_sec: float,
    *,
    margin_sec: float = AUDIO_TAIL_MARGIN_SEC,
    epsilon_sec: float = AUDIO_TAIL_EPSILON_SEC,
) -> SceneValidationIssue | None:
    required_margin = float(margin_sec or 0.0)
    actual_margin = float(render_duration_sec or 0.0) - float(narration_audio_sec or 0.0)
    if actual_margin + float(epsilon_sec or 0.0) < required_margin:
        return SceneValidationIssue(
            type="audio_fit",
            scene_id=None,
            severity="blocking_error",
            detail=(
                f"Narration audio ({float(narration_audio_sec):.1f}s) exceeds video duration "
                f"({float(render_duration_sec):.1f}s) with margin {margin_sec:.1f}s."
            ),
            repair_hint="Condense narration or increase valid scene durations without exceeding scene caps.",
        )
    return None


def extend_scene_durations_for_audio_tail(
    scenes_doc: dict[str, Any],
    narration_audio_sec: float,
    *,
    margin_sec: float = AUDIO_TAIL_MARGIN_SEC,
    repair_buffer_sec: float = AUDIO_TAIL_REPAIR_BUFFER_SEC,
    max_auto_extension_sec: float = 1.5,
) -> dict[str, Any]:
    """Add a small deterministic tail pad when narration fits except margin.

    This handles the common exact-fit case (e.g. 22.4s audio over a 22.4s
    scene plan) without asking the LLM to regenerate an otherwise good Short.
    Large shortages still fail audio_fit and go through script compression.
    """
    scenes = list((scenes_doc or {}).get("scenes") or [])
    current_total = float(sum(_duration(scene) for scene in scenes))
    required_total = (
        math.ceil(
            (
                float(narration_audio_sec or 0.0)
                + float(margin_sec or 0.0)
                + float(repair_buffer_sec or 0.0)
            )
            * 10.0
        )
        / 10.0
    )
    shortage = round(required_total - current_total, 3)
    if shortage <= 0:
        if scenes_doc is not None:
            scenes_doc["total_duration_sec"] = round(current_total, 1)
        return {"changed": False, "added_sec": 0.0, "reason": "already_fits"}
    if shortage > float(max_auto_extension_sec or 0.0):
        return {
            "changed": False,
            "added_sec": 0.0,
            "reason": "shortage_too_large",
            "shortage_sec": shortage,
        }
    if current_total + shortage > MAX_SHORT_DURATION_SEC:
        return {
            "changed": False,
            "added_sec": 0.0,
            "reason": "would_exceed_short_cap",
            "shortage_sec": shortage,
        }

    remaining = shortage
    notes: list[str] = []
    distribution: list[dict[str, Any]] = []
    # Prefer extending the final scene(s) first so the hook stays tight; only
    # walk backward into earlier scenes once the later ones hit their hard cap.
    # Respect per-layout hard caps and the global hard cap.
    for scene in reversed(scenes):
        if remaining <= 0:
            break
        layout = str(scene.get("layout") or "")
        hard_max = LAYOUT_DURATION_TARGETS.get(layout, (0.0, 0.0, GLOBAL_SCENE_MAX_SEC))[2]
        hard_max = min(float(hard_max), GLOBAL_SCENE_MAX_SEC)
        dur = _duration(scene)
        room = round(hard_max - dur, 3)
        if room <= 0:
            continue
        add = min(room, remaining)
        scene["duration_sec"] = round(dur + add, 1)
        remaining = round(remaining - add, 3)
        sid = scene.get("id") or scene.get("scene_id") or "?"
        added_here = round(scene["duration_sec"] - dur, 1)
        distribution.append({"scene_id": sid, "added_sec": added_here})
        notes.append(f"Extended {sid} by {added_here:.1f}s for audio tail.")

    added = round(shortage - max(remaining, 0.0), 3)
    new_total = round(sum(_duration(scene) for scene in scenes), 1)
    scenes_doc["scenes"] = scenes
    scenes_doc["total_duration_sec"] = new_total
    if remaining > 0:
        return {
            "changed": added > 0,
            "added_sec": added,
            "reason": "insufficient_scene_room",
            "shortage_sec": shortage,
            "notes": notes,
            "tail_repair_distribution": distribution,
        }
    return {
        "changed": True,
        "added_sec": added,
        "reason": "extended_for_audio_tail",
        "notes": notes,
        "tail_repair_distribution": distribution,
    }


def probe_audio_duration_sec(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate <= 0:
                return None
            return handle.getnframes() / float(rate)
    except Exception:
        return None


def audio_sync_summary(
    render_duration_sec: float,
    narration_audio_sec: float,
    *,
    tail_added_sec: float = 0.0,
    per_scene_padding_sec: list[float] | None = None,
    tail_repair_distribution: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Measurable audio/video sync verdict.

    Thresholds are derived from the real tail-margin/buffer constants (plus a
    small epsilon) so a healthy Short whose video intentionally outlasts the
    narration by the tail margin still PASSes instead of a false WARN.
    """
    pass_delta = round(
        AUDIO_TAIL_MARGIN_SEC + AUDIO_TAIL_REPAIR_BUFFER_SEC + AUDIO_SYNC_EPSILON_SEC,
        3,
    )
    warn_delta = round(pass_delta + 0.5, 3)
    delta = round(abs(float(render_duration_sec) - float(narration_audio_sec)), 3)
    if delta <= pass_delta:
        verdict = "PASS"
    elif delta <= warn_delta:
        verdict = "WARN"
    else:
        verdict = "FAIL"
    return {
        "render_duration_sec": round(float(render_duration_sec), 3),
        "narration_audio_sec": round(float(narration_audio_sec), 3),
        "audio_visual_delta_sec": delta,
        "tail_added_sec": round(float(tail_added_sec), 3),
        "tail_margin_sec": AUDIO_TAIL_MARGIN_SEC,
        "tail_buffer_sec": AUDIO_TAIL_REPAIR_BUFFER_SEC,
        "pass_delta_sec": pass_delta,
        "warn_delta_sec": warn_delta,
        "verdict": verdict,
        "per_scene_padding_sec": list(per_scene_padding_sec or []),
        "tail_repair_distribution": list(tail_repair_distribution or []),
    }
