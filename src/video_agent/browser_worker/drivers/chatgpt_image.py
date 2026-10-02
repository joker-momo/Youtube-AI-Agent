from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import re
import time
import unicodedata
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


def _normalized_semantic_text(text: str) -> str:
    """Case/accent-insensitive text for small multilingual UI-error checks."""
    decomposed = unicodedata.normalize("NFKD", text).casefold()
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return " ".join(without_marks.split())


def _requires_source_image(text: str) -> bool:
    """Whether an assistant response asks for an image to edit/select/upload.

    Match both a source-image concept and an edit/upload action so ordinary
    generation status text cannot trigger recovery. The current account may
    answer in Vietnamese, while prompt-language responses are usually Spanish;
    English remains the product UI fallback.
    """
    normalized = _normalized_semantic_text(text)
    language_contracts = (
        (
            ("hinh anh goc", "anh goc", "anh nguon"),
            ("tai len", "chon lai", "chinh sua", "khong co"),
            (),
        ),
        (
            ("source image", "original image"),
            ("upload", "select", "edit", "not available", "missing"),
            (),
        ),
        (
            ("imagen original", "imagen de origen", "imagen fuente"),
            (
                "sube",
                "subas",
                "subir",
                "carga",
                "cargar",
                "selecciona",
                "editar",
                "falta",
            ),
            (),
        ),
        (
            ("anh", "hinh anh"),
            ("tai len", "chon", "dinh kem"),
            ("chinh sua",),
        ),
        (
            ("image",),
            ("upload", "select", "attach"),
            ("edit",),
        ),
        (
            ("imagen",),
            (
                "sube",
                "subas",
                "subir",
                "carga",
                "cargar",
                "selecciona",
                "adjunta",
            ),
            ("editar",),
        ),
    )
    return any(
        any(source in normalized for source in source_terms)
        and any(action in normalized for action in action_terms)
        and (not edit_terms or any(edit in normalized for edit in edit_terms))
        for source_terms, action_terms, edit_terms in language_contracts
    )


