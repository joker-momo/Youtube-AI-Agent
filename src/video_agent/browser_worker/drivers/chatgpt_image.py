from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    save_trace_screenshot,
)
from video_agent.browser_worker.drivers.humanize import (
    human_click,
    human_pause,
    human_type,
)

if TYPE_CHECKING:
    from playwright.async_api import Page


CHATGPT_HOME = "https://chatgpt.com/"

PROJECTS_HEADER_SELECTOR = "button:has-text('Projects')"
NEW_PROJECT_BUTTON_SELECTOR = "button:has-text('New project')"
PROJECT_NAME_INPUT_SELECTOR = "input[name='projectName']"
CREATE_PROJECT_BUTTON_SELECTOR = "button:has-text('Create project')"
COMPOSER_SELECTORS = (
    "div#prompt-textarea[contenteditable='true']",
    "textarea[name='prompt-textarea']",
    "[contenteditable='true'][role='textbox']",
)
SEND_BUTTON_SELECTORS = (
    "[data-testid='send-button']",
    "button[aria-label*='Send' i]",
)
STOP_BUTTON_SELECTORS = (
    "[data-testid='stop-button']",
    "button[aria-label*='Stop' i]",
)
ASSISTANT_IMG_SELECTOR = "[data-message-author-role='assistant'] img"


def _is_login_url(url: str) -> bool:
    return (
        "auth.openai.com" in url
        or "/auth/login" in url
        or re.search(r"chatgpt\.com/(login|auth)", url) is not None
    )


