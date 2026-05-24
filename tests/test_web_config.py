from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import video_agent.web.app as web_app
from video_agent.web.app import app


def _client_with_env_paths(monkeypatch, tmp_path: Path) -> TestClient:
    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    monkeypatch.setattr(web_app, "_env_path", lambda: env_path)
    monkeypatch.setattr(web_app, "_env_example_path", lambda: example_path)
    return TestClient(app)


def test_get_env_masks_secret_values(monkeypatch, tmp_path: Path):
    client = _client_with_env_paths(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text(
        "PEXELS_API_KEY=abcd1234\nTZ=Asia/Ho_Chi_Minh\nADMIN_TOKEN=secret-token\n",
        encoding="utf-8",
    )

    response = client.get("/config/env")

    assert response.status_code == 200
    content = response.json()["content"]
    assert "abcd1234" not in content
    assert "secret-token" not in content
    assert "PEXELS_API_KEY=********1234" in content
    assert "ADMIN_TOKEN=********oken" in content
    assert "TZ=Asia/Ho_Chi_Minh" in content


def test_save_env_disabled_by_default(monkeypatch, tmp_path: Path):
    client = _client_with_env_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLE_ENV_EDITOR", raising=False)

    response = client.post("/config/env", json={"content": "TZ=UTC\n"})

    assert response.status_code == 403
    assert not (tmp_path / ".env").exists()


def test_save_env_requires_admin_token_when_enabled(monkeypatch, tmp_path: Path):
    client = _client_with_env_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ENV_EDITOR", "true")
    monkeypatch.setenv("ADMIN_TOKEN", "let-me-in")

    denied = client.post("/config/env", json={"content": "TZ=UTC\n"})
    allowed = client.post(
        "/config/env",
        json={"content": "TZ=UTC\n"},
        headers={"X-Admin-Token": "let-me-in"},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "TZ=UTC\n"


def test_bootstrap_env_disabled_by_default(monkeypatch, tmp_path: Path):
    client = _client_with_env_paths(monkeypatch, tmp_path)
    (tmp_path / ".env.example").write_text("TZ=UTC\n", encoding="utf-8")
    monkeypatch.delenv("ENABLE_ENV_EDITOR", raising=False)

    response = client.post("/config/env/bootstrap")

    assert response.status_code == 403
    assert not (tmp_path / ".env").exists()


def test_get_env_masks_common_secret_names(monkeypatch, tmp_path: Path):
    client = _client_with_env_paths(monkeypatch, tmp_path)
    raw_secret_values = [
        "sk-test-secret",
        "123456:secret",
        "supersecret",
    ]
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=sk-test-secret",
                "TELEGRAM_BOT_TOKEN=123456:secret",
                "PASSWORD=supersecret",
                "NORMAL_VALUE=visible",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get("/config/env")

    assert response.status_code == 200
    content = response.json()["content"]
    for secret in raw_secret_values:
        assert secret not in content
    assert "NORMAL_VALUE=visible" in content
