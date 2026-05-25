from __future__ import annotations

import asyncio
import os
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


# ``fast`` is the default because the browser is signed in with a real
# Chrome profile, persisted cookies, and real WebGL/audio fingerprints —
# ChatGPT/Claude do not throttle real accounts, so the long human-style
# pauses add latency without anti-bot value. Set ``BROWSER_HUMAN_MODE=human``
# to restore the slower, conspicuously-human cadence if a session ever needs
# extra trust-building (e.g. recovering from a temporary rate-limit).
HUMAN_MODE = os.environ.get("BROWSER_HUMAN_MODE", "fast").strip().lower()
_FAST_MODE = HUMAN_MODE != "human"

# Per-character keystroke delay window. ChatGPT/Claude both accept real
# keystrokes; spacing them out makes the typing speed look human instead
# of an instant insert_text dump. Tune via env without rebuild.
TYPING_MIN_MS = _env_int(
    "BROWSER_HUMAN_TYPING_MIN_MS", 18 if _FAST_MODE else 35
)
TYPING_MAX_MS = _env_int(
    "BROWSER_HUMAN_TYPING_MAX_MS", 55 if _FAST_MODE else 110
)

# Idle pauses between high-level actions (page load -> modal dismiss
# -> composer click -> typing -> send click). Burst-style automation
# without these reads as obviously bot, but the slow defaults add
# multiple seconds per turn; the fast profile keeps just enough jitter
# to avoid identical timing.
PAUSE_MIN_MS = _env_int(
    "BROWSER_HUMAN_PAUSE_MIN_MS", 100 if _FAST_MODE else 400
)
PAUSE_MAX_MS = _env_int(
    "BROWSER_HUMAN_PAUSE_MAX_MS", 400 if _FAST_MODE else 1400
)

# Occasionally insert a "thinking" pause inside long typing runs so the
# cadence is not perfectly uniform. Fast mode keeps it on but rarer.
THINKING_PAUSE_MIN_MS = _env_int(
    "BROWSER_HUMAN_THINK_MIN_MS", 120 if _FAST_MODE else 200
)
THINKING_PAUSE_MAX_MS = _env_int(
    "BROWSER_HUMAN_THINK_MAX_MS", 400 if _FAST_MODE else 900
)
THINKING_PROBABILITY = _env_float(
    "BROWSER_HUMAN_THINK_PROB", 0.02 if _FAST_MODE else 0.04
)

# Above this length we paste instead of typing — humans paste long
# prompts too, and per-char typing 2 000+ chars takes minutes. Fast mode
# pastes earlier and shortens the post-paste review pause.
PASTE_THRESHOLD_CHARS = _env_int(
    "BROWSER_HUMAN_PASTE_THRESHOLD", 100 if _FAST_MODE else 200
)
PASTE_REVIEW_PAUSE_MIN_MS = _env_int(
    "BROWSER_HUMAN_PASTE_PAUSE_MIN_MS", 400 if _FAST_MODE else 1500
)
PASTE_REVIEW_PAUSE_MAX_MS = _env_int(
    "BROWSER_HUMAN_PASTE_PAUSE_MAX_MS", 1200 if _FAST_MODE else 3500
)

# Post-response read pause window applied after a model finishes streaming.
# Useful in human mode to look natural; useless in fast pipeline mode.
POST_READ_PAUSE_ENABLED = (
    os.environ.get("BROWSER_HUMAN_POST_READ_PAUSE", "1" if not _FAST_MODE else "0").strip()
    not in {"0", "false", "False", "no", ""}
)

# How long the stability watcher should wait after the stream stops before
# trusting the response is complete. Fast mode trims this aggressively;
# the stop-button-hidden state already proved the response finished.
STABLE_MS = _env_int(
    "BROWSER_HUMAN_STABLE_MS", 600 if _FAST_MODE else 2000
)
STABLE_POLL_MS = _env_int(
    "BROWSER_HUMAN_STABLE_POLL_MS", 250 if _FAST_MODE else 500
)


async def human_pause(page: "Page", *, min_ms: int | None = None, max_ms: int | None = None) -> None:
    """Sleep a randomised interval between two high-level UI actions."""
    lo = min_ms if min_ms is not None else PAUSE_MIN_MS
    hi = max_ms if max_ms is not None else PAUSE_MAX_MS
    if hi <= lo:
        hi = lo + 1
    await page.wait_for_timeout(random.randint(lo, hi))


async def human_click(locator, *, hover_pause_min_ms: int = 80, hover_pause_max_ms: int = 240, post_pause_min_ms: int = 250, post_pause_max_ms: int = 700, click_timeout_ms: int = 5_000) -> None:
    """Hover, pause, click, pause — the cadence of a human pointer.

    Falls back to a plain click if hover is unsupported (e.g. headless
    runtimes that disable real pointer events).
    """
    try:
        await locator.hover(timeout=2_000)
        await asyncio.sleep(random.randint(hover_pause_min_ms, hover_pause_max_ms) / 1000.0)
    except Exception:
        pass
    await locator.click(timeout=click_timeout_ms)
    await asyncio.sleep(random.randint(post_pause_min_ms, post_pause_max_ms) / 1000.0)


def estimate_read_pause_ms(text: str) -> int:
    """Reading-time pause applied after a model response arrives.

    ``BROWSER_HUMAN_MODE=fast`` (default) returns 0 — the pipeline is the
    only "reader" of these tabs, so a 0.8–4 s linger per turn is pure
    latency. Set ``BROWSER_HUMAN_MODE=human`` (or
    ``BROWSER_HUMAN_POST_READ_PAUSE=1``) to restore the natural cadence
    when reviewing live in a shared tab.
    """
    if not POST_READ_PAUSE_ENABLED:
        return 0
    words = max(len(text.split()), 1)
    base_ms = int((words / 300.0) * 60_000)  # words / (wpm) * 60 000 ms
    jitter = random.randint(-300, 600)
    return max(800, min(4_000, base_ms + jitter))


async def human_type(page: "Page", text: str) -> None:
    """Insert ``text`` into the focused field the way a person would.

    - Long text (> ``PASTE_THRESHOLD_CHARS``): paste-like fast insert,
      then a 1.5-3.5 s "reading what I just pasted" pause. Per-char
      typing of multi-KB prompts would take minutes and isn't how a
      real user submits a long prompt anyway.
    - Short text: per-character keystrokes with randomised inter-key
      delay and rare longer "thinking" pauses.
    """
    if not text:
        return

    if len(text) > PASTE_THRESHOLD_CHARS:
        await page.keyboard.insert_text(text)
        await asyncio.sleep(
            random.randint(PASTE_REVIEW_PAUSE_MIN_MS, PASTE_REVIEW_PAUSE_MAX_MS)
            / 1000.0
        )
        return

    if len(text) <= 1:
        await page.keyboard.type(text, delay=random.randint(TYPING_MIN_MS, TYPING_MAX_MS))
        return

    for ch in text:
        await page.keyboard.type(ch, delay=random.randint(TYPING_MIN_MS, TYPING_MAX_MS))
        if random.random() < THINKING_PROBABILITY:
            await asyncio.sleep(
                random.randint(THINKING_PAUSE_MIN_MS, THINKING_PAUSE_MAX_MS) / 1000.0
            )
