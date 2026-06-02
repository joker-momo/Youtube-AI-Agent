from __future__ import annotations

import asyncio

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    QuotaExceededError,
    normalise_response_text,
)
from video_agent.browser_worker.drivers.chatgpt import (
    CHATGPT_URL,
    _is_login_url as chatgpt_is_login,
)
from video_agent.browser_worker.drivers.claude import _is_login_url as claude_is_login
from video_agent.browser_worker.drivers.gemini import _is_login_url as gemini_is_login


def test_login_required_error_is_browser_driver_error():
    err = LoginRequiredError("login", screenshot_path="/tmp/x.png")
    assert isinstance(err, BrowserDriverError)
    assert err.screenshot_path == "/tmp/x.png"


def test_browser_driver_error_carries_layout_diagnostics():
    err = BrowserDriverError(
        "Claude layout may have changed.",
        screenshot_path="/tmp/trace.png",
        diagnostic_path="/tmp/trace.json",
        layout_warning=True,
    )

    assert err.screenshot_path == "/tmp/trace.png"
    assert err.diagnostic_path == "/tmp/trace.json"
    assert err.layout_warning is True


def test_browser_worker_error_detail_includes_layout_warning_metadata():
    from video_agent.browser_worker.app import _driver_error_detail

    err = BrowserDriverError(
        "Claude composer not found. Claude layout may have changed.",
        screenshot_path="/tmp/trace.png",
        diagnostic_path="/tmp/trace.json",
        layout_warning=True,
    )

    assert _driver_error_detail(err) == {
        "error": "Claude composer not found. Claude layout may have changed.",
        "screenshot": "/tmp/trace.png",
        "diagnostic": "/tmp/trace.json",
        "layout_warning": True,
    }


def test_browser_worker_error_detail_marks_quota_exhaustion():
    from video_agent.browser_worker.app import _driver_error_detail

    err = QuotaExceededError(
        "Claude quota exhausted",
        screenshot_path="/tmp/trace.png",
        diagnostic_path="/tmp/trace.json",
    )

    assert _driver_error_detail(err) == {
        "error": "Claude quota exhausted",
        "screenshot": "/tmp/trace.png",
        "diagnostic": "/tmp/trace.json",
        "quota_exhausted": True,
    }


def test_normalise_response_text_strips_json_fence():
    raw = '```json\n{"a": 1}\n```'
    assert normalise_response_text(raw) == '{"a": 1}'


def test_normalise_response_text_strips_plain_fence():
    raw = "```\n{\"a\": 1}\n```"
    assert normalise_response_text(raw) == '{"a": 1}'


def test_normalise_response_text_passes_through_plain_text():
    assert normalise_response_text("hello world") == "hello world"


def test_normalise_response_text_handles_empty():
    assert normalise_response_text("") == ""


def test_chatgpt_login_url_detection():
    assert chatgpt_is_login("https://auth.openai.com/login")
    assert chatgpt_is_login("https://chatgpt.com/login")
    assert chatgpt_is_login("https://chatgpt.com/auth/login")
    assert not chatgpt_is_login("https://chatgpt.com/?temporary-chat=true")


def test_chatgpt_driver_uses_temporary_chat_url_without_model_pin():
    assert CHATGPT_URL == "https://chatgpt.com/?temporary-chat=true"


def test_chatgpt_stable_detector_requires_streaming_to_stop():
    from video_agent.browser_worker.drivers.chatgpt import _stable_response_detector_js

    detector = _stable_response_detector_js()

    assert "!streaming" in detector
    assert "stop-streaming-button" in detector
    assert "Stop" in detector
    assert "streaming" in detector


def test_gemini_login_url_detection():
    assert gemini_is_login("https://accounts.google.com/signin/v2/identifier")
    assert not gemini_is_login("https://gemini.google.com/app")


