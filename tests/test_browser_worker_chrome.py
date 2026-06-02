from __future__ import annotations

from fastapi.testclient import TestClient

from video_agent.browser_worker.app import (
    _cdp_url,
    _is_logged_out_url,
    app,
)


def test_runtime_returns_503_when_cdp_unreachable(monkeypatch):
    monkeypatch.setenv("CHROME_CDP_URL", "http://127.0.0.1:1")
    client = TestClient(app)
    response = client.get("/runtime")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["cdp_url"] == "http://127.0.0.1:1"
    assert "error" in detail


def test_cdp_url_defaults_to_runtime_container(monkeypatch):
    monkeypatch.delenv("CHROME_CDP_URL", raising=False)
    assert _cdp_url() == "http://browser-runtime:9222"


def test_cdp_url_respects_override(monkeypatch):
    monkeypatch.setenv("CHROME_CDP_URL", "http://other-host:9333")
    assert _cdp_url() == "http://other-host:9333"


def test_is_logged_out_url_detects_chatgpt_login_redirects():
    assert _is_logged_out_url("chatgpt", "https://auth.openai.com/login")
    assert _is_logged_out_url("chatgpt", "https://chatgpt.com/auth/login")
    assert not _is_logged_out_url("chatgpt", "https://chatgpt.com/")


def test_is_logged_out_url_detects_gemini_login_redirects():
    assert _is_logged_out_url(
        "gemini", "https://accounts.google.com/signin/v2/identifier"
    )
    assert not _is_logged_out_url("gemini", "https://gemini.google.com/app")


def test_is_logged_out_url_detects_gemini_login_redirects():
    assert _is_logged_out_url("gemini", "https://accounts.google.com/signin/v2")
    assert _is_logged_out_url("gemini", "https://gemini.google.com/signin")
    assert not _is_logged_out_url("gemini", "https://gemini.google.com/app")
