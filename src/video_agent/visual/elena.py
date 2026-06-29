"""Elena presenter cue planning (pure, deterministic) — long-form, simple rule.

Builds the frame-accurate ``elena_cues.json`` document from the scene plan. Elena
is a brand-support overlay (NOT evidence).

Simple per-scene rule (NO frequency / cadence / visibility band — every eligible
scene gets exactly one Elena cue spanning that scene):

* **Hidden** (no cue): the hard-hidden set ``{checklist, quote, cta}`` (no
  annotation can override), any scene with ``scene.elena.mode == "hidden"``, and a
  ``warning`` whose on-screen label is wordy (> ``_WARNING_LABEL_MAX_WORDS``)
  unless an annotation forces it.
* **Elena large** (``large`` + ``talk-emphasis``, 384px): ``hook`` and an eligible
  ``warning`` (short label or annotation-forced).
* **Elena small circle** (``circle`` + ``talk-neutral``, 240px): ``subtitle`` (and
  any other non-hidden layout) — the small overlay clears the centered caption band.
* An explicit ``scene.elena.treatment``/``variant`` annotation overrides the size /
  asset for that scene.

Each cue starts at its scene start and lasts the scene duration, capped to the
clip's playable length (clip ~10s minus the 1s entry trim). Each cue trims the
clip's first second so Elena enters mid-sentence. Position: bottom-right, clear of
the subtitle band. Deterministic by layout + duration + annotation.

Pure: no IO, no LLM, no provider. Independent of ``video_agent.shorts``.
"""

from __future__ import annotations

import math
from typing import Any

SCHEMA_VERSION = 1

# The only two Elena assets (1280x720, 24fps, ~10s, audio always muted at render).
ELENA_ASSETS = {
    "talk-neutral": "assets/elena/ELENA_TALK_NEUTRAL.mp4",
    "talk-emphasis": "assets/elena/ELENA_TALK_EMPHASIS.mp4",
}
# Both Elena clips are ~10s. A cue plays from ``source_trim_frames`` onward, so
# ``trim + duration`` must stay within this or the tail freezes on the last frame.
_CLIP_LEN_SEC = 10.0

# Memory-beat layouts → large/emphasis treatment (warning handled separately).
_EMPHASIS_LAYOUTS = frozenset({"hook"})

# Elena-specific hard-hidden set (NOT the background GRAPHIC_LAYOUTS): these
# always hide Elena and no annotation can re-enable them. ``warning`` is
# deliberately excluded — it is content-aware (see ``_warning_allows_elena``).
ELENA_HARD_HIDDEN = frozenset({"checklist", "quote", "cta"})

# A warning's on-screen label must be this short (in words) to host a LARGE Elena.
_WARNING_LABEL_MAX_WORDS = 8

# Skip each clip's first second so Elena is already mid-sentence on entry (the
# raw clips open with a beat of silence/settling). Non-destructive: applied at
# render via the cue's ``source_trim_frames`` → Remotion ``trimBefore``.
_SOURCE_TRIM_SEC = 1.0


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


def _label_word_count(scene: dict[str, Any]) -> int:
    """Word count of the scene's short on-screen label (``on_screen_text`` first,
    then ``caption``). Used only to gate the content-aware warning treatment."""
    for key in ("on_screen_text", "caption"):
        text = str(scene.get(key) or "").strip()
        if text:
            return len(text.split())
    return 0


def _annotation_forces_large(ann: dict[str, Any]) -> bool:
    """An explicit annotation that asks Elena to appear LARGE / talking."""
    if str(ann.get("treatment") or "").strip().lower() == "large":
        return True
    return str(ann.get("mode") or "").strip().lower() == "talking"


def _warning_allows_elena(scene: dict[str, Any], ann: dict[str, Any]) -> bool:
    """Content-aware warning gate: a short label (<=8 words) hosts Elena; an
    explicit annotation overrides; a wordy warning hides."""
    if _annotation_forces_large(ann):
        return True
    words = _label_word_count(scene)
    return 0 < words <= _WARNING_LABEL_MAX_WORDS


