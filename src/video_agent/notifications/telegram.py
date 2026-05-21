"""Telegram notification helpers for the video-agent pipeline.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment.
All send functions are fire-and-forget: a Telegram failure never raises
to the caller — it is logged to stderr and swallowed so a network
hiccup never aborts a pipeline run.

Usage (from async context)::

    from video_agent.notifications.telegram import notify_job_done
    await notify_job_done(job_id="my-job", stages_done=["script", "render"])

Usage (from sync context)::

    from video_agent.notifications.telegram import notify_sync
    notify_sync("✅ render done for my-job")
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import httpx

_TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB — Bot API hard cap

_API_BASE = "https://api.telegram.org"


def _bot_token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None


def _chat_id() -> str | None:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None


def _configured() -> bool:
    return bool(_bot_token() and _chat_id())


async def _send_message(text: str, *, parse_mode: str = "HTML") -> None:
    """Raw async send. Swallows all errors."""
    token = _bot_token()
    chat_id = _chat_id()
    if not token or not chat_id:
        return
    url = f"{_API_BASE}/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if not resp.is_success:
                print(
                    f"[telegram] sendMessage HTTP {resp.status_code}: {resp.text[:200]}",
                    file=sys.stderr,
                )
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] send failed: {exc}", file=sys.stderr)


async def send(text: str) -> None:
    """Send a plain-text (HTML-formatted) message. Never raises."""
    await _send_message(text)


async def _send_photo_file(path: Path, caption: str = "") -> None:
    """Send an image file via sendPhoto. Swallows all errors."""
    token = _bot_token()
    chat_id = _chat_id()
    if not token or not chat_id or not path.exists():
        return
    url = f"{_API_BASE}/bot{token}/sendPhoto"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            with path.open("rb") as fh:
                resp = await client.post(
                    url,
                    data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                    files={"photo": (path.name, fh, "image/jpeg")},
                )
            if not resp.is_success:
                print(f"[telegram] sendPhoto HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] sendPhoto failed: {exc}", file=sys.stderr)


async def _send_video_file(path: Path, caption: str = "") -> None:
    """Send a video file via sendDocument if ≤50 MB, else send a text fallback."""
    token = _bot_token()
    chat_id = _chat_id()
    if not token or not chat_id or not path.exists():
        return
    size = path.stat().st_size
    if size > _TELEGRAM_FILE_LIMIT:
        mb = size / (1024 * 1024)
        await _send_message(
            f"🎬 <b>Video ready</b> ({mb:.0f} MB)\n"
            f"Too large for Telegram (limit 50 MB). Download from dashboard."
        )
        return
    url = f"{_API_BASE}/bot{token}/sendDocument"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            with path.open("rb") as fh:
                resp = await client.post(
                    url,
                    data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                    files={"document": (path.name, fh, "video/mp4")},
                )
            if not resp.is_success:
                print(f"[telegram] sendDocument HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] sendDocument failed: {exc}", file=sys.stderr)


def notify_sync(text: str) -> None:
    """Sync wrapper — schedules send on a new event loop if none running."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Inside an already-running loop (FastAPI): schedule as a task.
            loop.create_task(_send_message(text))
        else:
            loop.run_until_complete(_send_message(text))
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] notify_sync failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# High-level pipeline event helpers
# ---------------------------------------------------------------------------

def _job_url(job_id: str) -> str:
    """Deep-link to dashboard job detail."""
    host = os.environ.get("APP_PUBLIC_URL", "http://127.0.0.1:8000")
    return f"{host}/?job={job_id}"


async def notify_job_started(job_id: str) -> None:
    text = (
        f"🚀 <b>Job started</b>\n"
        f"<code>{job_id}</code>"
    )
    await send(text)


async def notify_stage_done(job_id: str, stage: str, output: str | None = None) -> None:
    """Notify for long/important stages: render, whisper_timestamps, assets_chatgpt."""
    label_map = {
        "render": "🎬 Render done",
        "whisper_timestamps": "🎙 Whisper done",
        "assets_chatgpt": "🖼 Assets done",
        "thumbnail_image": "🖼 Thumbnail generated",
        "idea_research": "🔍 Research done",
        "seo_vidiq": "📊 vidIQ tag scoring done",
    }
    label = label_map.get(stage)
    if not label:
        return  # skip minor stages
    text = f"{label}\n<code>{job_id}</code>"
    if output:
        text += f"\n→ <code>{output}</code>"
    await send(text)


async def notify_job_done(
    job_id: str,
    *,
    stages_done: list[str] | None = None,
    wall_seconds: float | None = None,
) -> None:
    parts = [f"✅ <b>Job complete</b>", f"<code>{job_id}</code>"]
    if stages_done:
        parts.append(f"{len(stages_done)} stages completed")
    if wall_seconds is not None:
        mins, secs = divmod(int(wall_seconds), 60)
        parts.append(f"⏱ {mins}m{secs:02d}s")
    parts.append(f'<a href="{_job_url(job_id)}">Open dashboard →</a>')
    await send("\n".join(parts))


async def notify_job_failed(
    job_id: str,
    *,
    stopped_at: str | None = None,
    error: str | None = None,
) -> None:
    parts = [f"❌ <b>Job failed</b>", f"<code>{job_id}</code>"]
    if stopped_at:
        parts.append(f"Stopped at: <code>{stopped_at}</code>")
    if error:
        # truncate long errors
        err_short = str(error)[:300]
        parts.append(f"Error: <code>{err_short}</code>")
    parts.append(f'<a href="{_job_url(job_id)}">Open dashboard →</a>')
    await send("\n".join(parts))


async def notify_job_done_with_files(
    job_id: str,
    *,
    job_dir: Path,
    stages_done: list[str] | None = None,
    wall_seconds: float | None = None,
) -> None:
    """Send job-done message, then thumbnail image, then video file."""
    # 1. Text summary (same as notify_job_done)
    parts = [f"✅ <b>Job complete</b>", f"<code>{job_id}</code>"]
    if stages_done:
        parts.append(f"{len(stages_done)} stages completed")
    if wall_seconds is not None:
        mins, secs = divmod(int(wall_seconds), 60)
        parts.append(f"⏱ {mins}m{secs:02d}s")
    parts.append(f'<a href="{_job_url(job_id)}">Open dashboard →</a>')
    await send("\n".join(parts))

    # 2. Thumbnail — try thumbnail_1.jpg first, fall back to thumbnail.jpg
    thumb = job_dir / "thumbnail_1.jpg"
    if not thumb.exists():
        thumb = job_dir / "thumbnail.jpg"
    await _send_photo_file(thumb, caption=f"🖼 Thumbnail — {job_id}")

    # 3. Video
    video = job_dir / "video.mp4"
    await _send_video_file(video, caption=f"🎬 {job_id}")


async def notify_batch_done(
    *,
    total: int,
    succeeded: int,
    failed: int,
    failed_jobs: list[str] | None = None,
) -> None:
    icon = "✅" if failed == 0 else "⚠️"
    parts = [
        f"{icon} <b>Batch complete</b>",
        f"{succeeded}/{total} jobs succeeded",
    ]
    if failed_jobs:
        short = ", ".join(f"<code>{j}</code>" for j in failed_jobs[:5])
        if len(failed_jobs) > 5:
            short += f" + {len(failed_jobs) - 5} more"
        parts.append(f"Failed: {short}")
    await send("\n".join(parts))
