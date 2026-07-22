"""Gemini image-generation driver — fallback when ChatGPT image gen fails.

ChatGPT's Free-tier account intermittently cannot generate images at all
("Image generation failed" twice in a row, or a 502 from the backend). Rather
than blocking the whole Short on that, /chatgpt/image[/batch] falls back to
this driver so the render can still get a usable graphic image.

Unlike ChatGPTImageDriver, this driver never needs post-hoc conversation
cleanup: Gemini's temporary-chat mode (same toggle GeminiDriver uses for text)
means nothing is saved to history in the first place.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    save_trace_screenshot,
)
from video_agent.browser_worker.drivers.gemini import (
    COMPOSER_SELECTORS,
    SEND_BUTTON_SELECTORS,
    STOP_BUTTON_SELECTORS,
    _enter_temporary_chat,
    _first_matching,
    _is_login_url,
)
from video_agent.browser_worker.drivers.humanize import human_click, human_pause, human_type
from video_agent.storage.atomic import atomic_write_bytes

if TYPE_CHECKING:
    from playwright.async_api import Page


GEMINI_IMAGE_URL = "https://gemini.google.com/images"

IMAGE_GEN_INSTRUCTION = (
    "Generate one photorealistic image at exactly 1920x1080 pixels "
    "(Full HD, 16:9 landscape orientation). Fill the entire 1920x1080 frame — "
    "no borders, no padding, no commentary, no text overlays, no watermark."
)

IMAGE_GEN_INSTRUCTION_PORTRAIT = (
    "Generate one photorealistic image at exactly 1080x1920 pixels "
    "(Full HD, 9:16 portrait orientation). Fill the entire 1080x1920 frame — "
    "no borders, no padding, no commentary, no text overlays, no watermark."
)


def build_gemini_image_prompt(prompt: str, aspect_ratio: str = "16:9") -> str:
    instruction = (
        IMAGE_GEN_INSTRUCTION_PORTRAIT if aspect_ratio == "9:16" else IMAGE_GEN_INSTRUCTION
    )
    return f"{instruction}\n\n{prompt}"


class GeminiImageDriver:
    """Driver for Gemini (Imagen) image generation.

    Flow per call:
      1. Open gemini.google.com/images in TEMPORARY chat mode (no history).
      2. Send the prompt (aspect-ratio instruction folded into the text).
      3. Wait for an assistant <img> to appear.
      4. Download the rendered image bytes via Playwright APIRequest.
    No conversation cleanup needed — temporary chat never saves history.
    """

    def __init__(self, page: Page) -> None:
        self.page = page
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        navigated = False
        nav_errors: list[str] = []
        for attempt in range(3):
            try:
                await self.page.goto(
                    GEMINI_IMAGE_URL, wait_until="domcontentloaded", timeout=30_000
                )
                navigated = True
                break
            except Exception as exc:
                nav_errors.append(f"attempt {attempt + 1}: {exc}")
                await self.page.wait_for_timeout(800)
        if not navigated:
            shot = await save_trace_screenshot(self.page, prefix="gemini-image-goto-failed")
            raise BrowserDriverError(
                "Gemini image navigation failed: " + " | ".join(nav_errors[-3:]),
                screenshot_path=shot,
            )
        await human_pause(self.page, min_ms=1200, max_ms=2200)
        if _is_login_url(str(getattr(self.page, "url", "") or "")):
            shot = await save_trace_screenshot(self.page, prefix="gemini-image-login")
            raise LoginRequiredError(
                "Gemini profile is signed out. Open http://localhost:7900 to sign in.",
                screenshot_path=shot,
            )
        # Best-effort: some Gemini surfaces/rollouts don't expose the toggle on
        # this route. Unlike GeminiDriver's text chat, a missing toggle here
        # is not fatal — the /images entry point may already be ephemeral.
        await _enter_temporary_chat(self.page)
        self._opened = True

    async def generate_image(
        self,
        prompt: str,
        *,
        project_name: str,
        out_path: Path,
        response_timeout_ms: int = 240_000,
        aspect_ratio: str = "16:9",
    ) -> dict:
        """End-to-end: send prompt, save image to ``out_path``.

        Returns ``{src, local_path, project_name, bytes, provider}``.
        """
        if not self._opened:
            await self.open()
        if not prompt.strip():
            raise BrowserDriverError("Empty image prompt")

        composer = await _first_matching(self.page, COMPOSER_SELECTORS, 10_000)
        if composer is None:
            shot = await save_trace_screenshot(self.page, prefix="gemini-image-no-composer")
            raise BrowserDriverError("Gemini composer not found.", screenshot_path=shot)

        full_prompt = build_gemini_image_prompt(prompt, aspect_ratio=aspect_ratio)
        await human_click(composer, hover_pause_min_ms=60, hover_pause_max_ms=200)
        await composer.focus()
        await human_pause(self.page)
        await human_type(self.page, full_prompt)
        await human_pause(self.page, min_ms=1500, max_ms=3500)

        send_button = await _first_matching(self.page, SEND_BUTTON_SELECTORS, 5_000)
        if send_button is None:
            shot = await save_trace_screenshot(self.page, prefix="gemini-image-no-send")
            raise BrowserDriverError("Gemini send button not found.", screenshot_path=shot)
        await human_click(send_button)

        try:
            stop = await _first_matching(self.page, STOP_BUTTON_SELECTORS, 5_000)
            if stop is not None:
                await stop.wait_for(state="hidden", timeout=response_timeout_ms)
        except Exception:
            pass

        src = await self._wait_for_image(response_timeout_ms)
        await self._download_image(src, out_path)
        return {
            "src": src,
            "local_path": str(out_path),
            "project_name": project_name,
            "bytes": out_path.stat().st_size,
            "provider": "gemini",
        }

    async def _wait_for_image(self, response_timeout_ms: int) -> str:
        """Poll the assistant turn until a generated response image appears.

        See ``_find_response_image_src``: two live trace screenshots (bug-511)
        confirmed the image DOES render correctly on Gemini, but neither plain
        ``document.querySelectorAll`` NOR Playwright's shadow-piercing
        ``page.locator("img")`` found it — the response is nested behind
        shadow roots AND/OR rendered as a CSS ``background-image`` rather than
        a plain ``<img>``. A manual recursive walker handles both.
        """
        deadline = time.monotonic() + response_timeout_ms / 1000.0
        last_logged = 0
        while time.monotonic() < deadline:
            try:
                src = await self._find_response_image_src()
            except Exception as exc:
                if "context was destroyed" in str(exc) or "navigation" in str(exc).lower():
                    await self.page.wait_for_timeout(600)
                    continue
                raise
            if src:
                return src
            failure = await self._detect_generation_failure()
            if failure:
                shot = await save_trace_screenshot(self.page, prefix="gemini-image-gen-failed")
                raise BrowserDriverError(
                    "Gemini reported an image-generation failure.",
                    screenshot_path=shot,
                )
            await self.page.wait_for_timeout(600)
            now = int(time.monotonic())
            if now - last_logged >= 10:
                last_logged = now
                remaining = int(deadline - time.monotonic())
                print(
                    f"[gemini-image] waiting for generated image... ~{remaining}s left",
                    flush=True,
                )
        shot = await save_trace_screenshot(self.page, prefix="gemini-image-timeout")
        raise BrowserDriverError("Gemini image generation timed out.", screenshot_path=shot)

    # Recursively walks light DOM + every OPEN shadow root (Gemini's Angular UI
    # nests the response deep in shadow roots, and — per a live trace screenshot
    # taken after Playwright's own shadow-piercing `page.locator("img")` STILL
    # found nothing despite the image having rendered — the response image is
    # apparently NOT a plain <img> at all, but a CSS `background-image` on a
    # div. This walker catches both shapes in one pass.
    _FIND_IMAGE_JS = """() => {
        function walk(root, out) {
            const all = root.querySelectorAll('*');
            for (const el of all) {
                if (el.tagName === 'IMG' && el.src) {
                    const r = el.getBoundingClientRect();
                    out.push({src: el.src, w: r.width, h: r.height});
                } else {
                    const bg = getComputedStyle(el).backgroundImage;
                    if (bg && bg.startsWith('url(')) {
                        const m = bg.match(/url\\((['"]?)(.*?)\\1\\)/);
                        if (m && m[2]) {
                            const r = el.getBoundingClientRect();
                            out.push({src: m[2], w: r.width, h: r.height});
                        }
                    }
                }
                if (el.shadowRoot) walk(el.shadowRoot, out);
            }
        }
        const out = [];
        walk(document, out);
        return out;
    }"""

    async def _find_response_image_src(self) -> str:
        """Locate the generated response image, in light DOM or any shadow root.

        Excludes small chrome (sidebar avatar, nav icons) by requiring a
        real rendered size; prefers the LAST match (most recent turn).
        """
        candidates = await self.page.evaluate(self._FIND_IMAGE_JS)
        for item in reversed(candidates or []):
            src = str(item.get("src") or "")
            # bug-511 recurrence (bridge 20260722): the live Gemini response image
            # carries a `blob:` src, and sometimes a `data:` URI — NOT http. The old
            # `startswith("http")` filter skipped exactly the real generated image
            # and the wait timed out at 0s while the picture sat on screen.
            if not (src.startswith("http") or src.startswith("blob:") or src.startswith("data:")):
                continue
            low = src.lower()
            if "avatar" in low or "favicon" in low or "/icon" in low:
                continue
            # Sidebar avatar / nav icons render well under 200px; the
            # generated response image fills most of the content column.
            if float(item.get("w") or 0) < 200 or float(item.get("h") or 0) < 200:
                continue
            return src
        return ""

    async def _detect_generation_failure(self) -> bool:
        try:
            return await self.page.evaluate(
                """() => {
                    const t = (document.body.innerText || '').toLowerCase();
                    return t.includes('unable to create')
                        || t.includes('unable to generate')
                        || t.includes('something went wrong')
                        || t.includes('no se pudo generar')
                        || t.includes('no se pudo crear');
                }"""
            )
        except Exception:
            return False

    # Fetch a blob:/data: URL from INSIDE the page (those URLs live only in the
    # page's origin/context — Playwright's request API cannot reach them) and
    # return the ORIGINAL-resolution bytes as base64. Screenshotting the displayed
    # <img> would save the shrunk 708x395 box, not the generated image (bug-511
    # recurrence 20260722 explicitly forbids that).
    _READ_BLOB_JS = """async (src) => {
        const resp = await fetch(src);
        const buf = await resp.arrayBuffer();
        let binary = '';
        const bytes = new Uint8Array(buf);
        const chunk = 0x8000;
        for (let i = 0; i < bytes.length; i += chunk) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
        }
        return btoa(binary);
    }"""

    async def _download_image(self, src: str, dest: Path) -> Path:
        max_attempts = 3
        in_page = src.startswith("blob:") or src.startswith("data:")
        for attempt in range(1, max_attempts + 1):
            try:
                if in_page:
                    import base64
                    b64 = await self.page.evaluate(self._READ_BLOB_JS, src)
                    body = base64.b64decode(b64)
                    if not body:
                        raise BrowserDriverError("Gemini blob fetch returned no bytes.")
                else:
                    response = await self.page.context.request.get(src)
                    if response.status != 200:
                        raise BrowserDriverError(f"Image download failed: HTTP {response.status}")
                    body = await response.body()
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(dest, body)
                return dest
            except Exception as exc:
                if attempt < max_attempts:
                    print(f"Gemini image download attempt {attempt} failed: {exc}. Retrying in 2 seconds...")
                    await self.page.wait_for_timeout(2_000)
                else:
                    raise BrowserDriverError(
                        f"Gemini image download error after {max_attempts} attempts: {exc}"
                    ) from exc
