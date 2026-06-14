"""Regression guards for the visual contact-sheet generator.

Bug: scene backgrounds became .mp4 clips, but the generator passed them
straight to PIL.Image.open, which raised UnidentifiedImageError for every
scene and rendered an all-"missing image" sheet. _resolve_still must turn a
video background into a still (sibling preview, else extracted frame).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from PIL import Image

from video_agent.stages.visual_contact_sheet import (
    _resolve_still,
    create_visual_contact_sheet,
)

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _make_png(path):
    Image.new("RGB", (32, 32), "#112233").save(path)


def _make_mp4(path):
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
            "-frames:v", "1", str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_resolve_still_returns_image_background_directly(tmp_path):
    img = tmp_path / "s01.png"
    _make_png(img)
    assert _resolve_still(img, tmp_path) == img


def test_resolve_still_prefers_sibling_preview_for_video(tmp_path):
    video = tmp_path / "s02.mp4"
    video.write_bytes(b"not really a video")  # never opened; preview wins
    preview = tmp_path / "s02_preview.jpg"
    _make_png(preview)
    assert _resolve_still(video, tmp_path) == preview


@ffmpeg_required
def test_resolve_still_extracts_frame_when_no_preview(tmp_path):
    video = tmp_path / "s03.mp4"
    _make_mp4(video)
    still = _resolve_still(video, tmp_path)
    assert still is not None
    with Image.open(still) as frame:  # must be a real, openable image
        frame.verify()


def test_resolve_still_missing_background_returns_none(tmp_path):
    assert _resolve_still(tmp_path / "nope.mp4", tmp_path) is None


@ffmpeg_required
def test_contact_sheet_renders_video_scenes_without_missing_tiles(tmp_path):
    # A scene whose background is a video must still produce a non-blank tile.
    video = tmp_path / "s01.mp4"
    _make_mp4(video)
    visual_review = {
        "scenes": [
            {
                "scene_id": "s01",
                "background": str(video),
                "provider": "pexels",
                "on_screen_text": "HOLA",
                "selection": {"score": 78},
                "qa": {"status": "WARN"},
            }
        ]
    }
    out = create_visual_contact_sheet(tmp_path, visual_review)
    assert out.exists()
    with Image.open(out) as sheet:
        sheet.load()
        # The extracted frame is red; assert that red reached the canvas, i.e.
        # the tile is the frame and not the grey "missing image" placeholder.
        colors = sheet.convert("RGB").getcolors(maxcolors=100000) or []
        assert any(r > 150 and g < 90 and b < 90 for _, (r, g, b) in colors)
