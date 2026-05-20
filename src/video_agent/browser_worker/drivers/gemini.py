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

# Selectors that toggle Gemini's temporary chat (no history, no model
# training). Tried in order; any visible match is clicked. If none
# match (rollout/locale variation), the driver falls back to "New chat".
TEMP_CHAT_TOGGLE_SELECTORS = (
    "button[aria-label*='Temporary chat' i]",
    "button[aria-label*='Temporary' i]",
    "[data-test-id*='temporary' i]",
    "button:has-text('Temporary chat')",
)

NEW_CHAT_SELECTORS = (
    "a[aria-label*='New chat' i]",
    "button[aria-label*='New chat' i]",
    "button:has-text('New chat')",
)

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

RESPONSE_BLOCK_SELECTOR = (
    "model-response, "
    "message-content, "
    ".model-response-text, "
    "[data-test-id='conversation-turn-2'], "
    ".markdown.markdown-main-panel"
)


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


async def _try_click(page: "Page", selectors: tuple[str, ...], timeout_ms: int = 1500) -> bool:
    """Best-effort click — returns True if any selector matched and clicked."""
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.is_visible(timeout=timeout_ms):
                await locator.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


async def _enter_temporary_chat(page: "Page") -> None:
    """Switch Gemini into temporary chat mode.

    Tries the temporary-chat toggle first; if the rollout/locale does
    not expose it, falls back to creating a new chat so at least no
    prior conversation context bleeds into the prompt. Both clicks are
    best-effort; silent if the UI hides the buttons.
    """
    if await _try_click(page, TEMP_CHAT_TOGGLE_SELECTORS):
        await page.wait_for_timeout(500)
        return
    if await _try_click(page, NEW_CHAT_SELECTORS):
        await page.wait_for_timeout(500)


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

        await _enter_temporary_chat(self.page)

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

        scrape_js = """
            () => {
              const selectors = [
                ".model-response-text",
                "message-content .markdown",
                "message-content",
                "model-response .markdown",
                "model-response",
                ".markdown.markdown-main-panel",
              ];
              for (const s of selectors) {
                const nodes = document.querySelectorAll(s);
                if (nodes.length === 0) continue;
                const last = nodes[nodes.length - 1];
                const text = (last.innerText || '').trim();
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
            shot = await save_trace_screenshot(self.page, prefix="gemini-no-response")
            raise BrowserDriverError(
                "Gemini response did not arrive in time.",
                screenshot_path=shot,
            )

        text = await self.page.evaluate(scrape_js)
        if not text:
            shot = await save_trace_screenshot(self.page, prefix="gemini-empty")
            raise BrowserDriverError(
                "Gemini returned an empty response.",
                screenshot_path=shot,
            )
        return text
