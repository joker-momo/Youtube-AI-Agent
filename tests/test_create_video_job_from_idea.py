from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.orchestrator.queue import JobQueue
from video_agent.web.app import app
from video_agent.web.routes._legacy import get_browser_client, get_jobs_root
from video_agent.web.services.video_job_creator import (
    check_content_duplicate,
    collect_existing_video_ideas,
)


VALID_IDEA = {
    "topic": "Cómo dormir mejor después de los 45 con una rutina sencilla y realista.",
    "angle": "Una guía práctica para reducir hábitos nocturnos que empeoran el descanso sin prometer curas.",
    "target_duration_sec": 840,
    "key_points": [
        "Qué señales indican que la rutina nocturna necesita ajustes",
        "Cómo ordenar la cena y la luz de la tarde sin cambios extremos",
        "Cuándo consultar con un profesional si el insomnio persiste",
    ],
    "title_seed": "Dormir mejor después de los 45 con una rutina sencilla",
    "target_keyword": "dormir mejor despues de los 45",
}


class FakeBrowserClient:
    def __init__(self, response: dict | str | None = None):
        self.response = response
        self.sessions: list[tuple[str, list[str]]] = []

    async def run_session(self, site: str, messages: list[str], **kwargs) -> str:
        self.sessions.append((site, list(messages)))
        if isinstance(self.response, str):
            return self.response
        payload = self.response or {
            **VALID_IDEA,
            "duration_mode": "auto",
            "duration_reason": "El tema requiere explicar hábitos, ejemplos y una cautela profesional sin alargarlo demasiado.",
            "source": "manual_title_expansion",
            "duplicate_check": {
                "verdict": "UNIQUE",
                "closest_existing_title": "",
                "overlap_reason": "",
                "how_this_angle_is_different": "",
            },
        }
        return json.dumps(payload, ensure_ascii=False)


