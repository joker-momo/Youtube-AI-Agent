from __future__ import annotations

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

GEMINI_URL = "https://gemini.google.com/app"

# Selectors that toggle Gemini's temporary chat (no history, no model
# training). Tried in order; any visible match is clicked. If none
# match (rollout/locale variation), the driver fails before sending so
# prompts never leak into a normal history-backed chat.
TEMP_CHAT_TOGGLE_SELECTORS = (
    "button[aria-label*='Temporary chat' i]",
    "button[aria-label*='Temporary' i]",
    "[data-test-id*='temporary' i]",
    "button:has-text('Temporary chat')",
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
    """Best-effort human click — True if any selector matched and clicked."""
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.is_visible(timeout=timeout_ms):
                await human_click(locator, click_timeout_ms=3_000)
                return True
        except Exception:
            continue
    return False


async def _enter_temporary_chat(page: "Page") -> bool:
    """Switch Gemini into temporary chat mode.

    Tries the temporary-chat toggle first; if the rollout/locale does
    not expose it, falls back to creating a new chat so at least no
    prior conversation context bleeds into the prompt. Both clicks are
    best-effort; silent if the UI hides the buttons.
    """
    if await _try_click(page, TEMP_CHAT_TOGGLE_SELECTORS):
        await human_pause(page, min_ms=600, max_ms=1300)
        return True
    return False


async def _raise_gemini_layout_warning(
    page: "Page", *, prefix: str, message: str
) -> None:
    shot, diagnostic = await save_layout_diagnostics(
        page,
        prefix=prefix,
        selectors=(
            *TEMP_CHAT_TOGGLE_SELECTORS,
            *COMPOSER_SELECTORS,
            *SEND_BUTTON_SELECTORS,
            *STOP_BUTTON_SELECTORS,
        ),
    )
    raise BrowserDriverError(
        f"{message} Gemini layout may have changed; inspect diagnostic trace.",
        screenshot_path=shot,
        diagnostic_path=diagnostic,
        layout_warning=True,
    )


RESPONSE_BLOCK_FOR_SCRAPE = (
    ".model-response-text",
    "message-content",
    "model-response",
)


class GeminiDriver:
    """Session-style Gemini driver: open temp chat, send 1+ messages, close."""

    def __init__(self, page: "Page") -> None:
        self.page = page
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        await self.page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=30_000)
        await human_pause(self.page, min_ms=1200, max_ms=2200)

        if _is_login_url(self.page.url):
            shot = await save_trace_screenshot(self.page, prefix="gemini-login")
            raise LoginRequiredError(
                "Gemini profile is signed out. Open http://localhost:7900 "
                "to sign in.",
                screenshot_path=shot,
            )

        if not await _enter_temporary_chat(self.page):
            await _raise_gemini_layout_warning(
                self.page,
                prefix="gemini-not-temporary",
                message="Gemini temporary chat control not found.",
            )
        await human_pause(self.page)
        self._opened = True

    async def send_message(self, prompt: str, *, response_timeout_ms: int = 300_000) -> str:
        if not self._opened:
            await self.open()
        if not prompt.strip():
            raise BrowserDriverError("Empty prompt")

        # Scrape returns a composite "[count=N]\n<text>" so the
        # _wait_for_stable_response text-diff strategy reacts to EITHER
        # a new JSON response (count grows) OR a new plain reply
        # (text changes — e.g. "OK" to a briefing). The driver strips
        # the [count=N] prefix before returning the response.
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
                ".model-response-text",
                "message-content .markdown",
                "message-content",
                "model-response .markdown",
                "model-response",
                ".markdown.markdown-main-panel",
                ".markdown",
                "[data-test-id*='response']",
                "[data-message-author='model']",
                "chat-history-message:last-child",
                "model-response-content",
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
              const last = knownText
                || (responses.length ? responses[responses.length - 1] : '');
              return `[count=${responses.length}]\\n${last}`;
            }
        """
        prior_text = await self.page.evaluate(scrape_js)

        composer = await _first_matching(self.page, COMPOSER_SELECTORS, 10_000)
        if composer is None:
            shot = await save_trace_screenshot(self.page, prefix="gemini-no-composer")
            raise BrowserDriverError(
                "Gemini composer not found.", screenshot_path=shot
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
            shot = await save_trace_screenshot(self.page, prefix="gemini-no-send")
            raise BrowserDriverError(
                "Gemini send button not found.", screenshot_path=shot
            )
        await human_click(send_button)

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
            log_tag="gemini",
        )
        if text is None:
            shot = await save_trace_screenshot(self.page, prefix="gemini-no-response")
            raise BrowserDriverError(
                "Gemini response did not arrive in time.",
                screenshot_path=shot,
            )
        # Strip the "[count=N]\n" prefix the scrape composite uses.
        import re as _re
        text = _re.sub(r"^\[count=\d+\]\n", "", text)
        if not text:
            shot = await save_trace_screenshot(self.page, prefix="gemini-empty")
            raise BrowserDriverError(
                "Gemini returned an empty response.",
                screenshot_path=shot,
            )
        await human_pause(
            self.page,
            min_ms=estimate_read_pause_ms(text),
            max_ms=estimate_read_pause_ms(text) + 200,
        )
        return text

    async def send(self, prompt: str, *, response_timeout_ms: int = 300_000) -> str:
        await self.open()
        return await self.send_message(prompt, response_timeout_ms=response_timeout_ms)
