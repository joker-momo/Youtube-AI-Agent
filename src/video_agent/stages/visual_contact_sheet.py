from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from video_agent.contracts import ARTIFACT_VISUAL_CONTACT_SHEET


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
    width = padding + len(visual_review["scenes"]) * (thumb_size[0] + padding)
    height = padding + thumb_size[1] + label_height + padding
    sheet = Image.new("RGB", (width, height), "#f7f7f2")
    draw = ImageDraw.Draw(sheet)

    for index, scene in enumerate(visual_review["scenes"]):
        x = padding + index * (thumb_size[0] + padding)
        y = padding
        image_path = Path(scene["background"])
        try:
            thumb = _fit_image(image_path, thumb_size)
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
    sheet.save(output_path, quality=90)
    return output_path
