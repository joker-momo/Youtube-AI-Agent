from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.contracts import repo_root
from video_agent.orchestrator import create_job
from video_agent.orchestrator.stages import (
    SCENES_PROMPT_PATH,
    SCENES_RAW_PATH,
    SCRIPT_PROMPT_PATH,
    SCRIPT_RAW_PATH,
    StageInputMissingError,
    promote_scenes_stage,
    promote_script_stage,
    run_scenes_stage,
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


@pytest.fixture
def valid_script_payload() -> dict:
    return {
        "channel_id": "vida-plena-45",
        "job_id": "job-s1",
        "hook": "Dormir mejor empieza con una decisión simple.",
        "sections": [
            {
                "title": "Calma",
                "text": "Baja el ritmo una hora antes de acostarte.",
            }
        ],
        "narration": "Dormir mejor empieza con una decisión simple. Baja el ritmo una hora antes de acostarte.",
        "cta": "Prueba este hábito esta noche.",
        "qa": {"verdict": "PENDING_GEMINI_QA"},
    }


@pytest.fixture
def valid_scenes_payload() -> dict:
    return {
        "channel_id": "vida-plena-45",
        "job_id": "job-s1",
        "total_duration_sec": 48,
        "scenes": [
            {
                "id": "scene-01",
                "duration_sec": 24,
                "narration": "Dormir mejor empieza con una decisión simple.",
                "on_screen_text": "Respira y baja el ritmo",
                "caption": "Un hábito sencillo para descansar mejor.",
                "visual_prompt": "Calm evening bedroom with warm lamp light and a tidy nightstand",
                "motion": "slow push-in",
                "asset_refs": {},
            },
            {
                "id": "scene-02",
                "duration_sec": 24,
                "narration": "Baja el ritmo una hora antes de acostarte.",
                "on_screen_text": "Una hora sin prisa",
                "caption": "Prepara el sueño con calma.",
                "visual_prompt": "Middle aged woman reading quietly on a sofa before bedtime",
                "motion": "gentle pan",
                "asset_refs": {},
            },
        ],
        "qa": {"verdict": "PENDING_GEMINI_QA"},
    }


def _prepare_promoted_script(
    job_dir: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
) -> None:
    create_job(job_dir, job_id="job-s1", channel_id="vida-plena-45", idea_path="idea.json")
    (job_dir / "idea.json").write_text(
        json.dumps(idea_payload, ensure_ascii=False), encoding="utf-8"
    )
    run_script_stage(job_dir, channel_path)
    promote_script_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_script_payload, ensure_ascii=False),
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
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "script_promote"


def test_run_script_stage_missing_idea_raises(tmp_path: Path, channel_path: Path):
    job_dir = tmp_path / "job-s2"
    create_job(job_dir, job_id="job-s2", channel_id="vida-plena-45", idea_path="idea.json")
    with pytest.raises(StageInputMissingError):
        run_script_stage(job_dir, channel_path)


