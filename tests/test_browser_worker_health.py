from fastapi.testclient import TestClient

from video_agent.browser_worker.app import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "browser-worker"}
