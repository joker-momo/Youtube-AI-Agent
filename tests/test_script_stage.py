from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.contracts import repo_root
from video_agent.orchestrator import create_job
from video_agent.orchestrator.stages import (
    SCRIPT_PROMPT_PATH,
    StageInputMissingError,
    run_script_stage,
)
from video_agent.web.app import app, get_channel_path, get_jobs_root


@pytest.fixture
def channel_path() -> Path:
    return repo_root() / "configs/vida-plena-45/channel.yaml"


@pytest.fixture
def idea_payload() -> dict:
    return json.loads(
        (repo_root() / "inputs/manual_idea.json").read_text(encoding="utf-8")
    )


def test_run_script_stage_writes_prompt(tmp_path: Path, channel_path: Path, idea_payload: dict):
    job_dir = tmp_path / "job-s1"
    create_job(job_dir, job_id="job-s1", channel_id="vida-plena-45", idea_path="idea.json")
    (job_dir / "idea.json").write_text(
        json.dumps(idea_payload, ensure_ascii=False), encoding="utf-8"
    )

    output = run_script_stage(job_dir, channel_path)
    assert output == job_dir / SCRIPT_PROMPT_PATH
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "SCRIPT artifact" in text
    assert idea_payload["topic"] in text

    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    completed = [e for e in events if e["event"] == "STAGE_COMPLETED"]
    assert any(e["data"]["stage"] == "script" for e in completed)


def test_run_script_stage_missing_idea_raises(tmp_path: Path, channel_path: Path):
    job_dir = tmp_path / "job-s2"
    create_job(job_dir, job_id="job-s2", channel_id="vida-plena-45", idea_path="idea.json")
    with pytest.raises(StageInputMissingError):
        run_script_stage(job_dir, channel_path)


@pytest.fixture
def client(tmp_path: Path, channel_path: Path):
    app.dependency_overrides[get_jobs_root] = lambda: tmp_path
    app.dependency_overrides[get_channel_path] = lambda: channel_path
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_job(client: TestClient, job_id: str = "job-http"):
    response = client.post(
        "/jobs",
        json={
            "job_id": job_id,
            "channel_id": "vida-plena-45",
            "idea_path": "idea.json",
        },
    )
    assert response.status_code == 201, response.text


def test_post_idea_then_run_script_via_http(client: TestClient, idea_payload: dict):
    _create_job(client, "job-http")
    response = client.post("/jobs/job-http/idea", json=idea_payload)
    assert response.status_code == 201

    response = client.post("/jobs/job-http/stages/script/run")
    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "operator/chatgpt/script_prompt.md"
    state = body["state"]
    script_stage = next(s for s in state["stages"] if s["name"] == "script")
    assert script_stage["status"] == "completed"
    assert state["current_stage"] == "scenes"


def test_run_script_without_idea_returns_409(client: TestClient):
    _create_job(client, "job-no-idea")
    response = client.post("/jobs/job-no-idea/stages/script/run")
    assert response.status_code == 409


def test_post_idea_unknown_job_returns_404(client: TestClient, idea_payload: dict):
    response = client.post("/jobs/missing/idea", json=idea_payload)
    assert response.status_code == 404
