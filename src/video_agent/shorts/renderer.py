"""Render a Short to vertical ``short.mp4`` + ``short_cover.jpg``.

A Short folder is materialized into a self-contained mini-job (long-form-named
aliases) so the existing Remotion render pipeline can render it vertically
(1080x1920). The cover is a frame extracted from the rendered video.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

from video_agent.assets.materialize import materialize_media
from video_agent.contracts import repo_root
from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json

SHORT_RESOLUTION = "1080x1920"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _mirror_short_assets_to_public(short_dir: Path) -> None:
    """Mirror prepared short assets into Remotion's public dir.

    The legacy background stage already copies scene-level assets to
    ``remotion/public/jobs/<short>/assets`` while preparing them. PR D local QA
    downloads finalist span videos later, so ``assets/visual_spans`` can exist
    only in the short folder unless we mirror it before render/rerender.
    """
    src_root = short_dir / "assets"
    if not src_root.exists():
        return
    public_root = repo_root() / "remotion" / "public" / "jobs" / short_dir.name / "assets"
    for src in src_root.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(src_root)
        dst = public_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        materialize_media(src, dst)


def materialize_short_job_aliases(short_dir: Path, channel_config: dict | None = None) -> None:
    """Transform short_*.json into schema-valid long-form script/scenes/seo so the
    existing Remotion render pipeline (which validates against the long-form
    schemas) can render the Short as a self-contained mini-job."""
    channel_config = channel_config or {}
    channel_id = (channel_config.get("channel") or {}).get("id", "vida-plena-45")
    job_id = short_dir.name

    # Read from json/ subdir first (new layout), fall back to root (legacy).
    short_script = _load(paths.resolve_short_json(short_dir, paths.SHORT_SCRIPT_FILE))
    short_scenes = _load(paths.resolve_short_json(short_dir, paths.SHORT_SCENES_FILE))
    short_seo = _load(paths.resolve_short_json(short_dir, paths.SHORT_SEO_FILE))
    short_render_props = _load(paths.resolve_short_json(short_dir, paths.SHORT_RENDER_PROPS_FILE))

    # Write Remotion aliases to json/ subdirectory so pipeline._resolve_json_file
    # picks them up automatically (it checks json/ before root).
    jd = short_dir / paths.SHORT_JSON_SUBDIR
    jd.mkdir(parents=True, exist_ok=True)

    if short_script:
        sections = short_script.get("sections") or [
            {"title": short_script.get("hook", ""), "focus": short_script.get("narration", "")}
        ]
        atomic_write_json(
            jd / "script.json",
            {
                "channel_id": channel_id,
                "job_id": job_id,
                "hook": short_script.get("hook", ""),
                "sections": sections,
                "narration": short_script.get("narration", ""),
                "cta": short_script.get("cta", ""),
                "qa": {"verdict": "PASS"},
            },
        )

    if short_scenes:
        scenes = dict(short_scenes)
        scenes["channel_id"] = channel_id
        scenes["job_id"] = job_id
        if not scenes.get("total_duration_sec"):
            scenes["total_duration_sec"] = round(
                sum(float(s.get("duration_sec") or 0) for s in (scenes.get("scenes") or [])), 1
            )
        scenes["total_duration_sec"] = int(math.ceil(float(scenes.get("total_duration_sec") or 0)))
        scenes["qa"] = {"verdict": "PASS"}
        atomic_write_json(jd / "scenes.json", scenes)

    if short_seo:
        title = short_seo.get("title", "")
        atomic_write_json(
            jd / "seo.json",
            {
                "job_id": job_id,
                "title": title,
                "description": short_seo.get("description", ""),
                "tags": short_seo.get("tags") or short_seo.get("hashtags") or ["shorts"],
                "language": short_seo.get("language", "es-ES"),
                "ai_disclosure": bool(short_seo.get("ai_disclosure", True)),
                "thumbnail_path": "outputs/short_cover.jpg",
                "thumbnail_text": (title[:25] or "SHORT").upper(),
                "suggested_pinned_comments": short_seo.get("pinned_comment", ""),
            },
        )

    if short_render_props:
        atomic_write_json(
            jd / "render_props.json",
            {**short_render_props, "_render_props_path": str(jd / "render_props.json")},
        )

    _mirror_short_assets_to_public(short_dir)


def _is_portrait_image(image_path: Path) -> bool:
    """Best-effort aspect check using ffprobe. Returns True only when we can
    confirm height > width. Errors → False so the caller falls back to a
    fresh Remotion render instead of trusting a possibly wrong asset."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                str(image_path),
            ],
            check=True,
            capture_output=True,
            text=True,
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
        "-ss",
        str(frame_sec),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]


def _channel_path(channel_config: dict) -> Path:
    from video_agent.contracts import repo_root

    channel_id = (channel_config.get("channel") or {}).get("id", "vida-plena-45")
    return repo_root() / "configs" / channel_id / "channel.yaml"


