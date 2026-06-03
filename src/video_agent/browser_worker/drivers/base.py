from __future__ import annotations

import asyncio
import os
import re
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


class BrowserDriverError(RuntimeError):
    """Driver-level failure: navigation, send, scrape, or timeout."""

    def __init__(
        self,
        message: str,
        *,
        screenshot_path: str | None = None,
        diagnostic_path: str | None = None,
        layout_warning: bool = False,
    ) -> None:
        super().__init__(message)
        self.screenshot_path = screenshot_path
        self.diagnostic_path = diagnostic_path
        self.layout_warning = layout_warning


class LoginRequiredError(BrowserDriverError):
    """The dedicated profile is not logged in to the target site.

    The operator needs to bring the native Chromium window forward and
    sign in once. The driver should never attempt to log the user in.
    """


class QuotaExceededError(BrowserDriverError):
    """The target model account is temporarily out of messages/credits."""


def trace_root() -> Path:
    """Directory under which screenshots/HTML dumps are written.

    Defaults to ``/data/trace`` inside the worker container. Mount this
    as a volume (or share the jobs volume) so artifacts are inspectable
    from the host. Tests can override via ``BROWSER_TRACE_DIR``.
    """
    return Path(os.environ.get("BROWSER_TRACE_DIR", "/data/trace"))


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "trace"


async def save_trace_screenshot(page: "Page", *, prefix: str) -> str:
    """Save a PNG of ``page`` under ``trace_root()`` and return its path.

    Never raises; on failure returns an empty string so the caller can
    still surface the original error.
    """
    try:
        root = trace_root()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = root / f"{_slug(prefix)}-{stamp}.png"
        await page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:  # pragma: no cover - best-effort
        return ""


async def save_layout_diagnostics(
    page: "Page", *, prefix: str, selectors: tuple[str, ...] = ()
) -> tuple[str, str]:
    """Save screenshot plus DOM diagnostics for browser layout drift warnings."""
    screenshot_path = await save_trace_screenshot(page, prefix=prefix)
    try:
        root = trace_root()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = root / f"{_slug(prefix)}-{stamp}"
        html_path = base.with_suffix(".html")
        html_path_text = str(html_path)
        diagnostic_path = base.with_suffix(".json")
        try:
            html_path.write_text(await page.content(), encoding="utf-8")
        except Exception:
            html_path_text = ""
        snapshot = await page.evaluate(
            """
            (selectors) => {
              const textOf = (node) => (node.innerText || node.value || node.textContent || '').trim();
              const clip = (value, max = 1200) => {
                value = String(value || '');
                return value.length > max ? value.slice(0, max) + '...' : value;
              };
              const summarize = (nodes) => Array.from(nodes).slice(0, 30).map((node) => ({
                tag: node.tagName,
                text: clip(textOf(node), 500),
                ariaLabel: node.getAttribute('aria-label') || '',
                testId: node.getAttribute('data-testid') || '',
                role: node.getAttribute('role') || '',
                placeholder: node.getAttribute('placeholder') || '',
                classes: clip(node.getAttribute('class') || '', 300),
              }));
              const selectorCounts = selectors.map((selector) => {
                try {
                  const nodes = document.querySelectorAll(selector);
                  return {
                    selector,
                    count: nodes.length,
                    lastText: nodes.length ? clip(textOf(nodes[nodes.length - 1]), 500) : '',
                  };
                } catch (error) {
                  return { selector, count: 0, error: String(error) };
                }
              });
              return {
                url: location.href,
                title: document.title,
                bodyTextTail: clip((document.body && document.body.innerText || '').slice(-4000), 4000),
                selectorCounts,
                buttons: summarize(document.querySelectorAll('button')),
                composers: summarize(document.querySelectorAll("[contenteditable='true'], textarea, [role='textbox']")),
              };
            }
            """,
            list(selectors),
        )
        payload = {
            "screenshot_path": screenshot_path,
            "html_path": html_path_text,
            "snapshot": snapshot,
        }
        diagnostic_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return screenshot_path, str(diagnostic_path)
    except Exception:  # pragma: no cover - best-effort
        return screenshot_path, ""


