"""Shared media primitives (ffmpeg encode, placeholder, preview, frame extract).

Pure plumbing used by BOTH the long-video and Shorts asset pipelines. Must not
import from video_agent.stages or video_agent.shorts (leaf module — see
tests/test_asset_layer_boundary.py).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

SUPPORTED_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _write_placeholder_image(
    path: Path,
    scene: dict[str, Any],
    index: int,
    palette: dict[str, str],
    is_portrait: bool = False
) -> None:
    width, height = (1080, 1920) if is_portrait else (1920, 1080)

    # Warm brand color pairings for gradient
    color_pairs = [
        ((74, 93, 77), (110, 128, 113)),   # #4A5D4D to #6E8071 (Muted Olive)
        ((92, 77, 60), (126, 110, 92)),    # #5C4D3C to #7E6E5C (Warm Brown)
        ((140, 79, 62), (172, 111, 94))    # #8C4F3E to #AC6F5E (Muted Terracotta)
    ]
    color1, color2 = color_pairs[index % len(color_pairs)]

    is_standard = not (scene.get("layout") or "").startswith("graphic_")

    if is_standard:
        # Create a smooth gradient using upscale bilinear filtering
        base = Image.new("RGB", (1, 2))
        base.putpixel((0, 0), color1)
        base.putpixel((0, 1), color2)
        image = base.resize((width, height), resample=Image.Resampling.BILINEAR)

        # Soft paper texture: grid of tiny dots with very low opacity (~3.5%)
        texture = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(texture)
        dot_color = (47, 42, 36, 9)
        for x in range(0, width, 7):
            for y in range(0, height, 7):
                draw.point((x, y), fill=dot_color)
        image.paste(texture, (0, 0), texture)

        # FIX: Even for standard layouts, draw the text if available so it's not a blank screen
        draw = ImageDraw.Draw(image)
        text_content = scene.get("on_screen_text", "")
        if text_content:
            # Draw semi-transparent background box for readability
            draw.rectangle((0, int(height * 0.7), width, height), fill=_hex_to_rgb(palette["text"]))
            draw.text((int(width * 0.05), int(height * 0.76)), text_content, fill=_hex_to_rgb(palette["accent"]))

        image.save(path, quality=92)
    else:
        # Original behavior for graphic layouts (flat color + bottom text box + text)
        image = Image.new("RGB", (width, height), color1)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, int(height * 0.7), width, height), fill=_hex_to_rgb(palette["text"]))
        text_content = scene.get("on_screen_text", "")
        if text_content:
            draw.text((int(width * 0.05), int(height * 0.76)), text_content, fill=_hex_to_rgb(palette["accent"]))
        image.save(path, quality=92)


def _write_preview_still(src_image: Path, dst_jpg: Path, *, max_h: int = 360) -> bool:
    """Save a small JPEG preview of a still-image source for the UI report.

    Returns True on success. Best-effort: any failure just means the UI falls
    back to the .mp4 frame, so this must never raise into asset prep.
    """
    try:
        with Image.open(src_image) as im:
            im = im.convert("RGB")
            if im.height > max_h:
                w = int(im.width * (max_h / im.height))
                im = im.resize((max(1, w), max_h))
            dst_jpg.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst_jpg, format="JPEG", quality=80)
        return True
    except Exception:
        return False


def _write_video_from_image(image_path: Path, output_path: Path, duration_sec: float, is_portrait: bool = False) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    vf_filter = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" if is_portrait else "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-t",
        f"{max(1.0, float(duration_sec)):.2f}",
        "-vf",
        vf_filter,
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _write_placeholder_video(
    path: Path,
    scene: dict[str, Any],
    index: int,
    palette: dict[str, str],
    duration_sec: float,
    is_portrait: bool = False,
) -> None:
    temp_image = path.with_suffix(".jpg")
    _write_placeholder_image(temp_image, scene, index, palette, is_portrait=is_portrait)
    try:
        _write_video_from_image(temp_image, path, duration_sec, is_portrait=is_portrait)
    finally:
        try:
            temp_image.unlink()
        except OSError:
            pass


def extract_asset_frame(asset_path: str | Path, out_path: str | Path, *, at_sec: float = 0.5) -> Path | None:
    """Extract a representative still frame from an asset for Vision QA.

    Images are copied as-is; videos get one frame at ``at_sec`` via ffmpeg.
    Returns the frame path, or None when extraction fails (QA then skips —
    a broken probe must never block asset selection).
    """
    src = Path(asset_path)
    dst = Path(out_path)
    if not src.exists():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() in _IMAGE_SUFFIXES:
        shutil.copy2(src, dst)
        return dst
    cmd = [
        "ffmpeg", "-y", "-ss", f"{max(0.0, at_sec):.2f}", "-i", str(src),
        "-frames:v", "1", "-q:v", "2", str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    return dst if dst.exists() else None
