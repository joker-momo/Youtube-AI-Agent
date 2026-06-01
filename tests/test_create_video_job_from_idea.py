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
    assert body["idea"]["title_seed"] == VALID_IDEA["title_seed"]
    assert (tmp_path / body["job_id"] / "idea.json").exists()
    saved = json.loads((tmp_path / body["job_id"] / "idea.json").read_text(encoding="utf-8"))
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
):
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


def test_create_job_from_title_expands_with_temporary_chat_history_and_enqueues(
    client: TestClient,
    tmp_path: Path,
):
    client.fake_browser.response = {  # type: ignore[attr-defined]
        "topic": "Cómo cuidar la masa muscular después de los 60 con alimentos cotidianos y hábitos realistas.",
        "angle": "Una guía prudente sobre cinco grupos de alimentos que pueden apoyar el cuidado muscular, con ejemplos de comidas y una nota profesional.",
        "target_duration_sec": 900,
        "duration_mode": "auto",
        "duration_reason": "El tema necesita explicar sarcopenia, alimentos, ejemplos y cautelas sin prometer curas.",
        "key_points": [
            "Qué es la sarcopenia explicada sin alarmismo",
            "Cinco grupos de alimentos útiles dentro de una rutina realista",
            "Cuándo consultar con un profesional si hay pérdida de fuerza",
        ],
        "title_seed": "Sarcopenia después de los 60: 5 alimentos que ayudan a proteger tus músculos",
        "target_keyword": "sarcopenia despues de los 60",
        "source": "manual_title_expansion",
        "duplicate_check": {
            "verdict": "UNIQUE",
            "closest_existing_title": "",
            "overlap_reason": "",
            "how_this_angle_is_different": "",
        },
    }
    existing_job = tmp_path / "existing-video"
    existing_job.mkdir()
    (existing_job / "job.json").write_text(
        json.dumps({"job_id": "existing-video", "channel_id": "vida-plena-45"}),
        encoding="utf-8",
    )
    (existing_job / "idea.json").write_text(
        json.dumps(
            {
                **VALID_IDEA,
                "title_seed": "Dormir mal por estrés después de los 45: rutina realista",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/jobs/from-idea-title",
        json={
            "channel_id": "vida-plena-45",
            "title_seed": "Sarcopenia después de los 60: 5 alimentos que ayudan a proteger tus músculos",
            "duration_mode": "auto",
            "run_now": True,
            "check_duplicates": True,
            "duplicate_policy": "rewrite_angle",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["idea_source"] == "manual_title_expansion"
    assert body["idea"]["title_seed"] == "Sarcopenia después de los 60: 5 alimentos que ayudan a proteger tus músculos"
    assert body["idea"]["duration_mode"] == "auto"
    assert body["idea"]["duration_reason"]
    assert body["pipeline_status"] == "queued"
    fake = client.fake_browser  # type: ignore[attr-defined]
    assert fake.sessions
    assert fake.sessions[0][0] == "chatgpt"
    assert "Existing published/generated videos to avoid" in fake.sessions[0][1][0]
    assert "Dormir mal por estrés después de los 45: rutina realista" in fake.sessions[0][1][0]
    queued = JobQueue(tmp_path / "queue.db").get_job(body["job_id"])
    assert queued is not None


def test_create_job_from_title_fixed_duration_overrides_model_duration(
    client: TestClient,
):
    client.fake_browser.response = {  # type: ignore[attr-defined]
        **VALID_IDEA,
        "target_duration_sec": 600,
        "duration_mode": "fixed",
        "source": "manual_title_expansion",
        "duplicate_check": {"verdict": "UNIQUE"},
    }

    response = client.post(
        "/jobs/from-idea-title",
        json={
            "channel_id": "vida-plena-45",
            "title_seed": VALID_IDEA["title_seed"],
            "duration_mode": "fixed",
            "target_duration_sec": 840,
            "run_now": False,
            "check_duplicates": False,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["idea"]["target_duration_sec"] == 840
    assert response.json()["idea"]["duration_mode"] == "fixed"


def test_create_job_from_title_retries_invalid_chatgpt_json(client: TestClient):
    class RetryBrowser(FakeBrowserClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def run_session(self, site: str, messages: list[str], **kwargs) -> str:
            self.calls += 1
            self.sessions.append((site, list(messages)))
            if self.calls == 1:
                return "not json"
            return json.dumps(
                {
                    **VALID_IDEA,
                    "duration_mode": "auto",
                    "duration_reason": "Explica hábitos, ejemplos y cautelas profesionales.",
                    "source": "manual_title_expansion",
                    "duplicate_check": {"verdict": "UNIQUE"},
                },
                ensure_ascii=False,
            )

    retry = RetryBrowser()
    app.dependency_overrides[get_browser_client] = lambda: retry

    response = client.post(
        "/jobs/from-idea-title",
        json={
            "channel_id": "vida-plena-45",
            "title_seed": VALID_IDEA["title_seed"],
            "duration_mode": "auto",
            "run_now": False,
            "check_duplicates": False,
        },
    )

    assert response.status_code == 201, response.text
    assert retry.calls == 2
    assert "Previous attempt failed validation" in retry.sessions[1][1][0]


def test_create_job_from_title_retries_validation_errors(client: TestClient):
    class ValidationRetryBrowser(FakeBrowserClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def run_session(self, site: str, messages: list[str], **kwargs) -> str:
            self.calls += 1
            self.sessions.append((site, list(messages)))
            payload = {
                **VALID_IDEA,
                "duration_mode": "auto",
                "source": "manual_title_expansion",
                "duplicate_check": {"verdict": "UNIQUE"},
            }
            if self.calls == 1:
                payload["duration_reason"] = ""
            else:
                payload["duration_reason"] = "Explica hábitos, ejemplos y cautelas profesionales."
            return json.dumps(payload, ensure_ascii=False)

    retry = ValidationRetryBrowser()
    app.dependency_overrides[get_browser_client] = lambda: retry

    response = client.post(
        "/jobs/from-idea-title",
        json={
            "channel_id": "vida-plena-45",
            "title_seed": VALID_IDEA["title_seed"],
            "duration_mode": "auto",
            "run_now": False,
            "check_duplicates": False,
        },
    )

    assert response.status_code == 201, response.text
    assert retry.calls == 2
    assert "duration_reason is required" in retry.sessions[1][1][0]


def test_create_job_from_title_returns_422_after_invalid_chatgpt_json_retries(
    client: TestClient,
):
    client.fake_browser.response = "still not json"  # type: ignore[attr-defined]

    response = client.post(
        "/jobs/from-idea-title",
        json={
            "channel_id": "vida-plena-45",
            "title_seed": VALID_IDEA["title_seed"],
            "duration_mode": "auto",
            "run_now": False,
            "check_duplicates": False,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "idea_expansion_failed"


def test_create_job_from_title_blocks_exact_duplicate_when_policy_block(
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
    client.fake_browser.response = {  # type: ignore[attr-defined]
        **VALID_IDEA,
        "duration_mode": "fixed",
        "duration_reason": "",
        "duplicate_check": {
            "verdict": "UNIQUE",
            "closest_existing_title": "",
            "overlap_reason": "",
            "how_this_angle_is_different": "",
        },
        "source": "manual_title_expansion",
    }

    response = client.post(
        "/jobs/from-idea-title",
        json={
            "channel_id": "vida-plena-45",
            "title_seed": VALID_IDEA["title_seed"],
            "duration_mode": "fixed",
            "target_duration_sec": 840,
            "run_now": False,
            "duplicate_policy": "block",
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "duplicate_idea"
    assert detail["closest_existing_title"] == VALID_IDEA["title_seed"]


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


def test_create_job_from_title_rejects_invalid_title(client: TestClient):
    response = client.post(
        "/jobs/from-idea-title",
        json={
            "channel_id": "vida-plena-45",
            "title_seed": "short",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_title_seed"


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
