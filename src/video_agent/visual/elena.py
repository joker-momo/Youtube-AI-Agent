"""Elena presenter cue planning (pure, deterministic) — long-form v2 §6.

Builds the frame-accurate ``elena_cues.json`` document from the scene plan. Elena
is a brand-support overlay (NOT evidence): the editorial priority is
``evidence/demo > B-roll > graphic/text > Elena``.

Hard rules baked in (spec §6):

* Only two real assets exist → modes are ``talking`` and ``hidden`` only
  (**IDLE removed**). ``hidden`` emits no cue (the renderer does not mount).
* Each talking appearance is 5–10s; appearances are spaced **≥15s** apart, so two
  talking cues are never adjacent.
* Consecutive talking cues never reuse the same asset/variant.
* Graphic / evidence / checklist scenes force ``hidden`` (fail-safe: when in
  doubt, hide).
* ``large`` + ``TALK_EMPHASIS`` for memory beats (hook / core rule / conclusion);
  ``circle`` + ``TALK_NEUTRAL`` otherwise.
* Selection is **deterministic by job_id + scene index**, so renders reproduce.

Pure: no IO, no LLM, no provider. Independent of ``video_agent.shorts``.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from video_agent.visual.spans import GRAPHIC_LAYOUTS

SCHEMA_VERSION = 1

# The only two Elena assets (1280x720, 24fps, ~10s, audio always muted at render).
ELENA_ASSETS = {
    "talk-neutral": "assets/elena/ELENA_TALK_NEUTRAL.mp4",
    "talk-emphasis": "assets/elena/ELENA_TALK_EMPHASIS.mp4",
}
# Memory-beat layouts → large/emphasis treatment.
_EMPHASIS_LAYOUTS = frozenset({"hook"})

_MIN_GAP_SEC = 15.0
# Per-treatment appearance length (infographic): circle 6-10s, large 5-8s.
_APPEARANCE_BOUNDS_SEC = {"circle": (6.0, 10.0), "large": (5.0, 8.0)}


def _frames(duration_sec: Any, fps: int) -> int:
    try:
        sec = float(duration_sec or 0.0)
    except (TypeError, ValueError):
        sec = 0.0
    return math.floor(sec * fps + 0.5)


def _layout(scene: dict[str, Any]) -> str:
    return str(scene.get("layout") or "").strip().lower()


def _elena_annotation(scene: dict[str, Any]) -> dict[str, Any]:
    ann = scene.get("elena")
    return ann if isinstance(ann, dict) else {}


def _deterministic_bit(job_id: str, index: int, salt: str = "") -> int:
    """Reproducible per (job, scene) pseudo-random byte in [0, 255]."""
    digest = hashlib.sha256(f"{job_id}:{index}:{salt}".encode("utf-8")).digest()
    return digest[0]


def _treatment_for(scene: dict[str, Any], ann: dict[str, Any]) -> tuple[str, str]:
    """Return ``(treatment, variant)`` for a talking cue."""
    treatment = str(ann.get("treatment") or "").strip().lower()
    variant = str(ann.get("variant") or "").strip().lower()
    if treatment in {"circle", "large"} and variant in ELENA_ASSETS:
        return treatment, variant
    if _layout(scene) in _EMPHASIS_LAYOUTS:
        return "large", "talk-emphasis"
    return "circle", "talk-neutral"


def _forces_hidden(scene: dict[str, Any], ann: dict[str, Any]) -> bool:
    if ann.get("mode") == "hidden":
        return True
    # Graphic layouts (checklist/warning/quote/cta) are evidence/label-like → hide.
    return _layout(scene) in GRAPHIC_LAYOUTS


def _wants_talking(scene: dict[str, Any], ann: dict[str, Any], job_id: str, index: int) -> bool:
    if ann.get("mode") == "talking":
        return True
    if _layout(scene) in _EMPHASIS_LAYOUTS:
        return True  # hook is a memory beat → Elena emphasis
    # Otherwise a sparse, deterministic subset of plain scenes (keep budget low).
    return _deterministic_bit(job_id, index, "talk") % 4 == 0


def build_elena_cues(
    scene_doc: dict[str, Any],
    channel_config: dict[str, Any],
    fps: int,
    *,
    job_id: str | None = None,
    mode: str = "report_only",
) -> dict[str, Any]:
    """Build the schema-v1 ``elena_cues`` document from the scene plan."""
    scenes = list((scene_doc or {}).get("scenes") or [])
    fps = int(fps)
    job = job_id or str((scene_doc or {}).get("job_id") or "")
    min_gap = _MIN_GAP_SEC * fps

    boundaries: list[tuple[int, int]] = []
    cursor = 0
    for scene in scenes:
        dur = _frames(scene.get("duration_sec"), fps)
        boundaries.append((cursor, cursor + dur))
        cursor += dur
    total_frames = cursor

    cues: list[dict[str, Any]] = []
    last_talk_end = -(10**9)
    last_variant: str | None = None

    for index, scene in enumerate(scenes):
        from_frame, end_frame = boundaries[index]
        scene_frames = end_frame - from_frame
        ann = _elena_annotation(scene)

        if _forces_hidden(scene, ann):
            continue
        if not _wants_talking(scene, ann, job, index):
            continue
        # Spacing: never within 15s of the previous talking appearance.
        if from_frame - last_talk_end < min_gap:
            continue

        treatment, variant = _treatment_for(scene, ann)
        # Never reuse the same asset on two consecutive appearances.
        if variant == last_variant:
            variant = "talk-neutral" if variant == "talk-emphasis" else "talk-emphasis"
            treatment = "circle" if variant == "talk-neutral" else "large"

        # Appearance length is per-treatment (circle 6-10s, large 5-8s); skip a
        # scene that is too short to host the minimum for its treatment.
        lo_sec, hi_sec = _APPEARANCE_BOUNDS_SEC[treatment]
        if scene_frames < lo_sec * fps:
            continue
        duration_frames = min(scene_frames, int(hi_sec * fps))

        cues.append(
            {
                "start_frame": from_frame,
                "duration_frames": duration_frames,
                "mode": "talking",
                "treatment": treatment,
                "variant": variant,
                "position": "bottom-right",
                "asset_ref": ELENA_ASSETS[variant],
                "source_trim_frames": 0,
                "reason": str(ann.get("reason") or f"{treatment} appearance on {_layout(scene)}"),
            }
        )
        last_talk_end = from_frame + duration_frames
        last_variant = variant

    talking_frames = sum(c["duration_frames"] for c in cues)
    gaps = [
        cues[i]["start_frame"] - (cues[i - 1]["start_frame"] + cues[i - 1]["duration_frames"])
        for i in range(1, len(cues))
    ]
    metrics = {
        "appearance_count": len(cues),
        "talking_pct": round(100.0 * talking_frames / total_frames, 2) if total_frames else 0.0,
        "hidden_pct": round(100.0 * (total_frames - talking_frames) / total_frames, 2) if total_frames else 0.0,
        "visible_pct": round(100.0 * talking_frames / total_frames, 2) if total_frames else 0.0,
        "min_gap_sec": round(min(gaps) / fps, 2) if gaps else None,
    }

    # Invariant self-audit (gives enforced mode teeth): no two consecutive cues
    # share an asset; every gap respects the minimum; nothing exceeds the comp.
    warnings: list[str] = []
    for i in range(1, len(cues)):
        if cues[i]["variant"] == cues[i - 1]["variant"]:
            warnings.append(f"adjacent_same_asset:{cues[i]['start_frame']}")
        if gaps[i - 1] < min_gap:
            warnings.append(f"gap_below_min:{cues[i]['start_frame']}")
    for c in cues:
        if c["start_frame"] + c["duration_frames"] > total_frames:
            warnings.append(f"cue_exceeds_total:{c['start_frame']}")

    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job,
        "generation_mode": mode,
        "fps": fps,
        "total_frames": total_frames,
        "cues": cues,
        "metrics": metrics,
        "qa": {"verdict": "FAIL" if (warnings and mode == "enforced") else "PASS", "warnings": warnings},
    }
