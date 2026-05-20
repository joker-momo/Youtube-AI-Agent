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


# Per-character keystroke delay window. ChatGPT/Gemini both accept
# real keystrokes; spacing them out makes the typing speed look human
# instead of an instant insert_text dump. Tune via env without rebuild.
TYPING_MIN_MS = _env_int("BROWSER_HUMAN_TYPING_MIN_MS", 35)
TYPING_MAX_MS = _env_int("BROWSER_HUMAN_TYPING_MAX_MS", 110)

# Idle pauses between high-level actions (page load -> modal dismiss
# -> composer click -> typing -> send click). Burst-style automation
# without these reads as obviously bot.
PAUSE_MIN_MS = _env_int("BROWSER_HUMAN_PAUSE_MIN_MS", 400)
PAUSE_MAX_MS = _env_int("BROWSER_HUMAN_PAUSE_MAX_MS", 1400)

# Occasionally insert a "thinking" pause inside long typing runs so
# the cadence is not perfectly uniform.
THINKING_PAUSE_MIN_MS = _env_int("BROWSER_HUMAN_THINK_MIN_MS", 200)
THINKING_PAUSE_MAX_MS = _env_int("BROWSER_HUMAN_THINK_MAX_MS", 900)
THINKING_PROBABILITY = 0.04  # ~1 per 25 chars on average

# Above this length we paste instead of typing — humans paste long
# prompts too, and per-char typing 2 000+ chars takes minutes.
PASTE_THRESHOLD_CHARS = _env_int("BROWSER_HUMAN_PASTE_THRESHOLD", 200)
PASTE_REVIEW_PAUSE_MIN_MS = _env_int("BROWSER_HUMAN_PASTE_PAUSE_MIN_MS", 1500)
PASTE_REVIEW_PAUSE_MAX_MS = _env_int("BROWSER_HUMAN_PASTE_PAUSE_MAX_MS", 3500)


async def human_pause(page: "Page", *, min_ms: int | None = None, max_ms: int | None = None) -> None:
    """Sleep a randomised interval between two high-level UI actions."""
    lo = min_ms if min_ms is not None else PAUSE_MIN_MS
    hi = max_ms if max_ms is not None else PAUSE_MAX_MS
    if hi <= lo:
        hi = lo + 1
    await page.wait_for_timeout(random.randint(lo, hi))


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
