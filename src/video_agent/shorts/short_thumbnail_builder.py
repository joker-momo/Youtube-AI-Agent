"""Generate a thumbnail for a Short via ChatGPT image generation.

Reuses the *exact same* topic-aware planner (``thumbnail_planner``) as
long-form videos.  The only difference is the output dimensions:
**1080×1920 (9:16 portrait)** instead of 1920×1080 (16:9 landscape).
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json


# Portrait Full-HD instruction prepended to every short thumbnail prompt.
_SHORT_IMAGE_GEN_INSTRUCTION = (
    "Generate one photorealistic image at exactly 1080x1920 pixels "
    "(Full HD, 9:16 portrait orientation). Fill the entire 1080x1920 frame — "
    "no borders, no padding, no commentary, no watermark."
)


def _build_short_thumbnail_prompt(plan: dict) -> str:
    """Build the ChatGPT prompt for a Short thumbnail (9:16 portrait)."""
    from video_agent.thumbnail_planner import build_thumbnail_prompt

    # Get the base prompt from the planner (shared with long videos)
    base_prompt = build_thumbnail_prompt(plan)

    # Replace dimension references:  16:9 / 1920x1080  →  9:16 / 1080x1920
    prompt = base_prompt
    prompt = prompt.replace("16:9", "9:16")
    prompt = prompt.replace("1920x1080", "1080x1920")
    prompt = prompt.replace("landscape", "portrait")
    prompt = re.sub(
        r"YouTube thumbnail",
        "YouTube Shorts thumbnail (vertical portrait)",
        prompt,
        count=1,
    )

    return prompt


def build_short_thumbnail(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    image_fn: Callable[..., Any],
    *,
    is_async: bool = False,
) -> Path:
    """Generate a ChatGPT thumbnail for a Short and save it.

    Parameters
    ----------
    long_job_dir:
        Root directory of the parent long-form job.
    short_id:
        e.g. ``"short-06"``.
    channel_config:
        Parsed ``channel.yaml``.
    image_fn:
        Async callable ``(prompt, project_name, out_path) -> dict``.
        Typically ``BrowserClient.generate_image``.
    is_async:
        If True, ``image_fn`` is an async function and will be awaited.
        If False, it will be wrapped in ``asyncio.run()``.

    Returns
    -------
    Path to the generated thumbnail JPEG.
    """
    from PIL import Image as _PilImage, ImageOps as _PilImageOps
    from video_agent.thumbnail_planner import plan_thumbnail_prompts

    short_dir = paths.short_dir(long_job_dir, short_id)
    seo_path = short_dir / paths.SHORT_SEO_FILE
    if not seo_path.exists():
        raise FileNotFoundError(f"Missing SEO file: {seo_path}")

    seo = json.loads(seo_path.read_text(encoding="utf-8"))

    # Channel description for prompt context
    channel_description = (
        channel_config.get("description")
        or "Vida Plena 45+, practical wellness, nutrition and lifestyle "
        "for Spanish adults over 45."
    )

    palette = (channel_config.get("style") or {}).get("palette") or {}
    accent_color = palette.get("accent", "#F2C94C")

    planner_channel_config = dict(channel_config or {})
    planner_channel_config["description"] = channel_description
    planner_channel_config.setdefault("thumbnail", {"accent_color": accent_color})

    # Plan using the shared planner (same as long videos)
    plans = plan_thumbnail_prompts(seo, planner_channel_config)
    if not plans:
        raise RuntimeError("thumbnail_planner returned no plans for Short SEO")

    plan = plans[0]  # Use only the first variant for shorts
    prompt = _build_short_thumbnail_prompt(plan)

    # Output paths
    png_path = short_dir / "thumbnail_raw.png"
    jpg_path = short_dir / "thumbnail.jpg"
    project_name = f"{long_job_dir.name[:25]}-{short_id}-thumb"[:45]

    # Prepend the portrait image gen instruction
    full_prompt = _SHORT_IMAGE_GEN_INSTRUCTION + "\n\n" + prompt

    print(f"\n[ShortThumbnail] Generating thumbnail for {short_id}...")
    print(f"[ShortThumbnail] Project: {project_name}")

    # Call ChatGPT image generation
    if is_async:
        result = asyncio.run(
            image_fn(
                prompt=full_prompt,
                project_name=project_name,
                out_path=str(png_path),
            )
        )
    else:
        # image_fn is already sync (wrapped by caller)
        result = image_fn(
            prompt=full_prompt,
            project_name=project_name,
            out_path=str(png_path),
        )

    # Find the generated image
    source_path = png_path
    if not source_path.exists() and isinstance(result, dict):
        returned_path = result.get("local_path")
        if returned_path:
            source_path = Path(str(returned_path)).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(
            f"Generated thumbnail image not found: {png_path}"
        )

    # Convert to portrait JPEG 1080x1920
    img = _PilImage.open(source_path).convert("RGB")
    img = _PilImageOps.fit(
        img, (1080, 1920), method=_PilImage.Resampling.LANCZOS
    )
    img.save(jpg_path, "JPEG", quality=94, optimize=True)
    source_path.unlink(missing_ok=True)  # remove intermediate PNG

    print(f"[ShortThumbnail] Saved: {jpg_path} ({jpg_path.stat().st_size} bytes)")

    # Save prompt metadata for debugging
    meta = {
        "short_id": short_id,
        "variant_title": plan.get("variant_title", ""),
        "thumbnail_text": plan.get("thumbnail_text", ""),
        "primary_category": plan.get("primary_category", ""),
        "visual_strategy": plan.get("visual_strategy", ""),
        "dimensions": "1080x1920",
        "aspect_ratio": "9:16",
    }
    atomic_write_json(short_dir / "thumbnail_prompt_meta.json", meta)

    return jpg_path
