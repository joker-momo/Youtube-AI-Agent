"""Render an infographic short via Remotion (direct InfographicShort composition).

The scene-based shorts renderer (`render_short_video` / `render_with_remotion`) is
coupled to the multi-scene pipeline (it validates a scene-sum duration and transforms
short_*.json into long-form schemas). An infographic short has no scenes, so it renders
the `InfographicShort` composition directly: mirror the poster + audio into Remotion's
public dir, then run the Remotion CLI built by `build_remotion_commands`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from video_agent.shorts import paths
from video_agent.shorts.renderer import _mirror_short_assets_to_public


def build_infographic_render_command(render_props_path: Path, video_path: Path) -> list[str]:
    """Remotion CLI command list to render the InfographicShort composition.

    Delegates to ``build_remotion_commands``, which reads ``render.composition`` and
    ``render.concurrency`` from the props. Concurrency is ``"auto"`` in the props and
    is resolved to the machine core count by ``_render_concurrency`` — it is NEVER
    hardcoded here (render-concurrency HARD RULE).
    """
    from video_agent.stages.render import build_remotion_commands

    return build_remotion_commands(Path(render_props_path), Path(video_path)).video


def render_infographic(short_dir: Path, channel_config: dict) -> Path:
    """Publish assets + render the InfographicShort composition to ``outputs/short.mp4``."""
    short_dir = Path(short_dir)
    render_props_path = short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE
    outputs = short_dir / paths.SHORT_OUTPUTS_SUBDIR
    outputs.mkdir(parents=True, exist_ok=True)
    video_path = outputs / paths.SHORT_VIDEO_FILE
    _mirror_short_assets_to_public(short_dir)
    cmd = build_infographic_render_command(render_props_path, video_path)
    subprocess.run(cmd, check=True)  # pragma: no cover - needs a live Remotion install
    return video_path


def make_infographic_render_fn(channel_config: dict):
    """Adapter matching ``run_infographic_short``'s ``render_fn(short_dir, props)``."""

    def _render_fn(short_dir: Path, _props: dict) -> Path:
        return render_infographic(short_dir, channel_config)

    return _render_fn
