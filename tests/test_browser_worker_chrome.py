from __future__ import annotations

from fastapi.testclient import TestClient

from video_agent.browser_worker.app import app


def test_chrome_returns_503_when_cdp_unreachable(monkeypatch):
    monkeypatch.setenv("CHROME_CDP_URL", "http://127.0.0.1:1")
    client = TestClient(app)
    response = client.get("/chrome")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["cdp_url"] == "http://127.0.0.1:1"
    assert "error" in detail
