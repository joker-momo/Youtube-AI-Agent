"""Deterministic fixed-ROI continuity fixture (spec v3.2.3 §33 / v4.0.3 §37).

Generates a 1080x1920 @ 30fps MP4 where each SOURCE frame N carries a
machine-readable binary marker encoding N inside the ROI {x:40,y:40,w:360,h:180}.
The marker is pure black/white cells with two calibration cells (a known white
and a known black), so it decodes via adaptive thresholding even under the
renderer's uniform readability darken. No OCR.

Used by ``test_remotion_visual_timeline_continuity.py`` to prove that one native
clip plays continuously across scene boundaries without reset/skip/black.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# ROI per spec §33.1.
ROI_X, ROI_Y, ROI_W, ROI_H = 40, 40, 360, 180
FPS = 30
WIDTH, HEIGHT = 1080, 1920
DATA_BITS = 9  # encodes source frame index up to 511
N_CELLS = DATA_BITS + 2  # + white-calibration + black-calibration
SOURCE_FRAMES = 240  # >= trim_before(42) + composition(180) = 222


def _cell_bounds(i: int) -> tuple[int, int, int, int]:
    cw = ROI_W / N_CELLS
    x0 = int(round(ROI_X + i * cw))
    x1 = int(round(ROI_X + (i + 1) * cw))
    return x0, ROI_Y, x1, ROI_Y + ROI_H


def _cell_center(i: int) -> tuple[int, int]:
    x0, y0, x1, y1 = _cell_bounds(i)
    return (x0 + x1) // 2, (y0 + y1) // 2


def encode_frame_value(n: int) -> list[int]:
    """Cell grayscale values for source frame ``n``: [white_cal, black_cal, bits...]."""
    cells = [255, 0]
    for b in range(DATA_BITS - 1, -1, -1):  # MSB first
        cells.append(255 if (n >> b) & 1 else 0)
    return cells


def decode_marker(pixels, width: int, height: int) -> int | None:
    """Decode source frame index from a decoded RGB frame (``pixels`` = flat RGB
    sequence indexable as PIL ``Image.load()``). Adaptive threshold from the two
    calibration cells. Returns None if the calibration is degenerate (e.g. a
    black/gap frame)."""
    def lum(i: int) -> float:
        x, y = _cell_center(i)
        x = min(max(x, 0), width - 1)
        y = min(max(y, 0), height - 1)
        r, g, b = pixels[x, y][:3]
        return 0.299 * r + 0.587 * g + 0.114 * b

    white_ref = lum(0)
    black_ref = lum(1)
    if white_ref - black_ref < 25:  # not enough contrast → unreadable/black frame
        return None
    threshold = (white_ref + black_ref) / 2.0
    value = 0
    for k in range(DATA_BITS):
        bit = 1 if lum(2 + k) > threshold else 0
        value = (value << 1) | bit
    return value


def generate_fixture(
    out_path: Path,
    *,
    frames: int = SOURCE_FRAMES,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Path:
    """Render the marker MP4 to ``out_path`` (lossless all-intra). Idempotent:
    skips if the file already exists. Returns ``out_path``.

    ``width``/``height`` default to the portrait (Shorts) frame; pass a landscape
    size (e.g. 1920x1080) for the long-form composition so ``objectFit: cover``
    does not crop the top-left marker ROI."""
    from PIL import Image, ImageDraw  # local import: optional dep

    if out_path.exists():
        return out_path
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg required to build the continuity fixture")

    tmp = out_path.parent / "_frames_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for n in range(frames):
            img = Image.new("RGB", (width, height), (60, 60, 70))  # distinct "video" bg
            draw = ImageDraw.Draw(img)
            for i, val in enumerate(encode_frame_value(n)):
                x0, y0, x1, y1 = _cell_bounds(i)
                draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=(val, val, val))
            # Human-readable secondary marker (not used for assertions).
            draw.text((ROI_X, ROI_Y + ROI_H + 20), f"FRAME {n:03d}", fill=(255, 255, 0))
            img.save(tmp / f"{n:04d}.png")
        # Lossless, all-intra (qp 0, gop 1) so decode is frame-exact.
        subprocess.run(
            [
                "ffmpeg", "-y", "-framerate", str(FPS), "-i", str(tmp / "%04d.png"),
                "-c:v", "libx264", "-qp", "0", "-g", "1", "-pix_fmt", "yuv420p",
                str(out_path),
            ],
            check=True, capture_output=True,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path


def decode_video_markers(video_path: Path, *, max_frames: int | None = None) -> list[int | None]:
    """Extract every frame of ``video_path`` and decode its marker. Returns one
    value (or None) per output frame in order."""
    from PIL import Image

    tmp = video_path.parent / "_decode_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-vsync", "0", str(tmp / "%04d.png")],
            check=True, capture_output=True,
        )
        out: list[int | None] = []
        pngs = sorted(tmp.glob("*.png"))
        if max_frames is not None:
            pngs = pngs[:max_frames]
        for p in pngs:
            with Image.open(p) as im:
                im = im.convert("RGB")
                out.append(decode_marker(im.load(), im.width, im.height))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
