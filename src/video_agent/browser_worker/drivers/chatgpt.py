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
CHATGPT_FALLBACK_URL = "https://chatgpt.com/"

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


async def _wait_for_stable_response(
    page: "Page",
    scrape_js: str,
    prior_text: str,
    *,
    response_timeout_ms: int,
    poll_ms: int = 500,
    stable_ms: int = 2_000,
    log_tag: str = "scrape",
) -> str | None:
    """Poll ``scrape_js`` until the result differs from ``prior_text`` AND
    has not changed for ``stable_ms``. Returns the stable text or None on
    timeout. Defends against scraping a partial streaming response.

    Emits one log line per poll iteration so the operator can see what
    the scrape is (or is not) picking up while a stage runs.
    """
    import sys
    import time

    deadline = time.monotonic() + response_timeout_ms / 1000.0
    last_seen: str | None = None
    stable_since: float | None = None
    iteration = 0
    prior_len = len(prior_text or "")
    print(
        f"[{log_tag}] start prior_len={prior_len} timeout_ms={response_timeout_ms}",
        flush=True,
        file=sys.stderr,
    )
    while time.monotonic() < deadline:
        iteration += 1
        current = await page.evaluate(scrape_js)
        cur_len = len(current or "")
        diff = (current or "") != prior_text
        same_as_last = current == last_seen
        stable_for = (
            (time.monotonic() - stable_since) * 1000.0 if stable_since else 0
        )
        if iteration <= 3 or iteration % 10 == 0:
            preview = (current or "")[:80].replace("\n", " ")
            print(
                f"[{log_tag}] iter={iteration} len={cur_len} "
                f"diff_from_prior={diff} same_as_last={same_as_last} "
                f"stable_ms={int(stable_for)} preview={preview!r}",
                flush=True,
                file=sys.stderr,
            )
        if current and current != prior_text:
            if current == last_seen:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif (time.monotonic() - stable_since) * 1000.0 >= stable_ms:
                    print(
                        f"[{log_tag}] STABLE after iter={iteration} len={cur_len}",
                        flush=True,
                        file=sys.stderr,
                    )
                    return current
            else:
                last_seen = current
                stable_since = None
        await page.wait_for_timeout(poll_ms)
    print(
        f"[{log_tag}] TIMEOUT after iter={iteration} last_len={len(last_seen or '')}",
        flush=True,
        file=sys.stderr,
    )
    return None


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
        self._opened = False

    async def open(self) -> None:
        """Navigate to a new temporary chat and dismiss consent modals.

        Idempotent: safe to call once per session before the first
        ``send_message``. Raises ``LoginRequiredError`` if the profile
        is signed out.
        """
        if self._opened:
            return

        # ChatGPT temporary-chat URL can intermittently return HTTP
        # failure pages (cloud edge / anti-bot / transient network).
        # Fall back to the root URL so we can still continue in the
        # same signed-in profile.
        nav_errors: list[str] = []
        navigated = False
        for url in (CHATGPT_URL, CHATGPT_FALLBACK_URL):
            for _ in range(2):
                try:
                    await self.page.goto(
                        url, wait_until="domcontentloaded", timeout=30_000
                    )
                    navigated = True
                    break
                except Exception as exc:
                    nav_errors.append(f"{url}: {exc}")
                    await self.page.wait_for_timeout(800)
            if navigated:
                break
        if not navigated:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-goto-failed")
            raise BrowserDriverError(
                "ChatGPT navigation failed: " + " | ".join(nav_errors[-3:]),
                screenshot_path=shot,
            )

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
        self._opened = True

    async def send_message(self, prompt: str, *, response_timeout_ms: int = 300_000) -> str:
        """Type ``prompt`` into the open chat, send, wait for the new
        assistant turn, and return its scraped text. Does NOT close the
        tab — caller decides session lifetime.
        """
        if not self._opened:
            await self.open()
        if not prompt.strip():
            raise BrowserDriverError("Empty prompt")

        # Capture the LAST assistant text before sending so we can wait
        # for a different, non-empty text to appear afterwards. This is
        # more robust than counting turns: ChatGPT temporary chat
        # sometimes renders short answers (e.g. "OK") in a "Fast answer"
        # block that doesn't increment the regular assistant-turn count.
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
        prior_text = await self.page.evaluate(scrape_js)

        composer = await _first_matching(self.page, COMPOSER_SELECTORS, 10_000)
        if composer is None:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-no-composer")
            raise BrowserDriverError(
                "ChatGPT composer not found.", screenshot_path=shot
            )

        await human_click(composer, hover_pause_min_ms=120, hover_pause_max_ms=320)
        await composer.focus()
        await human_pause(self.page, min_ms=200, max_ms=600)
        await human_type(self.page, prompt)
        await human_pause(self.page, min_ms=500, max_ms=1300)

        send_button = await _first_matching(self.page, SEND_BUTTON_SELECTORS, 5_000)
        if send_button is None:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-no-send")
            raise BrowserDriverError(
                "ChatGPT send button not found.", screenshot_path=shot
            )
        await human_click(send_button)

        try:
            stop = await _first_matching(self.page, STOP_BUTTON_SELECTORS, 5_000)
            if stop is not None:
                await stop.wait_for(state="hidden", timeout=response_timeout_ms)
        except Exception:
            pass

        # Wait until the response (a) differs from prior AND (b) stops
        # growing for 2 s. Without the stability check we sometimes
        # scrape mid-stream and get a truncated JSON.
        text = await _wait_for_stable_response(
            self.page,
            scrape_js,
            prior_text,
            response_timeout_ms=response_timeout_ms,
            log_tag="chatgpt",
        )
        if text is None:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-no-response")
            raise BrowserDriverError(
                "ChatGPT response did not arrive in time.",
                screenshot_path=shot,
            )
        if not text:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-empty")
            raise BrowserDriverError(
                "ChatGPT returned an empty response.",
                screenshot_path=shot,
            )
        await human_pause(
            self.page,
            min_ms=estimate_read_pause_ms(text),
            max_ms=estimate_read_pause_ms(text) + 200,
        )
        return text

    async def send(self, prompt: str, *, response_timeout_ms: int = 300_000) -> str:
        """One-shot: open + send_message. Tab stays open; caller closes."""
        await self.open()
        return await self.send_message(prompt, response_timeout_ms=response_timeout_ms)
