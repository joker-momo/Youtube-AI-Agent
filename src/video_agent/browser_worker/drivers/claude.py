from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    save_trace_screenshot,
)
from video_agent.browser_worker.drivers.humanize import (
    estimate_read_pause_ms,
    human_click,
    human_pause,
    human_type,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

CLAUDE_URL = "https://claude.ai/new"

# Claude rollout/locale can vary. We try explicit incognito/temporary
# toggles first, then fall back to a fresh "new chat" so sessions still
# avoid stale context bleed.
TEMP_CHAT_TOGGLE_SELECTORS = (
    "button[aria-label*='Incognito' i]",
    "button[aria-label*='Temporary' i]",
    "button:has-text('Incognito')",
    "button:has-text('Temporary')",
    "[data-testid*='incognito' i]",
)

NEW_CHAT_SELECTORS = (
    "a[href='/new']",
    "button[aria-label*='New chat' i]",
    "button:has-text('New chat')",
)

COMPOSER_SELECTORS = (
    "div[contenteditable='true'][role='textbox']",
    "div[contenteditable='true'][data-lexical-editor='true']",
    "textarea[placeholder*='Message']",
    "[contenteditable='true'][role='textbox']",
)

SEND_BUTTON_SELECTORS = (
    "button[aria-label*='Send' i]",
    "button[data-testid='send-message-button']",
    "button[data-testid*='send' i]",
    "button:has-text('Send')",
)

STOP_BUTTON_SELECTORS = (
    "button[aria-label*='Stop' i]",
    "button:has-text('Stop')",
)


def _is_login_url(url: str) -> bool:
    lower = (url or "").lower()
    return (
        "claude.ai/login" in lower
        or "claude.ai/sign-in" in lower
        or "/signin" in lower
        or "/auth/" in lower
    )


async def _first_matching(page: "Page", selectors: tuple[str, ...], timeout_ms: int):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception:
            continue
    return None


async def _try_click(page: "Page", selectors: tuple[str, ...], timeout_ms: int = 1500) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.is_visible(timeout=timeout_ms):
                await human_click(locator, click_timeout_ms=3_000)
                return True
        except Exception:
            continue
    return False


async def _enter_temporary_chat(page: "Page") -> None:
    if await _try_click(page, TEMP_CHAT_TOGGLE_SELECTORS):
        await human_pause(page, min_ms=600, max_ms=1300)
        return
    if await _try_click(page, NEW_CHAT_SELECTORS):
        await human_pause(page, min_ms=600, max_ms=1300)


class ClaudeDriver:
    """Session-style Claude driver for persistent temp-chat workflows."""

    def __init__(self, page: "Page") -> None:
        self.page = page
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        await self.page.goto(CLAUDE_URL, wait_until="domcontentloaded", timeout=30_000)
        await human_pause(self.page, min_ms=1200, max_ms=2200)

        if _is_login_url(self.page.url):
            shot = await save_trace_screenshot(self.page, prefix="claude-login")
            raise LoginRequiredError(
                "Claude profile is signed out. Open http://localhost:7900 "
                "to sign in.",
                screenshot_path=shot,
            )

        await _enter_temporary_chat(self.page)
        await human_pause(self.page)
        self._opened = True

    async def send_message(self, prompt: str, *, response_timeout_ms: int = 300_000) -> str:
        if not self._opened:
            await self.open()
        if not prompt.strip():
            raise BrowserDriverError("Empty prompt")

        # Composite scrape so stable-wait can detect either a fresh JSON
        # payload or a simple acknowledgement text.
        scrape_js = """
            () => {
              const text = document.body.innerText || '';
              const objects = [];
              let depth = 0;
              let start = -1;
              for (let i = 0; i < text.length; i++) {
                const ch = text[i];
                if (ch === '{') {
                  if (depth === 0) start = i;
                  depth++;
                } else if (ch === '}') {
                  if (depth > 0) {
                    depth--;
                    if (depth === 0 && start >= 0) {
                      objects.push(text.slice(start, i + 1));
                      start = -1;
                    }
                  }
                }
              }
              const responses = objects.filter(o =>
                !o.includes('Cambia de sombrero')
                && !o.includes('Restricciones absolutas')
              );
              const selectors = [
                "[data-testid^='ai-turn']",
                "[data-testid='assistant-message']",
                "div[data-is-streaming='false']",
                "[data-is-streaming='false']",
                "[data-testid*='assistant-message']",
                "article[data-role='assistant']",
                "[data-is-streaming='false'] .prose",
                ".prose",
                "main [role='article']",
              ];
              let knownText = '';
              for (const s of selectors) {
                const nodes = document.querySelectorAll(s);
                if (nodes.length === 0) continue;
                const last = nodes[nodes.length - 1];
                const t = (last.innerText || '').trim();
                if (t && !t.includes('Cambia de sombrero')
                      && !t.includes('Restricciones absolutas')) {
                  knownText = t;
                  break;
                }
              }
              const last = knownText || (responses.length ? responses[responses.length - 1] : '');
              return `[count=${responses.length}]\\n${last}`;
            }
        """
        prior_text = await self.page.evaluate(scrape_js)

        composer = await _first_matching(self.page, COMPOSER_SELECTORS, 10_000)
        if composer is None:
            shot = await save_trace_screenshot(self.page, prefix="claude-no-composer")
            raise BrowserDriverError(
                "Claude composer not found.", screenshot_path=shot
            )

        # See chatgpt driver for rationale: use ``human_pause`` defaults so
        # BROWSER_HUMAN_MODE controls the per-turn cadence centrally.
        await human_click(composer, hover_pause_min_ms=60, hover_pause_max_ms=200)
        await composer.focus()
        await human_pause(self.page)
        await human_type(self.page, prompt)
        await human_pause(self.page)

        send_button = await _first_matching(self.page, SEND_BUTTON_SELECTORS, 5_000)
        if send_button is None:
            shot = await save_trace_screenshot(self.page, prefix="claude-no-send")
            raise BrowserDriverError(
                "Claude send button not found.", screenshot_path=shot
            )
        await human_click(send_button)
        # Claude sometimes ignores a click while composer focus is still
        # transitioning; an immediate Enter mirrors a human retry and
        # helps reliably submit without waiting a full timeout window.
        try:
            await self.page.keyboard.press("Enter")
        except Exception:
            pass

        try:
            stop = await _first_matching(self.page, STOP_BUTTON_SELECTORS, 5_000)
            if stop is not None:
                await stop.wait_for(state="hidden", timeout=response_timeout_ms)
        except Exception:
            pass

        from video_agent.browser_worker.drivers.chatgpt import (
            _wait_for_stable_response,
        )

        text = await _wait_for_stable_response(
            self.page,
            scrape_js,
            prior_text,
            response_timeout_ms=response_timeout_ms,
            log_tag="claude",
        )
        if text is None:
            shot = await save_trace_screenshot(self.page, prefix="claude-no-response")
            raise BrowserDriverError(
                "Claude response did not arrive in time.",
                screenshot_path=shot,
            )
        text = re.sub(r"^\[count=\d+\]\n", "", text)
        if not text:
            shot = await save_trace_screenshot(self.page, prefix="claude-empty")
            raise BrowserDriverError(
                "Claude returned an empty response.",
                screenshot_path=shot,
            )
        # Claude often prepends commentary and may include multiple JSON
        # blocks. Keep only the last valid QA-like JSON object so
        # downstream promotion parses the intended payload.
        normalised = _extract_last_qa_json(text)
        if normalised:
            text = normalised
        await human_pause(
            self.page,
            min_ms=estimate_read_pause_ms(text),
            max_ms=estimate_read_pause_ms(text) + 200,
        )
        return text

    async def send(self, prompt: str, *, response_timeout_ms: int = 300_000) -> str:
        await self.open()
        return await self.send_message(prompt, response_timeout_ms=response_timeout_ms)


def _extract_last_qa_json(text: str) -> str | None:
    depth = 0
    start = -1
    candidates: list[str] = []
    for idx, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append(text[start : idx + 1])
                    start = -1
    picked: dict | None = None
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict) and "verdict" in obj and "scores" in obj:
            picked = obj
    if picked is None:
        return None
    return json.dumps(picked, ensure_ascii=False)