def normalise_response_text(text: str) -> str:
    """Strip leading/trailing whitespace and a single ```json fence.

    Models often wrap JSON output in a ```json``` block. The downstream
    ``promote_operator_artifact`` flow handles that already, but the
    helper exists for drivers that need the raw payload elsewhere.
    """
    if not text:
        return ""
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n(?P<body>.*?)\n```$", stripped, re.DOTALL)
    if fence:
        return fence.group("body").strip()
    return stripped


# ---------------------------------------------------------------------------
# Domain sets whose cookies must survive a browser-data clear.
# ---------------------------------------------------------------------------
_AUTH_COOKIE_DOMAINS: set[str] = {
    "chatgpt.com",
    ".chatgpt.com",
    "auth.openai.com",
    ".auth.openai.com",
    "openai.com",
    ".openai.com",
    "auth0.com",
    ".auth0.com",
    "accounts.google.com",
    ".accounts.google.com",
    "google.com",
    ".google.com",
    "gemini.google.com",
    ".gemini.google.com",
}

# Cookie names that are known to carry authentication state.
# If a cookie's domain matches *and* its name is in this set, it is kept.
# An empty set means "keep ALL cookies from matching domains".
_AUTH_COOKIE_NAMES: set[str] = set()  # empty → keep every cookie on auth domains


def _is_auth_cookie(cookie: dict) -> bool:
    """Return True if the cookie should be preserved during a data clear."""
    domain = (cookie.get("domain") or "").lower()
    if not any(domain == d or domain.endswith(d) for d in _AUTH_COOKIE_DOMAINS):
        return False
        
    name = cookie.get("name", "")
    
    # ChatGPT / OpenAI essential tokens
    if "session-token" in name or "csrf-token" in name or name == "__Secure-oai-is" or name == "cf_clearance":
        return True
        
    # Google / Gemini essential login tokens
    essential_google_names = {
        "SID", "HSID", "SSID", "APISID", "SAPISID",
        "__Secure-1PSID", "__Secure-3PSID",
        "__Secure-1PAPISID", "__Secure-3PAPISID",
        "__Secure-ENID", "__Secure-1PSIDTS", "__Secure-3PSIDTS",
        "NID", "LOGIN_INFO"
    }
    if name in essential_google_names:
        return True
        
    # Also check if it's an Auth0 cookie (for auth.openai.com)
    if "auth0" in domain or "auth0" in name.lower():
        return True
        
    return False


async def clear_browser_data_via_ui(context: "BrowserContext") -> None:
    """Clear history and cache directly using Chrome's settings UI."""
    try:
        page = await context.new_page()
        # Navigate to settings page with wait_until="commit" to avoid hanging on chrome:// urls
        await page.goto("chrome://settings/clearBrowserData", wait_until="commit")
        
        # Wait for the settings page elements to be loaded in the shadow DOM
        found = False
        for _ in range(10):
            has_elements = await page.evaluate("""() => {
                try {
                    const settingsUi = document.querySelector('settings-ui');
                    const settingsMain = settingsUi.shadowRoot.querySelector('settings-main');
                    const basicPage = settingsMain.shadowRoot.querySelector('settings-basic-page');
                    const privacyPage = basicPage.shadowRoot.querySelector('settings-section > settings-privacy-page');
                    const dialog = privacyPage.shadowRoot.querySelector('settings-clear-browsing-data-dialog');
                    const clearBtn = dialog.shadowRoot.querySelector('#clearButton');
                    return !!clearBtn;
                } catch (e) {
                    return false;
                }
            }""")
            if has_elements:
                found = True
                break
            await asyncio.sleep(1)
            
        if not found:
            print("[browser] Warning: clearButton not found inside settings shadow DOM", flush=True)
            await page.close()
            return
            
        # Configure checkboxes and click the clear button
        result = await page.evaluate("""() => {
            try {
                const settingsUi = document.querySelector('settings-ui');
                const settingsMain = settingsUi.shadowRoot.querySelector('settings-main');
                const basicPage = settingsMain.shadowRoot.querySelector('settings-basic-page');
                const privacyPage = basicPage.shadowRoot.querySelector('settings-section > settings-privacy-page');
                const dialog = privacyPage.shadowRoot.querySelector('settings-clear-browsing-data-dialog');
                const shadow = dialog.shadowRoot;
                
                // Configure basic checkboxes: clear history and cache, but KEEP cookies unchecked
                const browsingCheckboxBasic = shadow.querySelector('#browsingCheckboxBasic');
                const cookiesCheckboxBasic = shadow.querySelector('#cookiesCheckboxBasic');
                const cacheCheckboxBasic = shadow.querySelector('#cacheCheckboxBasic');
                
                if (browsingCheckboxBasic) browsingCheckboxBasic.checked = true;
                if (cookiesCheckboxBasic) cookiesCheckboxBasic.checked = false; // preserve logins!
                if (cacheCheckboxBasic) cacheCheckboxBasic.checked = true;
                
                // Configure advanced checkboxes if present (to be safe)
                const browsingCheckbox = shadow.querySelector('#browsingCheckbox');
                const cookiesCheckbox = shadow.querySelector('#cookiesCheckbox');
                const cacheCheckbox = shadow.querySelector('#cacheCheckbox');
                
                if (browsingCheckbox) browsingCheckbox.checked = true;
                if (cookiesCheckbox) cookiesCheckbox.checked = false; // preserve logins!
                if (cacheCheckbox) cacheCheckbox.checked = true;
                
                // Click the clear button
                const clearBtn = shadow.querySelector('#clearButton');
                if (clearBtn) {
                    clearBtn.click();
                    return { success: true };
                }
                return { success: false, error: "clearButton not found in click step" };
            } catch (e) {
                return { success: false, error: String(e) };
            }
        }""")
        
        if result.get("success"):
            print("[browser] Successfully cleared browsing history and cache via Chrome settings UI.", flush=True)
            # Give Chrome 3 seconds to execute the deletion
            await asyncio.sleep(3)
        else:
            print(f"[browser] Warning: Failed to trigger UI clear: {result.get('error')}", flush=True)
            
        await page.close()
    except Exception as e:
        print(f"[browser] Warning: Exception during UI-based clear: {e}", flush=True)


async def clear_browser_data_keep_login(context: "BrowserContext") -> dict:
    """Clear all browser data (cookies, storage, cache) but keep login cookies.

    Steps:
      0. Clear history and cache directly via Chrome Settings UI (preserving cookies).
      1. Snapshot cookies from the browser context.
      2. Filter → keep only auth-related cookies.
      3. Clear ALL cookies from the context.
      4. Clear localStorage / sessionStorage on every open page (best-effort).
      5. Restore the saved auth cookies.

    Returns a summary dict with counts for logging.
    """
    # 0. Clear history and cache directly using Chrome's settings UI
    await clear_browser_data_via_ui(context)
    # 1. Snapshot all cookies
    all_cookies = await context.cookies()
    total_cookies = len(all_cookies)

    # 2. Filter auth cookies to preserve
    auth_cookies = [c for c in all_cookies if _is_auth_cookie(c)]
    kept_count = len(auth_cookies)

    # 3. Clear ALL cookies
    await context.clear_cookies()

    # 4. Clear localStorage / sessionStorage on every open page
    pages_cleared = 0
    for page in context.pages:
        try:
            await page.evaluate("""() => {
                try { localStorage.clear(); } catch(e) {}
                try { sessionStorage.clear(); } catch(e) {}
            }""")
            pages_cleared += 1
        except Exception:
            pass  # page may be about:blank or navigating

    # 5. Restore auth cookies
    if auth_cookies:
        # Playwright's add_cookies expects a specific format; adapt
        cookies_to_add = []
        for c in auth_cookies:
            entry = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
            }
            if c.get("expires", -1) > 0:
                entry["expires"] = c["expires"]
            if c.get("httpOnly"):
                entry["httpOnly"] = True
            if c.get("secure"):
                entry["secure"] = True
            if c.get("sameSite"):
                entry["sameSite"] = c["sameSite"]
            cookies_to_add.append(entry)
        await context.add_cookies(cookies_to_add)

    summary = {
        "total_cookies_before": total_cookies,
        "auth_cookies_kept": kept_count,
        "cookies_cleared": total_cookies - kept_count,
        "pages_storage_cleared": pages_cleared,
    }
    print(
        f"[browser] Cleared browser data: {summary['cookies_cleared']} cookies removed, "
        f"{kept_count} auth cookies preserved, "
        f"{pages_cleared} pages storage cleared.",
        flush=True,
    )
    return summary

