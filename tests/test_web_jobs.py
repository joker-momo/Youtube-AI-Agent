from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.web.app import app, get_jobs_root


@pytest.fixture
def client(tmp_path: Path):
    app.dependency_overrides[get_jobs_root] = lambda: tmp_path
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create(client: TestClient, job_id: str = "job-a") -> dict:
    response = client.post(
        "/jobs",
        json={
            "job_id": job_id,
            "channel_id": "vida-plena-45",
            "idea_path": "inputs/manual_idea.json",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_get_job(client: TestClient):
    created = _create(client)
    assert created["job_id"] == "job-a"
    assert created["current_stage"] == "script"

    response = client.get("/jobs/job-a")
    assert response.status_code == 200
    assert response.json()["channel_id"] == "vida-plena-45"


def test_duplicate_create_returns_409(client: TestClient):
    _create(client)
    response = client.post(
        "/jobs",
        json={
            "job_id": "job-a",
            "channel_id": "c",
            "idea_path": "i.json",
        },
    )
    assert response.status_code == 409


def test_get_unknown_job_returns_404(client: TestClient):
    response = client.get("/jobs/missing")
    assert response.status_code == 404


def test_advance_progresses_and_emits_events(client: TestClient):
    _create(client, "job-b")

    response = client.post("/jobs/job-b/advance")
    assert response.status_code == 200
    state = response.json()
    assert state["stages"][0]["status"] == "in_progress"

    response = client.post("/jobs/job-b/advance")
    state = response.json()
    assert state["stages"][0]["status"] == "completed"
    assert state["current_stage"] == "scenes"

    response = client.get("/jobs/job-b/events")
    assert response.status_code == 200
    events = [e["event"] for e in response.json()["events"]]
    assert events[0] == "JOB_CREATED"
    assert "STAGE_STARTED" in events
    assert "STAGE_COMPLETED" in events


def test_advance_unknown_job_returns_404(client: TestClient):
    response = client.post("/jobs/missing/advance")
    assert response.status_code == 404
