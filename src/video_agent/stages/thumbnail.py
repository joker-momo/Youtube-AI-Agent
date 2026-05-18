from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from video_agent.contracts import ARTIFACT_SEO, ARTIFACT_THUMBNAIL
from video_agent.qa.thumbnail_title_qa import check_thumbnail_title
from video_agent.utils.json_io import write_json


def create_thumbnail_and_seo(
    provider: Any,
    channel_config: dict[str, Any],
    style_dna: dict[str, Any],
    idea: dict[str, Any],
    job_dir: Path,
) -> dict[str, Any]:
    thumbnail_path = job_dir / ARTIFACT_THUMBNAIL
    palette = style_dna["palette"]
    image = Image.new("RGB", (1280, 720), palette["primary"])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 470, 1280, 720), fill=palette["background"])
    draw.text((70, 500), "DORMIR MEJOR", fill=palette["text"])
    draw.text((70, 580), "DESPUES DE LOS 45", fill=palette["secondary"])
    image.save(thumbnail_path, quality=92)

    seo = provider.generate_seo(channel_config, idea, str(thumbnail_path))
    qa = check_thumbnail_title(seo, channel_config)
    seo["qa"] = {"iterations": [{"iteration": 1, **qa}], "verdict": qa["verdict"]}
    if qa["verdict"] != "PASS":
        raise RuntimeError("Thumbnail/title QA failed.")
    write_json(job_dir / ARTIFACT_SEO, seo)
    return seo
