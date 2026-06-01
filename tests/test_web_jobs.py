from __future__ import annotations

import json
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
    assert created["current_stage"] == "idea_research"

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


def test_list_jobs_includes_original_idea_title(client: TestClient, tmp_path: Path):
    _create(client, "job-title")
    idea = {
        "topic": "Tema fallback",
        "title_seed": "Cómo dormir mejor después de los 45 sin complicarte",
    }
    (tmp_path / "job-title" / "idea.json").write_text(
        json.dumps(idea, ensure_ascii=False),
        encoding="utf-8",
    )

    response = client.get("/jobs")

    assert response.status_code == 200
    [job] = response.json()["jobs"]
    assert job["idea_title"] == "Cómo dormir mejor después de los 45 sin complicarte"


def test_timeline_includes_original_idea_title(client: TestClient, tmp_path: Path):
    _create(client, "job-timeline-title")
    idea = {
        "topic": "Tema fallback",
        "title_seed": "Rutina breve para dormir mejor después de los 45",
    }
    (tmp_path / "job-timeline-title" / "idea.json").write_text(
        json.dumps(idea, ensure_ascii=False),
        encoding="utf-8",
    )

    response = client.get("/jobs/job-timeline-title/timeline")

    assert response.status_code == 200
    assert response.json()["idea_title"] == "Rutina breve para dormir mejor después de los 45"


def test_advance_progresses_and_emits_events(client: TestClient):
    _create(client, "job-b")

    response = client.post("/jobs/job-b/advance")
    assert response.status_code == 200
    state = response.json()
    assert state["stages"][0]["status"] == "in_progress"

    response = client.post("/jobs/job-b/advance")
    state = response.json()
    assert state["stages"][0]["status"] == "completed"
    assert state["current_stage"] == "script"

    response = client.get("/jobs/job-b/events")
    assert response.status_code == 200
    events = [e["event"] for e in response.json()["events"]]
    assert events[0] == "JOB_CREATED"
    assert "STAGE_STARTED" in events
    assert "STAGE_COMPLETED" in events


def test_advance_unknown_job_returns_404(client: TestClient):
    response = client.post("/jobs/missing/advance")
    assert response.status_code == 404


def test_delete_job_removes_completed_job(client: TestClient):
    _create(client, "job-del")

    # Mark the first stage complete so the job is not "running".
    client.post("/jobs/job-del/advance")
    client.post("/jobs/job-del/advance")

    response = client.delete("/jobs/job-del")
    assert response.status_code == 204

    response = client.get("/jobs/job-del")
    assert response.status_code == 404


def test_delete_job_rejects_running_job(client: TestClient):
    _create(client, "job-running")

    # First advance marks current stage as in_progress.
    response = client.post("/jobs/job-running/advance")
    assert response.status_code == 200

    response = client.delete("/jobs/job-running")
    assert response.status_code == 409
    assert "running job" in response.json()["detail"].lower()


def test_ws_events_replays_existing_entries(client: TestClient, monkeypatch):
    monkeypatch.setenv("EVENTS_POLL_SECONDS", "0.05")
    _create(client, "job-ws")
    client.post("/jobs/job-ws/advance")
    client.post("/jobs/job-ws/advance")

    received: list[dict] = []
    with client.websocket_connect("/jobs/job-ws/events") as ws:
        for _ in range(3):
            received.append(json.loads(ws.receive_text()))

    events = [e["event"] for e in received]
    assert events[0] == "JOB_CREATED"
    assert "STAGE_STARTED" in events
    assert "STAGE_COMPLETED" in events


def test_ws_events_unknown_job_closes(client: TestClient):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/jobs/missing/events") as ws:
            ws.receive_text()