def _eligible(scene: dict[str, Any], ann: dict[str, Any]) -> bool:
    """Whether this scene shows Elena at all.

    ``checklist``/``quote``/``cta`` always hide (annotation cannot override).
    ``elena.mode == "hidden"`` hides any other scene. ``warning`` is gated by
    :func:`_warning_allows_elena`. Everything else (hook, subtitle, untyped) shows.
    """
    layout = _layout(scene)
    if layout in ELENA_HARD_HIDDEN:
        return False
    if str(ann.get("mode") or "").strip().lower() == "hidden":
        return False
    if layout == "warning":
        return _warning_allows_elena(scene, ann)
    return True


def _treatment_for(scene: dict[str, Any], ann: dict[str, Any]) -> str:
    """Elena **size** for an eligible scene.

    ``subtitle`` (and any other non-hidden layout) → ``circle`` so the small
    overlay clears the centered caption band; ``hook`` and an eligible ``warning``
    → ``large``. An explicit annotation ``treatment`` wins.
    """
    treatment = str(ann.get("treatment") or "").strip().lower()
    if treatment in {"circle", "large"}:
        return treatment
    layout = _layout(scene)
    if layout in _EMPHASIS_LAYOUTS or layout == "warning":
        return "large"
    return "circle"


def _variant_for(treatment: str, ann: dict[str, Any]) -> str:
    """Asset for a cue: ``large`` → emphasis clip, ``circle`` → neutral clip; an
    explicit annotation ``variant`` wins."""
    variant = str(ann.get("variant") or "").strip().lower()
    if variant in ELENA_ASSETS:
        return variant
    return "talk-emphasis" if treatment == "large" else "talk-neutral"


def build_elena_cues(
    scene_doc: dict[str, Any],
    channel_config: dict[str, Any],
    fps: int,
    *,
    job_id: str | None = None,
    mode: str = "report_only",
) -> dict[str, Any]:
    """Build the schema-v1 ``elena_cues`` document — one cue per eligible scene.

    No frequency/cadence/band logic: every eligible scene (see :func:`_eligible`)
    gets exactly one cue, at the scene's start, lasting the FULL scene duration so
    Elena never cuts off mid-sentence (the renderer loops the ~10s clip to fill it).
    Size/asset come from :func:`_treatment_for` / :func:`_variant_for`. Hidden
    scenes emit no cue.
    """
    scenes = list((scene_doc or {}).get("scenes") or [])
    fps = int(fps)
    job = job_id or str((scene_doc or {}).get("job_id") or "")
    source_trim = int(round(_SOURCE_TRIM_SEC * fps))

    cues: list[dict[str, Any]] = []
    cursor = 0
    for scene in scenes:
        seg_start = cursor
        dur = _frames(scene.get("duration_sec"), fps)
        cursor += dur
        ann = _elena_annotation(scene)
        if dur <= 0 or not _eligible(scene, ann):
            continue
        treatment = _treatment_for(scene, ann)
        variant = _variant_for(treatment, ann)
        cues.append(
            {
                "start_frame": seg_start,
                # Span the FULL scene so Elena never cuts off mid-sentence. The
                # ~10s clip is shorter than most scenes, so the renderer LOOPS it
                # (OffthreadVideo loop) to fill this duration — Elena stays present
                # edge-to-edge of her scene instead of vanishing partway through.
                "duration_frames": dur,
                "mode": "talking",
                "treatment": treatment,
                "variant": variant,
                "position": "bottom-right",
                "asset_ref": ELENA_ASSETS[variant],
                "source_trim_frames": source_trim,
                "reason": str(ann.get("reason") or f"{treatment} on {_layout(scene)}"),
            }
        )
    total_frames = cursor

    talking_frames = sum(c["duration_frames"] for c in cues)
    visible_pct = round(100.0 * talking_frames / total_frames, 2) if total_frames else 0.0
    metrics = {
        "appearance_count": len(cues),
        "talking_pct": visible_pct,
        "hidden_pct": round(100.0 - visible_pct, 2) if total_frames else 0.0,
        "visible_pct": visible_pct,
    }

    # No frequency/band gate any more — the cue set is a direct function of the
    # scene layouts, so QA is informational and always PASS. ``errors``/``warnings``
    # kept for shape compatibility with consumers.
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job,
        "generation_mode": mode,
        "fps": fps,
        "total_frames": total_frames,
        "cues": cues,
        "metrics": metrics,
        "qa": {"verdict": "PASS", "errors": [], "warnings": []},
    }
