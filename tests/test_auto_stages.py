from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.contracts import repo_root
from video_agent.orchestrator import create_job
from video_agent.orchestrator.browser_client import (
    BrowserClient,
    BrowserClientError,
    LoginRequiredFromWorker,
)
from video_agent.orchestrator.stages import (
    SCENES_PROMPT_PATH,
    SCRIPT_PROMPT_PATH,
    SEO_PROMPT_PATH,
    StageInputMissingError,
    auto_scenes_stage,
    auto_script_stage,
    auto_seo_stage,
    promote_scenes_stage,
    promote_script_stage,
    run_scenes_stage,
    run_script_stage,
)
from video_agent.web.app import (
    app,
    get_browser_client,
    get_channel_path,
    get_jobs_root,
)


# ---------- fixtures shared with existing script tests ----------------------


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
        "job_id": "job-auto",
        "hook": "Dormir mejor empieza con una decisión simple.",
        "sections": [
            {"title": "Calma", "text": "Baja el ritmo una hora antes de acostarte."}
        ],
        "narration": "Dormir mejor empieza con una decisión simple. Baja el ritmo una hora antes de acostarte.",
        "cta": "Prueba este hábito esta noche.",
        "qa": {"verdict": "PENDING_GEMINI_QA"},
    }


@pytest.fixture
def valid_scenes_payload() -> dict:
    return {
        "channel_id": "vida-plena-45",
        "job_id": "job-auto",
        "total_duration_sec": 48,
        "scenes": [
            {
                "id": "scene-01",
                "duration_sec": 24,
                "narration": "Dormir mejor empieza con una decisión simple.",
                "on_screen_text": "Respira y baja el ritmo",
                "caption": "Un hábito sencillo para descansar mejor.",
                "visual_prompt": "Calm evening bedroom with warm lamp light",
                "motion": "slow push-in",
                "asset_refs": {},
            },
            {
                "id": "scene-02",
                "duration_sec": 24,
                "narration": "Baja el ritmo una hora antes de acostarte.",
                "on_screen_text": "Una hora sin prisa",
                "caption": "Prepara el sueño con calma.",
                "visual_prompt": "Middle aged woman reading quietly",
                "motion": "gentle pan",
                "asset_refs": {},
            },
        ],
        "qa": {"verdict": "PENDING_GEMINI_QA"},
    }


@pytest.fixture
def valid_seo_payload() -> dict:
    return {
        "job_id": "job-auto",
        "title": "5 hábitos nocturnos para dormir mejor después de los 45",
        "description": "Rutina suave y realista para descansar mejor por la noche.",
        "tags": [
            "dormir mejor",
            "rutina nocturna",
            "vida plena 45",
            "bienestar 45",
            "descanso",
        ],
        "language": "es-419",
        "ai_disclosure": True,
        "thumbnail_path": "thumbnail.jpg",
    }


# ---------- fake client ----------------------------------------------------


class FakeBrowserClient:
    """In-process double for the browser-worker."""

    def __init__(self, queue: list[str] | None = None) -> None:
        self.queue = list(queue or [])
        self.calls: list[str] = []
        self.error: Exception | None = None

    async def chatgpt_send(
        self, prompt: str, *, response_timeout_ms: int = 180_000
    ) -> str:
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        if not self.queue:
            raise AssertionError("FakeBrowserClient out of canned responses")
        return self.queue.pop(0)


# ---------- direct module-level auto stage tests ---------------------------


def _seed_script(job_dir: Path, channel_path: Path, idea_payload: dict) -> None:
    create_job(
        job_dir,
        job_id="job-auto",
        channel_id="vida-plena-45",
        idea_path="idea.json",
    )
    (job_dir / "idea.json").write_text(
        json.dumps(idea_payload, ensure_ascii=False), encoding="utf-8"
    )


