from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.web.app import app
from video_agent.web.routes._legacy import (
    get_browser_client,
    get_inputs_root,
    get_jobs_root,
)

CHANNEL = "vida-plena-45"

EXPANDED_IDEA = {
    "topic": "Cómo dormir mejor después de los 45 con una rutina sencilla y realista.",
    "angle": "Una guía práctica para reducir hábitos nocturnos que empeoran el descanso sin prometer curas.",
    "target_duration_sec": 840,
    "duration_mode": "auto",
    "duration_reason": "El tema requiere explicar hábitos, ejemplos y una cautela profesional sin alargarlo demasiado.",
    "key_points": [
        "Qué señales indican que la rutina nocturna necesita ajustes",
        "Cómo ordenar la cena y la luz de la tarde sin cambios extremos",
        "Cuándo consultar con un profesional si el insomnio persiste",
    ],
    "title_seed": "Dormir mejor después de los 45 con una rutina sencilla",
    "target_keyword": "dormir mejor despues de los 45",
    "source": "manual_title_expansion",
    "duplicate_check": {"verdict": "UNIQUE"},
}

TITLE = "Dormir mejor después de los 45 con una rutina sencilla y realista"


class FakeBrowserClient:
    def __init__(self, response: dict | str | None = None):
        self.response = response
        self.sessions: list[tuple[str, list[str]]] = []

    async def run_session(self, site: str, messages: list[str], **kwargs) -> str:
        self.sessions.append((site, list(messages)))
        if isinstance(self.response, str):
            return self.response
        return json.dumps(self.response or EXPANDED_IDEA, ensure_ascii=False)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    fake = FakeBrowserClient()
    inputs_root = tmp_path / "inputs"
    inputs_root.mkdir()
    # No accidental duplicate hits against the real published_videos.json.
    monkeypatch.setattr(
        "video_agent.web.services.video_job_creator.load_published_videos",
        lambda *a, **k: [],
    )
    app.dependency_overrides[get_jobs_root] = lambda: tmp_path
    app.dependency_overrides[get_inputs_root] = lambda: inputs_root
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        with TestClient(app) as test_client:
            test_client.fake_browser = fake  # type: ignore[attr-defined]
            test_client.inputs_root = inputs_root  # type: ignore[attr-defined]
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_from_title_returns_idea_and_does_not_create_job(client: TestClient, tmp_path: Path):
    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": TITLE})

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["channel_id"] == CHANNEL
    assert body["idea"]["title_seed"] == TITLE
    assert body["idea"]["topic"]
    assert body["idea"]["key_points"]
    # No job created anywhere.
    assert "job_id" not in body
    assert not any((tmp_path / p).joinpath("job.json").exists() for p in [body["idea"]["title_seed"]])
    job_dirs = [d for d in tmp_path.iterdir() if (d / "job.json").exists()]
    assert job_dirs == []


def test_from_title_saves_idea_under_inputs_ideas(client: TestClient):
    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": TITLE})

    assert r.status_code == 201, r.text
    saved = r.json()["saved"]
    full = client.inputs_root / saved  # type: ignore[attr-defined]
    assert full.exists()
    on_disk = json.loads(full.read_text(encoding="utf-8"))
    assert on_disk["title_seed"] == TITLE


def test_from_title_score_fields_are_empty(client: TestClient):
    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": TITLE})

    assert r.status_code == 201, r.text
    idea = r.json()["idea"]
    assert idea.get("keyword_final_score") is None
    assert idea.get("keyword_source_score") is None
    assert idea.get("bucket") is None


def test_from_title_applies_hidden_auto_duration_defaults(client: TestClient):
    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": TITLE})

    assert r.status_code == 201, r.text
    idea = r.json()["idea"]
    assert idea["duration_mode"] == "auto"
    assert 360 <= idea["target_duration_sec"] <= 1200
    assert idea["duration_reason"]


def test_from_title_flags_duplicate_but_still_returns_and_saves(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "video_agent.web.services.video_job_creator.load_published_videos",
        lambda *a, **k: [{"title": EXPANDED_IDEA["topic"]}],
    )

    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": TITLE})

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_duplicate"] is True
    assert body["duplicate_of"]
    assert body["idea"]["is_duplicate"] is True
    assert body["saved"]


def test_from_title_rejects_short_title(client: TestClient):
    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": "short"})

    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_title_seed"


def test_from_title_returns_422_when_chatgpt_never_valid(client: TestClient):
    client.fake_browser.response = "still not json"  # type: ignore[attr-defined]

    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": TITLE})

    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "idea_expansion_failed"


def test_from_title_only_needs_title_seed(client: TestClient):
    # No duration / policy / notes fields — title is the only required input.
    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": TITLE})
    assert r.status_code == 201, r.text


def test_from_title_retries_invalid_chatgpt_json(client: TestClient):
    class RetryBrowser(FakeBrowserClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def run_session(self, site: str, messages: list[str], **kwargs) -> str:
            self.calls += 1
            self.sessions.append((site, list(messages)))
            if self.calls == 1:
                return "not json"
            return json.dumps(EXPANDED_IDEA, ensure_ascii=False)

    retry = RetryBrowser()
    app.dependency_overrides[get_browser_client] = lambda: retry

    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": TITLE})

    assert r.status_code == 201, r.text
    assert retry.calls == 2


def test_from_title_prompt_includes_existing_videos_to_avoid(client: TestClient, tmp_path: Path):
    existing_job = tmp_path / "existing-video"
    existing_job.mkdir()
    (existing_job / "job.json").write_text(
        json.dumps({"job_id": "existing-video", "channel_id": CHANNEL}),
        encoding="utf-8",
    )
    (existing_job / "idea.json").write_text(
        json.dumps({**EXPANDED_IDEA, "title_seed": "Rutina nocturna distinta para descansar"}, ensure_ascii=False),
        encoding="utf-8",
    )

    r = client.post(f"/channels/{CHANNEL}/ideas/from-title", json={"title_seed": TITLE})

    assert r.status_code == 201, r.text
    prompt = client.fake_browser.sessions[0][1][0]  # type: ignore[attr-defined]
    assert "Rutina nocturna distinta para descansar" in prompt
