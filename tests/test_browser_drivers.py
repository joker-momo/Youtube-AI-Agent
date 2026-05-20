from __future__ import annotations

from video_agent.browser_worker.drivers.base import (
    BrowserDriverError,
    LoginRequiredError,
    normalise_response_text,
)
from video_agent.browser_worker.drivers.chatgpt import _is_login_url as chatgpt_is_login
from video_agent.browser_worker.drivers.claude import _is_login_url as claude_is_login
from video_agent.browser_worker.drivers.gemini import _is_login_url as gemini_is_login


def test_login_required_error_is_browser_driver_error():
    err = LoginRequiredError("login", screenshot_path="/tmp/x.png")
    assert isinstance(err, BrowserDriverError)
    assert err.screenshot_path == "/tmp/x.png"


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
    assert not chatgpt_is_login("https://chatgpt.com/?model=gpt-4o")


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
