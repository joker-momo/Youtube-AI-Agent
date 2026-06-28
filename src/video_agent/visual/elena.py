"""Elena presenter cue planning (pure, deterministic) — long-form v3 §6.

Builds the frame-accurate ``elena_cues.json`` document from the scene plan. Elena
is a brand-support overlay (NOT evidence): the editorial priority is
``evidence/demo > B-roll > graphic/text > Elena``.

Hard rules baked in (spec §6 + Part-1 v3):

* Only two real assets exist → modes are ``talking`` and ``hidden`` only
  (**IDLE removed**). ``hidden`` emits no cue (the renderer does not mount).
* Each talking appearance is 5–10s; appearances are placed on a deterministic
  **cadence** (target gap ~21s) so the aggregate ``visible_pct`` lands inside the
  [20, 30]% band; gaps stay **>=15s** (never adjacent) and **<=30s**.
* Consecutive talking cues never reuse the same asset/variant.
* The Elena **hard-hidden** set is exactly ``{checklist, quote, cta}`` — these
  always hide and **no annotation can override** them.
* ``warning`` is **content-aware**: a warning with a SHORT on-screen label
  (<=8 words) may host a ``large`` + ``TALK_EMPHASIS`` Elena; a wordy warning
  hides. A ``scene.elena`` annotation (``treatment="large"`` / ``mode="talking"``)
  wins over that forced-hidden default for warning.
* ``large`` + ``TALK_EMPHASIS`` for memory beats (hook / short warning / explicit
  annotation); ``circle`` + ``TALK_NEUTRAL`` otherwise.
* Selection is **deterministic by scene durations + layouts + annotations**, so
  renders reproduce. Cue placement may start mid-scene to hold the cadence; it
  never changes the render span (composition length).

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

# Spacing / cadence band (seconds). Gaps live in [MIN, MAX]; the planner aims for
# TARGET so a ~APPEARANCE-long cue yields ~25% visible (centre of the band).
_MIN_GAP_SEC = 15.0
_MAX_GAP_SEC = 30.0
_TARGET_GAP_SEC = 21.0
_TARGET_APPEARANCE_SEC = 7.0
_VISIBLE_BAND_PCT = (20.0, 30.0)

# Skip each clip's first second so Elena is already mid-sentence on entry (the
# raw clips open with a beat of silence/settling). Non-destructive: applied at
# render via the cue's ``source_trim_frames`` → Remotion ``trimBefore``.
_SOURCE_TRIM_SEC = 1.0

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
    """Whether this scene may host a talking Elena cue at all.

    ``checklist``/``quote``/``cta`` always hide (annotation cannot override).
    ``elena.mode == "hidden"`` hides any other scene. ``warning`` is gated by
    :func:`_warning_allows_elena`. Everything else (hook, subtitle, untyped) is
    eligible; the cadence decides which eligible scenes actually host a cue.
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
    """Elena **size** for an eligible cue (decoupled from the asset/variant).

    ``subtitle`` is the running word-highlight screen whose centered caption band
    reaches the bottom-right, so Elena stays ``circle`` there to clear it. ``hook``
    and an eligible ``warning`` are memory beats → ``large`` (their overlays are not
    the centered band, so a large overlay does not collide). An explicit annotation
    ``treatment`` always wins.
    """
    treatment = str(ann.get("treatment") or "").strip().lower()
    if treatment in {"circle", "large"}:
        return treatment
    layout = _layout(scene)
    if layout == "subtitle":
        return "circle"
    if layout in _EMPHASIS_LAYOUTS or layout == "warning":
        return "large"
    return "circle"


def _next_variant(ann: dict[str, Any], last_variant: str | None) -> str:
    """Asset (variant) for a cue. Decoupled from size: explicit annotation wins,
    otherwise alternate so two consecutive cues never reuse the same asset — this
    lets ``subtitle`` stay ``circle`` on every appearance while assets still
    alternate."""
    variant = str(ann.get("variant") or "").strip().lower()
    if variant not in ELENA_ASSETS:
        variant = "talk-neutral" if last_variant == "talk-emphasis" else "talk-emphasis"
    if variant == last_variant:  # annotation collided with the previous asset → flip
        variant = "talk-neutral" if variant == "talk-emphasis" else "talk-emphasis"
    return variant


def _appearance_frames(treatment: str, available: int, fps: int) -> int:
    """Cue length for ``treatment``: aim for the target, clamp to the per-treatment
    bounds, then to the frames available before the scene ends."""
    lo_sec, hi_sec = _APPEARANCE_BOUNDS_SEC[treatment]
    target = min(max(_TARGET_APPEARANCE_SEC, lo_sec), hi_sec)
    length = min(int(round(target * fps)), int(hi_sec * fps), available)
    return length


