from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    save_trace_screenshot,
)
from video_agent.browser_worker.drivers.humanize import (
    human_click,
    human_pause,
)
from video_agent.storage.atomic import atomic_write_text

if TYPE_CHECKING:
    from playwright.async_api import Page


# vidIQ free tier surfaces keyword data via the Chrome extension
# overlay on YouTube search results pages. We navigate to each
# search URL and scrape the "Search Companion" panel the extension
# injects into the right sidebar.
YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query={query}"

VIDIQ_OVERLAY_READY_TIMEOUT_MS = 12_000


def _is_login_url(url: str) -> bool:
    return (
        "accounts.google.com" in url
        or "app.vidiq.com/login" in url
        or "app.vidiq.com/auth" in url
    )


_SCORE_RE = re.compile(r"(?P<num>\d{1,3})\s*\n?\s*Overall Score", re.IGNORECASE)
_VOLUME_RE = re.compile(r"VOLUME\s*\n?\s*(?P<v>[A-Za-z ]+?)\s*(?:\n|COMPETITION)", re.IGNORECASE)
_COMPETITION_RE = re.compile(
    r"COMPETITION\s*\n?\s*(?P<c>[A-Za-z ]+?)\s*(?:\n|VPH|Search)", re.IGNORECASE
)
_RELATED_BLOCK_RE = re.compile(
    r"Related keywords\s*\n(?P<body>.+?)(?:\n\s*Unlock|\Z)", re.IGNORECASE | re.DOTALL
)
_RELATED_ITEM_RE = re.compile(r"^(?P<kw>.+?)\s*\n\s*(?P<score>\d{1,3})\s*$", re.MULTILINE)


def parse_vidiq_overlay(text: str) -> dict:
    """Parse the vidIQ Search Companion overlay innerText into a dict.

    Tolerant of missing fields: each value is None / [] when absent.
    Exposed at module level so tests can exercise the parser without a
    live browser.
    """
    score = volume = competition = None
    m = _SCORE_RE.search(text)
    if m:
        try:
            score = int(m.group("num"))
        except ValueError:
            pass
    m = _VOLUME_RE.search(text)
    if m:
        volume = m.group("v").strip()
    m = _COMPETITION_RE.search(text)
    if m:
        competition = m.group("c").strip()

    related: list[dict] = []
    m = _RELATED_BLOCK_RE.search(text)
    if m:
        for item in _RELATED_ITEM_RE.finditer(m.group("body")):
            kw = item.group("kw").strip()
            try:
                related.append({"keyword": kw, "score": int(item.group("score"))})
            except ValueError:
                continue

    return {
        "score": score,
        "volume": volume,
        "competition": competition,
        "related": related,
    }


