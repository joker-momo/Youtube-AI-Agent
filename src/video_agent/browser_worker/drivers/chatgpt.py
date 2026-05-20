from __future__ import annotations

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

CHATGPT_URL = "https://chatgpt.com/?model=gpt-4o&temporary-chat=true"

# Composer textarea is a contenteditable div in the current UI; the
# textarea fallback covers legacy renders.
COMPOSER_SELECTORS = (
    "div#prompt-textarea[contenteditable='true']",
    "textarea#prompt-textarea",
    "textarea[placeholder*='Message']",
    "[contenteditable='true'][role='textbox']",
)

SEND_BUTTON_SELECTORS = (
    "[data-testid='send-button']",
    "[data-testid='fruitjuice-send-button']",
    "button[aria-label='Send prompt']",
    "button[aria-label*='Send']",
)

STOP_BUTTON_SELECTORS = (
    "[data-testid='stop-button']",
    "button[aria-label='Stop generating']",
    "button[aria-label*='Stop']",
)

ASSISTANT_TURN_SELECTOR = "[data-message-author-role='assistant']"
MARKDOWN_BODY_SELECTOR = ".markdown.prose, [data-message-author-role='assistant'] .markdown"


def _is_login_url(url: str) -> bool:
    return (
        "auth.openai.com" in url
        or "/auth/login" in url
        or re.search(r"chatgpt\.com/(login|auth)", url) is not None
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


async def _dismiss_modals(page: "Page") -> None:
    """Best-effort dismiss of ChatGPT consent / onboarding dialogs.

    The ?temporary-chat=true URL surfaces a "No model training" dialog
    whose backdrop intercepts pointer events; until it is closed the
    composer cannot be clicked. We click the most common confirmation
    buttons and fall back to pressing Escape.
    """
    dismiss_selectors = (
        "dialog button:has-text('Continue')",
        "dialog button:has-text('Got it')",
        "dialog button:has-text('Okay')",
        "dialog button:has-text('OK')",
        "dialog button:has-text('I understand')",
        "dialog button[aria-label*='Close']",
    )
    for _ in range(3):
        any_dialog = page.locator("dialog[open]").first
        try:
            visible = await any_dialog.is_visible(timeout=500)
        except Exception:
            visible = False
        if not visible:
            return
        # A real user spots the dialog before reacting.
        await human_pause(page, min_ms=350, max_ms=900)
        clicked = False
        for selector in dismiss_selectors:
            button = page.locator(selector).first
            try:
                if await button.is_visible(timeout=300):
                    await human_click(button)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
        await human_pause(page, min_ms=300, max_ms=700)


class ChatGPTDriver:
    """Single-shot ChatGPT driver: open temporary chat, send, scrape.

    The driver does **not** log the user in. If the dedicated profile is
    signed out it raises ``LoginRequiredError`` with the path to a debug
    screenshot so the operator can sign in via noVNC.
    """

    def __init__(self, page: "Page") -> None:
        self.page = page

    async def send(self, prompt: str, *, response_timeout_ms: int = 180_000) -> str:
        if not prompt.strip():
            raise BrowserDriverError("Empty prompt")

        await self.page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=30_000)
        await human_pause(self.page, min_ms=1200, max_ms=2200)

        if _is_login_url(self.page.url):
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-login")
            raise LoginRequiredError(
                "ChatGPT profile is signed out. Open http://localhost:7900 "
                "to sign in.",
                screenshot_path=shot,
            )

        await _dismiss_modals(self.page)
        await human_pause(self.page)

        composer = await _first_matching(self.page, COMPOSER_SELECTORS, 10_000)
        if composer is None:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-no-composer")
            raise BrowserDriverError(
                "ChatGPT composer not found.", screenshot_path=shot
            )

        await human_click(composer, hover_pause_min_ms=120, hover_pause_max_ms=320)
        await composer.focus()
        await human_pause(self.page, min_ms=200, max_ms=600)
        # Type via keyboard with randomised per-key delays so the
        # contenteditable div sees a realistic cadence instead of an
        # instant insert_text dump.
        await human_type(self.page, prompt)
        await human_pause(self.page, min_ms=500, max_ms=1300)

        send_button = await _first_matching(self.page, SEND_BUTTON_SELECTORS, 5_000)
        if send_button is None:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-no-send")
            raise BrowserDriverError(
                "ChatGPT send button not found.", screenshot_path=shot
            )
        await human_click(send_button)

        # Wait until the stop-generating button disappears, signalling
        # the assistant turn is complete. Fall back to a long poll on
        # the assistant turn count.
        try:
            stop = await _first_matching(self.page, STOP_BUTTON_SELECTORS, 5_000)
            if stop is not None:
                await stop.wait_for(state="hidden", timeout=response_timeout_ms)
        except Exception:
            pass

        # Belt-and-braces: wait for any assistant text to appear. The
        # temporary-chat UI sometimes renders a short "Fast answer"
        # block outside of the regular assistant turn container, so we
        # accept multiple candidate selectors and any non-empty text.
        scrape_js = """
            () => {
              const selectors = [
                "[data-message-author-role='assistant']",
                "[data-testid='conversation-turn-content'][data-author='assistant']",
                "article[data-message-author-role='assistant']",
                "div[data-message-author-role='assistant'] .markdown",
              ];
              for (const s of selectors) {
                const nodes = document.querySelectorAll(s);
                if (nodes.length === 0) continue;
                const last = nodes[nodes.length - 1];
                const inner = last.querySelector('.markdown') || last;
                const text = (inner.innerText || '').trim();
                if (text) return text;
              }
              return '';
            }
        """
        try:
            await self.page.wait_for_function(
                f"() => ({scrape_js})().length > 0",
                timeout=response_timeout_ms,
            )
        except Exception:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-no-response")
            raise BrowserDriverError(
                "ChatGPT response did not arrive in time.",
                screenshot_path=shot,
            )

        text = await self.page.evaluate(scrape_js)
        if not text:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-empty")
            raise BrowserDriverError(
                "ChatGPT returned an empty response.",
                screenshot_path=shot,
            )
        # User would skim the answer before navigating away. Pause
        # proportional to response length, clamped 0.8-4 s.
        await human_pause(
            self.page, min_ms=estimate_read_pause_ms(text), max_ms=estimate_read_pause_ms(text) + 200
        )
        return text
