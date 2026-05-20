from __future__ import annotations

import re
from typing import TYPE_CHECKING

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    save_trace_screenshot,
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
        await self.page.wait_for_timeout(1500)

        if _is_login_url(self.page.url):
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-login")
            raise LoginRequiredError(
                "ChatGPT profile is signed out. Open http://localhost:7900 "
                "to sign in.",
                screenshot_path=shot,
            )

        composer = await _first_matching(self.page, COMPOSER_SELECTORS, 10_000)
        if composer is None:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-no-composer")
            raise BrowserDriverError(
                "ChatGPT composer not found.", screenshot_path=shot
            )

        await composer.click()
        await composer.focus()
        # Type via keyboard so contenteditable div accepts the input
        # the same way the user would.
        await self.page.keyboard.insert_text(prompt)

        send_button = await _first_matching(self.page, SEND_BUTTON_SELECTORS, 5_000)
        if send_button is None:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-no-send")
            raise BrowserDriverError(
                "ChatGPT send button not found.", screenshot_path=shot
            )
        await send_button.click()

        # Wait until the stop-generating button disappears, signalling
        # the assistant turn is complete. Fall back to a long poll on
        # the assistant turn count.
        try:
            stop = await _first_matching(self.page, STOP_BUTTON_SELECTORS, 5_000)
            if stop is not None:
                await stop.wait_for(state="hidden", timeout=response_timeout_ms)
        except Exception:
            pass

        # Belt-and-braces: wait for the latest assistant turn to settle.
        try:
            await self.page.wait_for_function(
                "(s) => {"
                "  const nodes = document.querySelectorAll(s);"
                "  const last = nodes[nodes.length - 1];"
                "  return last && last.innerText && last.innerText.length > 32;"
                "}",
                arg=ASSISTANT_TURN_SELECTOR,
                timeout=response_timeout_ms,
            )
        except Exception:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-no-response")
            raise BrowserDriverError(
                "ChatGPT response did not arrive in time.",
                screenshot_path=shot,
            )

        text = await self.page.evaluate(
            "(s) => {"
            "  const nodes = document.querySelectorAll(s);"
            "  const last = nodes[nodes.length - 1];"
            "  if (!last) return '';"
            "  const md = last.querySelector('.markdown') || last;"
            "  return md.innerText.trim();"
            "}",
            arg=ASSISTANT_TURN_SELECTOR,
        )
        if not text:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-empty")
            raise BrowserDriverError(
                "ChatGPT returned an empty response.",
                screenshot_path=shot,
            )
        return text
