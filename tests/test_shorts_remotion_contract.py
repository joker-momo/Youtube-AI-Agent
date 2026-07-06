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
ROOT = REPO / "remotion/src/Root.tsx"
SHORT_MEDIA_LAYER = REPO / "remotion/src/shorts/ShortMediaLayer.tsx"
VISUAL_TIMELINE = REPO / "remotion/src/shorts/VisualTimeline.tsx"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── Fix A: frame-accurate media background, no black lead-in ────────────────

def test_short_background_uses_remotion_media_video():
    src = _read(SHORT_BG)
    assert "@remotion/media" in src, "ShortBackground must use @remotion/media"
    assert "MediaVideo" in src
    assert "<MediaVideo" in src
    assert 'objectFit="cover"' in src
    # No bare Remotion <Video> playback for Shorts backgrounds.
    assert "<Video" not in src, "Shorts background must not use bare <Video>"


def test_short_media_layer_uses_remotion_media_cover_prop():
    src = _read(SHORT_MEDIA_LAYER)
    assert "@remotion/media" in src, "ShortMediaLayer must use @remotion/media"
    assert src.count('objectFit="cover"') >= 2
    assert "MediaVideo src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}}" not in src


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
GRAPHICS_DIR = REPO / "remotion/src/graphics"
LEGACY_GRAPHIC_PROPS = (
    REPO / "remotion/test-props/graphic-mvp-short.json",
    REPO / "remotion/test-props/graphic-kit-phase15-short.json",
    REPO / "remotion/test-props/graphic-theme-preview-short.json",
)

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


def test_legacy_remotion_graphics_are_removed():
    src = _read(SHORT_VIDEO)
    root = _read(ROOT)
    assert not GRAPHICS_DIR.exists()
    assert not any(path.exists() for path in LEGACY_GRAPHIC_PROPS)
    assert "GraphicSceneRenderer" not in src
    assert "./graphics/" not in src
    assert "GraphicMvpPreview" not in root
    assert "GraphicKitPhase15Preview" not in root
    assert "GraphicThemePreview" not in root


def test_short_video_rejects_unconverted_graphic_layouts():
    src = _read(SHORT_VIDEO)
    assert "startsWith('graphic_')" in src
    assert "must be converted to a ChatGPT image-backed layout" in src


def test_layout_payload_title_fallback_source_contract():
    src = _read(SHORT_LAYOUTS)
    helper = src[src.index("getPrimarySceneText"):]
    helper = helper[: helper.index("\n}") + 2]
    # Fallback chain still references layout_payload?.title.
    assert "title" in helper


# ── PR B: compiled visual schedule handoff drives timeline rendering ────────

def test_root_metadata_prefers_compiled_schedule_duration():
    src = _read(ROOT)
    assert "visual_schedule?.total_duration_in_frames" in src
    assert "render?.duration_in_frames" in src
    schedule_idx = src.find("visual_schedule?.total_duration_in_frames")
    render_idx = src.find("render?.duration_in_frames")
    sec_idx = src.find("duration_sec * fps")
    assert -1 not in (schedule_idx, render_idx, sec_idx)
    assert schedule_idx < render_idx < sec_idx


def test_short_video_routes_schedule_to_visual_timeline():
    src = _read(SHORT_VIDEO)
    assert "VisualTimeline" in src
    assert "props.visual_schedule" in src
    assert "sceneBoundaries" in src
    assert "Missing visual schedule boundary" in src


def test_visual_timeline_renders_compiled_tracks_with_runtime_guards():
    src = _read(VISUAL_TIMELINE)
    assert "schedule.schema_version !== 2" in src
    assert "track.loop_policy !== 'forbid'" in src
    assert "track.from_frame" in src
    assert "track.duration_in_frames" in src
    assert "track.trim_before_in_frames" in src
    assert "track.trim_end_in_frames" in src


def test_short_media_layer_uses_remotion_frame_trims():
    src = _read(SHORT_MEDIA_LAYER)
    assert "trimBefore={trimBefore}" in src
    assert "trimAfter={trimAfter}" in src
    assert "startFrom" not in src