class ChatGPTImageDriver:
    """Driver for ChatGPT image generation via the Projects workflow.

    Flow per call:
      1. Open ``chatgpt.com/`` (regular chat — image gen is NOT
         available in temporary chat).
      2. Expand "Projects" header if collapsed.
      3. Click "New project", fill projectName, click "Create project".
      4. Inside the new project, click "Create an image" quick action
         (optional; falls back to typing a "Generate image:" prefix).
      5. Send the prompt, wait for an assistant <img> to appear.
      6. Download the rendered image bytes via Playwright APIRequest.
    """

    def __init__(self, page: "Page") -> None:
        self.page = page
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        await self.page.goto(CHATGPT_HOME, wait_until="domcontentloaded", timeout=30_000)
        await human_pause(self.page, min_ms=1200, max_ms=2200)
        if _is_login_url(self.page.url):
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-login")
            raise LoginRequiredError(
                "ChatGPT profile is signed out. Open http://localhost:7900 to sign in.",
                screenshot_path=shot,
            )
        self._opened = True

    async def _ensure_projects_expanded(self) -> None:
        """Expand the "Projects" sidebar group so "New project" is visible."""
        header = self.page.locator(PROJECTS_HEADER_SELECTOR).first
        try:
            expanded = await header.get_attribute("aria-expanded")
        except Exception:
            expanded = None
        if expanded != "true":
            try:
                await human_click(header, hover_pause_min_ms=80, hover_pause_max_ms=180)
                await human_pause(self.page, min_ms=600, max_ms=1100)
            except Exception:
                pass  # best effort — may already be expanded

    async def _create_project(self, name: str) -> None:
        try:
            name_input = self.page.locator(PROJECT_NAME_INPUT_SELECTOR).first
            dialog_already_open = False
            try:
                if await name_input.is_visible():
                    dialog_already_open = True
            except Exception:
                pass

            if not dialog_already_open:
                await self._ensure_projects_expanded()
                new_btn = self.page.locator(NEW_PROJECT_BUTTON_SELECTOR).first
                try:
                    await new_btn.wait_for(state="visible", timeout=15_000)
                except Exception:
                    # Double-check if dialog appeared while expanding sidebar
                    try:
                        if await name_input.is_visible():
                            dialog_already_open = True
                    except Exception:
                        pass
                    if not dialog_already_open:
                        shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-no-new-project")
                        raise BrowserDriverError(
                            "ChatGPT 'New project' button not found.",
                            screenshot_path=shot,
                        )
                if not dialog_already_open:
                    await human_click(new_btn)
                    await human_pause(self.page, min_ms=600, max_ms=1200)

            try:
                await name_input.wait_for(state="visible", timeout=30_000)
            except Exception:
                shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-no-name-input")
                raise BrowserDriverError(
                    "ChatGPT new-project name input not found.",
                    screenshot_path=shot,
                )
            
            # Super-robust React input filling
            await name_input.click()
            await name_input.focus()
            await name_input.fill("")
            await name_input.type(name, delay=30)
            await self.page.evaluate(
                """(val) => {
                    const el = document.querySelector("input[name='projectName']");
                    if (el) {
                        el.value = val;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""",
                name
            )
            await human_pause(self.page, min_ms=500, max_ms=1000)

            create_btn = self.page.locator(CREATE_PROJECT_BUTTON_SELECTOR).first
            try:
                await create_btn.wait_for(state="visible", timeout=15_000)
            except Exception:
                shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-no-create-btn")
                raise BrowserDriverError(
                    "ChatGPT 'Create project' button not found.",
                    screenshot_path=shot,
                )
            await human_click(create_btn)
            
            # Wait for URL change to /g/g-p-<id>
            try:
                await self.page.wait_for_url(re.compile(r"/g/g-p-"), timeout=30_000)
            except Exception:
                # Project may still have created even if URL pattern differs.
                pass
            await human_pause(self.page, min_ms=1200, max_ms=2200)
        except Exception as exc:
            if isinstance(exc, BrowserDriverError):
                raise
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-create-project-error")
            raise BrowserDriverError(
                f"Create project failed: {exc}",
                screenshot_path=shot,
            ) from exc

    async def _focus_composer(self) -> None:
        composer = None
        for sel in COMPOSER_SELECTORS:
            loc = self.page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=4_000)
                composer = loc
                break
            except Exception:
                continue
        if composer is None:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-no-composer")
            raise BrowserDriverError(
                "ChatGPT composer not found inside project.",
                screenshot_path=shot,
            )
        try:
            await human_click(composer, hover_pause_min_ms=80, hover_pause_max_ms=200)
        except Exception:
            await self.page.evaluate(
                "() => document.querySelector(\"textarea[name='prompt-textarea']\")?.focus()"
            )
        await human_pause(self.page, min_ms=200, max_ms=500)

    async def _click_send(self) -> None:
        for sel in SEND_BUTTON_SELECTORS:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=1_500):
                    await human_click(btn)
                    return
            except Exception:
                continue
        raise BrowserDriverError("ChatGPT send button not found.")

    async def _wait_for_image(self, response_timeout_ms: int, exclude_urls: list[str] | None = None) -> str:
        """Poll the assistant turn until an <img> with a real src appears.

        Returns the image src URL.
        """
        deadline = time.monotonic() + response_timeout_ms / 1000.0
        last_logged = 0
        exclude_list = exclude_urls or []
        while time.monotonic() < deadline:
            src = await self.page.evaluate(
                """(excludeList) => {
                    const exclude = new Set(excludeList || []);
                    const containers = [
                        "[data-message-author-role='assistant'] img",
                        "main img",
                        "article img",
                    ];
                    for (const sel of containers) {
                        const imgs = document.querySelectorAll(sel);
                        for (let i = imgs.length - 1; i >= 0; i--) {
                            const s = imgs[i].src || '';
                            if (!s.startsWith('http')) continue;
                            if (exclude.has(s)) continue;
                            if (s.includes('avatar') || s.includes('icon')) continue;
                            if (imgs[i].naturalWidth < 256) continue;
                            return s;
                        }
                    }
                    return '';
                }""",
                exclude_list
            )
            if src:
                return src
            # Detect a "stop" button still present (model is generating).
            await self.page.wait_for_timeout(1_500)
            now = int(time.monotonic())
            if now - last_logged >= 10:
                last_logged = now
        shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-timeout")
        raise BrowserDriverError(
            "ChatGPT image generation timed out.",
            screenshot_path=shot,
        )

    async def _download_image(self, src: str, dest: Path) -> Path:
        """Download the image bytes via the page's APIRequest (carries auth)."""
        ctx = self.page.context
        try:
            response = await ctx.request.get(src)
            if response.status != 200:
                raise BrowserDriverError(
                    f"Image download failed: HTTP {response.status}"
                )
            body = await response.body()
        except Exception as exc:
            raise BrowserDriverError(f"Image download error: {exc}") from exc
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return dest

    async def generate_image(
        self,
        prompt: str,
        *,
        project_name: str,
        out_path: Path,
        response_timeout_ms: int = 240_000,
    ) -> dict:
        """End-to-end: create project, send prompt, save image to ``out_path``.

        Returns ``{src, local_path, project_name, bytes}``.
        """
        if not self._opened:
            await self.open()
        if not prompt.strip():
            raise BrowserDriverError("Empty image prompt")

        await self._create_project(project_name)
        await self._focus_composer()
        # Prepend an explicit "Generate one image" instruction; ChatGPT
        # interprets it as image-gen tool invocation when a project chat
        # is fresh.
        full_prompt = (
            "Generate one photorealistic image (16:9), no commentary, no "
            "text overlays, no watermark.\n\n" + prompt.strip()
        )
        await human_type(self.page, full_prompt)
        await human_pause(self.page, min_ms=500, max_ms=1200)
        await self._click_send()

        # Wait for the stop button to disappear (best-effort), then for
        # an assistant image.
        try:
            for sel in STOP_BUTTON_SELECTORS:
                stop = self.page.locator(sel).first
                try:
                    if await stop.is_visible(timeout=2_000):
                        await stop.wait_for(state="hidden", timeout=response_timeout_ms)
                        break
                except Exception:
                    continue
        except Exception:
            pass

        src = await self._wait_for_image(response_timeout_ms)
        await self._download_image(src, out_path)
        return {
            "src": src,
            "local_path": str(out_path),
            "project_name": project_name,
            "bytes": out_path.stat().st_size,
        }

    async def generate_images(
        self,
        prompts: list[str],
        *,
        project_name: str,
        out_paths: list[Path],
        response_timeout_ms: int = 240_000,
    ) -> list[dict]:
        """Generate multiple photorealistic images sequentially in the same ChatGPT project chat session."""
        if not self._opened:
            await self.open()
        if not prompts:
            raise BrowserDriverError("Empty prompts list")
        if len(prompts) != len(out_paths):
            raise BrowserDriverError("Prompts and out_paths length mismatch")

        await self._create_project(project_name)

        results = []
        exclude_urls = []
        for i, (prompt, out_path) in enumerate(zip(prompts, out_paths), start=1):
            if not prompt.strip():
                raise BrowserDriverError(f"Empty prompt at index {i}")

            await self._focus_composer()
            full_prompt = (
                "Generate one photorealistic image (16:9), no commentary, no "
                "text overlays, no watermark.\n\n" + prompt.strip()
            )
            await human_type(self.page, full_prompt)
            await human_pause(self.page, min_ms=500, max_ms=1200)
            await self._click_send()

            # Wait for the stop button to disappear (best-effort), then for
            # an assistant image.
            try:
                for sel in STOP_BUTTON_SELECTORS:
                    stop = self.page.locator(sel).first
                    try:
                        if await stop.is_visible(timeout=2_000):
                            await stop.wait_for(state="hidden", timeout=response_timeout_ms)
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            src = await self._wait_for_image(response_timeout_ms, exclude_urls=exclude_urls)
            await self._download_image(src, out_path)
            exclude_urls.append(src)
            results.append({
                "src": src,
                "local_path": str(out_path),
                "project_name": project_name,
                "bytes": out_path.stat().st_size,
            })
            await human_pause(self.page, min_ms=1500, max_ms=3000)

        return results
