"""YouTube channel sync + published-video duplicate detection."""

from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

try:
    from defusedxml import ElementTree as ET  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - falls back when defusedxml missing
    import xml.etree.ElementTree as ET  # noqa: S405 — XXE-safe payload only via defusedxml; install defusedxml

from video_agent.orchestrator.idea_constants import *  # noqa: F401,F403
from video_agent.storage.atomic import atomic_write_json


def _yt_rss_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def _yt_videos_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}/videos"


def _decode_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def _title_from_accessibility_label(label: str) -> str:
    label = html.unescape(_decode_json_string(label)).strip()
    label = re.sub(
        r"\s+\d+\s+(?:phút|minutos?|minutes?|giây|segundos?|seconds?).*$",
        "",
        label,
        flags=re.IGNORECASE,
    ).strip()
    return label


def _parse_channel_videos_page(raw_html: str, channel_id: str) -> list[dict]:
    """Extract public video ids/titles from YouTube channel /videos HTML.

    YouTube's RSS endpoint can return 404 even when the channel page lists
    public videos. This fallback reads only stable public page data and keeps
    duplicate detection working with id + title.
    """
    pattern = re.compile(
        r'"(?:contentId|videoId)":"(?P<id>[A-Za-z0-9_-]{11})"'
        r'.{0,2500}?"accessibilityContext":\{"label":"(?P<label>[^"]+)"',
        re.DOTALL,
    )
    seen: set[str] = set()
    videos: list[dict] = []
    for match in pattern.finditer(raw_html):
        vid_id = match.group("id")
        if vid_id in seen:
            continue
        title = _title_from_accessibility_label(match.group("label"))
        if not title or not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", title):
            continue
        seen.add(vid_id)
        videos.append(
            {
                "id": vid_id,
                "title": title,
                "published": "",
                "updated": None,
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "author": channel_id,
                "description": None,
                "views": None,
                "rating_average": None,
                "rating_count": None,
            }
        )
    return videos


def _fetch_channel_page_videos(channel_id: str) -> list[dict]:
    req = urllib.request.Request(_yt_videos_url(channel_id), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return _parse_channel_videos_page(raw, channel_id)


def sync_published_videos(channel_config: dict, configs_dir: Path) -> list[dict]:
    """Fetch published videos from YouTube RSS and save to published_videos.json.

    Returns list of video dicts: [{id, title, published}].
    Raises ValueError if youtube_channel_id not set in channel config.
    """
    channel_id = channel_config.get("channel", {}).get("youtube_channel_id")
    ch_id = channel_config.get("channel", {}).get("id", "")
    if not channel_id:
        raise ValueError(f"youtube_channel_id not set in channel config for {ch_id}")

    url = _yt_rss_url(channel_id)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except Exception as exc:
        videos = _fetch_channel_page_videos(channel_id)
        if not videos:
            raise ValueError(f"Failed to fetch YouTube RSS for {channel_id}: {exc}") from exc
        out_path = configs_dir / ch_id / PUBLISHED_VIDEOS_FILE
        atomic_write_json(
            out_path,
            {
                "channel_id": channel_id,
                "source": "channel_page_fallback",
                "videos": videos,
                "synced_at": datetime.now(UTC).isoformat(),
            },
        )
        return videos

    root = ET.fromstring(raw)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    videos: list[dict] = []
    for entry in root.findall("atom:entry", ns):
        vid_id = entry.findtext("yt:videoId", namespaces=ns) or ""
        title = entry.findtext("atom:title", namespaces=ns) or ""
        published = entry.findtext("atom:published", namespaces=ns) or ""
        updated = entry.findtext("atom:updated", namespaces=ns) or ""
        description = entry.findtext("media:group/media:description", namespaces=ns) or ""
        # Watch URL is exposed as the entry's <link rel="alternate"> href. We
        # also derive a thumbnail URL from the video id (the RSS feed itself
        # lists multiple thumbnail variants; the mqdefault.jpg form is the
        # smallest stable one and is good enough for the dashboard).
        link_el = entry.find("atom:link[@rel='alternate']", ns)
        url = (
            link_el.attrib.get("href")
            if link_el is not None
            else (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
        )
        author_name = entry.findtext("atom:author/atom:name", namespaces=ns) or ""

        # YouTube RSS exposes ``media:community`` per entry with:
        #   - ``media:statistics views="N"`` — total view count
        #   - ``media:starRating count="N" average="X" min="1" max="5"``
        #     — like ratio (the visible 👍 count is not in RSS; average
        #     rating is a public 0-5 scale proxy).
        views: int | None = None
        stats = entry.find("media:group/media:community/media:statistics", ns)
        if stats is not None:
            raw_views = stats.attrib.get("views")
            if raw_views and raw_views.isdigit():
                views = int(raw_views)
        rating_avg: float | None = None
        rating_count: int | None = None
        rating = entry.find("media:group/media:community/media:starRating", ns)
        if rating is not None:
            try:
                rating_avg = float(rating.attrib.get("average", "0")) or None
            except ValueError:
                rating_avg = None
            try:
                rating_count = int(rating.attrib.get("count", "0")) or None
            except ValueError:
                rating_count = None

        if vid_id and title:
            videos.append(
                {
                    "id": vid_id,
                    "title": title,
                    "published": published,
                    "updated": updated or None,
                    "url": url,
                    "author": author_name or None,
                    "description": description or None,
                    "views": views,
                    "rating_average": rating_avg,
                    "rating_count": rating_count,
                }
            )

    # Sort newest first so the dashboard does not need to re-order on every
    # render. ``published`` is ISO-8601 with timezone so a lexical reverse
    # sort matches chronological order.
    videos.sort(key=lambda v: str(v.get("published") or ""), reverse=True)

    out_path = configs_dir / ch_id / PUBLISHED_VIDEOS_FILE
    atomic_write_json(
        out_path,
        {
            "channel_id": channel_id,
            "source": "rss",
            "videos": videos,
            "synced_at": datetime.now(UTC).isoformat(),
        },
    )
    return videos


def load_published_videos(channel_config: dict, configs_dir: Path) -> list[dict]:
    """Load cached published_videos.json. Returns [] if file missing or empty."""
    ch_id = channel_config.get("channel", {}).get("id", "")
    path = configs_dir / ch_id / PUBLISHED_VIDEOS_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("videos", [])
    except Exception:
        return []


def _title_tokens(text: str) -> set[str]:
    """Lowercase, accent-strip, split to ≥4-char alpha tokens with stopwords removed."""
    norm = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    tokens = {w for w in re.findall(r"[a-z]{4,}", norm)}
    return tokens - _TITLE_STOPWORDS


def find_duplicate(idea: dict, published: list[dict], overlap_threshold: int = 2) -> str | None:
    """Return matching published video title if the idea genuinely overlaps, else None.

    The duplicate detector used to count any shared 4+-char tokens, which
    fired on filler words ("como", "después") that appear in every
    Spanish wellness title for adults 45+. We now strip a curated
    stopword list before comparison so only content tokens contribute,
    and require ``overlap_threshold`` (default 2) shared content tokens
    before flagging duplication. The match list is the union of the
    idea's title_seed, topic, and target_keyword.
    """
    idea_text = " ".join(
        [
            idea.get("title_seed", ""),
            idea.get("topic", ""),
            idea.get("target_keyword", ""),
        ]
    )
    idea_tokens = _title_tokens(idea_text)
    if not idea_tokens:
        return None
    for vid in published:
        pub_tokens = _title_tokens(vid.get("title", ""))
        shared = idea_tokens & pub_tokens
        if len(shared) >= overlap_threshold:
            return vid["title"]
    return None