class VidIQDriver:
    """vidIQ Search Companion driver (free tier via Chrome extension).

    For each keyword we navigate to YouTube's search results URL and
    let the installed vidIQ Chrome extension inject its right-sidebar
    overlay; then we scrape the panel innerText and parse out
    overall score, volume, competition, and related keywords. There
    is no paid-API call.
    """

    def __init__(self, page: "Page") -> None:
        self.page = page
        self._opened = False

    async def open(self) -> None:
        """Navigate to a neutral YouTube page once to warm up the extension."""
        if self._opened:
            return
        await self.page.goto(
            "https://www.youtube.com/",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await human_pause(self.page, min_ms=1200, max_ms=2200)
        if _is_login_url(self.page.url):
            shot = await save_trace_screenshot(self.page, prefix="vidiq-login")
            raise LoginRequiredError(
                "vidIQ/Google profile is signed out. Open "
                "http://localhost:7900 and sign in to YouTube and vidIQ.",
                screenshot_path=shot,
            )
        self._opened = True

    async def score_keyword(self, keyword: str, *, response_timeout_ms: int = 30_000) -> dict:
        """Return ``{score, volume, competition, related}`` for ``keyword``.

        Raises ``BrowserDriverError`` if the overlay never appears or
        the score field cannot be parsed.
        """
        if not self._opened:
            await self.open()
        if not keyword.strip():
            raise BrowserDriverError("Empty keyword")

        # Detour to about:blank first so the SPA re-mounts the vidIQ
        # overlay for the new keyword. Without this round trip vidIQ
        # reuses the prior keyword's panel since the YouTube
        # results-page route navigates client-side and the extension's
        # observer fires inconsistently.
        url = YOUTUBE_SEARCH_URL.format(query=quote_plus(keyword))
        try:
            await self.page.goto("about:blank", wait_until="domcontentloaded", timeout=10_000)
        except Exception:
            pass
        await human_pause(self.page, min_ms=400, max_ms=900)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await human_pause(self.page, min_ms=2000, max_ms=3500)

        # Wait for the overlay to render the Overall Score text.
        scrape_js = """
            () => {
              const candidates = document.querySelectorAll('[class*="vidiq" i]');
              for (const el of candidates) {
                const t = (el.innerText || '').trim();
                if (t.includes('Overall Score') || t.includes('Keyword Score')) {
                  return t;
                }
              }
              return '';
            }
        """
        import asyncio
        import time

        # Wait until the panel renders a numeric score next to the
        # "Overall Score" label AND the panel's "SEARCH TERM:" matches
        # the current keyword. Without the search-term check we sometimes
        # scrape the prior keyword's score from a not-yet-refreshed
        # overlay between consecutive queries.
        deadline = time.monotonic() + response_timeout_ms / 1000.0
        text = ""
        score_ready_re = re.compile(
            r"\d{1,3}\s*\n?\s*Overall Score|Not enough search data",
            re.IGNORECASE,
        )
        kw_re = re.compile(
            r"SEARCH TERM:\s*\n+\s*[“\"']\s*"
            + re.escape(keyword)
            + r"\s*[”\"']",
            re.IGNORECASE,
        )
        while time.monotonic() < deadline:
            text = await self.page.evaluate(scrape_js)
            if text and kw_re.search(text) and score_ready_re.search(text):
                break
            await self.page.wait_for_timeout(500)

        if not text or not re.search(r"\bOverall Score\b", text):
            shot = await save_trace_screenshot(self.page, prefix="vidiq-no-overlay")
            raise BrowserDriverError(
                f"vidIQ overlay did not appear for keyword {keyword!r}.",
                screenshot_path=shot,
            )

        parsed = parse_vidiq_overlay(text)
        parsed["keyword"] = keyword
        parsed["raw_overlay_preview"] = text[:400]
        if parsed["score"] is None:
            # vidIQ free tier sometimes shows "Not enough search data"
            # for low-volume keywords. Soft-fail with score=None + a
            # ``note`` field so the orchestrator can treat it as a
            # low-signal hit instead of aborting the whole batch.
            try:
                from pathlib import Path as _P
                dump = _P("/data/trace") / f"vidiq-raw-{keyword[:30].replace(' ', '_')}.txt"
                dump.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(dump, text, encoding="utf-8")
            except Exception:
                pass
            if "not enough search data" in text.lower():
                parsed["note"] = "not_enough_search_data"
                return parsed
            shot = await save_trace_screenshot(self.page, prefix="vidiq-no-score")
            raise BrowserDriverError(
                f"vidIQ overlay did not yield a score for {keyword!r}.",
                screenshot_path=shot,
            )
        return parsed

    async def score_keywords(self, keywords: list[str]) -> list[dict]:
        """Score each keyword sequentially; returns one dict per keyword.

        Errors per keyword are captured into the dict as ``error`` so
        a single bad keyword does not abort the whole list.
        """
        results = []
        for kw in keywords:
            try:
                results.append(await self.score_keyword(kw))
            except BrowserDriverError as exc:
                results.append({"keyword": kw, "error": str(exc)})
        return results