def render_short_video(
    short_dir: Path,
    channel_config: dict,
    stop_request_path: Path | None = None,
) -> Path:  # pragma: no cover - needs Remotion
    from video_agent.pipeline import OperatorRenderOptions, render_operator_job

    materialize_short_job_aliases(short_dir, channel_config)

    outputs_dir = short_dir / paths.SHORT_OUTPUTS_SUBDIR
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # Bypass Remotion thumbnail generation:
    # If the ChatGPT-generated thumbnail.jpg exists, copy it to outputs/thumbnail_1.jpg
    # so that render_with_remotion will see it and completely skip Remotion still rendering.
    thumb = paths.resolve_short_output(short_dir, paths.SHORT_THUMBNAIL_FILE)
    if not thumb.exists():
        thumb = short_dir / paths.SHORT_THUMBNAIL_FILE  # fallback root location
    if thumb.exists():
        shutil.copyfile(thumb, outputs_dir / "thumbnail_1.jpg")

    render_operator_job(
        OperatorRenderOptions(
            channel_path=_channel_path(channel_config),
            job_dir=short_dir,
            render=True,
            require_operator_qa=False,
            prepared_short=True,
            # Let the operator press Stop mid-render: render_with_remotion polls
            # this path and SIGTERMs the Remotion subprocess when it appears.
            stop_request_path=stop_request_path,
        )
    )
    produced = outputs_dir / "video.mp4"  # render_operator_job puts it here
    if not produced.exists():
        produced = short_dir / "video.mp4"
    out = outputs_dir / paths.SHORT_VIDEO_FILE
    if produced.exists() and produced != out:
        shutil.copyfile(produced, out)
    try:
        _save_friendly_copy(short_dir, out, ext=".mp4")
    except Exception:
        pass
    return out


def build_remotion_short_cover_command(
    entry: Path,
    render_props: Path,
    out_path: Path,
) -> list[str]:
    """Pure command builder for the Remotion ShortCover still."""
    return [
        "npx",
        "--prefix",
        str(entry.parent.parent),
        "remotion",
        "still",
        str(entry),
        "ShortCover",
        str(out_path),
        "--props",
        str(render_props),
        "--image-format",
        "jpeg",
    ]


def render_short_cover(short_dir: Path, channel_config: dict) -> Path:
    """ChatGPT-generated thumbnail is the primary cover (from Stage 5b).

    Bypasses Remotion completely. If the ChatGPT thumbnail is missing,
    falls back to extracting a frame from the rendered video using ffmpeg.
    """
    outputs_dir = short_dir / paths.SHORT_OUTPUTS_SUBDIR
    outputs_dir.mkdir(parents=True, exist_ok=True)
    out = outputs_dir / paths.SHORT_COVER_FILE
    # Find the ChatGPT-generated thumbnail (may be in outputs/ or at root)
    thumb = outputs_dir / paths.SHORT_THUMBNAIL_FILE
    if not thumb.exists():
        thumb = short_dir / paths.SHORT_THUMBNAIL_FILE

    if thumb.exists():
        # Copy ChatGPT generated thumbnail as the cover image
        shutil.copyfile(thumb, out)
        try:
            _save_friendly_copy(short_dir, out, ext=".jpg")
        except Exception:
            pass
        return out

    # Fallback: ffmpeg frame extraction from the rendered video.
    cover_cfg = (channel_config.get("shorts") or {}).get("cover") or {}
    frame_sec = float(cover_cfg.get("cover_frame_sec", 0.3))
    video = outputs_dir / paths.SHORT_VIDEO_FILE
    if not video.exists():
        video = short_dir / paths.SHORT_VIDEO_FILE
    if video.exists():
        try:
            subprocess.run(
                build_cover_extract_command(video, out, frame_sec),
                check=True,
                capture_output=True,
            )
            _save_friendly_copy(short_dir, out, ext=".jpg")
        except Exception:
            pass
    return out


def _save_friendly_copy(short_dir: Path, source_file: Path, ext: str) -> None:
    import datetime
    import json
    import re
    import shutil
    import unicodedata

    idea_path = paths.resolve_short_json(short_dir, paths.SHORT_IDEA_FILE)
    if not idea_path.exists():
        return

    try:
        idea_data = json.loads(idea_path.read_text(encoding="utf-8"))
    except Exception:
        return

    idea_id = idea_data.get("idea_id") or "idea"
    title = idea_data.get("title") or ""
    if not title:
        title = idea_data.get("hook_text") or "short"

    # Normalize/slugify title
    title_slug = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("utf-8")
    title_slug = title_slug.lower()
    title_slug = re.sub(r"[^a-z0-9\s-]", "", title_slug)
    title_slug = re.sub(r"[\s-]+", "-", title_slug).strip("-")

    # Use localized timestamp
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    short_id = short_dir.name
    friendly_name = f"{short_id}_{idea_id}_{timestamp}_{title_slug}{ext}"

    dest = short_dir.parent / friendly_name
    if source_file.exists():
        materialize_media(source_file, dest)
