from __future__ import annotations

import re
from typing import TYPE_CHECKING

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    save_trace_screenshot,
)
from video_agent.browser_worker.drivers.humanize import (
    STABLE_MS,
    STABLE_POLL_MS,
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


def _stable_response_detector_js() -> str:
    """Page-side stable-response predicate shared by ChatGPT and Claude."""
    return """
        ({scrapeFnSource, priorText, stableMs}) => {
          // Re-eval the scrape function on every check so the latest
          // assistant turn is always considered, not the one that
          // existed when the observer was first attached.
          let scrapeFn;
          try {
            scrapeFn = eval('(' + scrapeFnSource + ')');
          } catch (err) {
            return {ready: false, error: 'scrape_fn_eval_failed:' + err.message};
          }

          if (!window.__chatStableTracker) {
            window.__chatStableTracker = {
              text: '',
              lastMutationTs: Date.now(),
              observerAttached: false,
              attachedTo: null,
            };
          }
          const tracker = window.__chatStableTracker;
          const current = String(scrapeFn() || '');

          // Identify the DOM node currently holding the assistant turn
          // so we can (re)attach the observer if ChatGPT rerenders the
          // turn into a new container (e.g. when the streaming bubble
          // is swapped for the final rendered markdown block).
          const turnCandidates = [
            'article[data-message-author-role="assistant"]:last-of-type',
            '[data-message-author-role="assistant"]:last-of-type',
            '[data-testid="conversation-turn-content"][data-author="assistant"]:last-of-type',
          ];
          let turnNode = null;
          for (const sel of turnCandidates) {
            const found = document.querySelector(sel);
            if (found) {
              turnNode = found;
              break;
            }
          }

          if (turnNode && tracker.attachedTo !== turnNode) {
            if (tracker.observer) {
              try { tracker.observer.disconnect(); } catch (_) {}
            }
            const obs = new MutationObserver(() => {
              tracker.lastMutationTs = Date.now();
            });
            obs.observe(turnNode, {
              childList: true,
              subtree: true,
              characterData: true,
            });
            tracker.observer = obs;
            tracker.attachedTo = turnNode;
            tracker.observerAttached = true;
            tracker.lastMutationTs = Date.now();
          }

          if (current !== tracker.text) {
            tracker.text = current;
            tracker.lastMutationTs = Date.now();
          }

          // Root-cause guard against truncated captures: the assistant turn
          // can pause mutating for longer than stableMs *while still
          // generating* (token/render gaps, network stalls). Quiet time alone
          // then latches a half-finished response. So also require that the
          // model is NOT actively streaming, detected via the "stop
          // generating" control both ChatGPT and Claude expose only while a
          // response is in flight. Best-effort: if no known stop control is
          // present (unknown UI), fall back to quiet-only so we never hang.
          const stopSelectors = [
            'button[data-testid="stop-button"]',
            'button[aria-label^="Stop"]',
            'button[aria-label*="stop generating" i]',
            '[data-testid="stop-streaming-button"]',
          ];
          let streaming = false;
          for (const sel of stopSelectors) {
            const node = document.querySelector(sel);
            if (node && node.offsetParent !== null) { streaming = true; break; }
          }

          const quietFor = Date.now() - tracker.lastMutationTs;
          const ready = !!(
            current &&
            current !== priorText &&
            quietFor >= stableMs &&
            !streaming
          );
          return {ready, len: current.length, quietFor, streaming};
        }
    """


async def _wait_for_stable_response(
    page: "Page",
    scrape_js: str,
    prior_text: str,
    *,
    response_timeout_ms: int,
    poll_ms: int | None = None,
    stable_ms: int | None = None,
    log_tag: str = "scrape",
) -> str | None:
    """Wait for the assistant turn to (a) differ from ``prior_text`` and
    (b) stop mutating for ``stable_ms``, then return the stable text.

    The watcher runs entirely inside the page using a ``MutationObserver``
    that records the timestamp of the most recent DOM mutation. The Python
    side calls ``page.wait_for_function`` once and Playwright polls the
    page-side predicate roughly every 100 ms — no Python⇄Chrome CDP
    round-trip per check. Compared to the older "evaluate every 250-500 ms"
    loop this cuts roughly 50 CDP messages per ChatGPT turn and detects
    stream-end within ~100 ms of the final mutation instead of one full
    poll interval. The human-cadence look is preserved because all of the
    typing, hovering, and pause behavior happens in the calling driver.
    """
    if poll_ms is None:
        poll_ms = STABLE_POLL_MS
    if stable_ms is None:
        stable_ms = STABLE_MS

    import sys
    import time

    deadline = time.monotonic() + response_timeout_ms / 1000.0
    prior_len = len(prior_text or "")
    print(
        f"[{log_tag}] start prior_len={prior_len} timeout_ms={response_timeout_ms} "
        f"stable_ms={stable_ms} (observer-based)",
        flush=True,
        file=sys.stderr,
    )

    # JS expression executed once per Playwright poll inside the page.
    # Installs a MutationObserver lazily and only resolves when the latest
    # assistant turn has been quiet and no visible stop-generating control
    # remains. The observer survives between calls because state lives on
    # ``window``, which is fine for persistent ChatGPT/Claude tabs.
    detector_js = _stable_response_detector_js()

    try:
        handle = await page.wait_for_function(
            detector_js,
            arg={
                "scrapeFnSource": scrape_js.strip(),
                "priorText": prior_text or "",
                "stableMs": int(stable_ms),
            },
            timeout=response_timeout_ms,
            polling=poll_ms,
        )
    except Exception as exc:
        # Reset tracker state so the next call doesn't inherit stale
        # observer references from the timed-out attempt.
        try:
            await page.evaluate("() => { delete window.__chatStableTracker; }")
        except Exception:
            pass
        elapsed_ms = int((response_timeout_ms / 1000.0 - max(0.0, deadline - time.monotonic())) * 1000)
        print(
            f"[{log_tag}] TIMEOUT after ~{elapsed_ms}ms ({exc.__class__.__name__})",
            flush=True,
            file=sys.stderr,
        )
        return None

    try:
        info = await handle.json_value()
    except Exception:
        info = None
    final_text = await page.evaluate(scrape_js)
    final_len = len(final_text or "")
    quiet_for = (info or {}).get("quietFor") if isinstance(info, dict) else None
    streaming_flag = (info or {}).get("streaming") if isinstance(info, dict) else None
    print(
        f"[{log_tag}] STABLE len={final_len} quiet_for_ms={quiet_for} streaming={streaming_flag}",
        flush=True,
        file=sys.stderr,
    )
    # Reset tracker so a subsequent send in the same tab starts fresh
    # (the prior_text it watches against changes per call).
    try:
        await page.evaluate("() => { delete window.__chatStableTracker; }")
    except Exception:
        pass
    return final_text if final_text else None


async def _wait_for_stable_response_legacy(
    page: "Page",
    scrape_js: str,
    prior_text: str,
    *,
    response_timeout_ms: int,
    poll_ms: int | None = None,
    stable_ms: int | None = None,
    log_tag: str = "scrape",
) -> str | None:
    """Legacy poll-evaluate loop retained for emergency fallback.

    The active code path is the MutationObserver-based watcher above.
    This function kept in case a future page-context restriction blocks
    ``page.wait_for_function`` (no known case today).
    """
    if poll_ms is None:
        poll_ms = STABLE_POLL_MS
    if stable_ms is None:
        stable_ms = STABLE_MS

    import sys
    import time

    deadline = time.monotonic() + response_timeout_ms / 1000.0
    last_seen: str | None = None
    stable_since: float | None = None
    iteration = 0
    prior_len = len(prior_text or "")
    print(
        f"[{log_tag}] start prior_len={prior_len} timeout_ms={response_timeout_ms} (legacy-poll)",
        flush=True,
        file=sys.stderr,
    )
    while time.monotonic() < deadline:
        iteration += 1
        current = await page.evaluate(scrape_js)
        if current and current != prior_text:
            if current == last_seen:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif (time.monotonic() - stable_since) * 1000.0 >= stable_ms:
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


async def _recover_interrupted_response(page: "Page") -> bool:
    """Best-effort recovery when ChatGPT reports interrupted generation.

    Returns True if an interruption marker is visible and we attempted a
    resume action (click retry/continue or press Enter).
    """
    interruption_markers = (
        "text=/Connection interrupted/i",
        "text=/waiting for the complete answer/i",
        "text=/network error/i",
        "text=/something went wrong/i",
    )
    resume_selectors = (
        "button:has-text('Try again')",
        "button:has-text('Continue generating')",
        "button:has-text('Regenerate')",
        "button[aria-label*='Try again']",
        "button[aria-label*='Continue']",
        "button[aria-label*='Regenerate']",
    )

    interrupted = False
    for marker in interruption_markers:
        try:
            if await page.locator(marker).first.is_visible(timeout=300):
                interrupted = True
                break
        except Exception:
            continue
    if not interrupted:
        return False

    for selector in resume_selectors:
        button = page.locator(selector).first
        try:
            if await button.is_visible(timeout=500):
                await human_click(button)
                return True
        except Exception:
            continue

    try:
        await page.keyboard.press("Enter")
    except Exception:
        pass
    return True


class ChatGPTDriver:
    """Single-shot ChatGPT driver: open temporary chat, send, scrape.

    The driver does **not** log the user in. If the dedicated profile is
    signed out it raises ``LoginRequiredError`` with the path to a debug
    screenshot so the operator can sign in via KasmVNC.
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

        # Pause windows below intentionally use ``human_pause`` defaults
        # so BROWSER_HUMAN_MODE can collapse them in fast pipeline mode.
        await human_click(composer, hover_pause_min_ms=60, hover_pause_max_ms=200)
        await composer.focus()
        await human_pause(self.page)
        await human_type(self.page, prompt)
        await human_pause(self.page)

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
        # If ChatGPT reports an interrupted connection, try one quick
        # self-recovery action and continue waiting.
        attempts = 0
        text = None
        max_recoveries = 2
        while attempts <= max_recoveries:
            text = await _wait_for_stable_response(
                self.page,
                scrape_js,
                prior_text,
                response_timeout_ms=response_timeout_ms,
                log_tag="chatgpt",
            )
            if text:
                break
            recovered = await _recover_interrupted_response(self.page)
            if not recovered:
                break
            attempts += 1
            await human_pause(self.page, min_ms=600, max_ms=1200)

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
