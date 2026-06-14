"""Render props writer extracted from short_builder."""

from __future__ import annotations

from pathlib import Path

from video_agent.shorts import paths
from video_agent.shorts.builder.snapshots import _scene_duration_sum
from video_agent.storage.atomic import atomic_write_json


def _write_render_props(
    short_dir: Path, short_scenes: dict, channel_config: dict, music_track: str | None
) -> None:
    rcfg = (channel_config.get("shorts") or {}).get("render") or {}
    # Inherit performance + encoding tunables from the channel-wide render
    # config so Shorts also get VideoToolbox HW encode, Metal/ANGLE WebGL,
    # and proper concurrency on Mac. ``shorts.render`` overrides win.
    base_render = channel_config.get("render") or {}
    duration_sec = _scene_duration_sum(short_scenes) or float(
        short_scenes.get("total_duration_sec") or 35
    )
    short_scenes["total_duration_sec"] = round(duration_sec, 1)
    render_block = {
        "composition": rcfg.get("composition", "ShortVideoStandard"),
        "thumbnail_composition": rcfg.get("thumbnail_composition", "ShortCover"),
        "resolution": rcfg.get("resolution", "1080x1920"),
        "fps": rcfg.get("fps", base_render.get("fps", 30)),
        "duration_sec": duration_sec,
        "codec": rcfg.get("codec", base_render.get("codec", "h264")),
        "video_bitrate": rcfg.get("video_bitrate", base_render.get("video_bitrate")),
        "gl": rcfg.get("gl", base_render.get("gl")),
        "concurrency": rcfg.get("concurrency", base_render.get("concurrency", "auto")),
    }
    props = {
        "render": render_block,
        # Keep these at the top level for ShortVideo.tsx + ShortCover.tsx
        # which read props.scenes / props.audio / props.music_track directly.
        "scenes": short_scenes.get("scenes") or [],
        "total_duration_sec": duration_sec,
        "audio": "audio/short_mix.m4a",
        "music_track": music_track,
    }
    jd = short_dir / paths.SHORT_JSON_SUBDIR
    jd.mkdir(parents=True, exist_ok=True)
    atomic_write_json(jd / paths.SHORT_RENDER_PROPS_FILE, props)
