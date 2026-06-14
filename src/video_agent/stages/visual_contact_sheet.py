from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from video_agent.contracts import ARTIFACT_VISUAL_CONTACT_SHEET

# Backgrounds may be stills (jpg/png) or rendered clips (mp4/webm). PIL can only
# open stills, so video backgrounds must be turned into a frame first.
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def _extract_video_frame(video_path: Path, dst_dir: Path) -> Path | None:
    """Grab the first frame of a clip as a JPEG so it can be thumbnailed.

    Returns None when ffmpeg is unavailable or extraction fails; callers then
    fall back to the "missing image" tile.
    """
    if shutil.which("ffmpeg") is None:
        return None
    out_path = dst_dir / f"{video_path.stem}.frame.jpg"
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "-y",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        return None
    return out_path if out_path.exists() else None


def _resolve_still(background: Path, tmp_dir: Path) -> Path | None:
    """Resolve a background reference to a still image PIL can open.

    Order: the background itself if it is already an image; a sibling
    ``<stem>_preview.jpg`` written for image-sourced scenes; otherwise the first
    frame extracted from the clip.
    """
    if not background.exists():
        return None
    if background.suffix.lower() in _IMAGE_EXTS:
        return background
    preview = background.with_name(f"{background.stem}_preview.jpg")
    if preview.exists():
        return preview
    return _extract_video_frame(background, tmp_dir)


def _fit_image(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail(size)
        frame = Image.new("RGB", size, "white")
        x = (size[0] - image.width) // 2
        y = (size[1] - image.height) // 2
        frame.paste(image, (x, y))
        return frame


def create_visual_contact_sheet(job_dir: Path, visual_review: dict) -> Path:
    thumb_size = (320, 180)
    padding = 24
    label_height = 92
    scenes = visual_review.get("scenes") or []
    if not scenes:
        raise RuntimeError("visual_review has no scenes; refusing to render an empty contact sheet.")
    width = padding + len(scenes) * (thumb_size[0] + padding)
    height = padding + thumb_size[1] + label_height + padding
    sheet = Image.new("RGB", (width, height), "#f7f7f2")
    draw = ImageDraw.Draw(sheet)

    tmp_ctx = tempfile.TemporaryDirectory(prefix="contact_sheet_")
    tmp_dir = Path(tmp_ctx.name)

    for index, scene in enumerate(scenes):
        x = padding + index * (thumb_size[0] + padding)
        y = padding
        background = scene.get("background")
        if not background:
            thumb = Image.new("RGB", thumb_size, "#d8d8d8")
            ImageDraw.Draw(thumb).text((16, 76), "no background", fill="#333333")
            sheet.paste(thumb, (x, y))
            continue
        still_path = _resolve_still(Path(background), tmp_dir)
        try:
            if still_path is None:
                raise OSError("no still resolvable from background")
            thumb = _fit_image(still_path, thumb_size)
        except OSError:
            thumb = Image.new("RGB", thumb_size, "#d8d8d8")
            ImageDraw.Draw(thumb).text((16, 76), "missing image", fill="#333333")
        sheet.paste(thumb, (x, y))
        provider = scene.get("provider") or scene.get("source") or "-"
        score = (scene.get("selection") or {}).get("score", "-")
        qa_status = scene.get("qa", {}).get("status", "-")
        lines = [
            f"{scene['scene_id']}  {qa_status}",
            f"{provider}  score {score}",
            scene.get("on_screen_text") or "",
        ]
        for line_index, line in enumerate(lines):
            draw.text((x, y + thumb_size[1] + 10 + line_index * 24), line[:42], fill="#222222")

    output_path = job_dir / ARTIFACT_VISUAL_CONTACT_SHEET
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=90)
    tmp_ctx.cleanup()
    return output_path
