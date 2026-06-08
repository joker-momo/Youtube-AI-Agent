"""Source-inspection contracts for the Shorts Remotion renderer.

The repo has no JS test runner, so these verify the TSX source patterns in
Python (spec v3 §0.1). They are contract/regression guards, not pixel checks.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHORT_BG = REPO / "remotion/src/shorts/ShortBackground.tsx"
SHORT_VIDEO = REPO / "remotion/src/ShortVideo.tsx"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── Fix A: frame-accurate background, no black lead-in ──────────────────────

def test_short_background_uses_offthread_video():
    src = _read(SHORT_BG)
    assert "OffthreadVideo" in src, "ShortBackground must use OffthreadVideo"
    assert "<OffthreadVideo" in src
    # No bare <Video> playback for Shorts backgrounds (causes black frame 0).
    assert "<Video" not in src, "Shorts background must not use bare <Video>"


def test_first_short_sequence_starts_at_frame_zero():
    src = _read(SHORT_VIDEO)
    # First scene sequence cursor starts at 0; no pre-roll/intro sequence.
    assert "let cursor = 0" in src or "cursor = 0" in src
    assert "introFrames" not in src, "Shorts must not add an intro pre-roll"


def test_first_scene_has_no_zero_opacity_fade_guard():
    # Regression guard: no first-scene fade-in from opacity 0 in Shorts renderer.
    bg = _read(SHORT_BG)
    vid = _read(SHORT_VIDEO)
    for src in (bg, vid):
        assert not re.search(r"opacity\s*:\s*0\b", src), "no opacity:0 fade-in on Shorts first frame"


# ── Fix B: on_screen_text must win over layout_payload.title everywhere ──────

SHORT_LAYOUTS = REPO / "remotion/src/shorts/ShortLayouts.tsx"
GRAPHIC = REPO / "remotion/src/graphics/GraphicSceneRenderer.tsx"

_PRIMARY_LAYOUTS = [
    "ShortHookLayout", "ShortPainLayout", "ShortTipLayout",
    "ShortChecklistLayout", "ShortMythLayout", "ShortQuoteLayout", "ShortCtaLayout",
]


def test_short_text_helper_exists():
    src = _read(SHORT_LAYOUTS)
    assert "getPrimarySceneText" in src, "shared primary-text helper must exist"
    # Helper must consult on_screen_text before layout_payload.title.
    helper = src[src.index("getPrimarySceneText"):]
    on_idx = helper.find("on_screen_text")
    title_idx = helper.find("title")
    assert on_idx != -1 and title_idx != -1 and on_idx < title_idx, \
        "helper must check on_screen_text before layout_payload.title"


def test_short_layouts_use_primary_text_helper():
    src = _read(SHORT_LAYOUTS)
    # Inverted priority patterns must be gone.
    assert "p.title || on_screen_text" not in src
    assert "layout_payload.title || on_screen_text" not in src
    assert "title || scene.on_screen_text" not in src
    # Every primary layout derives its title via the shared helper.
    assert src.count("getPrimarySceneText(") >= len(_PRIMARY_LAYOUTS)


def test_short_cta_uses_on_screen_text_priority():
    src = _read(SHORT_LAYOUTS)
    cta = src[src.index("ShortCtaLayout"):]
    cta = cta[: cta.index("SHORT_LAYOUTS")] if "SHORT_LAYOUTS" in cta else cta
    assert "getPrimarySceneText(on_screen_text" in cta
    for default in ("'COMENTA'", '"COMENTA"', "'CUÉNTAME'", "'GUÁRDALO'"):
        assert default not in cta, f"CTA must not hardcode {default} over on_screen_text"


def test_graphic_renderer_uses_on_screen_text_priority():
    src = _read(GRAPHIC)
    assert "on_screen_text" in src, "graphic renderer must consider on_screen_text"
    on_idx = src.find("on_screen_text")
    # on_screen_text referenced before the payload title is handed to visualPayload
    vp_idx = src.find("visualPayload")
    assert on_idx != -1 and on_idx < vp_idx, \
        "on_screen_text must override before visualPayload title is built"


def test_layout_payload_title_fallback_source_contract():
    src = _read(SHORT_LAYOUTS)
    helper = src[src.index("getPrimarySceneText"):]
    helper = helper[: helper.index("\n}") + 2]
    # Fallback chain still references layout_payload?.title.
    assert "title" in helper
