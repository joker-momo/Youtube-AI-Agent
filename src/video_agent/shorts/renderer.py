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


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def materialize_short_job_aliases(short_dir: Path, channel_config: dict | None = None) -> None:
    """Transform short_*.json into schema-valid long-form script/scenes/seo so the
    existing Remotion render pipeline (which validates against the long-form
    schemas) can render the Short as a self-contained mini-job."""
    channel_config = channel_config or {}
    channel_id = (channel_config.get("channel") or {}).get("id", "vida-plena-45")
    job_id = short_dir.name

    short_script = _load(short_dir / "short_script.json")
    short_scenes = _load(short_dir / "short_scenes.json")
    short_seo = _load(short_dir / "short_seo.json")

    if short_script:
        sections = short_script.get("sections") or [
            {"title": short_script.get("hook", ""), "focus": short_script.get("narration", "")}
        ]
        atomic_write_json(short_dir / "script.json", {
            "channel_id": channel_id,
            "job_id": job_id,
            "hook": short_script.get("hook", ""),
            "sections": sections,
            "narration": short_script.get("narration", ""),
            "cta": short_script.get("cta", ""),
            "qa": {"verdict": "PASS"},
        })

    if short_scenes:
        scenes = dict(short_scenes)
        scenes["channel_id"] = channel_id
        scenes["job_id"] = job_id
        if not scenes.get("total_duration_sec"):
            scenes["total_duration_sec"] = round(
                sum(float(s.get("duration_sec") or 0) for s in (scenes.get("scenes") or [])), 1
            )
        scenes["qa"] = {"verdict": "PASS"}
        atomic_write_json(short_dir / "scenes.json", scenes)

    if short_seo:
        title = short_seo.get("title", "")
        atomic_write_json(short_dir / "seo.json", {
            "job_id": job_id,
            "title": title,
            "description": short_seo.get("description", ""),
            "tags": short_seo.get("tags") or short_seo.get("hashtags") or ["shorts"],
            "language": short_seo.get("language", "es-ES"),
            "ai_disclosure": bool(short_seo.get("ai_disclosure", True)),
            "thumbnail_path": "short_cover.jpg",
            "thumbnail_text": (title[:25] or "SHORT").upper(),
            "suggested_pinned_comments": short_seo.get("pinned_comment", ""),
        })


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

    materialize_short_job_aliases(short_dir, channel_config)
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
