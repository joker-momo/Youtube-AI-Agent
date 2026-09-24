"""Legacy long-form renderer honors selected source windows (spec 2026-09-24).

``visual.span_planning.mode`` stays ``report_only``, so ``ChannelVideo``'s
per-scene ``SceneView`` is the production background path. A scene carrying
``source_trim_*`` bounds must start its native clip at the selected source frame
and keep real footage through the crossfade lead; scenes without trims keep the
legacy frame-zero start.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import _continuity_fixture as cf  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REMOTION = REPO / "remotion"
CHANNEL_VIDEO = REMOTION / "src" / "ChannelVideo.tsx"
RENDER_PROPS = REMOTION / "src" / "render-props.ts"
FIXTURE = REMOTION / "public" / "test_fixtures" / "continuity_marker_landscape.mp4"
FIXTURE_REF = "test_fixtures/continuity_marker_landscape.mp4"

FPS = 30
SCENE_FRAMES = 60
XFADE = 15
TRIM_BEFORE = 42
TRIM_END = TRIM_BEFORE + SCENE_FRAMES + XFADE  # selector window incl. crossfade lead


def _render_available() -> bool:
    return bool(
        shutil.which("node")
        and shutil.which("ffmpeg")
        and (REMOTION / "node_modules" / "@remotion" / "renderer").exists()
    )


# --------------------------------------------------------------------------- #
# Source contract
# --------------------------------------------------------------------------- #
def test_scene_asset_refs_type_declares_optional_trim_fields() -> None:
    src = RENDER_PROPS.read_text(encoding="utf-8")
    match = re.search(r"asset_refs: \{([^}]*)\};", src)
    assert match, "scene asset_refs type not found"
    body = match.group(1)
    for key in ("source_trim_before_in_frames", "source_trim_end_in_frames", "source_trim_timebase_fps"):
        assert f"{key}?: number" in body


def test_only_the_legacy_native_video_branch_receives_trim_props() -> None:
    src = CHANNEL_VIDEO.read_text(encoding="utf-8")
    assert src.count("trimBefore=") == 1
    assert src.count("trimAfter=") == 1
    native_start = src.index(") : scene.asset_refs.background.endsWith('.mp4') ? (")
    native_end = src.index("<Img", native_start)
    native_branch = src[native_start:native_end]
    assert "trimBefore={legacyTrimBefore}" in native_branch
    assert "trimAfter={legacyTrimAfter}" in native_branch
    # Graphic living-bg, intro/outro/disclaimer media mount no trims.
    assert src.index("trimBefore=") > native_start and src.index("trimBefore=") < native_end
    # Photo-backed MP4 (background_media_kind 'image') never trims.
    assert re.search(r"background_media_kind !== 'image'\s*\?\s*legacyTrimFrame", src)
    # Same normalization rule as the schedule timeline; trimAfter only when > trimBefore.
    assert "Math.round((value * compositionFps) / timebase)" in src
    assert "(!legacyTrimBefore || legacyTrimEnd > legacyTrimBefore)" in src


# --------------------------------------------------------------------------- #
# Frame-numbered render proof
# --------------------------------------------------------------------------- #
def _scene(sid: str, refs: dict) -> dict:
    return {
        "id": sid, "duration_sec": SCENE_FRAMES / FPS, "narration": "", "caption": "",
        "on_screen_text": "", "visual_type": "stock", "visual_prompt": "", "motion": "none",
        "layout": "subtitle", "asset_refs": refs, "layout_payload": {},
    }


def _props() -> dict:
    untrimmed = {"background": FIXTURE_REF, "background_media_kind": "video"}
    trimmed = {
        **untrimmed,
        "source_trim_before_in_frames": TRIM_BEFORE,
        "source_trim_end_in_frames": TRIM_END,
        "source_trim_timebase_fps": FPS,
    }
    total = 2 * SCENE_FRAMES
    return {
        "channel": {"id": "vida-plena-45", "name": "Vida Plena", "description": "fixture"},
        "style": {"palette": {"background": "#0b1020", "primary": "#fff", "secondary": "#ccc",
                              "accent": "#f5a", "text": "#fff"}},
        "render": {"fps": FPS, "resolution": "1920x1080", "duration_sec": total / FPS,
                   "duration_in_frames": total, "subtitles": {"enabled": False}},
        "scenes": [_scene("scene-01", untrimmed), _scene("scene-02", trimmed)],
        "audio": {"narration": None, "music": None},
        "seo": {"title": "t", "description": "d", "thumbnail_path": "x.jpg"},
        "branding": {"intro_sec": 0, "outro_sec": 0, "show_channel_name_overlay": False,
                     "logo_path": None},
    }


def _render(props: dict, tmp_path: Path) -> Path:
    props_path = tmp_path / "props.json"
    out_path = tmp_path / "out.mp4"
    props_path.write_text(json.dumps(props))
    try:
        subprocess.run(
            ["npx", "--prefix", str(REMOTION), "remotion", "render", "src/index.ts",
             "ChannelVideoStandard", str(out_path), f"--props={props_path}"],
            check=True, capture_output=True, cwd=str(REMOTION), timeout=600,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        pytest.fail(f"Remotion render FAILED (exit {exc.returncode}):\n{stderr[-4000:]}")
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"Remotion render TIMED OUT after {exc.timeout}s")
    assert out_path.exists(), "Remotion produced no output"
    return out_path


@pytest.mark.integration
@pytest.mark.skipif(not _render_available(), reason="render toolchain unavailable (node/ffmpeg/@remotion/renderer)")
def test_legacy_channel_render_starts_at_scene_trim_frame(tmp_path: Path) -> None:
    cf.generate_fixture(FIXTURE, width=1920, height=1080)
    vals = cf.decode_video_markers(_render(_props(), tmp_path))

    assert len(vals) == 2 * SCENE_FRAMES
    # Untrimmed scene keeps the legacy contract: source frame N at output frame N.
    fade_in = 18
    lead_start = SCENE_FRAMES - XFADE
    assert vals[fade_in:lead_start] == list(range(fade_in, lead_start))
    # Trimmed scene mounts XFADE frames early, so output frame F shows source
    # TRIM_BEFORE + (F - lead_start): it starts at the requested trim frame and
    # stays sequential through the scene, including the frames beyond
    # TRIM_BEFORE + SCENE_FRAMES that the crossfade lead pushes out.
    check = range(SCENE_FRAMES, SCENE_FRAMES + 51)
    expected = [TRIM_BEFORE + f - lead_start for f in check]
    actual = [vals[f] for f in check]
    assert actual == expected, list(zip(check, actual, expected, strict=True))[:8]
    assert actual[-1] > TRIM_BEFORE + SCENE_FRAMES
