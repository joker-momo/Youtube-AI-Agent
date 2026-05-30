"""Render a Short to vertical ``short.mp4`` + ``short_cover.jpg``.

A Short folder is materialized into a self-contained mini-job (long-form-named
aliases) so the existing Remotion render pipeline can render it vertically
(1080x1920). The cover is a frame extracted from the rendered video.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json

SHORT_RESOLUTION = "1080x1920"


def materialize_short_job_aliases(short_dir: Path) -> None:
    """Copy short_*.json → long-form names so the renderer reads them."""
    mapping = {
        "short_script.json": "script.json",
        "short_scenes.json": "scenes.json",
        "short_seo.json": "seo.json",
    }
    for src_name, dst_name in mapping.items():
        src = short_dir / src_name
        if src.exists():
            atomic_write_json(short_dir / dst_name, json.loads(src.read_text(encoding="utf-8")))


def build_cover_extract_command(video_path: Path, out_path: Path, frame_sec: float) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss", str(frame_sec),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ]


def _channel_path(channel_config: dict) -> Path:
    from video_agent.contracts import repo_root

    channel_id = (channel_config.get("channel") or {}).get("id", "vida-plena-45")
    return repo_root() / "configs" / channel_id / "channel.yaml"


def render_short_video(short_dir: Path, channel_config: dict) -> Path:  # pragma: no cover - needs Remotion
    from video_agent.pipeline import OperatorRenderOptions, render_operator_job

    materialize_short_job_aliases(short_dir)
    render_operator_job(
        OperatorRenderOptions(
            channel_path=_channel_path(channel_config),
            job_dir=short_dir,
            render=True,
            require_operator_qa=False,
        )
    )
    produced = short_dir / "video.mp4"
    out = short_dir / paths.SHORT_VIDEO_FILE
    if produced.exists() and produced != out:
        shutil.copyfile(produced, out)
    return out


def render_short_cover(short_dir: Path, channel_config: dict) -> Path:  # pragma: no cover - needs ffmpeg
    cover_cfg = (channel_config.get("shorts") or {}).get("cover") or {}
    frame_sec = float(cover_cfg.get("cover_frame_sec", 0.3))
    video = short_dir / paths.SHORT_VIDEO_FILE
    out = short_dir / paths.SHORT_COVER_FILE
    if video.exists():
        subprocess.run(build_cover_extract_command(video, out, frame_sec), check=True, capture_output=True)
    return out
