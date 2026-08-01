from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from video_agent.localized_v2.browser_worker import create_app


def test_v2_browser_worker_reports_exact_isolated_identity(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    app = create_app(
        profile_root=profile,
        session_namespace="localized-v2:en-us",
    )

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "localized-v2-browser-worker",
        "sessionNamespace": "localized-v2:en-us",
        "profileRoot": str(profile.resolve()),
    }


def test_v2_browser_worker_exposes_only_required_generation_routes(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    app = create_app(
        profile_root=profile,
        session_namespace="localized-v2:en-us",
    )
    paths = {route.path for route in app.routes}

    assert {"/health", "/runtime", "/chatgpt/send", "/chatgpt/image"} <= paths
    assert "/auth/chatgpt/status" not in paths
    assert "/gemini/send" not in paths
