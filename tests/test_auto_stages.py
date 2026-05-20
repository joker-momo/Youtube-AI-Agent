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
    """In-process double for the browser-worker session API."""

    def __init__(self, queue: list[str] | None = None) -> None:
        self.queue = list(queue or [])
        self.sessions: list[list[str]] = []
        self.error: Exception | None = None

    async def chatgpt_send(
        self, prompt: str, *, response_timeout_ms: int = 180_000
    ) -> str:
        # Compatibility shim for the older one-shot tests.
        return await self.run_session("chatgpt", [prompt])

    async def run_session(
        self,
        site: str,
        messages,
        *,
        response_timeout_ms: int = 180_000,
    ) -> str:
        self.sessions.append(list(messages))
        if self.error is not None:
            raise self.error
        if not self.queue:
            raise AssertionError("FakeBrowserClient out of canned responses")
        return self.queue.pop(0)

    @property
    def calls(self) -> list[list[str]]:
        return self.sessions

    async def open_persistent_session(self, site: str):
        # All persistent sends go into one "session" list per call
        # so the test still asserts each stage's [briefing, task].
        async def sender(messages, *, response_timeout_ms: int = 180_000) -> str:
            return await self.run_session(site, messages)

        async def closer() -> None:
            return None

        return sender, closer


# ---------- direct module-level auto stage tests ---------------------------


def _fake_pass_qa(job_dir: Path, artifact: str) -> None:
    """Mark <artifact>_qa as PASS and advance current_stage.

    Lets tests that only care about the ChatGPT chain skip the Gemini
    QA stage that DEFAULT_STAGES now inserts between each promote and
    the next prompt stage.
    """
    from video_agent.orchestrator.job_state import load_job, save_job
    stage_name = f"{artifact}_qa"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        return
    stage = state.stage(stage_name)
    stage.status = "completed"
    next_pending = next((s for s in state.stages if s.status == "pending"), None)
    if next_pending is not None:
        state.current_stage = next_pending.name
    save_job(job_dir, state)
    qa_dir = job_dir / "operator" / "gemini"
    qa_dir.mkdir(parents=True, exist_ok=True)
    (qa_dir / f"{artifact}_qa.json").write_text(
        json.dumps(
            {
                "verdict": "PASS",
                "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5},
                "issues": [],
                "required_changes": [],
            }
        ),
        encoding="utf-8",
    )


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
        auto_script_stage(job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs))
    )

    assert output == job_dir / "script.json"
    assert (job_dir / SCRIPT_PROMPT_PATH).exists()
    promoted = json.loads(output.read_text(encoding="utf-8"))
    assert promoted["job_id"] == "job-auto"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "script_qa"
    assert len(fake.calls) == 1
    # Auto stage now sends only the task; briefing is the caller's job.
    assert len(fake.calls[0]) == 1
    assert "SCRIPT artifact" in fake.calls[0][0]


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
        auto_script_stage(job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs))
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
            auto_script_stage(job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs))
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
    _fake_pass_qa(job_dir, "script")
    fake = FakeBrowserClient(
        queue=[json.dumps(valid_scenes_payload, ensure_ascii=False)]
    )

    output = asyncio.run(
        auto_scenes_stage(job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs))
    )

    assert output == job_dir / "scenes.json"
    assert (job_dir / SCENES_PROMPT_PATH).exists()
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "scenes_qa"


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
    _fake_pass_qa(job_dir, "script")
    run_scenes_stage(job_dir, channel_path)
    promote_scenes_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_scenes_payload, ensure_ascii=False),
    )
    _fake_pass_qa(job_dir, "scenes")
    fake = FakeBrowserClient(
        queue=[json.dumps(valid_seo_payload, ensure_ascii=False)]
    )

    output = asyncio.run(
        auto_seo_stage(job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs))
    )

    assert output == job_dir / "seo.json"
    assert (job_dir / SEO_PROMPT_PATH).exists()
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "seo_qa"


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
            auto_script_stage(job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs))
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
    assert body["state"]["current_stage"] == "script_qa"
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


def test_browser_client_extends_http_timeout_past_worker_timeout():
    client = BrowserClient(request_timeout=300.0)

    assert client._timeout_for_response(300_000) == 330.0


def test_browser_client_keeps_larger_configured_http_timeout():
    client = BrowserClient(request_timeout=420.0)

    assert client._timeout_for_response(300_000) == 420.0


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

    qa_pass = json.dumps(
        {
            "verdict": "PASS",
            "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5},
            "issues": [],
            "required_changes": [],
        }
    )
    fake = FakeBrowserClient(
        queue=[
            "OK",  # chatgpt briefing ack
            "OK",  # gemini briefing ack
            json.dumps(valid_script_payload, ensure_ascii=False),
            qa_pass,
            json.dumps(valid_scenes_payload, ensure_ascii=False),
            qa_pass,
            json.dumps(valid_seo_payload, ensure_ascii=False),
            qa_pass,
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
        "script_qa",
        "scenes_promote",
        "scenes_qa",
        "seo_promote",
        "seo_qa",
        "render",
        "review",
    ]
    assert all(s["status"] == "completed" for s in body["state"]["stages"])
    # 2 briefing sends + 3 ChatGPT task sends + 3 Gemini task sends = 8
    assert len(fake.calls) == 8


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
    assert detail["completed"] == []
    # Persistent-session /run-all fails on the very first briefing
    # send, before run_script_stage advances the state.
    assert detail["stopped_at"] == "script"
    assert "state" in detail


def test_http_run_all_unknown_job_returns_404(http_client: TestClient):
    r = http_client.post("/jobs/missing/run-all")
    assert r.status_code == 404


# ---------- Gemini QA stages -----------------------------------------------


def _qa_pass_payload() -> dict:
    return {
        "verdict": "PASS",
        "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5},
        "issues": [],
        "required_changes": [],
    }


def _qa_fail_payload(issues: list[str]) -> dict:
    return {
        "verdict": "NEEDS_REWORK",
        "scores": {"schema_fit": 3, "channel_fit": 3, "safety": 4, "clarity": 4},
        "issues": issues,
        "required_changes": ["Rework upstream artifact"],
    }


def _advance_through_script_promote(
    job_dir: Path, channel_path: Path, idea_payload: dict, script_payload: dict
) -> None:
    _seed_script(job_dir, channel_path, idea_payload)
    run_script_stage(job_dir, channel_path)
    promote_script_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(script_payload, ensure_ascii=False),
    )