class ImageSourceRequiredError(BrowserDriverError):
    """ChatGPT routed a generation-only turn into its image-edit workflow."""


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
        self._assistant_turn_floor = 0
        # Kept for back-compat with the old Project cleanup path. The current
        # image flow always uses normal chats and deletes the conversation.
        self._used_project = False

    _CLEANUP_MAX_ATTEMPTS = 3
    _CLEANUP_URL_STABILITY_POLLS = 4
    _CLEANUP_VERIFY_POLLS = 3
    _CLEANUP_POLL_MS = 350

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

    async def _user_turn_count(self) -> int:
        try:
            return await self.page.locator(
                "[data-message-author-role='user']"
            ).count()
        except Exception:
            return 0

    async def _assistant_turn_count(self) -> int:
        try:
            return await self.page.locator(
                "[data-message-author-role='assistant']"
            ).count()
        except Exception:
            return 0

    async def _submission_already_started(self, before_user_turns: int) -> bool:
        """Detect the new UI state where Create image submits before our click.

        A visible Stop button alone is insufficient: in a batch it can belong
        to the previous turn. Require a newly-added user message as proof that
        the current prompt already left the composer.
        """
        stop_visible = False
        for sel in STOP_BUTTON_SELECTORS:
            try:
                if await self.page.locator(sel).first.is_visible(timeout=500):
                    stop_visible = True
                    break
            except Exception:
                continue
        if not stop_visible:
            return False
        return await self._user_turn_count() > before_user_turns

    async def _click_send(self, *, before_user_turns: int) -> None:
        if await self._submission_already_started(before_user_turns):
            print(
                "[chatgpt-image] prompt already submitted; continuing from Stop state",
                flush=True,
            )
            return
        for sel in SEND_BUTTON_SELECTORS:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=3_000):
                    await human_click(btn)
                    return
            except Exception:
                if await self._submission_already_started(before_user_turns):
                    print(
                        "[chatgpt-image] send click transitioned to Stop state",
                        flush=True,
                    )
                    return
                continue
        if await self._submission_already_started(before_user_turns):
            print(
                "[chatgpt-image] prompt transitioned to Stop state while locating Send",
                flush=True,
            )
            return
        shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-no-send")
        raise BrowserDriverError("ChatGPT send button not found.", screenshot_path=shot)

    # ChatGPT's current image conversation renders its response as
    # ``<img alt="Generated image 1" src="blob:...">`` without the legacy
    # ``data-message-author-role`` wrappers. Walk open shadow roots and return
    # structured candidates so we can accept that real response while still
    # rejecting a user attachment from the same page.
    _FIND_RESPONSE_IMAGE_JS = """() => {
        function walk(root, out) {
            const all = root.querySelectorAll('*');
            for (const el of all) {
                if (el.tagName === 'IMG' && el.src) {
                    const rect = el.getBoundingClientRect();
                    const roleEl = el.closest('[data-message-author-role]');
                    out.push({
                        src: el.src,
                        alt: el.alt || '',
                        role: roleEl ? roleEl.getAttribute('data-message-author-role') : '',
                        width: rect.width,
                        height: rect.height,
                    });
                }
                if (el.shadowRoot) walk(el.shadowRoot, out);
            }
        }
        const out = [];
        walk(document, out);
        return out;
    }"""

    async def _find_response_image_src(self, exclude_urls: list[str]) -> str:
        """Return the latest safe generated-image source from the live ChatGPT DOM."""
        candidates = await self.page.evaluate(self._FIND_RESPONSE_IMAGE_JS)
        if not isinstance(candidates, list):
            return ""
        exclude = set(exclude_urls)
        for item in reversed(candidates):
            if not isinstance(item, dict):
                continue
            src = str(item.get("src") or "")
            if src in exclude or not src.startswith(("http", "blob:", "data:")):
                continue
            low_src = src.lower()
            if "avatar" in low_src or "favicon" in low_src or "/icon" in low_src:
                continue
            if float(item.get("width") or 0) < 200 or float(item.get("height") or 0) < 200:
                continue
            role = str(item.get("role") or "").lower()
            if role == "user":
                continue
            alt = str(item.get("alt") or "").lower()
            # The current response surface has no role wrapper, but marks the
            # result explicitly as "Generated image N". Older surfaces keep
            # the assistant role and may omit that alt text.
            if "generated image" in alt or role == "assistant":
                return src
            # Preserve the old HTTP-only fallback for legacy ChatGPT markup;
            # never promote an unlabelled blob/data URL because it can be an
            # uploaded reference image.
            if src.startswith("http"):
                return src
        return ""

    async def _wait_for_image(self, response_timeout_ms: int, exclude_urls: list[str] | None = None) -> str:
        """Poll the assistant turn until an <img> with a real src appears.

        Returns the image src URL.
        """
        deadline = time.monotonic() + response_timeout_ms / 1000.0
        last_logged = 0
        exclude_list = exclude_urls or []
        retried_failure = False
        while time.monotonic() < deadline:
            try:
                src = await self._find_response_image_src(exclude_list)
            except Exception as exc:
                # ChatGPT is a SPA: posting the prompt navigates / to /c/<id>,
                # and it re-renders mid-stream. A poll that races a navigation
                # throws "Execution context was destroyed". That is transient —
                # the image is still coming. Swallow it and re-poll; only the
                # deadline should end this loop.
                if "context was destroyed" in str(exc) or "navigation" in str(exc).lower():
                    await self.page.wait_for_timeout(600)
                    continue
                raise
            if src:
                return src

            # A current ChatGPT backend failure mode keeps the visible
            # picture_v2/Create image pill but routes the turn as image editing.
            # The assistant then asks for an original/source image and never
            # emits an <img>. Inspect only the newest ASSISTANT turn (never the
            # whole body, which also contains the user's prompt) and fail fast;
            # the endpoint will recreate the page/chat once, then use its
            # existing Gemini fallback if ChatGPT misroutes again.
            assistant_text = await self._latest_assistant_text()
            if _requires_source_image(assistant_text):
                shot = await save_trace_screenshot(
                    self.page, prefix="chatgpt-image-source-required"
                )
                raise ImageSourceRequiredError(
                    "ChatGPT image generation was misrouted and requires a "
                    "source image for editing.",
                    screenshot_path=shot,
                )

            # ChatGPT renders "Image generation failed" (with a "Try again"
            # button) as a NON-message error node, so it carries no assistant
            # <img> and no assistant text — the poll above would otherwise burn
            # the full response_timeout_ms (observed: 220s) on a failure the
            # backend already reported in ~5s. Detect it, auto-retry once, then
            # fail fast with an accurate error instead of a blind timeout.
            failure = await self._detect_generation_failure()
            if failure:
                if not retried_failure:
                    retried_failure = True
                    print(
                        "[chatgpt-image] ChatGPT reported 'Image generation "
                        "failed'; clicking Try again (1/1).",
                        flush=True,
                    )
                    await self._click_first_visible(
                        (
                            "button:has-text('Try again')",
                            "button:has-text('Retry')",
                            "button:has-text('Reintentar')",
                        ),
                        timeout_ms=2_000,
                    )
                    await self.page.wait_for_timeout(1_500)
                    continue
                shot = await save_trace_screenshot(
                    self.page, prefix="chatgpt-image-gen-failed"
                )
                raise BrowserDriverError(
                    "ChatGPT reported 'Image generation failed' (twice). The "
                    "account may be rate-limited or lack image-generation "
                    "quota (Free tier often fails here).",
                    screenshot_path=shot,
                )

            # Poll fast so we return promptly once the image lands.
            await self.page.wait_for_timeout(600)
            now = int(time.monotonic())
            if now - last_logged >= 10:
                last_logged = now
                remaining = int(deadline - time.monotonic())
                print(
                    f"[chatgpt-image] waiting for generated image... ~{remaining}s left",
                    flush=True,
                )
        shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-timeout")
        raise BrowserDriverError(
            "ChatGPT image generation timed out.",
            screenshot_path=shot,
        )

    async def _latest_assistant_text(self) -> str:
        """Return only the newest assistant turn, excluding user prompt text."""
        try:
            return await self.page.evaluate(
                """(assistantTurnFloor) => {
                    const assistantMessages = document.querySelectorAll(
                        "[data-message-author-role='assistant']"
                    );
                    if (assistantMessages.length <= assistantTurnFloor) return '';
                    const latest = assistantMessages[assistantMessages.length - 1];
                    return (latest?.innerText || latest?.textContent || '').trim();
                }""",
                self._assistant_turn_floor,
            )
        except Exception:
            # Navigation races are transient; the next image poll checks again.
            return ""

    async def _detect_generation_failure(self) -> bool:
        """True when ChatGPT shows an image-generation failure banner.

        The banner is language-dependent; match the English/Spanish strings the
        current UI uses. Best-effort: a page navigation mid-check returns False
        (the next poll re-checks)."""
        try:
            return await self.page.evaluate(
                """() => {
                    const t = (document.body.innerText || '').toLowerCase();
                    return t.includes('image generation failed')
                        || t.includes('error al generar la imagen')
                        || t.includes('no se pudo generar la imagen');
                }"""
            )
        except Exception:
            return False

    # Blob/data responses must be read in the page context. The browser request
    # client cannot resolve a blob URL, and screenshotting would persist only
    # the scaled preview rather than the generated source bytes.
    _READ_BLOB_JS = """async ([src, maxBytes]) => {
        const resp = await fetch(src);
        const contentType = resp.headers.get('content-type') || '';
        if (!resp.ok) return {ok: false, status: resp.status, contentType, size: 0};
        const declared = parseInt(resp.headers.get('content-length') || '', 10);
        if (Number.isFinite(declared) && declared > maxBytes) {
            return {ok: true, status: resp.status, contentType, size: declared, oversize: true};
        }
        const reader = resp.body && typeof resp.body.getReader === 'function'
            ? resp.body.getReader() : null;
        let bytes;
        if (reader) {
            const chunks = [];
            let total = 0;
            while (true) {
                const {done, value} = await reader.read();
                if (done) break;
                total += value.length;
                if (total > maxBytes) {
                    try { await reader.cancel(); } catch (_) {}
                    return {ok: true, status: resp.status, contentType, size: total, oversize: true};
                }
                chunks.push(value);
            }
            bytes = new Uint8Array(total);
            let offset = 0;
            for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
        } else {
            bytes = new Uint8Array(await resp.arrayBuffer());
            if (bytes.length > maxBytes) {
                return {ok: true, status: resp.status, contentType, size: bytes.length, oversize: true};
            }
        }
        let binary = '';
        for (let index = 0; index < bytes.length; index += 0x8000) {
            binary += String.fromCharCode.apply(null, bytes.subarray(index, index + 0x8000));
        }
        return {ok: true, status: resp.status, contentType, size: bytes.length, b64: btoa(binary)};
    }"""
    _MAX_IMAGE_BYTES = 40 * 1024 * 1024
    _MIN_IMAGE_BYTES = 100

    @staticmethod
    def _parse_data_uri(src: str) -> tuple[str, bytes]:
        if not src.startswith("data:") or "," not in src:
            raise BrowserDriverError("Malformed data URI.")
        header, _, payload = src[len("data:"):].partition(",")
        mime = header.split(";")[0].strip().lower()
        if not header.endswith(";base64"):
            raise BrowserDriverError("ChatGPT image data URI is not base64 encoded.")
        if (len(payload) * 3) // 4 > ChatGPTImageDriver._MAX_IMAGE_BYTES:
            raise BrowserDriverError("ChatGPT image data URI exceeds the size cap.")
        try:
            return mime, base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BrowserDriverError(f"Malformed base64 in data URI: {exc}") from exc

    @staticmethod
    def _validate_image_bytes(body: bytes, *, declared_mime: str | None = None) -> None:
        if len(body) < ChatGPTImageDriver._MIN_IMAGE_BYTES:
            raise BrowserDriverError(f"ChatGPT image too small to be valid ({len(body)} bytes).")
        if len(body) > ChatGPTImageDriver._MAX_IMAGE_BYTES:
            raise BrowserDriverError(f"ChatGPT image exceeds the size cap ({len(body)} bytes).")
        declared = (declared_mime or "").split(";", 1)[0].strip().lower()
        if declared and not declared.startswith("image/"):
            raise BrowserDriverError(f"ChatGPT response declared a non-image type: {declared!r}.")
        try:
            from PIL import Image

            Image.open(io.BytesIO(body)).verify()
        except Exception as exc:
            raise BrowserDriverError(f"ChatGPT image failed to decode: {exc}") from exc

    async def _download_image(self, src: str, dest: Path) -> Path:
        """Persist valid source image bytes from http, blob, or data URLs."""
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                declared_mime: str | None = None
                if src.startswith("data:"):
                    declared_mime, body = self._parse_data_uri(src)
                elif src.startswith("blob:"):
                    meta = await asyncio.wait_for(
                        self.page.evaluate(self._READ_BLOB_JS, [src, self._MAX_IMAGE_BYTES]),
                        timeout=30.0,
                    )
                    if not isinstance(meta, dict) or not meta.get("ok"):
                        raise BrowserDriverError("ChatGPT blob fetch did not return a usable response.")
                    if meta.get("oversize") or int(meta.get("size") or 0) > self._MAX_IMAGE_BYTES:
                        raise BrowserDriverError("ChatGPT blob exceeds the size cap.")
                    declared_mime = str(meta.get("contentType") or "") or None
                    try:
                        body = base64.b64decode(str(meta.get("b64") or ""), validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise BrowserDriverError(f"Malformed base64 from ChatGPT blob: {exc}") from exc
                else:
                    response = await self.page.context.request.get(src)
                    if response.status != 200:
                        raise BrowserDriverError(f"Image download failed: HTTP {response.status}")
                    declared_mime = response.headers.get("content-type")
                    content_length = response.headers.get("content-length")
                    try:
                        declared_size = int(content_length) if content_length else None
                    except ValueError:
                        declared_size = None
                    if declared_size is not None and declared_size > self._MAX_IMAGE_BYTES:
                        raise BrowserDriverError(
                            "ChatGPT HTTP image exceeds the size cap before download."
                        )
                    body = await response.body()
                self._validate_image_bytes(body, declared_mime=declared_mime)
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(dest, body)
                return dest
            except Exception as exc:
                if attempt < max_attempts:
                    print(f"Image download attempt {attempt} failed: {exc}. Retrying in 2 seconds...")
                    await self.page.wait_for_timeout(2_000)
                else:
                    raise BrowserDriverError(f"Image download error after {max_attempts} attempts: {exc}") from exc

    # Composer indicators that a file attachment actually registered. Counted
    # before/after upload (not matched absolutely) so a chat that already holds
    # a previous attachment never false-positives.
    _ATTACHMENT_PREVIEW_SELECTORS = (
        "button[aria-label*='Remove' i]",
        "[data-testid*='attachment']",
        "img[src^='blob:']",
    )

    async def _attachment_preview_count(self) -> int:
        total = 0
        for sel in self._ATTACHMENT_PREVIEW_SELECTORS:
            try:
                total += await self.page.locator(sel).count()
            except Exception:
                pass
        return total

    async def _attach_reference_image(self, attachment_path: Path) -> None:
        """Attach a local image to the composer and VERIFY it registered.

        ChatGPT's composer keeps a hidden ``input[type=file]``; Playwright's
        ``set_input_files`` feeds it directly, so no file-chooser dialog opens.
        A silent miss (wrong/stale input, dropped upload) is dangerous: the
        prompt still claims a photo is ATTACHED, so ChatGPT replies "please
        upload the reference photo" and the persona lock is silently lost. So we
        wait for the upload preview chip to actually appear and raise loudly if
        it does not — a visible failure beats a wrong-identity image.
        """
        import asyncio

        attachment_path = Path(attachment_path)
        if not attachment_path.is_file():
            raise BrowserDriverError(f"Attachment not found: {attachment_path}")
        # ChatGPT mounts/activates the real composer file input only after the
        # "+" (Add photos & files) menu is opened; setting files on the stale
        # hidden input silently drops the upload, so ChatGPT replies "please
        # upload the reference photo" and the persona is lost. Open the menu
        # first (best-effort), then set files on a now-active input.
        await self._click_first_visible(IMAGE_TOOL_MENU_SELECTORS, timeout_ms=2500)
        await human_pause(self.page, min_ms=600, max_ms=1200)
        file_inputs = self.page.locator("input[type='file']")
        if await file_inputs.count() == 0:
            shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-no-file-input")
            raise BrowserDriverError(
                f"ChatGPT composer has no file input for attachments (trace: {shot})"
            )
        before = await self._attachment_preview_count()
        n_inputs = await file_inputs.count()
        # Feed file inputs ONE AT A TIME (ChatGPT may render several; only the
        # active composer input accepts the image, the rest ignore it). Setting
        # files on EVERY input up-front risks the active one taking the image
        # twice -> a doubled reference chip. So set on one input, then poll for
        # the preview to register before trying the next input.
        for idx in range(n_inputs):
            try:
                await file_inputs.nth(idx).set_input_files(str(attachment_path))
            except Exception:
                continue
            # Poll ~5s for THIS input's upload to register before trying another.
            for _ in range(10):
                await asyncio.sleep(0.5)
                if await self._attachment_preview_count() > before:
                    # The preview chip appears as soon as the upload STARTS; the
                    # image is not usable (send stays disabled) until it finishes
                    # processing, so keep the original generous settle wait.
                    await human_pause(self.page, min_ms=3500, max_ms=5000)
                    return
        # Last resort: a slow upload may still register after all inputs cycled.
        for _ in range(30):
            await asyncio.sleep(0.5)
            if await self._attachment_preview_count() > before:
                await human_pause(self.page, min_ms=3500, max_ms=5000)
                return
        shot = await save_trace_screenshot(self.page, prefix="chatgpt-image-attach-not-registered")
        raise BrowserDriverError(
            "Reference image upload did not register in the composer "
            f"(persona lock would be silently lost) (trace: {shot})"
        )

    async def generate_image(
        self,
        prompt: str,
        *,
        project_name: str,
        out_path: Path,
        response_timeout_ms: int = 240_000,
        aspect_ratio: str = "16:9",
        attachment_path: Path | None = None,
    ) -> dict:
        """End-to-end: create normal chat, send prompt, save image to ``out_path``.

        Returns ``{src, local_path, project_name, bytes}``.
        """
        if not self._opened:
            await self.open()
        if not prompt.strip():
            raise BrowserDriverError("Empty image prompt")

        result: dict | None = None
        try:
            await self._ensure_image_session(project_name)
            before_user_turns = await self._user_turn_count()
            self._assistant_turn_floor = await self._assistant_turn_count()
            if aspect_ratio != "16:9":
                await self._select_create_image_mode_and_aspect_ratio(aspect_ratio=aspect_ratio)
            else:
                await self._select_create_image_mode_and_aspect_ratio()
            composer = await self._focus_composer()
            if attachment_path is not None:
                await self._attach_reference_image(attachment_path)
            full_prompt = build_image_gen_prompt(prompt, aspect_ratio=aspect_ratio)
            await self._fill_composer_robust(composer, full_prompt)
            await human_pause(self.page, min_ms=1500, max_ms=3500)
            await self._click_send(before_user_turns=before_user_turns)

            # We do NOT wait for the stop button to disappear. ChatGPT often writes
            # a massive text explanation after the image is drawn, and waiting for
            # the stop button to hide can add 60-120 seconds of unnecessary delay.
            # We just wait directly for the image to appear.
            src = await self._wait_for_image(response_timeout_ms)
            await self._download_image(src, out_path)
            result = {
                "src": src,
                "local_path": str(out_path),
                "project_name": project_name,
                "bytes": out_path.stat().st_size,
            }
        finally:
            cleanup = await self._teardown_image_session(project_name)

        if result is None:  # pragma: no cover - exceptions leave the try directly
            raise BrowserDriverError("Image generation did not produce a result.")
        result["cleanup"] = cleanup
        return result

    async def generate_images(
        self,
        prompts: list[str],
        *,
        project_name: str,
        out_paths: list[Path],
        response_timeout_ms: int = 240_000,
        aspect_ratio: str = "16:9",
        attachment_path: Path | None = None,
    ) -> list[dict]:
        """Generate multiple photorealistic images sequentially in one normal chat session.

        ``attachment_path`` (persona/identity reference) is re-attached before
        EVERY prompt — each generation must carry the reference image."""
        if not self._opened:
            await self.open()
        if not prompts:
            raise BrowserDriverError("Empty prompts list")
        if len(prompts) != len(out_paths):
            raise BrowserDriverError("Prompts and out_paths length mismatch")

        results: list[dict] | None = None
        try:
            await self._ensure_image_session(project_name)

            results = []
            exclude_urls = []
            for i, (prompt, out_path) in enumerate(zip(prompts, out_paths), start=1):
                if not prompt.strip():
                    raise BrowserDriverError(f"Empty prompt at index {i}")

                before_user_turns = await self._user_turn_count()
                self._assistant_turn_floor = await self._assistant_turn_count()
                if aspect_ratio != "16:9":
                    await self._select_create_image_mode_and_aspect_ratio(aspect_ratio=aspect_ratio)
                else:
                    await self._select_create_image_mode_and_aspect_ratio()
                composer = await self._focus_composer()
                if attachment_path is not None:
                    await self._attach_reference_image(attachment_path)
                full_prompt = build_image_gen_prompt(prompt, aspect_ratio=aspect_ratio)
                await self._fill_composer_robust(composer, full_prompt)
                await human_pause(self.page, min_ms=1500, max_ms=3500)
                await self._click_send(before_user_turns=before_user_turns)

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

        finally:
            cleanup = await self._teardown_image_session(project_name)

        if results is None:  # pragma: no cover - exceptions leave the try directly
            raise BrowserDriverError("Image batch generation did not produce results.")
        for result in results:
            result["cleanup"] = dict(cleanup)
        return results

    async def _stable_conversation_id(self) -> str | None:
        """Wait until the SPA has settled on the chat URL created by this request."""
        previous: str | None = None
        for _ in range(self._CLEANUP_URL_STABILITY_POLLS):
            match = re.search(r"/c/([a-zA-Z0-9-]+)", str(getattr(self.page, "url", "")))
            conversation_id = match.group(1) if match else None
            if conversation_id and conversation_id == previous:
                return conversation_id
            previous = conversation_id
            await self.page.wait_for_timeout(self._CLEANUP_POLL_MS)
        return None

    async def _delete_chat_once(self, conversation_id: str) -> dict:
        """Attempt the sidebar deletion once, never targeting a global row."""
        link = self.page.locator(f"a[href*='/c/{conversation_id}']").first
        row = link.locator("xpath=ancestor::*[@role='group' or self::li][1]").first
        button = row.locator(
            "button[aria-label^='Open conversation options'], "
            "button[aria-label='Chat actions'], button[data-testid$='-options']"
        ).first
        try:
            if not await button.is_visible(timeout=2_000):
                return {"status": "retry", "reason": "conversation-row-or-options-missing"}
            # ChatGPT ignores JavaScript element.click() for this menu; this
            # Playwright click produces the trusted pointer interaction it requires.
            await button.click(timeout=3_000)
        except Exception as exc:
            return {
                "status": "retry",
                "reason": f"chat-actions-click-failed:{type(exc).__name__}",
            }
        clicked = "delete-menu-item-missing"
        for _ in range(self._CLEANUP_URL_STABILITY_POLLS):
            item = self.page.get_by_role(
                "menuitem", name=re.compile(r"^(delete|eliminar|xo)", re.IGNORECASE)
            ).first
            try:
                if await item.is_visible(timeout=self._CLEANUP_POLL_MS):
                    await item.click(timeout=3_000)
                    clicked = "clicked"
                    break
            except Exception:
                pass
            await self.page.wait_for_timeout(self._CLEANUP_POLL_MS)
        if clicked != "clicked":
            return {"status": "retry", "reason": str(clicked)}

        confirmed = "delete-confirmation-missing"
        for _ in range(self._CLEANUP_URL_STABILITY_POLLS):
            dialog = self.page.locator("div[role='dialog'], div[role='alertdialog']").last
            button = dialog.get_by_role(
                "button", name=re.compile(r"^(delete|eliminar|xo)", re.IGNORECASE)
            ).first
            try:
                if await button.is_visible(timeout=self._CLEANUP_POLL_MS):
                    await button.click(timeout=3_000)
                    confirmed = "confirmed"
                    break
            except Exception:
                pass
            await self.page.wait_for_timeout(self._CLEANUP_POLL_MS)
        return {
            "status": "confirmed" if confirmed == "confirmed" else "retry",
            "reason": None if confirmed == "confirmed" else str(confirmed),
        }

    async def _conversation_is_present(self, conversation_id: str) -> bool:
        """Return whether the conversation remains represented in the sidebar."""
        return bool(
            await self.page.evaluate(
                """(conversationId) => [...document.querySelectorAll('a[href]')].some(
                    candidate => candidate.getAttribute('href').includes(`/c/${conversationId}`)
                )""",
                conversation_id,
            )
        )

    async def _conversation_is_deleted_server_side(self, conversation_id: str) -> bool:
        """Confirm deletion through ChatGPT's authenticated conversation endpoint."""
        try:
            result = await self.page.evaluate(
                """async (conversationId) => {
                    const response = await fetch(
                        `/backend-api/conversation/${encodeURIComponent(conversationId)}`,
                        {credentials: 'include'}
                    );
                    return {status: response.status};
                }""",
                conversation_id,
            )
        except Exception:
            return False
        return isinstance(result, dict) and result.get("status") == 404

    async def _remove_conversation_from_sidebar(self, conversation_id: str) -> bool:
        """Remove only a server-deleted conversation's stale sidebar row."""
        return bool(
            await self.page.evaluate(
                """(conversationId) => {
                    const link = [...document.querySelectorAll('a[href]')].find(
                        candidate => candidate.getAttribute('href').includes(`/c/${conversationId}`)
                    );
                    const row = link && link.closest("[role='group'], li");
                    if (!row) return false;
                    row.remove();
                    return true;
                }""",
                conversation_id,
            )
        )

    @staticmethod
    def _log_cleanup_status(status: dict) -> None:
        print(
            "[chatgpt-image] cleanup_status="
            + json.dumps(status, ensure_ascii=False, sort_keys=True),
            flush=True,
        )

    async def delete_current_chat(self) -> dict:
        """Delete the active normal chat and verify that it leaves the sidebar.

        A click is only an attempt. A ``deleted`` result is emitted solely after
        the matching sidebar entry disappears; any other outcome is returned to
        the caller and logged, never silently treated as successful cleanup.
        """
        conversation_id = await self._stable_conversation_id()
        if not conversation_id:
            status = {
                "kind": "conversation",
                "status": "failed",
                "conversation_id": None,
                "attempts": 0,
                "verified": False,
                "reason": "conversation_id_unavailable",
            }
            self._log_cleanup_status(status)
            return status

        last_reason = "sidebar_entry_still_present"
        initial_sidebar_refreshed = False
        server_verified = False
        final_sidebar_refreshed = False
        sidebar_stale_pruned = False
        for attempt in range(1, self._CLEANUP_MAX_ATTEMPTS + 1):
            try:
                action = await self._delete_chat_once(conversation_id)
                if action.get("status") != "confirmed":
                    last_reason = str(action.get("reason") or "delete_action_unconfirmed")
                else:
                    if not initial_sidebar_refreshed:
                        try:
                            # ChatGPT removes the row optimistically, then may
                            # re-hydrate stale sidebar data. Reload once so a
                            # ``deleted`` result reflects server-backed state.
                            await self.page.wait_for_timeout(self._CLEANUP_POLL_MS * 3)
                            await self.page.reload(
                                wait_until="domcontentloaded", timeout=30_000
                            )
                            initial_sidebar_refreshed = True
                        except Exception as exc:
                            last_reason = f"sidebar_refresh_failed:{type(exc).__name__}"
                            continue
                    if not server_verified:
                        for _ in range(self._CLEANUP_VERIFY_POLLS):
                            if await self._conversation_is_deleted_server_side(conversation_id):
                                server_verified = True
                                break
                            await self.page.wait_for_timeout(self._CLEANUP_POLL_MS)
                        if not server_verified:
                            last_reason = "conversation_api_still_accessible"
                            continue
                    if not final_sidebar_refreshed:
                        try:
                            # A second reload happens after the API confirms
                            # deletion, bypassing the first page's stale list.
                            await self.page.reload(
                                wait_until="domcontentloaded", timeout=30_000
                            )
                            final_sidebar_refreshed = True
                        except Exception as exc:
                            last_reason = f"final_sidebar_refresh_failed:{type(exc).__name__}"
                            continue
                    # The authenticated endpoint above is authoritative. If
                    # the current React tree still has its old row, remove
                    # only that stale representation before UI verification.
                    if not sidebar_stale_pruned:
                        await self._remove_conversation_from_sidebar(conversation_id)
                        sidebar_stale_pruned = True
                    absent_polls = 0
                    for _ in range(self._CLEANUP_VERIFY_POLLS):
                        if not await self._conversation_is_present(conversation_id):
                            absent_polls += 1
                            if absent_polls == self._CLEANUP_VERIFY_POLLS:
                                status = {
                                    "kind": "conversation",
                                    "status": "deleted",
                                    "conversation_id": conversation_id,
                                    "attempts": attempt,
                                    "verified": True,
                                }
                                self._log_cleanup_status(status)
                                return status
                        else:
                            absent_polls = 0
                        await self.page.wait_for_timeout(self._CLEANUP_POLL_MS)
                    last_reason = "sidebar_entry_still_present"
            except Exception as exc:
                last_reason = f"cleanup_exception:{type(exc).__name__}"
            await self.page.wait_for_timeout(self._CLEANUP_POLL_MS)

        status = {
            "kind": "conversation",
            "status": "failed",
            "conversation_id": conversation_id,
            "attempts": self._CLEANUP_MAX_ATTEMPTS,
            "verified": False,
            "reason": last_reason,
        }
        self._log_cleanup_status(status)
        return status

    async def _teardown_image_session(self, project_name: str) -> dict:
        """Return an observable cleanup outcome without masking image generation."""
        try:
            if self._used_project:
                await self.delete_project(project_name)
                status = {
                    "kind": "project",
                    "status": "not_verified",
                    "project_name": project_name,
                    "verified": False,
                    "reason": "legacy_project_cleanup_not_verified",
                }
                self._log_cleanup_status(status)
                return status
            return await self.delete_current_chat()
        except Exception as exc:  # defensive boundary: generation errors stay primary
            status = {
                "kind": "conversation",
                "status": "failed",
                "conversation_id": None,
                "attempts": 0,
                "verified": False,
                "reason": f"teardown_exception:{type(exc).__name__}",
            }
            self._log_cleanup_status(status)
            return status

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