def test_claude_login_url_detection():
    assert claude_is_login("https://claude.ai/login")
    assert claude_is_login("https://claude.ai/sign-in")
    assert not claude_is_login("https://claude.ai/new")


def test_humanize_env_threshold(monkeypatch):
    monkeypatch.setenv("BROWSER_HUMAN_PASTE_THRESHOLD", "50")
    # Re-import to pick up the env override (module reads env at import).
    import importlib
    from video_agent.browser_worker.drivers import humanize as h_mod

    importlib.reload(h_mod)
    try:
        assert h_mod.PASTE_THRESHOLD_CHARS == 50
    finally:
        monkeypatch.delenv("BROWSER_HUMAN_PASTE_THRESHOLD", raising=False)
        importlib.reload(h_mod)


def test_humanize_defaults():
    from video_agent.browser_worker.drivers import humanize as h_mod

    assert h_mod.TYPING_MIN_MS > 0
    assert h_mod.TYPING_MAX_MS > h_mod.TYPING_MIN_MS
    assert h_mod.PAUSE_MIN_MS > 0
    assert h_mod.PAUSE_MAX_MS > h_mod.PAUSE_MIN_MS


def test_chatgpt_open_fails_when_temporary_url_cannot_open(monkeypatch):
    from video_agent.browser_worker.drivers import chatgpt as mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(mod, "_dismiss_modals", _noop)
    monkeypatch.setattr(mod, "human_pause", _noop)

    class _FakePage:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.url = "about:blank"
            self._remaining_failures = 2

        async def goto(self, url: str, **kwargs):
            self.calls.append(url)
            if url == mod.CHATGPT_URL and self._remaining_failures > 0:
                self._remaining_failures -= 1
                raise RuntimeError("net::ERR_HTTP_RESPONSE_CODE_FAILURE")
            self.url = url
            return None

        async def wait_for_timeout(self, _ms: int):
            return None

    page = _FakePage()
    driver = mod.ChatGPTDriver(page)

    try:
        asyncio.run(driver.open())
    except BrowserDriverError as exc:
        assert "temporary chat" in str(exc).lower()
    else:
        raise AssertionError("ChatGPTDriver.open should require temporary chat")

    assert page.calls == [mod.CHATGPT_URL, mod.CHATGPT_URL]


def test_claude_open_fails_when_temporary_chat_control_missing(monkeypatch):
    from video_agent.browser_worker.drivers import claude as mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(mod, "human_pause", _noop)

    class _FakeLocator:
        @property
        def first(self):
            return self

        async def is_visible(self, timeout=0):
            return False

    class _FakePage:
        # Simulate Claude bouncing back to a normal (non-incognito) chat and no
        # temporary-chat toggle being present.
        url = "https://claude.ai/new"

        async def goto(self, url: str, **kwargs):
            self.url = "https://claude.ai/new"
            return None

        def locator(self, selector: str):
            return _FakeLocator()

    driver = mod.ClaudeDriver(_FakePage())
    try:
        asyncio.run(driver.open())
    except BrowserDriverError as exc:
        assert "temporary chat" in str(exc).lower()
    else:
        raise AssertionError("ClaudeDriver.open should require temporary chat")


def test_gemini_open_fails_when_temporary_chat_control_missing(monkeypatch):
    from video_agent.browser_worker.drivers import gemini as mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(mod, "human_pause", _noop)

    class _FakeLocator:
        @property
        def first(self):
            return self

        async def is_visible(self, timeout=0):
            return False

    class _FakePage:
        url = mod.GEMINI_URL

        async def goto(self, url: str, **kwargs):
            self.url = url
            return None

        def locator(self, selector: str):
            return _FakeLocator()

    driver = mod.GeminiDriver(_FakePage())
    try:
        asyncio.run(driver.open())
    except BrowserDriverError as exc:
        assert "temporary chat" in str(exc).lower()
    else:
        raise AssertionError("GeminiDriver.open should require temporary chat")
