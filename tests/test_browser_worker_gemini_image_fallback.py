"""bug-511: when ChatGPT image gen fails twice, fall back to Gemini.

ChatGPT's Free-tier account intermittently cannot generate images at all
("Image generation failed" reported twice, or a 502 from the ChatGPT
backend) — this blocked the whole Short even after the pipeline's scene/QA
logic was fixed (bug-503..510). /chatgpt/image[/batch] now falls back to
GeminiImageDriver after the existing clear-data-and-retry pass fails, so a
single provider outage no longer blocks rendering.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_agent.browser_worker import app
from video_agent.browser_worker.drivers.base import BrowserDriverError
from video_agent.browser_worker.drivers.chatgpt_image import ImageSourceRequiredError


class _FakePage:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True

    async def wait_for_timeout(self, _ms):
        pass


class _FakeContext:
    def __init__(self, page: _FakePage):
        self._page = page
        self.pages_created = 0

    async def new_page(self):
        self.pages_created += 1
        return self._page


class _OkGeminiDriver:
    last_instance = None

    def __init__(self, page):
        self.page = page
        self.calls: list[dict] = []
        _OkGeminiDriver.last_instance = self

    async def generate_image(self, prompt, *, project_name, out_path, response_timeout_ms, aspect_ratio):
        self.calls.append(
            {
                "prompt": prompt,
                "project_name": project_name,
                "out_path": out_path,
                "response_timeout_ms": response_timeout_ms,
                "aspect_ratio": aspect_ratio,
            }
        )
        return {
            "src": "https://example.com/img.png",
            "local_path": str(out_path),
            "project_name": project_name,
            "bytes": 123,
            "provider": "gemini",
        }


class _FailingGeminiDriver:
    def __init__(self, page):
        self.page = page

    async def generate_image(self, *args, **kwargs):
        raise BrowserDriverError("Gemini also down")


@pytest.mark.parametrize(
    ("second_chatgpt_attempt_succeeds", "expected_provider"),
    [(True, "chatgpt"), (False, "gemini")],
)
def test_source_image_misroute_retries_fresh_then_falls_back(
    monkeypatch, tmp_path, second_chatgpt_attempt_succeeds, expected_provider
):
    class FakeContext:
        def __init__(self):
            self.pages = []

        async def new_page(self):
            page = _FakePage()
            self.pages.append(page)
            return page

    class FakeBrowser:
        def __init__(self, context):
            self.contexts = [context]
            self.closed = False

        async def close(self):
            self.closed = True

    class FakePlaywrightContextManager:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class RetryThenSucceedDriver:
        calls = []

        def __init__(self, page):
            self.page = page

        async def generate_image(self, *args, **kwargs):
            self.__class__.calls.append(self.page)
            if (
                len(self.__class__.calls) == 1
                or not second_chatgpt_attempt_succeeds
            ):
                raise ImageSourceRequiredError(
                    "ChatGPT image generation was misrouted and requires a "
                    "source image for editing."
                )
            return {
                "src": "https://example.com/retried.png",
                "local_path": str(kwargs["out_path"]),
                "project_name": kwargs["project_name"],
                "bytes": 123,
                "provider": "chatgpt",
            }

    context = FakeContext()
    browser = FakeBrowser(context)
    clear_calls = []
    gemini_calls = []

    async def fake_attach(_pw, _url):
        return browser

    async def fake_clear(ctx):
        clear_calls.append(ctx)

    async def fake_pause(*args, **kwargs):
        return None

    async def fake_gemini_fallback(*args, **kwargs):
        gemini_calls.append(kwargs)
        return {
            "src": "https://example.com/fallback.png",
            "local_path": str(kwargs["out_path"]),
            "project_name": kwargs["project_name"],
            "bytes": 123,
            "provider": "gemini",
        }

    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: FakePlaywrightContextManager(),
    )
    monkeypatch.setattr(app, "_attach_cdp_or_503", fake_attach)
    monkeypatch.setattr(app, "clear_browser_data_keep_login", fake_clear)
    monkeypatch.setattr(app, "human_pause", fake_pause)
    monkeypatch.setattr(app, "_generate_image_via_gemini", fake_gemini_fallback)
    monkeypatch.setattr(app, "_safe_asset_path", lambda _raw: tmp_path / "out.png")
    monkeypatch.setattr(
        "video_agent.browser_worker.drivers.ChatGPTImageDriver",
        RetryThenSucceedDriver,
    )

    result = asyncio.run(
        app._chatgpt_image_impl(
            app.ImagePromptRequest(
                prompt="create a poster",
                project_name="proj",
                out_path="out.png",
                aspect_ratio="9:16",
            )
        )
    )

    assert result["provider"] == expected_provider
    assert len(context.pages) == 2
    assert RetryThenSucceedDriver.calls == context.pages
    assert context.pages[0].closed is True
    assert clear_calls == [context]
    assert len(gemini_calls) == (0 if second_chatgpt_attempt_succeeds else 1)
    assert browser.closed is True


def test_generate_image_via_gemini_calls_driver_and_closes_page(monkeypatch):
    page = _FakePage()
    context = _FakeContext(page)
    monkeypatch.setattr(
        "video_agent.browser_worker.drivers.GeminiImageDriver", _OkGeminiDriver
    )

    result = asyncio.run(
        app._generate_image_via_gemini(
            context,
            prompt="a cat",
            project_name="proj",
            out_path=Path("/tmp/out.png"),
            response_timeout_ms=1000,
            aspect_ratio="9:16",
        )
    )

    assert result["provider"] == "gemini"
    assert context.pages_created == 1
    assert page.closed is True
    call = _OkGeminiDriver.last_instance.calls[0]
    assert call["prompt"] == "a cat"
    assert call["aspect_ratio"] == "9:16"


def test_generate_image_via_gemini_closes_page_even_on_error(monkeypatch):
    page = _FakePage()
    context = _FakeContext(page)
    monkeypatch.setattr(
        "video_agent.browser_worker.drivers.GeminiImageDriver", _FailingGeminiDriver
    )

    with pytest.raises(BrowserDriverError):
        asyncio.run(
            app._generate_image_via_gemini(
                context,
                prompt="a cat",
                project_name="proj",
                out_path=Path("/tmp/out.png"),
                response_timeout_ms=1000,
                aspect_ratio="16:9",
            )
        )
    assert page.closed is True  # cleanup ran despite the failure


def test_generate_images_via_gemini_generates_all_in_order(monkeypatch):
    page = _FakePage()
    context = _FakeContext(page)
    monkeypatch.setattr(
        "video_agent.browser_worker.drivers.GeminiImageDriver", _OkGeminiDriver
    )

    prompts = ["one", "two", "three"]
    out_paths = [Path(f"/tmp/{i}.png") for i in range(3)]
    results = asyncio.run(
        app._generate_images_via_gemini(
            context,
            prompts=prompts,
            project_name="proj",
            out_paths=out_paths,
            response_timeout_ms=1000,
            aspect_ratio="9:16",
        )
    )

    assert len(results) == 3
    assert context.pages_created == 1  # ONE page for the whole batch
    called_prompts = [c["prompt"] for c in _OkGeminiDriver.last_instance.calls]
    assert called_prompts == prompts  # order preserved


def test_gemini_fallback_error_detail_includes_both_errors():
    gemini_exc = BrowserDriverError("gemini failed", screenshot_path="shot.png")
    chatgpt_exc = RuntimeError("chatgpt failed twice")

    detail = app._gemini_fallback_error_detail(gemini_exc, chatgpt_exc)

    assert "gemini failed" in detail["error"]
    assert detail["chatgpt_error"] == "chatgpt failed twice"
    assert detail["gemini_fallback_attempted"] is True


class _FakePageWithEvalResults:
    """Mimics page.evaluate(...) returning the recursive walker's candidate list."""

    def __init__(self, candidates: list[dict]):
        self._candidates = candidates

    async def evaluate(self, _js: str):
        return self._candidates