@pytest.fixture
def client(tmp_path: Path):
    fake = FakeBrowserClient()
    app.dependency_overrides[get_jobs_root] = lambda: tmp_path
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        with TestClient(app) as test_client:
            test_client.fake_browser = fake  # type: ignore[attr-defined]
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_create_job_from_full_idea_writes_idea_and_enqueues_run_all(
    client: TestClient,
    tmp_path: Path,
):
    response = client.post(
        "/jobs/from-idea",
        json={
            "channel_id": "vida-plena-45",
            "idea": VALID_IDEA,
            "run_now": True,
            "check_duplicates": False,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["idea_source"] == "provided_full_idea"
    assert body["pipeline_status"] == "queued"
    assert body["job_id"].startswith("dormir-mejor-despues-de-los-45")
    idea_path = tmp_path / body["job_id"] / "json" / "idea.json"
    if not idea_path.exists():
        idea_path = tmp_path / body["job_id"] / "idea.json"
    assert idea_path.exists()
    saved = json.loads(idea_path.read_text(encoding="utf-8"))
    assert saved["title_seed"] == VALID_IDEA["title_seed"]
    job = json.loads((tmp_path / body["job_id"] / "job.json").read_text(encoding="utf-8"))
    assert job["current_stage"] == "idea_research"
    assert [s["name"] for s in job["stages"]][0] == "idea_research"
    queued = JobQueue(tmp_path / "queue.db").get_job(body["job_id"])
    assert queued is not None
    assert queued["command"] == "run_all"
    fake = client.fake_browser  # type: ignore[attr-defined]
    assert fake.sessions == []


def test_create_job_only_timeline_does_not_show_fake_running_stage(
    client: TestClient,
):
    response = client.post(
        "/jobs/from-idea",
        json={
            "channel_id": "vida-plena-45",
            "idea": VALID_IDEA,
            "run_now": False,
            "check_duplicates": False,
        },
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    jobs = client.get("/jobs").json()["jobs"]
    created = next(j for j in jobs if j["job_id"] == job_id)
    assert created["queue_status"] is None
    assert created["in_progress"] == []
    assert created["stages"][0]["status"] == "pending"

    timeline = client.get(f"/jobs/{job_id}/timeline").json()
    assert timeline["queue_status"] is None
    assert timeline["stages"][0]["status"] == "pending"


def test_create_job_from_full_idea_requires_title_seed(client: TestClient):
    idea = dict(VALID_IDEA)
    idea.pop("title_seed")

    response = client.post(
        "/jobs/from-idea",
        json={
            "channel_id": "vida-plena-45",
            "idea": idea,
            "run_now": False,
            "check_duplicates": False,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_idea"


def test_create_job_from_full_idea_appends_suffix_on_job_id_collision(
    client: TestClient,
    monkeypatch,
):
    import datetime as dt
    fixed_now = dt.datetime(2026, 6, 2, 12, 0, 0)

    class FakeDatetime:
        @classmethod
        def now(cls):
            return fixed_now

    monkeypatch.setattr("video_agent.utils.paths.datetime", FakeDatetime)

    payload = {
        "channel_id": "vida-plena-45",
        "idea": VALID_IDEA,
        "run_now": False,
        "check_duplicates": False,
    }

    first = client.post("/jobs/from-idea", json=payload)
    second = client.post("/jobs/from-idea", json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["job_id"] == first.json()["job_id"] + "-2"


def test_create_job_from_full_idea_warn_only_creates_job_with_duplicate_warning(
    client: TestClient,
    tmp_path: Path,
):
    existing_job = tmp_path / "existing-video"
    existing_job.mkdir()
    (existing_job / "job.json").write_text(
        json.dumps({"job_id": "existing-video", "channel_id": "vida-plena-45"}),
        encoding="utf-8",
    )
    (existing_job / "idea.json").write_text(
        json.dumps(VALID_IDEA, ensure_ascii=False),
        encoding="utf-8",
    )

    response = client.post(
        "/jobs/from-idea",
        json={
            "channel_id": "vida-plena-45",
            "idea": VALID_IDEA,
            "run_now": False,
            "duplicate_policy": "warn_only",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["duplicate_verdict"]["policy_action"] == "warning"
    assert body["pipeline_status"] == "created_not_started"


def test_create_job_from_full_idea_preserves_explicit_job_id(client: TestClient, tmp_path: Path):
    response = client.post(
        "/jobs/from-idea",
        json={
            "channel_id": "vida-plena-45",
            "idea": VALID_IDEA,
            "job_id": "Manual.Job_01",
            "run_now": False,
            "check_duplicates": False,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_id"] == "Manual.Job_01"
    assert (tmp_path / "Manual.Job_01" / "job.json").exists()


def test_existing_video_history_filters_by_channel_id_and_prefers_seo_title(tmp_path: Path):
    matching = tmp_path / "matching"
    matching.mkdir()
    (matching / "job.json").write_text(
        json.dumps({"job_id": "matching", "channel_id": "vida-plena-45"}),
        encoding="utf-8",
    )
    (matching / "idea.json").write_text(
        json.dumps({**VALID_IDEA, "title_seed": "Idea fallback"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (matching / "seo.json").write_text(
        json.dumps({"title": "SEO final title"}, ensure_ascii=False),
        encoding="utf-8",
    )
    other = tmp_path / "other"
    other.mkdir()
    (other / "job.json").write_text(
        json.dumps({"job_id": "other", "channel_id": "other-channel"}),
        encoding="utf-8",
    )
    (other / "idea.json").write_text(
        json.dumps({**VALID_IDEA, "title_seed": "Other channel title"}, ensure_ascii=False),
        encoding="utf-8",
    )

    history = collect_existing_video_ideas(
        channel_id="vida-plena-45",
        jobs_root=tmp_path,
        limit=10,
    )

    assert any(item["title"] == "SEO final title" and item["source"] == "seo.json" for item in history)
    assert all(item["title"] != "Other channel title" for item in history)


def test_duplicate_check_returns_verdict_object_for_exact_title():
    verdict = check_content_duplicate(
        idea=VALID_IDEA,
        existing_videos=[{"title": VALID_IDEA["title_seed"]}],
    )

    assert verdict.verdict == "DUPLICATE"
    assert verdict.closest_existing_title == VALID_IDEA["title_seed"]
    assert verdict.similarity == 1.0
