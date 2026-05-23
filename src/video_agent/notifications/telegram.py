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
import html
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from telegram import Bot
from telegram.error import TelegramError


def _esc(value: Any) -> str:
    """HTML-escape an interpolated value for Telegram parse_mode=HTML."""
    return html.escape(str(value), quote=False)

_TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB — Bot API hard cap

def _bot_token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None


def _chat_id() -> str | None:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None


def _configured() -> bool:
    return bool(_bot_token() and _chat_id())


def _bot() -> Bot | None:
    token = _bot_token()
    if not token:
        return None
    return Bot(token=token)


async def _with_retries(fn, *, attempts: int = 4) -> None:
    last_exc: Exception | None = None
    for idx in range(attempts):
        try:
            await fn()
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if idx < attempts - 1:
                await asyncio.sleep(0.8 * (idx + 1))
    if last_exc is not None:
        raise last_exc


async def _send_message(text: str, *, parse_mode: str = "HTML") -> None:
    """Raw async send. Swallows all errors."""
    chat_id = _chat_id()
    bot = _bot()
    if not bot or not chat_id:
        return
    try:
        await _with_retries(
            lambda: bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            ),
            attempts=4,
        )
    except TelegramError as exc:
        print(f"[telegram] send failed: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] send failed (unexpected): {exc}", file=sys.stderr)


async def send(text: str) -> None:
    """Send a plain-text (HTML-formatted) message. Never raises."""
    await _send_message(text)


async def _send_photo_file(path: Path, caption: str = "") -> None:
    """Send an image file via sendPhoto. Swallows all errors."""
    chat_id = _chat_id()
    bot = _bot()
    if not bot or not chat_id or not path.exists():
        return
    try:
        async def _send_once() -> None:
            with path.open("rb") as fh:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=fh,
                    caption=caption,
                    parse_mode="HTML",
                    filename=path.name,
                )
        await _with_retries(_send_once, attempts=4)
    except TelegramError as exc:
        print(f"[telegram] sendPhoto failed: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] sendPhoto failed (unexpected): {exc}", file=sys.stderr)


def _compress_video(src: Path, dst: Path, target_mb: float = 45.0) -> bool:
    """Re-encode src → dst targeting ~target_mb using ffmpeg downscaling and fast rate-control.

    Returns True on success. Original file is never modified.
    Downscales to 480p to reduce file size and encode time, uses preset 'veryfast'
    to prevent timeouts, and applies strict maxrate/bufsize limits to avoid overshooting.
    """
    try:
        # Get duration via ffprobe
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(src),
            ],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(probe.stdout.strip()) if probe.returncode == 0 else 0.0
    except Exception:
        duration = 0.0

    if duration > 0:
        # target_mb → kbps (reserve ~64 kbps for audio)
        target_kbps = int((target_mb * 1024 * 8) / duration) - 64
        target_kbps = max(200, target_kbps)
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-vf", "scale=-2:'min(480,ih)'",
            "-c:v", "libx264",
            "-b:v", f"{target_kbps}k",
            "-maxrate", f"{target_kbps}k",
            "-bufsize", f"{target_kbps * 2}k",
            "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart",
            str(dst),
        ]
    else:
        # Fallback: fixed CRF with downscaling (no duration info)
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-vf", "scale=-2:'min(480,ih)'",
            "-c:v", "libx264", "-crf", "30",
            "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart",
            str(dst),
        ]

    try:
        # Increase timeout to 900 seconds (15 minutes) for safety
        result = subprocess.run(cmd, capture_output=True, timeout=900)
        if result.returncode != 0:
            print(f"[telegram] ffmpeg compress failed:\n{result.stderr[-500:]}", file=sys.stderr)
            return False
        return dst.exists() and dst.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] ffmpeg compress error: {exc}", file=sys.stderr)
        return False


