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


# Three cadence profiles available via ``BROWSER_HUMAN_MODE``:
#   - ``balanced`` (default): preserves human-looking typing + pauses while
#     letting the technical detection layer (MutationObserver) cut waste.
#     The visible cadence still looks like a real user typing carefully;
#     savings come from smarter response-stable detection, not from
#     ripping out human-style pauses.
#   - ``fast``: aggressive cuts on visible cadence too. Use when the
#     browser tab is unattended and only the LLM host is observing.
#   - ``human``: original slow cadence, restored for trust-building or
#     live-demo sessions where the tab is being watched by a person.
HUMAN_MODE = os.environ.get("BROWSER_HUMAN_MODE", "balanced").strip().lower()
if HUMAN_MODE not in {"balanced", "fast", "human"}:
    HUMAN_MODE = "balanced"
_FAST_MODE = HUMAN_MODE == "fast"
_BALANCED_MODE = HUMAN_MODE == "balanced"


def _by_mode(fast: int, balanced: int, human: int) -> int:
    if _FAST_MODE:
        return fast
    if _BALANCED_MODE:
        return balanced
    return human


def _by_mode_f(fast: float, balanced: float, human: float) -> float:
    if _FAST_MODE:
        return fast
    if _BALANCED_MODE:
        return balanced
    return human


# Per-character keystroke delay window. ChatGPT/Claude both accept real
# keystrokes; spacing them out makes the typing speed look human instead
# of an instant insert_text dump. Tune via env without rebuild.
#
# Balanced default keeps the keystroke distribution centered around the
# 25-75 ms a careful 60-WPM typist produces — same look as human mode
# minus the slow upper tail (110 ms felt unnecessarily sluggish on long
# prompts).
TYPING_MIN_MS = _env_int(
    "BROWSER_HUMAN_TYPING_MIN_MS", _by_mode(fast=18, balanced=25, human=35)
)
TYPING_MAX_MS = _env_int(
    "BROWSER_HUMAN_TYPING_MAX_MS", _by_mode(fast=55, balanced=75, human=110)
)

# Idle pauses between high-level actions (page load -> modal dismiss
# -> composer click -> typing -> send click). Balanced shaves the longest
# tail (1400 ms) down to ~700 ms so the wait between actions still looks
# deliberate but the pipeline does not stall on randomness alone.
PAUSE_MIN_MS = _env_int(
    "BROWSER_HUMAN_PAUSE_MIN_MS", _by_mode(fast=100, balanced=200, human=400)
)
PAUSE_MAX_MS = _env_int(
    "BROWSER_HUMAN_PAUSE_MAX_MS", _by_mode(fast=400, balanced=700, human=1400)
)

# Occasionally insert a "thinking" pause inside long typing runs so the
# cadence is not perfectly uniform.
THINKING_PAUSE_MIN_MS = _env_int(
    "BROWSER_HUMAN_THINK_MIN_MS", _by_mode(fast=120, balanced=150, human=200)
)
THINKING_PAUSE_MAX_MS = _env_int(
    "BROWSER_HUMAN_THINK_MAX_MS", _by_mode(fast=400, balanced=600, human=900)
)
THINKING_PROBABILITY = _env_float(
    "BROWSER_HUMAN_THINK_PROB", _by_mode_f(fast=0.02, balanced=0.03, human=0.04)
)

# Above this length we paste instead of typing — humans paste long
# prompts too, and per-char typing 2 000+ chars takes minutes.
PASTE_THRESHOLD_CHARS = _env_int(
    "BROWSER_HUMAN_PASTE_THRESHOLD", _by_mode(fast=100, balanced=150, human=200)
)
PASTE_REVIEW_PAUSE_MIN_MS = _env_int(
    "BROWSER_HUMAN_PASTE_PAUSE_MIN_MS", _by_mode(fast=400, balanced=700, human=1500)
)
PASTE_REVIEW_PAUSE_MAX_MS = _env_int(
    "BROWSER_HUMAN_PASTE_PAUSE_MAX_MS", _by_mode(fast=1200, balanced=1800, human=3500)
)

# Post-response read pause window applied after a model finishes streaming.
# Always off outside ``human`` mode — the technical layer never benefits.
POST_READ_PAUSE_ENABLED = (
    os.environ.get(
        "BROWSER_HUMAN_POST_READ_PAUSE",
        "1" if HUMAN_MODE == "human" else "0",
    ).strip()
    not in {"0", "false", "False", "no", ""}
)

# Technical detection knobs. These do NOT affect the visible cadence —
# they only change how the Python side waits for the response to settle.
# The MutationObserver lives entirely inside the page, so ``STABLE_MS``
# 600 ms means "wait until the assistant turn DOM has been quiet for
# 600 ms" rather than "sleep 600 ms". Detection is event-driven, not
# poll-driven.
STABLE_MS = _env_int(
    "BROWSER_HUMAN_STABLE_MS", _by_mode(fast=400, balanced=600, human=1500)
)
STABLE_POLL_MS = _env_int(
    "BROWSER_HUMAN_STABLE_POLL_MS", _by_mode(fast=120, balanced=150, human=300)
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