def test_promote_script_stage_writes_raw_and_promoted_script(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    create_job(job_dir, job_id="job-s1", channel_id="vida-plena-45", idea_path="idea.json")
    (job_dir / "idea.json").write_text(
        json.dumps(idea_payload, ensure_ascii=False), encoding="utf-8"
    )
    run_script_stage(job_dir, channel_path)

    output = promote_script_stage(
        job_dir,
        channel_path,
        raw_response=f"```json\n{json.dumps(valid_script_payload, ensure_ascii=False)}\n```",
    )

    assert output == job_dir / "script.json"
    assert output.exists()
    assert (job_dir / SCRIPT_RAW_PATH).exists()
    promoted = json.loads(output.read_text(encoding="utf-8"))
    assert promoted["job_id"] == "job-s1"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "scenes"
    script_promote = next(s for s in state["stages"] if s["name"] == "script_promote")
    assert script_promote["status"] == "completed"

    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    completed = [e for e in events if e["event"] == "STAGE_COMPLETED"]
    assert any(e["data"]["stage"] == "script_promote" for e in completed)


def test_promote_script_stage_rejects_stale_raw_response(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    create_job(job_dir, job_id="job-s1", channel_id="vida-plena-45", idea_path="idea.json")
    (job_dir / "idea.json").write_text(
        json.dumps(idea_payload, ensure_ascii=False), encoding="utf-8"
    )
    run_script_stage(job_dir, channel_path)
    stale_payload = {**valid_script_payload, "job_id": "old-job"}

    with pytest.raises(StageInputMissingError, match="job_id mismatch"):
        promote_script_stage(job_dir, channel_path, raw_response=json.dumps(stale_payload))

    assert not (job_dir / "script.json").exists()


def test_run_scenes_stage_writes_prompt(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_script(job_dir, channel_path, idea_payload, valid_script_payload)

    output = run_scenes_stage(job_dir, channel_path)

    assert output == job_dir / SCENES_PROMPT_PATH
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "SCENES artifact" in text
    assert valid_script_payload["narration"] in text
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "scenes_promote"


def test_run_scenes_stage_missing_script_raises(tmp_path: Path, channel_path: Path):
    job_dir = tmp_path / "job-s1"
    create_job(job_dir, job_id="job-s1", channel_id="vida-plena-45", idea_path="idea.json")
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    state["current_stage"] = "scenes"
    (job_dir / "job.json").write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(StageInputMissingError, match="script.json"):
        run_scenes_stage(job_dir, channel_path)


def test_promote_scenes_stage_writes_raw_and_promoted_scenes(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_script(job_dir, channel_path, idea_payload, valid_script_payload)
    run_scenes_stage(job_dir, channel_path)

    output = promote_scenes_stage(
        job_dir,
        channel_path,
        raw_response=f"```json\n{json.dumps(valid_scenes_payload, ensure_ascii=False)}\n```",
    )

    assert output == job_dir / "scenes.json"
    assert output.exists()
    assert (job_dir / SCENES_RAW_PATH).exists()
    promoted = json.loads(output.read_text(encoding="utf-8"))
    assert promoted["job_id"] == "job-s1"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "seo"
    scenes_promote = next(s for s in state["stages"] if s["name"] == "scenes_promote")
    assert scenes_promote["status"] == "completed"


def test_promote_scenes_stage_rejects_stale_raw_response(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_script(job_dir, channel_path, idea_payload, valid_script_payload)
    run_scenes_stage(job_dir, channel_path)
    stale_payload = {**valid_scenes_payload, "job_id": "old-job"}

    with pytest.raises(StageInputMissingError, match="job_id mismatch"):
        promote_scenes_stage(job_dir, channel_path, raw_response=json.dumps(stale_payload))

    assert not (job_dir / "scenes.json").exists()


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
    assert state["current_stage"] == "script_promote"


def test_post_promote_script_via_http(client: TestClient, idea_payload: dict, valid_script_payload: dict):
    _create_job(client, "job-s1")
    response = client.post("/jobs/job-s1/idea", json=idea_payload)
    assert response.status_code == 201
    response = client.post("/jobs/job-s1/stages/script/run")
    assert response.status_code == 200

    response = client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "script.json"
    script_promote = next(s for s in body["state"]["stages"] if s["name"] == "script_promote")
    assert script_promote["status"] == "completed"
    assert body["state"]["current_stage"] == "scenes"


def test_post_run_scenes_via_http(
    client: TestClient,
    idea_payload: dict,
    valid_script_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )

    response = client.post("/jobs/job-s1/stages/scenes/run")

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "operator/chatgpt/scenes_prompt.md"
    scenes_stage = next(s for s in body["state"]["stages"] if s["name"] == "scenes")
    assert scenes_stage["status"] == "completed"
    assert body["state"]["current_stage"] == "scenes_promote"


def test_post_promote_scenes_via_http(
    client: TestClient,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    client.post("/jobs/job-s1/stages/scenes/run")

    response = client.post(
        "/jobs/job-s1/stages/scenes/promote",
        json={"raw_response": json.dumps(valid_scenes_payload, ensure_ascii=False)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "scenes.json"
    scenes_promote = next(s for s in body["state"]["stages"] if s["name"] == "scenes_promote")
    assert scenes_promote["status"] == "completed"
    assert body["state"]["current_stage"] == "seo"


def test_post_promote_scenes_invalid_raw_returns_409(
    client: TestClient,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    client.post("/jobs/job-s1/stages/scenes/run")
    stale_payload = {**valid_scenes_payload, "job_id": "old-job"}

    response = client.post(
        "/jobs/job-s1/stages/scenes/promote",
        json={"raw_response": json.dumps(stale_payload, ensure_ascii=False)},
    )

    assert response.status_code == 409
    assert "job_id mismatch" in response.json()["detail"]


def test_post_promote_script_invalid_raw_returns_409(
    client: TestClient,
    idea_payload: dict,
    valid_script_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    client.post("/jobs/job-s1/stages/script/run")
    stale_payload = {**valid_script_payload, "job_id": "old-job"}

    response = client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(stale_payload, ensure_ascii=False)},
    )

    assert response.status_code == 409
    assert "job_id mismatch" in response.json()["detail"]


def test_run_script_without_idea_returns_409(client: TestClient):
    _create_job(client, "job-no-idea")
    response = client.post("/jobs/job-no-idea/stages/script/run")
    assert response.status_code == 409


def test_post_idea_unknown_job_returns_404(client: TestClient, idea_payload: dict):
    response = client.post("/jobs/missing/idea", json=idea_payload)
    assert response.status_code == 404