def test_auto_script_qa_pass_advances_to_scenes(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    from video_agent.orchestrator.stages import auto_script_qa_stage

    job_dir = tmp_path / "job-auto"
    _advance_through_script_promote(
        job_dir, channel_path, idea_payload, valid_script_payload
    )
    fake = FakeBrowserClient(queue=[json.dumps(_qa_pass_payload())])

    output = asyncio.run(
        auto_script_qa_stage(
            job_dir,
            channel_path,
            lambda msgs: fake.run_session("gemini", msgs),
        )
    )

    assert output == job_dir / "operator" / "gemini" / "script_qa.json"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "scenes"
    assert any(s["name"] == "script_qa" and s["status"] == "completed" for s in state["stages"])


def test_auto_script_qa_fail_raises_with_issues(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    from video_agent.orchestrator.stages import auto_script_qa_stage

    job_dir = tmp_path / "job-auto"
    _advance_through_script_promote(
        job_dir, channel_path, idea_payload, valid_script_payload
    )
    fake = FakeBrowserClient(
        queue=[json.dumps(_qa_fail_payload(["tono fuera de marca"]))]
    )

    with pytest.raises(StageInputMissingError, match="NEEDS_REWORK"):
        asyncio.run(
            auto_script_qa_stage(
                job_dir,
                channel_path,
                lambda msgs: fake.run_session("gemini", msgs),
            )
        )
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "script_qa"  # not advanced


def test_auto_qa_with_rework_passes_after_one_retry(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    from video_agent.orchestrator.stages import auto_qa_with_rework

    job_dir = tmp_path / "job-auto"
    _advance_through_script_promote(
        job_dir, channel_path, idea_payload, valid_script_payload
    )

    # First QA call: NEEDS_REWORK. Then ChatGPT rework returns a valid
    # new script payload (same shape, fine for promoter). Second QA
    # call: PASS.
    rework_script = dict(valid_script_payload)
    rework_script["hook"] = "Reworked hook con pequeños ajustes prácticos."
    fake = FakeBrowserClient(
        queue=[
            json.dumps(_qa_fail_payload(["narration too long"])),
            json.dumps(rework_script, ensure_ascii=False),
            json.dumps(_qa_pass_payload()),
        ]
    )

    output = asyncio.run(
        auto_qa_with_rework(
            "script",
            job_dir,
            channel_path,
            chatgpt_fn=lambda msgs: fake.run_session("chatgpt", msgs),
            gemini_fn=lambda msgs: fake.run_session("gemini", msgs),
        )
    )
    assert output == job_dir / "operator" / "gemini" / "script_qa.json"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    script_qa = next(s for s in state["stages"] if s["name"] == "script_qa")
    assert script_qa["status"] == "completed"
    # Three sessions: failing QA, rework chat, passing QA.
    assert len(fake.calls) == 3


def test_auto_qa_with_rework_gives_up_after_max_retries(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    from video_agent.orchestrator.stages import auto_qa_with_rework

    job_dir = tmp_path / "job-auto"
    _advance_through_script_promote(
        job_dir, channel_path, idea_payload, valid_script_payload
    )

    # Always fail QA; channel.yaml default max_retry_per_qa=3 so the
    # sequence is qa, rework, qa, rework, qa, rework, qa -> 4 QA + 3
    # reworks = 7 sessions before raising.
    rework_script = dict(valid_script_payload)
    rework_script["hook"] = "Rework attempt"
    fake = FakeBrowserClient(
        queue=[
            json.dumps(_qa_fail_payload(["still broken"])),
            json.dumps(rework_script, ensure_ascii=False),
            json.dumps(_qa_fail_payload(["still broken"])),
            json.dumps(rework_script, ensure_ascii=False),
            json.dumps(_qa_fail_payload(["still broken"])),
            json.dumps(rework_script, ensure_ascii=False),
            json.dumps(_qa_fail_payload(["still broken"])),
        ]
    )
    with pytest.raises(StageInputMissingError, match="NEEDS_REWORK"):
        asyncio.run(
            auto_qa_with_rework(
                "script",
                job_dir,
                channel_path,
                chatgpt_fn=lambda msgs: fake.run_session("chatgpt", msgs),
                gemini_fn=lambda msgs: fake.run_session("gemini", msgs),
            )
        )
    assert len(fake.calls) == 7


def test_promote_qa_stage_rejects_non_pass(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    from video_agent.orchestrator.stages import promote_qa_stage

    job_dir = tmp_path / "job-auto"
    _advance_through_script_promote(
        job_dir, channel_path, idea_payload, valid_script_payload
    )
    with pytest.raises(StageInputMissingError, match="NEEDS_REWORK"):
        promote_qa_stage(
            job_dir,
            "script",
            json.dumps(_qa_fail_payload(["issue"])),
        )
