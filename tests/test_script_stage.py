from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.contracts import repo_root
from video_agent.orchestrator import create_job
from video_agent.orchestrator.queue import JobQueue
from video_agent.orchestrator.stages import (
    SCENES_PROMPT_PATH,
    SCENES_RAW_PATH,
    SEO_PROMPT_PATH,
    SEO_RAW_PATH,
    SCRIPT_PROMPT_PATH,
    SCRIPT_RAW_PATH,
    StageInputMissingError,
    promote_scenes_stage,
    promote_seo_stage,
    promote_script_stage,
    run_render_stage,
    run_scenes_stage,
    run_seo_stage,
    run_script_stage,
    run_review_stage,
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


@pytest.fixture
def valid_seo_payload() -> dict:
    return {
        "job_id": "job-s1",
        "title": "Duerme Mejor Con Un Hábito Sencillo",
        "description": "Una guía breve de Vida Plena 45+ para preparar el descanso con calma.",
        "tags": [
            "sueño saludable",
            "descanso",
            "bienestar 45+",
            "hábitos saludables",
            "vida plena",
        ],
        "language": "es-ES",
        "ai_disclosure": True,
        "thumbnail_path": "thumbnail.jpg",
    }


def _fake_pass_qa(job_dir: Path, artifact: str) -> None:
    from video_agent.orchestrator.job_state import load_job, save_job

    stage_name = f"{artifact}_qa"
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        return
    stage = state.stage(stage_name)
    stage.status = "completed"
    nxt = next((s for s in state.stages if s.status == "pending"), None)
    if nxt is not None:
        state.current_stage = nxt.name
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


def _fake_pass_stage(job_dir: Path, stage_name: str) -> None:
    """Mark any single stage as completed and advance current_stage."""
    from video_agent.orchestrator.job_state import load_job, save_job

    state = load_job(job_dir)
    if state.current_stage != stage_name:
        return
    stage = state.stage(stage_name)
    stage.status = "completed"
    nxt = next((s for s in state.stages if s.status == "pending"), None)
    if nxt is not None:
        state.current_stage = nxt.name
    save_job(job_dir, state)


def _fake_pass_idea_research(job_dir: Path) -> None:
    _fake_pass_stage(job_dir, "idea_research")


def _fake_pass_assets_chatgpt(job_dir: Path) -> None:
    """Mark assets_chatgpt as completed and advance to whisper stage."""
    _fake_pass_stage(job_dir, "assets_chatgpt")


def _fake_pass_thumbnail_image(job_dir: Path) -> None:
    """Advance the long-form graphic_images sidecar (if pending) then thumbnail_image.
    (_fake_pass_stage is a no-op when the named stage is not current.)"""
    _fake_pass_stage(job_dir, "graphic_images")
    _fake_pass_stage(job_dir, "thumbnail_image")


def _fake_pass_whisper_timestamps(job_dir: Path) -> None:
    """Mark whisper_timestamps + the long-form visual_schedule sidecar completed,
    advancing to render (visual_schedule now sits between whisper and render)."""
    _fake_pass_stage(job_dir, "whisper_timestamps")
    _fake_pass_stage(job_dir, "visual_schedule")


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
    _fake_pass_idea_research(job_dir)
    run_script_stage(job_dir, channel_path)
    promote_script_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_script_payload, ensure_ascii=False),
    )
    _fake_pass_qa(job_dir, "script")


def _prepare_promoted_scenes(
    job_dir: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
) -> None:
    _prepare_promoted_script(job_dir, channel_path, idea_payload, valid_script_payload)
    run_scenes_stage(job_dir, channel_path)
    promote_scenes_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_scenes_payload, ensure_ascii=False),
    )
    _fake_pass_qa(job_dir, "scenes")
    # Long-form visual_spans (report-only sidecar) now runs between scenes_qa and
    # seo; advance past it so downstream seo/render/review setups land on seo.
    _fake_pass_stage(job_dir, "visual_spans")


