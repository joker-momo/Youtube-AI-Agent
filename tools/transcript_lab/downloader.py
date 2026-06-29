"""Download a YouTube video (low-res) + audio + metadata for teardown analysis.

Runs in the tool venv (has yt-dlp). Keeps everything under output/<video_id>/.
Network-free helpers live in fetcher.py (extract_video_id is reused).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fetcher import extract_video_id

# Keep downloads small + fast on M2: 480p is plenty for layout/VFX/color analysis.
_FORMAT = "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b"

# Metadata fields worth keeping for competitive evaluation.
_META_KEYS = (
    "id", "title", "description", "tags", "categories", "duration",
    "view_count", "like_count", "comment_count", "upload_date",
    "channel", "channel_id", "uploader", "webpage_url", "thumbnail",
)


def download(url: str, out_root: Path) -> dict:
    """Download 480p video + 16k mono wav + metadata into out_root/<id>/.

    Returns {"video_id", "dir", "video", "audio", "meta"}.
    Raises RuntimeError on download/extract failure.
    """
    video_id = extract_video_id(url)
    job_dir = out_root / video_id
    job_dir.mkdir(parents=True, exist_ok=True)

    info_path = job_dir / "info.json"
    video_tmpl = str(job_dir / "video.%(ext)s")

    base = [
        "yt-dlp",
        "-f", _FORMAT,
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--no-playlist",
        "-o", video_tmpl,
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    # YouTube often 403s the default web client's media URLs. Retry across player
    # clients (android/tv/web) which expose downloadable format URLs.
    last_err = "unknown"
    for client in ("android", "tv", "web_safari", "web"):
        proc = _run_ytdlp([*base, "--extractor-args", f"youtube:player_client={client}"])
        if proc.returncode == 0:
            break
        tail = (proc.stderr or "").strip().splitlines()
        last_err = tail[-1] if tail else "unknown"
    else:
        raise RuntimeError(f"yt-dlp download failed (all clients): {last_err}")

    video_path = _first_existing(job_dir, ("video.mp4", "video.mkv", "video.webm"))
    if video_path is None:
        raise RuntimeError("yt-dlp produced no video file")

    # yt-dlp writes <stem>.info.json; normalise to info.json.
    raw_info = job_dir / f"{video_path.stem}.info.json"
    if raw_info.exists():
        raw_info.replace(info_path)

    meta = _extract_meta(info_path)
    (job_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    audio_path = job_dir / "audio.wav"
    _extract_audio(video_path, audio_path)

    thumb_path = _download_thumbnail(video_id, meta, job_dir)

    return {
        "video_id": video_id,
        "dir": str(job_dir),
        "video": str(video_path),
        "audio": str(audio_path),
        "thumbnail": str(thumb_path) if thumb_path else None,
        "meta": meta,
    }


def _download_thumbnail(video_id: str, meta: dict, job_dir: Path) -> Path | None:
    """Save the YouTube thumbnail (key for CTR analysis) to job_dir/thumbnail.jpg.

    Prefers the maxres JPG (skips webp); falls back to the metadata thumbnail URL.
    """
    import urllib.request

    out = job_dir / "thumbnail.jpg"
    urls = [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    ]
    meta_url = meta.get("thumbnail")
    if isinstance(meta_url, str) and meta_url.endswith((".jpg", ".jpeg", ".png")):
        urls.append(meta_url)
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=30).read()
            if data:
                out.write_bytes(data)
                return out
        except Exception:  # noqa: BLE001 - thumbnail is best-effort
            continue
    return None


def _run_ytdlp(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run yt-dlp, falling back to `python -m yt_dlp` when no binary is on PATH."""
    import shutil

    if shutil.which("yt-dlp") is None:
        import sys

        cmd = [sys.executable, "-m", "yt_dlp", *cmd[1:]]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1800)


def _extract_audio(video_path: Path, audio_path: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not audio_path.exists():
        tail = (proc.stderr or "").strip().splitlines()
        raise RuntimeError(f"ffmpeg audio extract failed: {tail[-1] if tail else 'unknown'}")


def _extract_meta(info_path: Path) -> dict:
    if not info_path.exists():
        return {}
    info = json.loads(info_path.read_text(encoding="utf-8"))
    return {k: info.get(k) for k in _META_KEYS}


def _first_existing(job_dir: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        p = job_dir / name
        if p.exists():
            return p
    return None