def test_auto_script_runs_prompt_and_promote(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    fake = FakeBrowserClient(
        queue=[json.dumps(valid_script_payload, ensure_ascii=False)]
    )

    output = asyncio.run(
        auto_script_stage(job_dir, channel_path, fake.chatgpt_send)
    )

    assert output == job_dir / "script.json"
    assert (job_dir / SCRIPT_PROMPT_PATH).exists()
    promoted = json.loads(output.read_text(encoding="utf-8"))
    assert promoted["job_id"] == "job-auto"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "scenes"
    assert len(fake.calls) == 1
    assert "SCRIPT artifact" in fake.calls[0]


def test_auto_script_skips_runner_when_already_promote(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    run_script_stage(job_dir, channel_path)  # advances to script_promote
    fake = FakeBrowserClient(
        queue=[json.dumps(valid_script_payload, ensure_ascii=False)]
    )

    output = asyncio.run(
        auto_script_stage(job_dir, channel_path, fake.chatgpt_send)
    )

    assert output == job_dir / "script.json"
    assert len(fake.calls) == 1  # runner not re-invoked, only one fetch


def test_auto_script_rejects_empty_worker_response(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
):
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    fake = FakeBrowserClient(queue=["   "])

    with pytest.raises(StageInputMissingError, match="empty response"):
        asyncio.run(
            auto_script_stage(job_dir, channel_path, fake.chatgpt_send)
        )


def test_auto_scenes_runs_after_script_promoted(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
):
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    run_script_stage(job_dir, channel_path)
    promote_script_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_script_payload, ensure_ascii=False),
    )
    fake = FakeBrowserClient(
        queue=[json.dumps(valid_scenes_payload, ensure_ascii=False)]
    )

    output = asyncio.run(
        auto_scenes_stage(job_dir, channel_path, fake.chatgpt_send)
    )

    assert output == job_dir / "scenes.json"
    assert (job_dir / SCENES_PROMPT_PATH).exists()
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "seo"


def test_auto_seo_runs_after_scenes_promoted(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
):
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    run_script_stage(job_dir, channel_path)
    promote_script_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_script_payload, ensure_ascii=False),
    )
    run_scenes_stage(job_dir, channel_path)
    promote_scenes_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_scenes_payload, ensure_ascii=False),
    )
    fake = FakeBrowserClient(
        queue=[json.dumps(valid_seo_payload, ensure_ascii=False)]
    )

    output = asyncio.run(
        auto_seo_stage(job_dir, channel_path, fake.chatgpt_send)
    )

    assert output == job_dir / "seo.json"
    assert (job_dir / SEO_PROMPT_PATH).exists()
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "render"


def test_auto_script_wrong_stage_raises(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    run_script_stage(job_dir, channel_path)
    promote_script_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_script_payload, ensure_ascii=False),
    )
    fake = FakeBrowserClient(queue=["irrelevant"])

    with pytest.raises(StageInputMissingError, match="Cannot auto-run"):
        asyncio.run(
            auto_script_stage(job_dir, channel_path, fake.chatgpt_send)
        )


# ---------- HTTP tests ------------------------------------------------------


@pytest.fixture
def http_client(tmp_path: Path, channel_path: Path):
    app.dependency_overrides[get_jobs_root] = lambda: tmp_path
    app.dependency_overrides[get_channel_path] = lambda: channel_path
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_job(client: TestClient, job_id: str = "job-auto") -> None:
    r = client.post(
        "/jobs",
        json={
            "job_id": job_id,
            "channel_id": "vida-plena-45",
            "idea_path": "idea.json",
        },
    )
    assert r.status_code == 201, r.text


def test_http_auto_script(
    http_client: TestClient,
    idea_payload: dict,
    valid_script_payload: dict,
):
    _create_job(http_client)
    http_client.post("/jobs/job-auto/idea", json=idea_payload)

    fake = FakeBrowserClient(
        queue=[json.dumps(valid_script_payload, ensure_ascii=False)]
    )
    app.dependency_overrides[get_browser_client] = lambda: fake

    try:
        r = http_client.post("/jobs/job-auto/stages/script/auto")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output"] == "script.json"
    assert body["state"]["current_stage"] == "scenes"
    assert len(fake.calls) == 1


