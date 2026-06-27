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
from video_agent.storage.atomic import atomic_write_bytes

if TYPE_CHECKING:
    from playwright.async_api import Page


CHATGPT_HOME = "https://chatgpt.com/"

# Single source of truth for the image-gen instruction prepended to every
# user image prompt. Enforces Full HD landscape so the rendered 1920x1080
# video composite never has to upscale a smaller generation.
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


def build_image_gen_prompt(prompt: str, aspect_ratio: str = "16:9") -> str:
    """Prepend the image-gen instruction to a user prompt, with automated contradiction checks."""
    if aspect_ratio == "9:16":
        instruction = IMAGE_GEN_INSTRUCTION_PORTRAIT
    else:
        instruction = IMAGE_GEN_INSTRUCTION
    
    prompt_lower = prompt.lower()
    
    # 1. Text overlays / Typography check
    text_indicators = ["text", "overlay", "word", "font", "typography", "title", "label", "writing", "letter", "quote"]
    if any(ind in prompt_lower for ind in text_indicators):
        instruction = instruction.replace(", no text overlays", "")
        
    # 2. Watermark / Brand check
    watermark_indicators = ["watermark", "logo", "brand", "signature"]
    if any(ind in prompt_lower for ind in watermark_indicators):
        instruction = instruction.replace(", no watermark", "")
        
    # 3. Border / Frame / Padding check
    border_indicators = ["border", "padding", "frame", "margin"]
    if any(ind in prompt_lower for ind in border_indicators):
        instruction = instruction.replace("no borders, ", "").replace("no padding, ", "")
        
    full_prompt = instruction + "\n\n" + prompt.strip()
    
    # Print the full prompt for transparency and debugging
    print(f"\n==================================================")
    print(f"[ChatGPTImageDriver] FINAL FULL IMAGE GENERATION PROMPT ({aspect_ratio}):")
    print(f"--------------------------------------------------")
    print(full_prompt)
    print(f"==================================================\n")
    
    return full_prompt