def test_find_response_image_src_skips_small_avatar_picks_big_response():
    """bug-511 follow-up #2: TWO live trace screenshots confirmed Gemini DID
    render the image, but neither plain document.querySelectorAll NOR
    Playwright's shadow-piercing page.locator("img") found it — the response
    is rendered as a CSS background-image (not a plain <img>), possibly also
    behind a shadow root. The recursive JS walker must catch both shapes and
    still filter out small sidebar/nav chrome."""
    from video_agent.browser_worker.drivers.gemini_image import GeminiImageDriver

    candidates = [
        {"src": "https://example.com/avatar.png", "w": 32, "h": 32},  # sidebar avatar
        {"src": "https://example.com/icon-share.svg", "w": 20, "h": 20},  # nav icon
        {"src": "https://lh3.googleusercontent.com/response.png", "w": 900, "h": 1600},  # real image
    ]
    driver = GeminiImageDriver(_FakePageWithEvalResults(candidates))

    src = asyncio.run(driver._find_response_image_src())

    assert src == "https://lh3.googleusercontent.com/response.png"


def test_find_response_image_src_returns_empty_when_only_small_images():
    from video_agent.browser_worker.drivers.gemini_image import GeminiImageDriver

    candidates = [{"src": "https://example.com/avatar.png", "w": 32, "h": 32}]
    driver = GeminiImageDriver(_FakePageWithEvalResults(candidates))

    src = asyncio.run(driver._find_response_image_src())

    assert src == ""


def test_find_response_image_src_skips_favicon_and_data_uris():
    from video_agent.browser_worker.drivers.gemini_image import GeminiImageDriver

    candidates = [
        {"src": "data:image/png;base64,abc", "w": 900, "h": 900},  # not http
        {"src": "https://example.com/favicon.ico", "w": 900, "h": 900},  # excluded by name
        {"src": "https://lh3.googleusercontent.com/real.png", "w": 900, "h": 900},
    ]
    driver = GeminiImageDriver(_FakePageWithEvalResults(candidates))

    src = asyncio.run(driver._find_response_image_src())

    assert src == "https://lh3.googleusercontent.com/real.png"


def test_find_response_image_src_prefers_last_match():
    """When multiple large candidates exist (e.g. a prior turn's image still
    in the DOM), prefer the LAST one — the newest response."""
    from video_agent.browser_worker.drivers.gemini_image import GeminiImageDriver

    candidates = [
        {"src": "https://lh3.googleusercontent.com/old.png", "w": 900, "h": 900},
        {"src": "https://lh3.googleusercontent.com/new.png", "w": 900, "h": 900},
    ]
    driver = GeminiImageDriver(_FakePageWithEvalResults(candidates))

    src = asyncio.run(driver._find_response_image_src())

    assert src == "https://lh3.googleusercontent.com/new.png"
