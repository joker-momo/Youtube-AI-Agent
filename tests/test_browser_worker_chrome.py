from __future__ import annotations

from fastapi.testclient import TestClient

from video_agent.browser_worker.app import (
    _is_logged_out_url,
    _resolve_cdp_url,
    app,
)


def test_chrome_returns_503_when_cdp_unreachable(monkeypatch):
    monkeypatch.setenv("CHROME_CDP_URL", "http://127.0.0.1:1")
    client = TestClient(app)
    response = client.get("/chrome")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["cdp_url"] == "http://127.0.0.1:1"
    assert "error" in detail


def test_resolve_cdp_url_replaces_host_docker_internal_with_ip(monkeypatch):
    monkeypatch.setattr("socket.gethostbyname", lambda host: "192.168.65.254")

    assert _resolve_cdp_url("http://host.docker.internal:9222") == "http://192.168.65.254:9222"


def test_resolve_cdp_url_leaves_other_hosts_unchanged(monkeypatch):
    monkeypatch.setattr("socket.gethostbyname", lambda host: "192.168.65.254")

    assert _resolve_cdp_url("http://127.0.0.1:9222") == "http://127.0.0.1:9222"


def test_is_logged_out_url_detects_chatgpt_login_redirects():
    assert _is_logged_out_url("chatgpt", "https://auth.openai.com/login")
    assert _is_logged_out_url("chatgpt", "https://chatgpt.com/auth/login")
    assert not _is_logged_out_url("chatgpt", "https://chatgpt.com/")


def test_is_logged_out_url_detects_gemini_login_redirects():
    assert _is_logged_out_url("gemini", "https://accounts.google.com/signin/v2/identifier")
    assert not _is_logged_out_url("gemini", "https://gemini.google.com/app")