PROJECTS_HEADER_SELECTOR = "button:has-text('Projects')"
# ChatGPT moved "Projects" from an expandable sidebar group (a <button> with
# aria-expanded) to a plain nav link (<a href=.../projects>). Cover both so the
# project flow works across the old and current layouts.
PROJECTS_NAV_SELECTORS = (
    "a[href$='/projects']",
    "a[href*='/project']",
    "nav a:has-text('Projects')",
    "aside a:has-text('Projects')",
    "a:has-text('Projects')",
    "button:has-text('Projects')",
)
# The "New project" entry point. In the new UI it can be a sidebar link, a "+"
# button next to the Projects heading, or a button on the /projects page.
NEW_PROJECT_BUTTON_SELECTORS = (
    "button:has-text('New project')",
    "a:has-text('New project')",
    "[role='menuitem']:has-text('New project')",
    "button[aria-label*='New project' i]",
    "a[aria-label*='New project' i]",
    "button:has-text('Create project')",
    "button:has-text('New Project')",
)
NEW_PROJECT_BUTTON_SELECTOR = "button:has-text('New project')"
# Fallback path when the Projects sidebar group is gone (ChatGPT moved
# "Projects" to a plain nav link with no inline "New project" button): start a
# normal, non-temporary chat and generate the image there instead.
NEW_CHAT_SELECTORS = (
    "a[data-testid='create-new-chat-button']",
    "button[data-testid='create-new-chat-button']",
    "a[aria-label*='New chat' i]",
    "button[aria-label*='New chat' i]",
    "a:has-text('New chat')",
    "button:has-text('New chat')",
)
PROJECT_NAME_INPUT_SELECTOR = "input[name='projectName']"
CREATE_PROJECT_BUTTON_SELECTOR = "button:has-text('Create project')"
COMPOSER_SELECTORS = (
    "div#prompt-textarea[contenteditable='true']",
    "textarea[name='prompt-textarea']",
    "[contenteditable='true'][role='textbox']",
)
IMAGE_MODE_PILL_SELECTOR = (
    "[data-system-hint-type='picture_v2'], "
    "[data-inline-selection-pill][data-keyword='Create image']"
)
SEND_BUTTON_SELECTORS = (
    "[data-testid='send-button']",
    "[data-testid='fruitjuice-send-button']",
    "button[aria-label='Send prompt']",
    "button[aria-label*='Send' i]",
)
STOP_BUTTON_SELECTORS = (
    "[data-testid='stop-button']",
    "button[aria-label*='Stop' i]",
)
ASSISTANT_IMG_SELECTOR = "[data-message-author-role='assistant'] img"
CREATE_IMAGE_MODE_SELECTORS = (
    "button:has-text('Create image')",
    "button:has-text('Create an image')",
    "[role='menuitem']:has-text('Create image')",
    "[role='menuitem']:has-text('Create an image')",
    "[role='option']:has-text('Create image')",
    "[aria-label*='Create image' i]",
)
IMAGE_TOOL_MENU_SELECTORS = (
    "button[aria-label*='Tools' i]",
    "button:has-text('Tools')",
    "button[aria-label*='Add' i]",
    "button[aria-label*='More' i]",
    "[data-testid='composer-plus-btn']",
)
ASPECT_RATIO_TRIGGER_SELECTORS = (
    "button:has-text('Aspect ratio')",
    "button[aria-label*='Aspect ratio' i]",
    "button:has-text('Size')",
    "button:has-text('Square')",
    "button:has-text('Landscape')",
)
ASPECT_RATIO_16_9_SELECTORS = (
    "button:has-text('16:9')",
    "[role='menuitem']:has-text('16:9')",
    "[role='option']:has-text('16:9')",
    "button:has-text('Landscape')",
    "[role='menuitem']:has-text('Landscape')",
    "[role='option']:has-text('Landscape')",
)
ASPECT_RATIO_9_16_SELECTORS = (
    "button:has-text('9:16')",
    "[role='menuitem']:has-text('9:16')",
    "[role='option']:has-text('9:16')",
    "button:has-text('Portrait')",
    "[role='menuitem']:has-text('Portrait')",
    "[role='option']:has-text('Portrait')",
)


def _is_login_url(url: str) -> bool:
    return (
        "auth.openai.com" in url
        or "/auth/login" in url
        or re.search(r"chatgpt\.com/(login|auth)", url) is not None
    )


