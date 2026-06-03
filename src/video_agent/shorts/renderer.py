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


def _is_portrait_image(image_path: Path) -> bool:
    """Best-effort aspect check using ffprobe. Returns True only when we can
    confirm height > width. Errors → False so the caller falls back to a
    fresh Remotion render instead of trusting a possibly wrong asset."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(image_path),
            ],
            check=True, capture_output=True, text=True,
        )
        parts = result.stdout.strip().split(",")
        if len(parts) >= 2:
            w = int(parts[0])
            h = int(parts[1])
            return h > w
    except (subprocess.SubprocessError, ValueError):
        pass
    return False


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
    produced = short_dir / "outputs" / "video.mp4"
    if not produced.exists():
        produced = short_dir / "video.mp4"
    out = short_dir / paths.SHORT_VIDEO_FILE
    if produced.exists() and produced != out:
        shutil.copyfile(produced, out)
    return out


def build_remotion_short_cover_command(
    entry: Path, render_props: Path, out_path: Path,
) -> list[str]:
    """Pure command builder for the Remotion ShortCover still."""
    return [
        "npx", "--prefix", str(entry.parent.parent),
        "remotion", "still",
        str(entry), "ShortCover", str(out_path),
        "--props", str(render_props),
        "--image-format", "jpeg",
    ]


def render_short_cover(short_dir: Path, channel_config: dict) -> Path:  # pragma: no cover - needs Remotion/ffmpeg
    """Spec v6 §12 — primary cover renderer is the Remotion ShortCover comp.

    ffmpeg frame extraction is the emergency fallback only, used when the
    Remotion still render fails or when the worker container has no Node.
    """
    from video_agent.contracts import repo_root

    out = short_dir / paths.SHORT_COVER_FILE

    # Optional shortcut: reuse outputs/thumbnail.jpg ONLY if it's already
    # portrait (rendered by the patched pipeline with composition=ShortCover).
    # The long-form pipeline used to dump a 1280x720 ThumbnailStandard here,
    # which would otherwise be blindly copied as the Short cover and break
    # the 9:16 aspect.
    produced_thumb = short_dir / "outputs" / "thumbnail.jpg"
    if produced_thumb.exists() and _is_portrait_image(produced_thumb):
        shutil.copyfile(produced_thumb, out)
        return out

    materialize_short_job_aliases(short_dir, channel_config)

    remotion_root = repo_root() / "remotion"
    entry = remotion_root / "src/index.ts"
    # Short jobs persist render props as ``short_render_props.json``; the
    # long-form ``render_props.json`` path is only created by
    # ``materialize_short_job_aliases`` when present. Prefer the Short
    # native props file so we can render the ShortCover composition even
    # when the long-form alias has not been materialized yet.
    render_props = short_dir / paths.SHORT_RENDER_PROPS_FILE
    if not render_props.exists():
        render_props = short_dir / "render_props.json"

    # Primary: Remotion ShortCover still.
    if render_props.exists():
        try:
            cmd = build_remotion_short_cover_command(entry, render_props, out)
            subprocess.run(cmd, check=True, capture_output=True, cwd=str(remotion_root))
            if out.exists():
                return out
        except Exception:
            pass  # fall through to fallback

    # Fallback: ffmpeg frame extraction from the rendered video.
    cover_cfg = (channel_config.get("shorts") or {}).get("cover") or {}
    frame_sec = float(cover_cfg.get("cover_frame_sec", 0.3))
    video = short_dir / paths.SHORT_VIDEO_FILE
    if video.exists():
        subprocess.run(
            build_cover_extract_command(video, out, frame_sec),
            check=True, capture_output=True,
        )
    return out
