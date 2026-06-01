from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    save_layout_diagnostics,
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

ASSISTANT_RESPONSE_SELECTORS = (
    "[data-testid^='ai-turn']",
    "[data-testid='assistant-message']",
    "[data-testid*='assistant-message']",
    "article[data-role='assistant']",
    "[data-is-streaming='false'][data-testid*='message']",
    "[data-is-streaming='false'] .prose",
    "[class*='font-claude']",
)

CLAUDE_DIAGNOSTIC_SELECTORS = (
    *COMPOSER_SELECTORS,
    *SEND_BUTTON_SELECTORS,
    *STOP_BUTTON_SELECTORS,
    *ASSISTANT_RESPONSE_SELECTORS,
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


async def _raise_claude_layout_warning(
    page: "Page", *, prefix: str, message: str
) -> None:
    shot, diagnostic = await save_layout_diagnostics(
        page,
        prefix=prefix,
        selectors=CLAUDE_DIAGNOSTIC_SELECTORS,
    )
    raise BrowserDriverError(
        f"{message} Claude layout may have changed; inspect diagnostic trace.",
        screenshot_path=shot,
        diagnostic_path=diagnostic,
        layout_warning=True,
    )


def _assistant_scrape_js() -> str:
    selectors_json = json.dumps(ASSISTANT_RESPONSE_SELECTORS)
    return f"""
        () => {{
          const selectors = {selectors_json};
          const candidates = [];
          for (const s of selectors) {{
            const nodes = document.querySelectorAll(s);
            for (const node of nodes) {{
              const t = (node.innerText || '').trim();
              if (t && !t.includes('Cambia de sombrero')
                    && !t.includes('Restricciones absolutas')
                    && !t.includes('Write your prompt to Claude')) {{
                candidates.push(t);
              }}
            }}
          }}
          return candidates.length ? candidates[candidates.length - 1] : '';
        }}
    """


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

        # Scrape only Claude assistant turns. Do not fall back to scanning the
        # whole page body: while Claude is still thinking, the user prompt can
        # contain JSON-like text and make the stable waiter return too early.
        scrape_js = _assistant_scrape_js()
        prior_text = await self.page.evaluate(scrape_js)

        composer = await _first_matching(self.page, COMPOSER_SELECTORS, 10_000)
        if composer is None:
            await _raise_claude_layout_warning(
                self.page,
                prefix="claude-no-composer",
                message="Claude composer not found.",
            )

        # Claude's composer is frequently wrapped by animated containers that
        # intercept pointer events. Prefer a human click, but fall back to DOM
        # focus so transient overlays do not turn into hard 502s.
        try:
            await human_click(composer, hover_pause_min_ms=60, hover_pause_max_ms=200)
        except Exception:
            await composer.focus(timeout=3_000)
        await human_pause(self.page)
        await human_type(self.page, prompt)
        await human_pause(self.page)

        send_button = await _first_matching(self.page, SEND_BUTTON_SELECTORS, 5_000)
        if send_button is None:
            await _raise_claude_layout_warning(
                self.page,
                prefix="claude-no-send",
                message="Claude send button not found.",
            )
        try:
            await human_click(send_button)
        except Exception:
            pass
        await self.page.wait_for_timeout(400)
        try:
            still_in_composer = (await composer.inner_text(timeout=1_000)).strip()
        except Exception:
            still_in_composer = ""
        if still_in_composer:
            try:
                await send_button.click(timeout=3_000, force=True)
            except Exception:
                try:
                    await send_button.evaluate("(node) => node.click()")
                except Exception:
                    pass
            await self.page.wait_for_timeout(400)
        # Claude sometimes ignores button clicks while composer focus is still
        # transitioning. Try the common submit shortcuts after the direct click.
        for key in ("Meta+Enter", "Control+Enter", "Enter"):
            try:
                still_in_composer = (await composer.inner_text(timeout=1_000)).strip()
            except Exception:
                still_in_composer = ""
            if not still_in_composer:
                break
            try:
                await self.page.keyboard.press(key)
                await self.page.wait_for_timeout(300)
            except Exception:
                pass
        try:
            still_in_composer = (await composer.inner_text(timeout=1_000)).strip()
        except Exception:
            still_in_composer = ""
        if still_in_composer:
            await _raise_claude_layout_warning(
                self.page,
                prefix="claude-submit-stuck",
                message="Claude prompt remained in the composer after submit attempts.",
            )

        try:
            stop = await _first_matching(self.page, STOP_BUTTON_SELECTORS, 5_000)
            if stop is not None:
                await stop.wait_for(state="hidden", timeout=response_timeout_ms)
        except Exception:
            pass

        from video_agent.browser_worker.drivers.chatgpt import (
            _wait_for_stable_response_legacy,
        )

        text = await _wait_for_stable_response_legacy(
            self.page,
            scrape_js,
            prior_text,
            response_timeout_ms=response_timeout_ms,
            log_tag="claude",
        )
        if text is None:
            await _raise_claude_layout_warning(
                self.page,
                prefix="claude-no-response",
                message="Claude response did not arrive in time.",
            )
        if not text:
            await _raise_claude_layout_warning(
                self.page,
                prefix="claude-empty",
                message="Claude returned an empty response.",
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
