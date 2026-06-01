from __future__ import annotations

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

    The operator needs to open KasmVNC (http://localhost:7900) and sign
    in once. The driver should never attempt to log the user in.
    """


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
