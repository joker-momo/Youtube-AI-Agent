"""Transcript fetching for Transcript Lab (standalone tool).

Strategy: youtube-transcript-api (tier 1) -> yt-dlp subtitles (tier 2).
No whisper/audio fallback. Language priority: Spanish first, then English.

Network-free helpers (``extract_video_id``, ``parse_srt``) are kept pure so they
can be unit-tested without hitting the network.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

# Spanish-first language priority. Used by both fetch tiers.
LANGUAGES: list[str] = ["es", "en"]


@dataclass
class Segment:
    """One transcript line with its start time in seconds (None if unknown)."""

    text: str
    start: float | None = None


@dataclass
class TranscriptResult:
    url: str
    video_id: str
    ok: bool
    lang: str | None = None
    source: str | None = None  # "youtube-transcript-api" | "yt-dlp"
    error: str | None = None
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.segments)


# --------------------------------------------------------------------------- #
# Pure helpers (no network)
# --------------------------------------------------------------------------- #


def extract_video_id(url: str) -> str:
    """Extract the 11-char video id from any common YouTube URL shape.

    Supports watch?v=, youtu.be/, shorts/, embed/, and a bare id.
    Raises ValueError if no id can be found.
    """
    url = url.strip()
    if not url:
        raise ValueError("empty url")

    # Bare 11-char id.
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")

    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
        if _is_video_id(candidate):
            return candidate

    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            vid = parse_qs(parsed.query).get("v", [""])[0]
            if _is_video_id(vid):
                return vid
        # /shorts/<id>, /embed/<id>, /v/<id>, /live/<id>
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "v", "live"}:
            if _is_video_id(parts[1]):
                return parts[1]

    # Last resort: scan for an id-looking token in the query.
    vid = parse_qs(parsed.query).get("v", [""])[0]
    if _is_video_id(vid):
        return vid

    raise ValueError(f"could not extract video id from: {url}")


def _is_video_id(candidate: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate))


_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
_SRT_TAG = re.compile(r"<[^>]+>")


def parse_srt(text: str) -> list[Segment]:
    """Parse an SRT (or VTT-ish) subtitle string into ordered Segments.

    De-duplicates consecutive identical lines (common in auto-captions).
    """
    segments: list[Segment] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    last_text: str | None = None

    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue

        start: float | None = None
        body_lines: list[str] = []
        for ln in lines:
            if ln.strip().isdigit() and start is None and not body_lines:
                continue  # sequence index
            m = _SRT_TIME.search(ln)
            if m:
                h, mi, s, ms = (int(m.group(i)) for i in range(1, 5))
                start = h * 3600 + mi * 60 + s + ms / 1000.0
                continue
            body_lines.append(_SRT_TAG.sub("", ln).strip())

        body = " ".join(b for b in body_lines if b).strip()
        if not body or body == last_text:
            continue
        segments.append(Segment(text=body, start=start))
        last_text = body

    return segments


# --------------------------------------------------------------------------- #
# Network tiers
# --------------------------------------------------------------------------- #


def _fetch_via_api(video_id: str) -> tuple[list[Segment], str]:
    """Tier 1: youtube-transcript-api. Returns (segments, lang)."""
    from youtube_transcript_api import YouTubeTranscriptApi  # lazy import

    raw = None
    lang = LANGUAGES[0]

    # Newer (>=1.0) instance API.
    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id, languages=LANGUAGES)
    lang = getattr(fetched, "language_code", lang)
    raw = list(fetched)

    segments: list[Segment] = []
    for item in raw:
        if isinstance(item, dict):
            txt, start = item.get("text", ""), item.get("start")
        else:
            txt, start = getattr(item, "text", ""), getattr(item, "start", None)
        txt = (txt or "").strip()
        if txt:
            segments.append(Segment(text=txt, start=start))

    if not segments:
        raise RuntimeError("youtube-transcript-api returned no segments")
    return segments, lang


def _fetch_via_ytdlp(video_id: str) -> tuple[list[Segment], str]:
    """Tier 2: yt-dlp subtitles -> SRT -> parse. Returns (segments, lang)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as tmp:
        out_tmpl = os.path.join(tmp, "%(id)s.%(ext)s")
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "es.*,en.*",
            "--convert-subs",
            "srt",
            "-o",
            out_tmpl,
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        srt_files = sorted(glob.glob(os.path.join(tmp, "*.srt")))
        if not srt_files:
            stderr = (proc.stderr or "").strip().splitlines()
            tail = stderr[-1] if stderr else "no subtitles produced"
            raise RuntimeError(f"yt-dlp: {tail}")

        # Prefer an es.* track, else first.
        chosen = next((f for f in srt_files if ".es" in os.path.basename(f)), srt_files[0])
        lang = "es" if ".es" in os.path.basename(chosen) else "en"
        with open(chosen, encoding="utf-8") as fh:
            segments = parse_srt(fh.read())

    if not segments:
        raise RuntimeError("yt-dlp produced an empty subtitle file")
    return segments, lang


def fetch_transcript(url: str) -> TranscriptResult:
    """Fetch a transcript for one URL, trying tier 1 then tier 2."""
    try:
        video_id = extract_video_id(url)
    except ValueError as exc:
        return TranscriptResult(url=url, video_id="", ok=False, error=str(exc))

    errors: list[str] = []
    for source, fn in (
        ("youtube-transcript-api", _fetch_via_api),
        ("yt-dlp", _fetch_via_ytdlp),
    ):
        try:
            segments, lang = fn(video_id)
            return TranscriptResult(
                url=url,
                video_id=video_id,
                ok=True,
                lang=lang,
                source=source,
                segments=segments,
            )
        except Exception as exc:  # noqa: BLE001 - record and fall through to next tier
            errors.append(f"{source}: {exc}")

    return TranscriptResult(
        url=url, video_id=video_id, ok=False, error=" | ".join(errors)
    )