def build_elena_cues(
    scene_doc: dict[str, Any],
    channel_config: dict[str, Any],
    fps: int,
    *,
    job_id: str | None = None,
    mode: str = "report_only",
) -> dict[str, Any]:
    """Build the schema-v1 ``elena_cues`` document from the scene plan.

    Deterministic cadence placement: cues are spaced ``_TARGET_GAP_SEC`` apart
    (clamped to [MIN, MAX]) so the aggregate ``visible_pct`` lands in the [20, 30]%
    band. A cue may start mid-scene to hold the cadence but never crosses its
    scene's end (treatment stays tied to the host scene) and never lands on a
    hard-hidden scene.
    """
    scenes = list((scene_doc or {}).get("scenes") or [])
    fps = int(fps)
    job = job_id or str((scene_doc or {}).get("job_id") or "")
    min_gap = _MIN_GAP_SEC * fps
    max_gap = _MAX_GAP_SEC * fps
    target_gap = _TARGET_GAP_SEC * fps

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
    source_trim = int(round(_SOURCE_TRIM_SEC * fps))
    # Cap a cue so trim + playback never runs past the end of the ~10s clip.
    max_play_frames = int(_CLIP_LEN_SEC * fps) - source_trim

    for index, scene in enumerate(scenes):
        seg_start, seg_end = boundaries[index]
        ann = _elena_annotation(scene)
        if not _eligible(scene, ann):
            continue

        treatment = _treatment_for(scene, ann)
        lo_sec, _ = _APPEARANCE_BOUNDS_SEC[treatment]
        lo_frames = int(lo_sec * fps)

        # Place as many cadence cues as fit INSIDE this scene: a long scene hosts
        # several (each a target_gap after the last) so one long subtitle never
        # leaves a gap above the 30s max. A short scene hosts exactly one — the
        # second candidate start already falls past the scene end. The first cue of
        # the video starts at the scene start; every later one is target_gap after
        # the previous cue's end.
        while True:
            start = seg_start if not cues else max(seg_start, last_talk_end + int(target_gap))
            # The cue must fit (>= the treatment minimum) before the scene ends.
            if start > seg_end - lo_frames:
                break
            available = seg_end - start
            duration_frames = min(_appearance_frames(treatment, available, fps), max_play_frames)
            if duration_frames < lo_frames:
                break
            variant = _next_variant(ann, last_variant)
            cues.append(
                {
                    "start_frame": start,
                    "duration_frames": duration_frames,
                    "mode": "talking",
                    "treatment": treatment,
                    "variant": variant,
                    "position": "bottom-right",
                    "asset_ref": ELENA_ASSETS[variant],
                    "source_trim_frames": source_trim,
                    "reason": str(ann.get("reason") or f"{treatment} appearance on {_layout(scene)}"),
                }
            )
            last_talk_end = start + duration_frames
            last_variant = variant

    talking_frames = sum(c["duration_frames"] for c in cues)
    gaps = [
        cues[i]["start_frame"] - (cues[i - 1]["start_frame"] + cues[i - 1]["duration_frames"])
        for i in range(1, len(cues))
    ]
    visible_pct = round(100.0 * talking_frames / total_frames, 2) if total_frames else 0.0
    metrics = {
        "appearance_count": len(cues),
        "talking_pct": visible_pct,
        "hidden_pct": round(100.0 - visible_pct, 2) if total_frames else 0.0,
        "visible_pct": visible_pct,
        "min_gap_sec": round(min(gaps) / fps, 2) if gaps else None,
        "max_gap_sec": round(max(gaps) / fps, 2) if gaps else None,
    }

    # ----- QA --------------------------------------------------------------- #
    # Band violations are HARD errors (verdict FAIL in any mode): the [20,30]%
    # visibility band and the 30s max-gap are quality gates, not advisory. The
    # structural invariants (adjacent asset / spacing / overflow) stay warnings
    # that only flip the verdict under ``enforced`` mode (legacy behaviour).
    errors: list[str] = []
    if total_frames and not (_VISIBLE_BAND_PCT[0] <= visible_pct <= _VISIBLE_BAND_PCT[1]):
        errors.append(f"visible_pct_out_of_band:{visible_pct}")
    for i in range(1, len(cues)):
        if gaps[i - 1] > max_gap:
            errors.append(f"gap_above_max:{cues[i]['start_frame']}")

    warnings: list[str] = []
    for i in range(1, len(cues)):
        if cues[i]["variant"] == cues[i - 1]["variant"]:
            warnings.append(f"adjacent_same_asset:{cues[i]['start_frame']}")
        if gaps[i - 1] < min_gap:
            warnings.append(f"gap_below_min:{cues[i]['start_frame']}")
    for c in cues:
        if c["start_frame"] + c["duration_frames"] > total_frames:
            warnings.append(f"cue_exceeds_total:{c['start_frame']}")

    verdict = "FAIL" if (errors or (warnings and mode == "enforced")) else "PASS"

    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job,
        "generation_mode": mode,
        "fps": fps,
        "total_frames": total_frames,
        "cues": cues,
        "metrics": metrics,
        "qa": {"verdict": verdict, "errors": errors, "warnings": warnings},
    }
