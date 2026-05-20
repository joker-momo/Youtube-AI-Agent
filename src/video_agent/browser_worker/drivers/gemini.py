from __future__ import annotations

from typing import TYPE_CHECKING

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    save_trace_screenshot,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

GEMINI_URL = "https://gemini.google.com/app"

COMPOSER_SELECTORS = (
    "rich-textarea div[contenteditable='true']",
    "[contenteditable='true'][role='textbox']",
    "textarea[aria-label*='prompt']",
)

SEND_BUTTON_SELECTORS = (
    "button[aria-label='Send message']",
    "button[aria-label*='Send']",
    "button:has-text('Submit')",
)

STOP_BUTTON_SELECTORS = (
    "button[aria-label='Stop generating']",
    "button[aria-label*='Stop']",
)

RESPONSE_BLOCK_SELECTOR = "model-response, message-content"


def _is_login_url(url: str) -> bool:
    return "accounts.google.com" in url or "signin" in url


async def _first_matching(page: "Page", selectors: tuple[str, ...], timeout_ms: int):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception:
            continue
    return None


class GeminiDriver:
    """Single-shot Gemini driver: open chat, send, scrape."""

    def __init__(self, page: "Page") -> None:
        self.page = page

    async def send(self, prompt: str, *, response_timeout_ms: int = 180_000) -> str:
        if not prompt.strip():
            raise BrowserDriverError("Empty prompt")

        await self.page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=30_000)
        await self.page.wait_for_timeout(1500)

        if _is_login_url(self.page.url):
            shot = await save_trace_screenshot(self.page, prefix="gemini-login")
            raise LoginRequiredError(
                "Gemini profile is signed out. Open http://localhost:7900 "
                "to sign in.",
                screenshot_path=shot,
            )

        composer = await _first_matching(self.page, COMPOSER_SELECTORS, 10_000)
        if composer is None:
            shot = await save_trace_screenshot(self.page, prefix="gemini-no-composer")
            raise BrowserDriverError(
                "Gemini composer not found.", screenshot_path=shot
            )

        await composer.click()
        await composer.focus()
        await self.page.keyboard.insert_text(prompt)

        send_button = await _first_matching(self.page, SEND_BUTTON_SELECTORS, 5_000)
        if send_button is None:
            shot = await save_trace_screenshot(self.page, prefix="gemini-no-send")
            raise BrowserDriverError(
                "Gemini send button not found.", screenshot_path=shot
            )
        await send_button.click()

        try:
            stop = await _first_matching(self.page, STOP_BUTTON_SELECTORS, 5_000)
            if stop is not None:
                await stop.wait_for(state="hidden", timeout=response_timeout_ms)
        except Exception:
            pass

        try:
            await self.page.wait_for_function(
                "(s) => {"
                "  const nodes = document.querySelectorAll(s);"
                "  const last = nodes[nodes.length - 1];"
                "  return last && last.innerText && last.innerText.length > 32;"
                "}",
                arg=RESPONSE_BLOCK_SELECTOR,
                timeout=response_timeout_ms,
            )
        except Exception:
            shot = await save_trace_screenshot(self.page, prefix="gemini-no-response")
            raise BrowserDriverError(
                "Gemini response did not arrive in time.",
                screenshot_path=shot,
            )

        text = await self.page.evaluate(
            "(s) => {"
            "  const nodes = document.querySelectorAll(s);"
            "  const last = nodes[nodes.length - 1];"
            "  return last ? (last.innerText || '').trim() : '';"
            "}",
            arg=RESPONSE_BLOCK_SELECTOR,
        )
        if not text:
            shot = await save_trace_screenshot(self.page, prefix="gemini-empty")
            raise BrowserDriverError(
                "Gemini returned an empty response.",
                screenshot_path=shot,
            )
        return text
