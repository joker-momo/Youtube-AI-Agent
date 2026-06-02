from __future__ import annotations

import re
from typing import TYPE_CHECKING

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    save_layout_diagnostics,
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

CHATGPT_URL = "https://chatgpt.com/?temporary-chat=true"

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
CHATGPT_DIAGNOSTIC_SELECTORS = (
    *COMPOSER_SELECTORS,
    *SEND_BUTTON_SELECTORS,
    *STOP_BUTTON_SELECTORS,
    ASSISTANT_TURN_SELECTOR,
    MARKDOWN_BODY_SELECTOR,
)


def _is_login_url(url: str) -> bool:
    return (
        "auth.openai.com" in url
        or "/auth/login" in url
        or re.search(r"chatgpt\.com/(login|auth)", url) is not None
    )


def _is_temporary_chat_url(url: str) -> bool:
    return "temporary-chat=true" in (url or "")


async def _ensure_temporary_chat(page: "Page") -> bool:
    """Guarantee the page is on a temporary chat before typing.

    A prior send navigates to a persistent conversation URL
    (``chatgpt.com/c/<id>``). Re-navigate to the temporary-chat URL when the
    current URL is no longer temporary, then confirm.
    """
    if _is_temporary_chat_url(page.url):
        return True
    await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=30_000)
    await human_pause(page, min_ms=800, max_ms=1600)
    await _dismiss_modals(page)
    return _is_temporary_chat_url(page.url)


