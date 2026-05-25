from fastapi.testclient import TestClient

from video_agent.web.app import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "app"}


def test_dashboard_returns_html():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text.lower()
