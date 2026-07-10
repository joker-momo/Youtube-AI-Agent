"""Generate the infographic poster PNG via the injected image function."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.orchestrator.image_prompt_log import safe_log_image_prompt
from video_agent.shorts import paths
from video_agent.shorts.infographic.poster_prompt import (
    build_poster_body,
    build_poster_prompt,
)


async def generate_poster(
    short_dir: Path, plan: dict[str, Any], image_fn, channel_config: dict[str, Any] | None = None
) -> Path:
    """Generate the infographic poster image for a short.

    Args:
        short_dir: The short's root directory.
        plan: The poster plan dict (contains title, items, format, etc.).
        image_fn: Async image generation function that accepts prompt, project_name,
                  out_path, and aspect_ratio kwargs.
        channel_config: Supplies the channel's brand identity as creative direction
                        for the poster (never rendered as on-screen text/watermark).

    Returns:
        Path to the generated poster PNG.
    """
    short_dir = Path(short_dir)
    out_path = short_dir / "assets" / paths.SHORT_POSTER_IMAGE_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Log the full wrapped prompt (what conceptually reaches ChatGPT) for audit...
    safe_log_image_prompt(
        short_dir,
        stage="infographic",
        kind="infographic_poster",
        prompt=build_poster_prompt(plan, channel_config),
        out_path=str(out_path),
        aspect_ratio="9:16",
    )
    # ...but pass the RAW body: the driver prepends the dimension instruction once.
    await image_fn(
        prompt=build_poster_body(plan, channel_config),
        project_name=f"{short_dir.name[:38]}-poster"[:45],
        out_path=str(out_path),
        aspect_ratio="9:16",
    )
    return out_path
