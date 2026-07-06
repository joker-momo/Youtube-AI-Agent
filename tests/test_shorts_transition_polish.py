"""Source-inspection contracts for the Shorts anti-slideshow polish.

The repo has no JS test runner, so these verify the TSX source patterns in
Python (mirrors test_shorts_remotion_contract). They guard the two levers that
remove the hard-cut "slide flip" feel:

  A) scene-to-scene cross-dissolve (no timeline shrink -> audio stays synced)
  B) a deliberate push-in on baked graphic/infographic cards so they never
     read as a frozen slide.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHORT_VIDEO = REPO / "remotion/src/ShortVideo.tsx"
SHORT_BG = REPO / "remotion/src/shorts/ShortBackground.tsx"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── A: cross-dissolve between scenes, timeline grid preserved ────────────────

def test_short_video_has_scene_crossfade():
    src = _read(SHORT_VIDEO)
    assert "SceneCrossfade" in src, "ShortVideo must wrap scenes in a cross-dissolve"
    assert "SHORT_SCENE_XFADE" in src
    # The incoming scene fades in (opacity ramp), the outgoing scene extends.
    assert "interpolate(frame, [0, fadeFrames], [0, 1]" in src


def test_crossfade_preserves_scene_start_grid_for_audio_sync():
    src = _read(SHORT_VIDEO)
    # cursor advances by the TRUE per-scene duration (not the extended render
    # length) so scene starts — and the single narration track + subtitles —
    # never shift. Only the render length is extended past the boundary.
    assert "cursor += durFrames;" in src
    assert "durFrames + (isLast ? 0 : SHORT_SCENE_XFADE)" in src
    # The Sequence renders the extended length, not the raw duration.
    assert "durationInFrames={renderFrames}" in src


# ── B: baked graphic cards get a deliberate push-in, not a frozen drift ───────

def test_short_video_marks_generated_graphic_scenes():
    src = _read(SHORT_VIDEO)
    assert "isGraphic={isGeneratedGraphic}" in src


def test_short_background_pushes_in_on_graphic_cards():
    src = _read(SHORT_BG)
    assert "isGraphic" in src
    # A graphic card gets a real slow push-in (>= ~10% zoom), overriding the weak
    # LLM text_pop drift, so the static card stops reading as a slide.
    assert "lerp(1.0, 1.1, p)" in src
    assert "shortMotion(motion, progress, isGraphic)" in src