class ChatGPTImageDriver:
    """Driver for ChatGPT image generation.

    Flow per call:
      1. Open a normal, non-temporary ChatGPT conversation.
      2. Select "Create image" mode and 16:9.
      3. Send the prompt, wait for an assistant <img> to appear.
      4. Download the rendered image bytes via Playwright APIRequest.
      5. Delete the conversation to avoid leaving history clutter.
    """

    def __init__(self, page: "Page") -> None:
        self.page = page
        self._opened = False
        # Kept for back-compat with the old Project cleanup path. The current
        # image flow always uses normal chats and deletes the conversation.
        self._used_project = False

    async def open(self) -> None:
        if self._opened:
            return

        # Navigate with retries — page.goto() throws on HTTP error responses
        # (e.g. 431 Request Header Fields Too Large).
        navigated = False
        nav_errors: list[str] = []
        for attempt in range(3):
            try:
                await self.page.goto(
                    CHATGPT_HOME, wait_until="domcontentloaded", timeout=30_000
                )
                navigated = True
                break
            except Exception as exc:
                nav_errors.append(f"attempt {attempt + 1}: {exc}")
                await self.page.wait_for_timeout(800)

        if not navigated:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-goto-failed")
            raise BrowserDriverError(
                "ChatGPT image navigation failed: " + " | ".join(nav_errors[-3:]),
                screenshot_path=shot,
            )

        await human_pause(self.page, min_ms=1200, max_ms=2200)

        # Check for HTTP ERROR 431 and click Reload (up to 2 attempts)
        for reload_attempt in range(2):
            try:
                content = await self.page.content()
                if "HTTP ERROR 431" in content or "431" in content:
                    reload_btn = self.page.locator(
                        "button#reload-button, button:has-text('Reload')"
                    ).first
                    if await reload_btn.is_visible(timeout=2000):
                        print(
                            f"[chatgpt-image] HTTP 431 detected (attempt {reload_attempt + 1}). Clicking Reload...",
                            flush=True,
                        )
                        await reload_btn.click()
                        await self.page.wait_for_timeout(3000)
                    else:
                        break
                else:
                    break
            except Exception:
                break

        if _is_login_url(str(getattr(self.page, "url", "") or "")):
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-login")
            raise LoginRequiredError(
                "ChatGPT profile is signed out. Open http://localhost:7900 to sign in.",
                screenshot_path=shot,
            )
        self._opened = True

    async def _ensure_projects_expanded(self) -> None:
        """Reveal the "Projects" section so a "New project" entry is reachable.

        Handles both layouts:
        * old — "Projects" is an expandable <button> group (toggle aria-expanded);
        * new — "Projects" is a nav link that navigates to the projects page.
        """
        # New UI: if a "New project" entry is already visible, nothing to do.
        for sel in NEW_PROJECT_BUTTON_SELECTORS:
            try:
                if await self.page.locator(sel).first.is_visible(timeout=400):
                    return
            except Exception:
                continue

        # Old UI: toggle the collapsible group if present.
        header = self.page.locator(PROJECTS_HEADER_SELECTOR).first
        try:
            if await header.is_visible(timeout=600):
                expanded = await header.get_attribute("aria-expanded")
                if expanded == "false":
                    await human_click(header, hover_pause_min_ms=80, hover_pause_max_ms=180)
                    await human_pause(self.page, min_ms=200, max_ms=400)
                    return
        except Exception:
            pass

        # New UI: click the Projects nav link to open the projects view.
        for sel in PROJECTS_NAV_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=600):
                    await human_click(loc, hover_pause_min_ms=80, hover_pause_max_ms=180)
                    await human_pause(self.page, min_ms=600, max_ms=1100)
                    return
            except Exception:
                continue

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
                # Locate the "New project" entry across old/new layouts.
                new_btn = None
                for sel in NEW_PROJECT_BUTTON_SELECTORS:
                    loc = self.page.locator(sel).first
                    try:
                        await loc.wait_for(state="visible", timeout=4_000)
                        new_btn = loc
                        break
                    except Exception:
                        continue
                if new_btn is None:
                    # Dialog may have opened directly while navigating the sidebar.
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
                if not dialog_already_open and new_btn is not None:
                    # The "New project" entry sits under the sticky sidebar
                    # header, which intercepts pointer events and makes a normal
                    # actionability-checked click time out (then we wrongly fell
                    # back to a plain chat with no Project). Bypass the overlay.
                    await human_click(new_btn, force_on_intercept=True)
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

    async def _start_new_chat(self) -> None:
        """Open a fresh, normal (non-temporary) chat for image generation.

        A plain new chat exposes the "Create image" tool + composer, unlike
        temporary chats in accounts where image generation is disabled there.
        The conversation is deleted after the image is downloaded.
        """
        current_url = str(getattr(self.page, "url", "") or "")
        already_in_chatgpt = current_url.startswith("https://chatgpt.com/")
        if not already_in_chatgpt:
            try:
                await self.page.goto(
                    CHATGPT_HOME, wait_until="domcontentloaded", timeout=30_000
                )
                await human_pause(self.page, min_ms=800, max_ms=1600)
            except Exception:
                # Best effort — fall back to clicking an in-page "New chat" control.
                pass
        # Best-effort click of an explicit "New chat" affordance to guarantee a
        # clean composer even if we were already on a stale conversation.
        await self._click_first_visible(NEW_CHAT_SELECTORS, timeout_ms=2_000)
        await human_pause(self.page, min_ms=300, max_ms=700)

    async def _start_temporary_chat(self) -> bool:
        """Temporary chats are intentionally disabled for image generation."""
        raise BrowserDriverError(
            "ChatGPT image generation must use a normal chat, not temporary chat."
        )

    async def _ensure_image_session(self, project_name: str) -> bool:
        """Prepare a chat for image generation.

        Always uses a normal, non-temporary conversation. Temporary chats are
        deliberately avoided because some ChatGPT accounts disable image
        generation there; Projects are also avoided to keep the flow simple.

        Returns ``False`` because no Project is created. Teardown deletes the
        just-used normal conversation.
        """
        _ = project_name
        await self._start_new_chat()
        if _is_login_url(str(getattr(self.page, "url", "") or "")):
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-login")
            raise LoginRequiredError(
                "ChatGPT profile is signed out. Open http://localhost:7900 to sign in.",
                screenshot_path=shot,
            )
        self._used_project = False
        return False

    async def _focus_composer(self) -> "Locator":
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
            await composer.focus()
        except Exception:
            await self.page.evaluate(
                "() => document.querySelector(\"textarea[name='prompt-textarea']\")?.focus()"
            )
        await human_pause(self.page, min_ms=200, max_ms=500)
        return composer

    async def _fill_composer_robust(self, composer, text: str) -> None:
        """Robustly fill the ChatGPT composer (contenteditable or textarea) with React updates."""
        if not hasattr(self.page, "evaluate"):
            return

        # "Create an image" is a starter action in the current UI. It inserts a
        # non-editable system-hint pill plus a canned sample prompt. A normal
        # locator.fill() replaces the entire ProseMirror document and removes
        # that pill, silently dropping explicit image mode. Select only the
        # editable content after the pill, then type the real prompt through
        # native keyboard events so the image-mode contract survives.
        has_image_mode_pill = False
        try:
            has_image_mode_pill = (
                await composer.locator(IMAGE_MODE_PILL_SELECTOR).count() > 0
            )
        except Exception:
            pass

        if has_image_mode_pill:
            image_mode_text = " ".join(
                line.strip() for line in text.splitlines() if line.strip()
            )
            prepared = await self.page.evaluate(
                """({selectors, pillSelector}) => {
                    let el = null;
                    for (const sel of selectors) {
                        const candidate = document.querySelector(sel);
                        if (candidate && candidate.offsetParent !== null) {
                            el = candidate;
                            break;
                        }
                    }
                    if (!el) return false;
                    const pill = el.querySelector(pillSelector);
                    if (!pill) return false;

                    // Put the native caret immediately after the non-editable
                    // pill. Keyboard selection below lets ProseMirror own the
                    // document update; direct DOM deletion desynchronizes its
                    // internal state and causes the pill/prompt to disappear.
                    const editableRange = document.createRange();
                    editableRange.setStartAfter(pill);
                    editableRange.collapse(true);
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(editableRange);
                    return true;
                }""",
                {
                    "selectors": COMPOSER_SELECTORS,
                    "pillSelector": IMAGE_MODE_PILL_SELECTOR,
                },
            )
            if not prepared:
                raise BrowserDriverError(
                    "ChatGPT Create image mode was selected, but its prompt pill "
                    "could not be preserved."
                )
            await composer.press("Shift+Meta+ArrowDown")
            await composer.type(" " + image_mode_text, delay=0)
            await human_pause(self.page, min_ms=300, max_ms=700)
            preserved = await self.page.evaluate(
                """({selectors, pillSelector, val}) => {
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (!el || el.offsetParent === null) continue;
                        return Boolean(el.querySelector(pillSelector))
                            && (el.innerText || el.value || '').includes(val);
                    }
                    return false;
                }""",
                {
                    "selectors": COMPOSER_SELECTORS,
                    "pillSelector": IMAGE_MODE_PILL_SELECTOR,
                    "val": image_mode_text,
                },
            )
            if not preserved:
                raise BrowserDriverError(
                    "ChatGPT prompt changed after preserving Create image mode."
                )
            return

        filled = False
        try:
            if composer is not None:
                await composer.fill(text)
                await human_pause(self.page, min_ms=300, max_ms=700)
                filled = await self.page.evaluate(
                    """({val, selectors}) => {
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (!el || el.offsetParent === null) continue;
                            return (el.innerText || el.value || '').trim() === val.trim();
                        }
                        return false;
                    }""",
                    {"val": text, "selectors": COMPOSER_SELECTORS},
                )
        except Exception:
            pass
        if filled:
            return

        # Ensure React state is fully sync'd by evaluating in-page JS.
        # This handles both contenteditable divs (standard) and textarea fallbacks.
        await self.page.evaluate(
            """({val, selectors}) => {
                let el = null;
                for (const sel of selectors) {
                    el = document.querySelector(sel);
                    if (el) break;
                }
                if (!el) {
                    // Fallback to active element if selectors fail
                    el = document.activeElement;
                }
                if (el) {
                    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                        el.value = val;
                    } else {
                        // For contenteditable, we must set textContent and trigger events
                        el.innerHTML = '';
                        const p = document.createElement('p');
                        p.textContent = val;
                        el.appendChild(p);
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }""",
            {"val": text, "selectors": COMPOSER_SELECTORS}
        )
        await human_pause(self.page, min_ms=400, max_ms=800)

    async def _click_first_visible(self, selectors: tuple[str, ...], *, timeout_ms: int = 1_500) -> bool:
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=timeout_ms):
                    await human_click(loc, hover_pause_min_ms=80, hover_pause_max_ms=200)
                    await human_pause(self.page, min_ms=350, max_ms=800)
                    return True
            except Exception:
                continue
        return False

    async def _click_text_exact(self, labels: tuple[str, ...], *, timeout_ms: int = 1_500) -> bool:
        for label in labels:
            try:
                loc = self.page.get_by_text(label, exact=True).first
                if await loc.is_visible(timeout=timeout_ms):
                    await human_click(loc, hover_pause_min_ms=80, hover_pause_max_ms=200)
                    await human_pause(self.page, min_ms=350, max_ms=800)
                    return True
            except Exception:
                continue
        return False

    async def _select_create_image_mode_and_aspect_ratio(self, aspect_ratio: str = "16:9") -> None:
        """Activate ChatGPT's image experience before replacing its starter prompt.

        The current UI exposes "Create an image" as a starter button, not a
        persistent mode toggle. Clicking it inserts ChatGPT's canned landscape
        prompt and activates the image composer. Do not pause to probe the old
        aspect-ratio controls: they no longer exist, and the delay leaves the
        canned prompt visible before we replace it with the real scene prompt.
        Orientation is enforced by :func:`build_image_gen_prompt`.
        """
        _ = aspect_ratio
        clicked_image_mode = await self._click_first_visible(
            CREATE_IMAGE_MODE_SELECTORS
        ) or await self._click_text_exact(("Create image", "Create an image"))
        if not clicked_image_mode:
            opened_tool_menu = await self._click_first_visible(IMAGE_TOOL_MENU_SELECTORS)
            if opened_tool_menu:
                clicked_image_mode = await self._click_first_visible(
                    CREATE_IMAGE_MODE_SELECTORS, timeout_ms=3_000
                ) or await self._click_text_exact(
                    ("Create image", "Create an image"), timeout_ms=3_000
                )

        if not clicked_image_mode:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-no-create-image-mode")
            raise BrowserDriverError(
                "ChatGPT image UI changed: 'Create image' control not found.",
                screenshot_path=shot,
            )

    async def _click_send(self) -> None:
        for sel in SEND_BUTTON_SELECTORS:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=3_000):
                    await human_click(btn)
                    return
            except Exception:
                continue
        shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-no-send")
        raise BrowserDriverError("ChatGPT send button not found.", screenshot_path=shot)

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
            # Poll fast so we return promptly once the image lands.
            await self.page.wait_for_timeout(600)
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
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = await ctx.request.get(src)
                if response.status != 200:
                    raise BrowserDriverError(
                        f"Image download failed: HTTP {response.status}"
                    )
                body = await response.body()
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(dest, body)
                return dest
            except Exception as exc:
                if attempt < max_attempts:
                    print(f"Image download attempt {attempt} failed: {exc}. Retrying in 2 seconds...")
                    await self.page.wait_for_timeout(2_000)
                else:
                    raise BrowserDriverError(f"Image download error after {max_attempts} attempts: {exc}") from exc

    async def generate_image(
        self,
        prompt: str,
        *,
        project_name: str,
        out_path: Path,
        response_timeout_ms: int = 240_000,
        aspect_ratio: str = "16:9",
    ) -> dict:
        """End-to-end: create normal chat, send prompt, save image to ``out_path``.

        Returns ``{src, local_path, project_name, bytes}``.
        """
        if not self._opened:
            await self.open()
        if not prompt.strip():
            raise BrowserDriverError("Empty image prompt")

        try:
            await self._ensure_image_session(project_name)
            if aspect_ratio != "16:9":
                await self._select_create_image_mode_and_aspect_ratio(aspect_ratio=aspect_ratio)
            else:
                await self._select_create_image_mode_and_aspect_ratio()
            composer = await self._focus_composer()
            full_prompt = build_image_gen_prompt(prompt, aspect_ratio=aspect_ratio)
            await self._fill_composer_robust(composer, full_prompt)
            await human_pause(self.page, min_ms=1500, max_ms=3500)
            await self._click_send()

            # We do NOT wait for the stop button to disappear. ChatGPT often writes
            # a massive text explanation after the image is drawn, and waiting for
            # the stop button to hide can add 60-120 seconds of unnecessary delay.
            # We just wait directly for the image to appear.
            src = await self._wait_for_image(response_timeout_ms)
            await self._download_image(src, out_path)
            return {
                "src": src,
                "local_path": str(out_path),
                "project_name": project_name,
                "bytes": out_path.stat().st_size,
            }
        finally:
            await self._teardown_image_session(project_name)

    async def generate_images(
        self,
        prompts: list[str],
        *,
        project_name: str,
        out_paths: list[Path],
        response_timeout_ms: int = 240_000,
        aspect_ratio: str = "16:9",
    ) -> list[dict]:
        """Generate multiple photorealistic images sequentially in one normal chat session."""
        if not self._opened:
            await self.open()
        if not prompts:
            raise BrowserDriverError("Empty prompts list")
        if len(prompts) != len(out_paths):
            raise BrowserDriverError("Prompts and out_paths length mismatch")

        try:
            await self._ensure_image_session(project_name)

            results = []
            exclude_urls = []
            for i, (prompt, out_path) in enumerate(zip(prompts, out_paths), start=1):
                if not prompt.strip():
                    raise BrowserDriverError(f"Empty prompt at index {i}")

                if aspect_ratio != "16:9":
                    await self._select_create_image_mode_and_aspect_ratio(aspect_ratio=aspect_ratio)
                else:
                    await self._select_create_image_mode_and_aspect_ratio()
                composer = await self._focus_composer()
                full_prompt = build_image_gen_prompt(prompt, aspect_ratio=aspect_ratio)
                await self._fill_composer_robust(composer, full_prompt)
                await human_pause(self.page, min_ms=1500, max_ms=3500)
                await self._click_send()

                # We do NOT wait for the stop button to disappear. ChatGPT often writes
                # a massive text explanation after the image is drawn, and waiting for
                # the stop button to hide can add 60-120 seconds of unnecessary delay.
                # We just wait directly for the image to appear.

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
        finally:
            await self._teardown_image_session(project_name)

    async def delete_current_chat(self) -> None:
        """Delete the active (just-used) normal chat to prevent clutter.

        Verified against the 2026 ChatGPT sidebar: the per-conversation options
        button and its menu items are hover-gated, so Playwright's actionable
        ``.click()`` times out on them. We dispatch DOM ``.click()`` via
        ``page.evaluate`` instead — it fires the handler regardless of hover
        state. Selectors (confirmed live):
          * options button — ``button[aria-label^='Open conversation options']``
          * delete item   — ``[data-testid='delete-chat-menu-item']``
          * confirm button — ``[data-testid='delete-conversation-confirm-button']``
        Best-effort: never raises.
        """
        try:
            match = re.search(r"/c/([a-zA-Z0-9-]+)", self.page.url)
            if not match:
                print("Not on a conversation URL. Skipping chat deletion.")
                return
            chat_id = match.group(1)
            print(f"Attempting to delete fallback chat conversation: {chat_id}")

            # 1. Open the conversation's options menu (hover-gated -> DOM click).
            opened = await self.page.evaluate(
                """(chatId) => {
                    const a = document.querySelector(`a[href*='${chatId}']`);
                    const row = a ? (a.closest('li') || a.parentElement) : null;
                    let btn = row ? row.querySelector(
                        "button[aria-label^='Open conversation options'], button[data-testid$='-options']"
                    ) : null;
                    if (!btn) {
                        btn = document.querySelector("button[aria-label^='Open conversation options']");
                    }
                    if (!btn) return 'no-options-button';
                    btn.click();
                    return 'opened';
                }""",
                chat_id,
            )
            if opened != "opened":
                print(f"Could not open options for chat {chat_id}: {opened}")
                return
            await human_pause(self.page, min_ms=300, max_ms=650)

            # 2. Click the Delete menu item.
            clicked = await self.page.evaluate(
                """() => {
                    let d = document.querySelector("[data-testid='delete-chat-menu-item']");
                    if (!d) {
                        d = [...document.querySelectorAll("[role='menuitem']")].find(
                            m => /^(delete|eliminar|xo)/i.test((m.innerText || '').trim()));
                    }
                    if (!d) return 'no-delete-item';
                    d.click();
                    return 'clicked';
                }"""
            )
            if clicked != "clicked":
                print(f"Delete menu item not found: {clicked}")
                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass
                return
            await human_pause(self.page, min_ms=300, max_ms=650)

            # 3. Confirm deletion in the dialog.
            confirmed = await self.page.evaluate(
                """() => {
                    let b = document.querySelector("[data-testid='delete-conversation-confirm-button']");
                    if (!b) {
                        b = [...document.querySelectorAll(
                            "div[role='dialog'] button, div[role='alertdialog'] button")].find(
                            x => /^(delete|eliminar|xo)/i.test((x.innerText || '').trim()));
                    }
                    if (!b) return 'no-confirm';
                    b.click();
                    return 'confirmed';
                }"""
            )
            await human_pause(self.page, min_ms=500, max_ms=900)
            if confirmed == "confirmed":
                print(f"Successfully deleted chat conversation: {chat_id}")
            else:
                print(f"Delete confirmation not found for chat {chat_id}: {confirmed}")
        except Exception as exc:
            print(f"Error while deleting chat conversation: {exc}")

    async def _teardown_image_session(self, project_name: str) -> None:
        """Clean up after an image session (best-effort, never raises).

        Deletes the normal chat used for image generation so it does not leave
        history clutter that bloats request headers and eventually triggers
        HTTP 431.
        """
        try:
            if self._used_project:
                await self.delete_project(project_name)
            else:
                await self.delete_current_chat()
        except Exception as exc:  # pragma: no cover - cleanup must never break gen
            print(f"[chatgpt-image] session teardown best-effort failed: {exc}", flush=True)

    async def delete_project(self, project_name: str) -> None:
        """Deletes the project with the specified name from ChatGPT to prevent clutter."""
        # Pause before deleting to look more human and prevent rate limit warnings from OpenAI
        await human_pause(self.page, min_ms=4000, max_ms=8000)

        if not self._used_project:
            # New-chat fallback path — no Project was created, delete the chat conversation instead.
            await self.delete_current_chat()
            return
        try:
            # Ensure the sidebar and projects section are visible/expanded
            await self._ensure_projects_expanded()
            
            # Locate all project options buttons in the sidebar
            buttons = await self.page.locator("button[aria-label^='Open project options for ']").all()
            target_btn = None
            
            for btn in buttons:
                label = await btn.get_attribute("aria-label") or ""
                label_name = label.replace("Open project options for ", "").strip()
                
                # Check for match (exact or truncated)
                if self._match_project_name(project_name, label_name):
                    target_btn = btn
                    break
            
            if not target_btn:
                print(f"Project options button not found for '{project_name}'. Skipping deletion.")
                return
                
            # Click the options button
            await human_click(target_btn)
            await human_pause(self.page, min_ms=150, max_ms=300)
            
            # Find and click the Delete project option (support English, Spanish, Vietnamese)
            opts = await self.page.locator("[role='menuitem'], [role='menu'] button, [role='menu'] div").all()
            delete_opt = None
            for opt in opts:
                text = (await opt.inner_text()).lower()
                if "delete" in text or "eliminar" in text or "xoá" in text or "xóa" in text:
                    delete_opt = opt
                    break
                    
            if not delete_opt:
                print("Delete option not found in project menu.")
                return
                
            await human_click(delete_opt)
            await human_pause(self.page, min_ms=200, max_ms=400)
            
            # Wait for and click the confirmation button
            confirm_buttons = await self.page.locator(
                "div[role='dialog'] button, button.btn-danger, button:has-text('Delete'), button:has-text('Eliminar'), button:has-text('Xoá'), button:has-text('Xóa')"
            ).all()
            confirm_btn = None
            for btn in confirm_buttons:
                text = (await btn.inner_text()).lower()
                if text in ["delete", "eliminar", "xoá", "xóa"] or "btn-danger" in (await btn.get_attribute("class") or ""):
                    confirm_btn = btn
                    break
                    
            if not confirm_btn:
                print("Delete confirmation button not found.")
                return
                
            await human_click(confirm_btn)
            # Wait for modal to disappear and UI to update
            await human_pause(self.page, min_ms=300, max_ms=600)
            print(f"Successfully deleted project: '{project_name}'")
        except Exception as exc:
            # Catch all errors during deletion to make it best-effort and prevent blocking
            print(f"Error while deleting project '{project_name}': {exc}")

    def _match_project_name(self, target_name: str, label_name: str) -> bool:
        if target_name == label_name:
            return True
        # Handle ChatGPT middle-truncation or smart truncation.
        # e.g., target_name: "thumb-nutrici-n-pr-ctica-despu-s-de--1779514911-v3"
        #       label_name:  "thumb-nutrici-n-pr-ctica-d-v3"
        # Minimum prefix length to match is 15.
        prefix_len = min(15, len(target_name))
        if not label_name.startswith(target_name[:prefix_len]):
            return False
            
        # If target has a version suffix (like -v1, -v2, -v3), verify it's kept in label_name
        version_match = re.search(r'-v\d+$', target_name)
        if version_match:
            suffix = version_match.group(0)
            if not label_name.endswith(suffix):
                return False
        return True