def test_http_auto_script_login_required_returns_409(
    http_client: TestClient,
    idea_payload: dict,
):
    _create_job(http_client)
    http_client.post("/jobs/job-auto/idea", json=idea_payload)

    fake = FakeBrowserClient()
    fake.error = LoginRequiredFromWorker(
        "ChatGPT signed out",
        status_code=409,
        detail={"login_required": True, "error": "x"},
    )
    app.dependency_overrides[get_browser_client] = lambda: fake

    try:
        r = http_client.post("/jobs/job-auto/stages/script/auto")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 409
    assert r.json()["detail"]["login_required"] is True


def test_http_auto_script_worker_error_returns_502(
    http_client: TestClient,
    idea_payload: dict,
):
    _create_job(http_client)
    http_client.post("/jobs/job-auto/idea", json=idea_payload)

    fake = FakeBrowserClient()
    fake.error = BrowserClientError(
        "selector failed", status_code=502, detail={"error": "boom"}
    )
    app.dependency_overrides[get_browser_client] = lambda: fake

    try:
        r = http_client.post("/jobs/job-auto/stages/script/auto")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["browser_worker_status"] == 502


def test_http_auto_script_unknown_job_returns_404(http_client: TestClient):
    r = http_client.post("/jobs/missing/stages/script/auto")
    assert r.status_code == 404


def test_browser_client_default_base_url(monkeypatch):
    monkeypatch.delenv("BROWSER_WORKER_URL", raising=False)
    client = BrowserClient()
    assert client.base_url == "http://browser-worker:8001"


def test_browser_client_env_override(monkeypatch):
    monkeypatch.setenv("BROWSER_WORKER_URL", "http://other:9999/")
    client = BrowserClient()
    assert client.base_url == "http://other:9999"


# ---------- /run-all -------------------------------------------------------


def test_http_run_all_success(
    http_client: TestClient,
    monkeypatch,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
):
    _create_job(http_client)
    http_client.post("/jobs/job-auto/idea", json=idea_payload)

    fake = FakeBrowserClient(
        queue=[
            json.dumps(valid_script_payload, ensure_ascii=False),
            json.dumps(valid_scenes_payload, ensure_ascii=False),
            json.dumps(valid_seo_payload, ensure_ascii=False),
        ]
    )
    app.dependency_overrides[get_browser_client] = lambda: fake

    # Render and review are expensive; stub them so the route exercise
    # only verifies orchestration, not Remotion.
    def fake_render(job_dir, channel_path):
        out = job_dir / "video.mp4"
        out.write_bytes(b"fake")
        # Mirror what the real stage does so /run-all sees the state advance.
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "render", out)
        return out

    def fake_review(job_dir):
        out = job_dir / "operator_review.html"
        out.write_text("<html/>", encoding="utf-8")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "review", out)
        return out

    monkeypatch.setattr(
        "video_agent.web.app.run_render_stage", fake_render
    )
    monkeypatch.setattr(
        "video_agent.web.app.run_review_stage", fake_review
    )

    try:
        r = http_client.post("/jobs/job-auto/run-all")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200, r.text
    body = r.json()
    stages = [c["stage"] for c in body["completed"]]
    assert stages == [
        "script_promote",
        "scenes_promote",
        "seo_promote",
        "render",
        "review",
    ]
    # All 8 underlying stages should be completed now.
    assert all(s["status"] == "completed" for s in body["state"]["stages"])
    assert len(fake.calls) == 3  # one ChatGPT call per auto stage


def test_http_run_all_stops_on_worker_error(
    http_client: TestClient,
    idea_payload: dict,
):
    _create_job(http_client)
    http_client.post("/jobs/job-auto/idea", json=idea_payload)

    fake = FakeBrowserClient()
    fake.error = BrowserClientError(
        "selector failed", status_code=502, detail={"error": "boom"}
    )
    app.dependency_overrides[get_browser_client] = lambda: fake

    try:
        r = http_client.post("/jobs/job-auto/run-all")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["completed"] == []  # nothing finished before the failure
    assert detail["stopped_at"] == "script_promote"
    assert "state" in detail


def test_http_run_all_unknown_job_returns_404(http_client: TestClient):
    r = http_client.post("/jobs/missing/run-all")
    assert r.status_code == 404