def _stable_response_detector_js() -> str:
    """Page-side stable-response predicate shared by ChatGPT and Gemini."""
    return r"""
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
          // generating" control both ChatGPT and Gemini expose only while a
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

          // JSON completion guard: only engage when the response actually
          // looks like JSON, or when the caller explicitly opts in. This
          // avoids timing out on normal prose that happens to contain "{".
          const trimmed = current.trim();
          const shouldCheckJson = args.expectJson === true
            || (args.expectJson !== false && (/^[\[{]/.test(trimmed)));
          let jsonComplete = true;
          let depth = 0;
          let inStr = false;
          if (shouldCheckJson) {
            let esc = false;
            const openCh = trimmed[0];
            const closeCh = openCh === '[' ? ']' : '}';
            for (let i = 0; i < trimmed.length; i++) {
              const ch = trimmed[i];
              if (inStr) {
                if (esc) { esc = false; continue; }
                if (ch === '\\') { esc = true; continue; }
                if (ch === '"') { inStr = false; }
                continue;
              }
              if (ch === '"') inStr = true;
              else if (ch === openCh) depth++;
              else if (ch === closeCh) depth--;
            }
          }
          jsonComplete = shouldCheckJson ? (depth === 0 && !inStr) : true;

          const ready = !!(
            current &&
            current !== priorText &&
            quietFor >= stableMs &&
            !streaming &&
            jsonComplete
          );
          return ready ? {ready, len: current.length, quietFor, streaming} : false;
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
    expect_json: bool | None = None,
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
    # ``window``, which is fine for persistent ChatGPT/Gemini tabs.
    detector_js = _stable_response_detector_js()

    try:
        handle = await page.wait_for_function(
            detector_js,
            arg={
                "scrapeFnSource": scrape_js.strip(),
                "priorText": prior_text or "",
                "stableMs": int(stable_ms),
                "expectJson": expect_json,
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
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        if isinstance(exc, PlaywrightTimeoutError) or "timeout" in str(exc).lower():
            print(
                f"[{log_tag}] TIMEOUT after ~{elapsed_ms}ms ({exc.__class__.__name__})",
                flush=True,
                file=sys.stderr,
            )
            return None
        else:
            print(
                f"[{log_tag}] ERROR after ~{elapsed_ms}ms ({exc.__class__.__name__}: {exc})",
                flush=True,
                file=sys.stderr,
            )
            raise

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


async def _locator_text(locator) -> str:
    try:
        value = await locator.evaluate(
            "(node) => (node.innerText || node.value || node.textContent || '').trim()",
            timeout=1_000,
        )
        return str(value or "").strip()
    except Exception:
        return ""


async def _raise_chatgpt_layout_warning(
    page: "Page", *, prefix: str, message: str
) -> None:
    shot, diagnostic = await save_layout_diagnostics(
        page,
        prefix=prefix,
        selectors=CHATGPT_DIAGNOSTIC_SELECTORS,
    )
    raise BrowserDriverError(
        f"{message} ChatGPT layout may have changed; inspect diagnostic trace.",
        screenshot_path=shot,
        diagnostic_path=diagnostic,
        layout_warning=True,
    )


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

        nav_errors: list[str] = []
        navigated = False
        for _ in range(2):
            try:
                await self.page.goto(
                    CHATGPT_URL, wait_until="domcontentloaded", timeout=30_000
                )
                navigated = True
                break
            except Exception as exc:
                nav_errors.append(f"{CHATGPT_URL}: {exc}")
                await self.page.wait_for_timeout(800)
        if not navigated:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-goto-failed")
            raise BrowserDriverError(
                "ChatGPT temporary chat navigation failed: "
                + " | ".join(nav_errors[-3:]),
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
        if "temporary-chat=true" not in (self.page.url or ""):
            try:
                body = await self.page.locator("body").inner_text(timeout=1_000)
            except Exception:
                body = ""
            if "temporary chat" not in body.lower():
                await _raise_chatgpt_layout_warning(
                    self.page,
                    prefix="chatgpt-not-temporary",
                    message="ChatGPT did not confirm temporary chat mode.",
                )
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

        # Verify we are still in a temporary chat right before typing — a prior
        # send can navigate to a persistent conversation URL.
        if not await _ensure_temporary_chat(self.page):
            try:
                body = await self.page.locator("body").inner_text(timeout=1_000)
            except Exception:
                body = ""
            if "temporary chat" not in body.lower():
                await _raise_chatgpt_layout_warning(
                    self.page,
                    prefix="chatgpt-not-temporary-presend",
                    message="ChatGPT is not on a temporary chat before send.",
                )

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
            await _raise_chatgpt_layout_warning(
                self.page,
                prefix="chatgpt-no-composer",
                message="ChatGPT composer not found.",
            )

        # Pause windows below intentionally use ``human_pause`` defaults
        # so BROWSER_HUMAN_MODE can collapse them in fast pipeline mode.
        try:
            await human_click(composer, hover_pause_min_ms=60, hover_pause_max_ms=200)
        except Exception:
            await composer.focus(timeout=3_000)
        await human_pause(self.page)
        await human_type(self.page, prompt)
        await human_pause(self.page)

        send_button = await _first_matching(self.page, SEND_BUTTON_SELECTORS, 5_000)
        if send_button is None:
            await _raise_chatgpt_layout_warning(
                self.page,
                prefix="chatgpt-no-send",
                message="ChatGPT send button not found.",
            )
        try:
            await human_click(send_button)
        except Exception:
            try:
                await send_button.click(timeout=3_000, force=True)
            except Exception:
                try:
                    await send_button.evaluate("(node) => node.click()")
                except Exception:
                    pass
        await self.page.wait_for_timeout(400)
        if await _locator_text(composer):
            try:
                await self.page.keyboard.press("Meta+Enter")
                await self.page.wait_for_timeout(300)
            except Exception:
                pass
        if await _locator_text(composer):
            await _raise_chatgpt_layout_warning(
                self.page,
                prefix="chatgpt-submit-stuck",
                message="ChatGPT prompt remained in the composer after submit attempts.",
            )

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
            await _raise_chatgpt_layout_warning(
                self.page,
                prefix="chatgpt-no-response",
                message="ChatGPT response did not arrive in time.",
            )
        if not text:
            await _raise_chatgpt_layout_warning(
                self.page,
                prefix="chatgpt-empty",
                message="ChatGPT returned an empty response.",
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