def test_run_script_stage_writes_prompt(tmp_path: Path, channel_path: Path, idea_payload: dict):
    job_dir = tmp_path / "job-s1"
    create_job(job_dir, job_id="job-s1", channel_id="vida-plena-45", idea_path="idea.json")
    (job_dir / "idea.json").write_text(
        json.dumps(idea_payload, ensure_ascii=False), encoding="utf-8"
    )
    _fake_pass_idea_research(job_dir)
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
    _fake_pass_idea_research(job_dir)
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
    _fake_pass_idea_research(job_dir)
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
    assert state["current_stage"] == "script_qa"
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
    _fake_pass_idea_research(job_dir)
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
    assert state["current_stage"] == "scenes_qa"
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


def test_run_seo_stage_writes_prompt(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_scenes(
        job_dir, channel_path, idea_payload, valid_script_payload, valid_scenes_payload
    )

    output = run_seo_stage(job_dir, channel_path)

    assert output == job_dir / SEO_PROMPT_PATH
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "SEO artifact" in text
    assert valid_script_payload["narration"] in text
    assert valid_scenes_payload["scenes"][0]["visual_prompt"] in text
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "seo_promote"


def test_run_seo_stage_missing_scenes_raises(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_script(job_dir, channel_path, idea_payload, valid_script_payload)
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    state["current_stage"] = "seo"
    (job_dir / "job.json").write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(StageInputMissingError, match="scenes.json"):
        run_seo_stage(job_dir, channel_path)


def test_promote_seo_stage_writes_raw_and_promoted_seo(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_scenes(
        job_dir, channel_path, idea_payload, valid_script_payload, valid_scenes_payload
    )
    run_seo_stage(job_dir, channel_path)

    output = promote_seo_stage(
        job_dir,
        channel_path,
        raw_response=f"```json\n{json.dumps(valid_seo_payload, ensure_ascii=False)}\n```",
    )

    assert output == job_dir / "seo.json"
    assert output.exists()
    assert (job_dir / SEO_RAW_PATH).exists()
    promoted = json.loads(output.read_text(encoding="utf-8"))
    assert promoted["job_id"] == "job-s1"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "seo_qa"
    seo_promote = next(s for s in state["stages"] if s["name"] == "seo_promote")
    assert seo_promote["status"] == "completed"


def test_promote_seo_stage_rejects_stale_raw_response(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_scenes(
        job_dir, channel_path, idea_payload, valid_script_payload, valid_scenes_payload
    )
    run_seo_stage(job_dir, channel_path)
    stale_payload = {**valid_seo_payload, "job_id": "old-job"}

    with pytest.raises(StageInputMissingError, match="job_id mismatch"):
        promote_seo_stage(job_dir, channel_path, raw_response=json.dumps(stale_payload))

    assert not (job_dir / "seo.json").exists()


def test_run_render_stage_uses_operator_render_without_qa_gate(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_scenes(
        job_dir, channel_path, idea_payload, valid_script_payload, valid_scenes_payload
    )
    run_seo_stage(job_dir, channel_path)
    promote_seo_stage(job_dir, channel_path, raw_response=json.dumps(valid_seo_payload))
    _fake_pass_qa(job_dir, "seo")
    _fake_pass_assets_chatgpt(job_dir)
    _fake_pass_thumbnail_image(job_dir)
    _fake_pass_whisper_timestamps(job_dir)
    calls = []

    def fake_render_operator_job(options):
        calls.append(options)
        for filename in [
            "render_props.json",
            "visual_review.json",
            "visual_contact_sheet.jpg",
            "thumbnail.jpg",
            "video.mp4",
            "report.md",
        ]:
            (job_dir / filename).write_text("ok", encoding="utf-8")
        return object()

    monkeypatch.setattr("video_agent.orchestrator.stages.render_operator_job", fake_render_operator_job)

    output = run_render_stage(job_dir, channel_path)

    assert output == job_dir / "video.mp4"
    assert calls[0].channel_path == channel_path
    assert calls[0].job_dir == job_dir
    assert calls[0].render is True
    assert calls[0].require_operator_qa is False
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    # render now advances to the long-form render_continuity_qa sidecar before review.
    assert state["current_stage"] == "render_continuity_qa"
    render_stage = next(s for s in state["stages"] if s["name"] == "render")
    assert render_stage["status"] == "completed"


def test_run_review_stage_writes_review_and_completes_job(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    job_dir = tmp_path / "job-s1"
    _prepare_promoted_scenes(
        job_dir, channel_path, idea_payload, valid_script_payload, valid_scenes_payload
    )
    run_seo_stage(job_dir, channel_path)
    promote_seo_stage(job_dir, channel_path, raw_response=json.dumps(valid_seo_payload))
    _fake_pass_qa(job_dir, "seo")
    _fake_pass_assets_chatgpt(job_dir)
    _fake_pass_thumbnail_image(job_dir)
    _fake_pass_whisper_timestamps(job_dir)
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    next(s for s in state["stages"] if s["name"] == "render")["status"] = "completed"
    next(s for s in state["stages"] if s["name"] == "render_continuity_qa")["status"] = "completed"
    state["current_stage"] = "review"
    (job_dir / "job.json").write_text(json.dumps(state), encoding="utf-8")

    def fake_write_operator_review(actual_job_dir):
        output = actual_job_dir / "operator_review.html"
        output.write_text("<html>review</html>", encoding="utf-8")
        return output

    monkeypatch.setattr("video_agent.orchestrator.stages.write_operator_review", fake_write_operator_review)

    output = run_review_stage(job_dir)

    assert output == job_dir / "operator_review.html"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "review"
    assert all(s["status"] == "completed" for s in state["stages"])
    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events[-1]["event"] == "JOB_COMPLETED"


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


def test_post_idea_then_run_script_via_http(client: TestClient, tmp_path: Path, idea_payload: dict):
    _create_job(client, "job-http")
    response = client.post("/jobs/job-http/idea", json=idea_payload)
    assert response.status_code == 201
    _fake_pass_idea_research(tmp_path / "job-http")

    response = client.post("/jobs/job-http/stages/script/run")
    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "operator/chatgpt/script_prompt.md"
    state = body["state"]
    script_stage = next(s for s in state["stages"] if s["name"] == "script")
    assert script_stage["status"] == "completed"
    assert state["current_stage"] == "script_promote"


def test_post_render_async_enqueues_stage_command(client: TestClient, tmp_path: Path):
    _create_job(client, "job-render-async")

    response = client.post("/jobs/job-render-async/stages/render/run?async=true")

    assert response.status_code == 200
    assert response.json()["status"] == "enqueued"
    assert response.json()["command"] == "stage_render"
    queued = JobQueue(tmp_path / "queue.db").get_job("job-render-async")
    assert queued is not None
    assert queued["command"] == "stage_render"


def test_post_whisper_async_enqueues_stage_command(client: TestClient, tmp_path: Path):
    _create_job(client, "job-whisper-async")

    response = client.post("/jobs/job-whisper-async/stages/whisper_timestamps/run?async=true")

    assert response.status_code == 200
    assert response.json()["status"] == "enqueued"
    assert response.json()["command"] == "stage_whisper_timestamps"
    queued = JobQueue(tmp_path / "queue.db").get_job("job-whisper-async")
    assert queued is not None
    assert queued["command"] == "stage_whisper_timestamps"


def test_post_thumbnail_async_enqueues_stage_command(client: TestClient, tmp_path: Path):
    _create_job(client, "job-thumbnail-async")

    response = client.post("/jobs/job-thumbnail-async/stages/thumbnail_image/auto?async=true")

    assert response.status_code == 200
    assert response.json()["status"] == "enqueued"
    assert response.json()["command"] == "stage_thumbnail_image_auto"
    queued = JobQueue(tmp_path / "queue.db").get_job("job-thumbnail-async")
    assert queued is not None
    assert queued["command"] == "stage_thumbnail_image_auto"


def test_post_promote_script_via_http(client: TestClient, tmp_path: Path, idea_payload: dict, valid_script_payload: dict):
    _create_job(client, "job-s1")
    response = client.post("/jobs/job-s1/idea", json=idea_payload)
    assert response.status_code == 201
    _fake_pass_idea_research(tmp_path / "job-s1")
    response = client.post("/jobs/job-s1/stages/script/run")
    assert response.status_code == 200

    response = client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "json/script.json"
    script_promote = next(s for s in body["state"]["stages"] if s["name"] == "script_promote")
    assert script_promote["status"] == "completed"
    assert body["state"]["current_stage"] == "script_qa"


def test_post_run_scenes_via_http(
    client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    _fake_pass_idea_research(tmp_path / "job-s1")
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "script")

    response = client.post("/jobs/job-s1/stages/scenes/run")

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "operator/chatgpt/scenes_prompt.md"
    scenes_stage = next(s for s in body["state"]["stages"] if s["name"] == "scenes")
    assert scenes_stage["status"] == "completed"
    assert body["state"]["current_stage"] == "scenes_promote"


def test_post_promote_scenes_via_http(
    client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    _fake_pass_idea_research(tmp_path / "job-s1")
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "script")
    client.post("/jobs/job-s1/stages/scenes/run")

    response = client.post(
        "/jobs/job-s1/stages/scenes/promote",
        json={"raw_response": json.dumps(valid_scenes_payload, ensure_ascii=False)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "json/scenes.json"
    scenes_promote = next(s for s in body["state"]["stages"] if s["name"] == "scenes_promote")
    assert scenes_promote["status"] == "completed"
    assert body["state"]["current_stage"] == "scenes_qa"


def test_post_promote_scenes_invalid_raw_returns_409(
    client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    _fake_pass_idea_research(tmp_path / "job-s1")
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "script")
    client.post("/jobs/job-s1/stages/scenes/run")
    stale_payload = {**valid_scenes_payload, "job_id": "old-job"}

    response = client.post(
        "/jobs/job-s1/stages/scenes/promote",
        json={"raw_response": json.dumps(stale_payload, ensure_ascii=False)},
    )

    assert response.status_code == 409
    assert "job_id mismatch" in response.json()["detail"]


def test_post_run_seo_via_http(
    client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    _fake_pass_idea_research(tmp_path / "job-s1")
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "script")
    client.post("/jobs/job-s1/stages/scenes/run")
    client.post(
        "/jobs/job-s1/stages/scenes/promote",
        json={"raw_response": json.dumps(valid_scenes_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "scenes")
    _fake_pass_stage(tmp_path / "job-s1", "visual_spans")

    response = client.post("/jobs/job-s1/stages/seo/run")

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "operator/chatgpt/seo_prompt.md"
    seo_stage = next(s for s in body["state"]["stages"] if s["name"] == "seo")
    assert seo_stage["status"] == "completed"
    assert body["state"]["current_stage"] == "seo_promote"


def test_post_promote_seo_via_http(
    client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    _fake_pass_idea_research(tmp_path / "job-s1")
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "script")
    client.post("/jobs/job-s1/stages/scenes/run")
    client.post(
        "/jobs/job-s1/stages/scenes/promote",
        json={"raw_response": json.dumps(valid_scenes_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "scenes")
    _fake_pass_stage(tmp_path / "job-s1", "visual_spans")
    client.post("/jobs/job-s1/stages/seo/run")

    response = client.post(
        "/jobs/job-s1/stages/seo/promote",
        json={"raw_response": json.dumps(valid_seo_payload, ensure_ascii=False)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "json/seo.json"
    seo_promote = next(s for s in body["state"]["stages"] if s["name"] == "seo_promote")
    assert seo_promote["status"] == "completed"
    assert body["state"]["current_stage"] == "seo_qa"


def test_post_promote_seo_invalid_raw_returns_409(
    client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    _fake_pass_idea_research(tmp_path / "job-s1")
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "script")
    client.post("/jobs/job-s1/stages/scenes/run")
    client.post(
        "/jobs/job-s1/stages/scenes/promote",
        json={"raw_response": json.dumps(valid_scenes_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "scenes")
    _fake_pass_stage(tmp_path / "job-s1", "visual_spans")
    client.post("/jobs/job-s1/stages/seo/run")
    stale_payload = {**valid_seo_payload, "job_id": "old-job"}

    response = client.post(
        "/jobs/job-s1/stages/seo/promote",
        json={"raw_response": json.dumps(stale_payload, ensure_ascii=False)},
    )

    assert response.status_code == 409
    assert "job_id mismatch" in response.json()["detail"]


def test_post_run_render_via_http(
    client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    _fake_pass_idea_research(tmp_path / "job-s1")
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "script")
    client.post("/jobs/job-s1/stages/scenes/run")
    client.post(
        "/jobs/job-s1/stages/scenes/promote",
        json={"raw_response": json.dumps(valid_scenes_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "scenes")
    _fake_pass_stage(tmp_path / "job-s1", "visual_spans")
    client.post("/jobs/job-s1/stages/seo/run")
    client.post(
        "/jobs/job-s1/stages/seo/promote",
        json={"raw_response": json.dumps(valid_seo_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "seo")
    _fake_pass_assets_chatgpt(tmp_path / "job-s1")
    _fake_pass_thumbnail_image(tmp_path / "job-s1")
    _fake_pass_whisper_timestamps(tmp_path / "job-s1")

    def fake_render_operator_job(options):
        for filename in [
            "render_props.json",
            "visual_review.json",
            "visual_contact_sheet.jpg",
            "thumbnail.jpg",
            "video.mp4",
            "report.md",
        ]:
            (options.job_dir / filename).write_text("ok", encoding="utf-8")
        return object()

    monkeypatch.setattr("video_agent.orchestrator.stages.render_operator_job", fake_render_operator_job)

    response = client.post("/jobs/job-s1/stages/render/run")

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "video.mp4"
    render_stage = next(s for s in body["state"]["stages"] if s["name"] == "render")
    assert render_stage["status"] == "completed"
    assert body["state"]["current_stage"] == "render_continuity_qa"


def test_post_run_review_via_http(
    client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    _fake_pass_idea_research(tmp_path / "job-s1")
    client.post("/jobs/job-s1/stages/script/run")
    client.post(
        "/jobs/job-s1/stages/script/promote",
        json={"raw_response": json.dumps(valid_script_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "script")
    client.post("/jobs/job-s1/stages/scenes/run")
    client.post(
        "/jobs/job-s1/stages/scenes/promote",
        json={"raw_response": json.dumps(valid_scenes_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "scenes")
    _fake_pass_stage(tmp_path / "job-s1", "visual_spans")
    client.post("/jobs/job-s1/stages/seo/run")
    client.post(
        "/jobs/job-s1/stages/seo/promote",
        json={"raw_response": json.dumps(valid_seo_payload, ensure_ascii=False)},
    )
    _fake_pass_qa(tmp_path / "job-s1", "seo")
    _fake_pass_assets_chatgpt(tmp_path / "job-s1")
    _fake_pass_thumbnail_image(tmp_path / "job-s1")
    _fake_pass_whisper_timestamps(tmp_path / "job-s1")

    def fake_render_operator_job(options):
        (options.job_dir / "video.mp4").write_text("ok", encoding="utf-8")
        return object()

    def fake_write_operator_review(job_dir):
        output = job_dir / "operator_review.html"
        output.write_text("<html>review</html>", encoding="utf-8")
        return output

    monkeypatch.setattr("video_agent.orchestrator.stages.render_operator_job", fake_render_operator_job)
    monkeypatch.setattr("video_agent.orchestrator.stages.write_operator_review", fake_write_operator_review)
    client.post("/jobs/job-s1/stages/render/run")
    # render now advances to render_continuity_qa before review.
    _fake_pass_stage(tmp_path / "job-s1", "render_continuity_qa")

    response = client.post("/jobs/job-s1/stages/review/run")

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "operator_review.html"
    review_stage = next(s for s in body["state"]["stages"] if s["name"] == "review")
    assert review_stage["status"] == "completed"
    assert all(s["status"] == "completed" for s in body["state"]["stages"])


def test_post_promote_script_invalid_raw_returns_409(
    client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    _create_job(client, "job-s1")
    client.post("/jobs/job-s1/idea", json=idea_payload)
    _fake_pass_idea_research(tmp_path / "job-s1")
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