async def _send_video_file(path: Path, caption: str = "") -> None:
    """Send video via sendDocument.

    If file > 50 MB: compress to temp file with ffmpeg, send compressed version,
    then delete the temp file. Original is never touched.
    """
    chat_id = _chat_id()
    bot = _bot()
    if not bot or not chat_id or not path.exists():
        return

    send_path = path

    size = path.stat().st_size
    if size > _TELEGRAM_FILE_LIMIT:
        mb = size / (1024 * 1024)
        print(f"[telegram] video {mb:.0f} MB > 50 MB — compressing for Telegram…", file=sys.stderr)
        # Run blocking ffmpeg in a thread so we don't block the event loop
        tmp = tempfile.NamedTemporaryFile(suffix="_tg.mp4", delete=False)
        tmp.close()
        tmp_path = Path(tmp.name)
        ok = await asyncio.get_running_loop().run_in_executor(
            None, _compress_video, path, tmp_path
        )
        if ok and tmp_path.stat().st_size <= _TELEGRAM_FILE_LIMIT:
            send_path = tmp_path
            print(f"[telegram] compressed to {tmp_path.stat().st_size / 1024 / 1024:.1f} MB", file=sys.stderr)
        else:
            # Compression didn't get it small enough — give up silently
            print("[telegram] compressed file still > 50 MB or failed, skipping video send", file=sys.stderr)
            tmp_path.unlink(missing_ok=True)
            return

    try:
        async def _send_once() -> None:
            with send_path.open("rb") as fh:
                await bot.send_document(
                    chat_id=chat_id,
                    document=fh,
                    caption=caption,
                    parse_mode="HTML",
                    filename=path.name,
                )
        await _with_retries(_send_once, attempts=4)
    except TelegramError as exc:
        print(f"[telegram] sendDocument failed: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] sendDocument failed (unexpected): {exc}", file=sys.stderr)
    finally:
        if send_path != path:
            send_path.unlink(missing_ok=True)  # delete temp, keep original


def notify_sync(text: str) -> None:
    """Sync wrapper — schedules send on a new event loop if none running."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Inside an already-running loop (FastAPI): schedule as a task.
            loop.create_task(_send_message(text))
        else:
            asyncio.run(_send_message(text))
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
        f"<code>{_esc(job_id)}</code>"
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
    text = f"{label}\n<code>{_esc(job_id)}</code>"
    if output:
        text += f"\n→ <code>{_esc(output)}</code>"
    await send(text)


async def notify_job_done(
    job_id: str,
    *,
    stages_done: list[str] | None = None,
    wall_seconds: float | None = None,
) -> None:
    parts = [f"✅ <b>Job complete</b>", f"<code>{_esc(job_id)}</code>"]
    if stages_done:
        parts.append(f"{len(stages_done)} stages completed")
    if wall_seconds is not None:
        mins, secs = divmod(int(wall_seconds), 60)
        parts.append(f"⏱ {mins}m{secs:02d}s")
    parts.append(f'<a href="{_esc(_job_url(job_id))}">Open dashboard →</a>')
    await send("\n".join(parts))


async def notify_job_failed(
    job_id: str,
    *,
    stopped_at: str | None = None,
    error: str | None = None,
) -> None:
    parts = [f"❌ <b>Job failed</b>", f"<code>{_esc(job_id)}</code>"]
    if stopped_at:
        parts.append(f"Stopped at: <code>{_esc(stopped_at)}</code>")
    if error:
        # truncate long errors
        err_short = str(error)[:300]
        parts.append(f"Error: <code>{_esc(err_short)}</code>")
    parts.append(f'<a href="{_esc(_job_url(job_id))}">Open dashboard →</a>')
    await send("\n".join(parts))


async def notify_job_done_with_files(
    job_id: str,
    *,
    job_dir: Path,
    stages_done: list[str] | None = None,
    wall_seconds: float | None = None,
) -> None:
    """Send persona summary (if any), then thumbnail image and video file."""
    persona_path = job_dir / "persona_eval.json"
    if persona_path.exists():
        try:
            persona = json.loads(persona_path.read_text(encoding="utf-8"))
            verdict = str(persona.get("verdict", "UNKNOWN"))
            median = persona.get("median_score")
            threshold = ((persona.get("threshold") or {}).get("min_median_score"))
            await send(
                "\n".join(
                    [
                        "🧪 <b>Persona evaluation</b>",
                        f"<code>{_esc(job_id)}</code>",
                        f"Verdict: <b>{_esc(verdict)}</b>",
                        f"Median: <b>{_esc(median)}</b> / threshold {_esc(threshold)}",
                    ]
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram] persona summary failed: {exc}", file=sys.stderr)

    # Thumbnail — try thumbnail_1.jpg first, fall back to thumbnail.jpg
    thumb = job_dir / "thumbnail_1.jpg"
    if not thumb.exists():
        thumb = job_dir / "thumbnail.jpg"
    await _send_photo_file(thumb, caption=f"🖼 {job_id}")

    # Video
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
