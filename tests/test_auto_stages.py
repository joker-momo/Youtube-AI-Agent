from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
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
    auto_scenes_qa_stage,
    auto_scenes_stage,
    auto_script_stage,
    auto_seo_stage,
    promote_scenes_stage,
    promote_script_stage,
    run_scenes_stage,
    run_script_stage,
    run_visual_spans_stage,
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
        "language": "es-ES",
        "ai_disclosure": True,
        "thumbnail_path": "thumbnail.jpg",
    }


# ---------- fake client ----------------------------------------------------


class FakeBrowserClient:
    """In-process double for the browser-worker session API."""

    def __init__(self, queue: list[str] | None = None) -> None:
        self.queue = list(queue or [])
        self.sessions: list[list[str]] = []
        self.events: list[str] = []
        self.send_timeouts: list[tuple[str, int]] = []
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
        self.events.append(f"send:{site}")
        self.send_timeouts.append((site, response_timeout_ms))
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
        self.events.append(f"open:{site}")

        async def sender(messages, *, response_timeout_ms: int = 180_000) -> str:
            return await self.run_session(
                site,
                messages,
                response_timeout_ms=response_timeout_ms,
            )

        async def closer() -> None:
            self.events.append(f"close:{site}")
            return None

        return sender, closer

    async def generate_image(
        self,
        prompt: str,
        *,
        project_name: str,
        out_path: str,
        response_timeout_ms: int = 360_000,
    ) -> dict:
        self.events.append("image")
        import os
        from PIL import Image
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        Image.new("RGB", (640, 360), (12, 34, 56)).save(out_path, format="PNG")
        return {"src": "https://example.com/img.png", "local_path": out_path, "project_name": project_name, "bytes": 9}

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


def _fake_pass_stage(job_dir: Path, stage_name: str) -> None:
    from video_agent.orchestrator.job_state import load_job, save_job
    state = load_job(job_dir)
    if state.current_stage != stage_name:
        return
    state.stage(stage_name).status = "completed"
    nxt = next((s for s in state.stages if s.status == "pending"), None)
    if nxt is not None:
        state.current_stage = nxt.name
    save_job(job_dir, state)


def _set_current_stage(job_dir: Path, stage_name: str) -> None:
    """Force job.json to a specific pending stage with consistent statuses."""
    from video_agent.orchestrator.job_state import load_job, save_job

    state = load_job(job_dir)
    found = False
    for stage in state.stages:
        if stage.name == stage_name:
            found = True
            stage.status = "pending"
            stage.started_at = None
            stage.completed_at = None
            stage.error = None
            continue
        if not found:
            stage.status = "completed"
            if stage.started_at is None:
                stage.started_at = state.updated_at
            if stage.completed_at is None:
                stage.completed_at = state.updated_at
            stage.error = None
        else:
            stage.status = "pending"
            stage.started_at = None
            stage.completed_at = None
            stage.error = None
    if not found:
        raise AssertionError(f"Unknown stage in test setup: {stage_name}")
    state.current_stage = stage_name
    save_job(job_dir, state)


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
    _fake_pass_stage(job_dir, "idea_research")


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


def test_auto_run_then_promote_retries_on_promotion_failure(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    
    # First response fails to parse, second succeeds
    fake = FakeBrowserClient(
        queue=[
            "invalid response that fails promoter",
            json.dumps(valid_script_payload, ensure_ascii=False)
        ]
    )

    output = asyncio.run(
        auto_script_stage(job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs))
    )

    assert output == job_dir / "script.json"
    assert len(fake.calls) == 2  # Verify retry occurred
    promoted = json.loads(output.read_text(encoding="utf-8"))
    assert promoted["job_id"] == "job-auto"


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


def test_auto_scenes_can_run_sharded_when_enabled(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
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
    plan = {
        "artifact_type": "scenes_plan",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": None,
        "batch_total": None,
        "data": {
            "target_scene_count": 2,
            "target_total_duration_sec": 48,
            "batch_size": 2,
            "batches": [
                {
                    "batch_index": 1,
                    "scene_start": "scene-01",
                    "scene_end": "scene-02",
                    "purpose": "full test batch",
                    "script_sections": ["section-01"],
                }
            ],
        },
        "warnings": [],
    }
    batch = {
        "artifact_type": "scenes_batch",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": 1,
        "batch_total": 1,
        "data": {"scene_start": "scene-01", "scene_end": "scene-02", "scenes": valid_scenes_payload["scenes"]},
        "warnings": [],
    }
    fake = FakeBrowserClient(queue=[json.dumps(plan), json.dumps(batch)])
    monkeypatch.setenv("SCENES_SHARDED_GENERATION", "1")

    output = asyncio.run(
        auto_scenes_stage(job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs))
    )

    assert output == job_dir / "scenes.json"
    assert (job_dir / "operator/chatgpt/scenes_plan.json").exists()
    assert (job_dir / "operator/chatgpt/scenes_batches/scenes_batch_01.json").exists()
    scenes = json.loads((job_dir / "scenes.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in scenes["scenes"]] == ["scene-01", "scene-02"]
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "scenes_qa"


def test_auto_scenes_repairs_invalid_batch_before_failing(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
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
    plan = {
        "artifact_type": "scenes_plan",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": None,
        "batch_total": None,
        "data": {
            "target_scene_count": 2,
            "target_total_duration_sec": 48,
            "batch_size": 2,
            "batches": [
                {
                    "batch_index": 1,
                    "scene_start": "scene-01",
                    "scene_end": "scene-02",
                    "purpose": "full test batch",
                    "script_sections": ["section-01"],
                }
            ],
        },
        "warnings": [],
    }
    bad_batch = {
        "artifact_type": "scenes_batch",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": 1,
        "batch_total": 1,
        "data": {
            "scene_start": "scene-01",
            "scene_end": "scene-02",
            "scenes": [
                {**valid_scenes_payload["scenes"][0], "visual_prompt": "cocina tranquila con lámpara cálida"},
                valid_scenes_payload["scenes"][1],
            ],
        },
        "warnings": [],
    }
    fixed_batch = {
        **bad_batch,
        "data": {
            **bad_batch["data"],
            "scenes": valid_scenes_payload["scenes"],
        },
    }
    fake = FakeBrowserClient(
        queue=[json.dumps(plan), json.dumps(bad_batch), json.dumps(fixed_batch)]
    )
    monkeypatch.setenv("SCENES_SHARDED_GENERATION", "1")

    output = asyncio.run(
        auto_scenes_stage(job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs))
    )

    assert output == job_dir / "scenes.json"
    assert len(fake.calls) == 3
    assert "Validation failed" in fake.calls[-1][0]
    repaired = json.loads(
        (job_dir / "operator/chatgpt/scenes_batches/scenes_batch_01.json").read_text(
            encoding="utf-8"
        )
    )
    assert repaired["data"]["scenes"][0]["visual_prompt"] == "Calm evening bedroom with warm lamp light"


def test_auto_scenes_qa_can_run_sharded_when_enabled(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
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
    many_scenes_payload = {
        **valid_scenes_payload,
        "total_duration_sec": 54,
        "scenes": [
            {
                **valid_scenes_payload["scenes"][index % 2],
                "id": f"scene-{index + 1:02d}",
                "duration_sec": 6,
            }
            for index in range(9)
        ],
    }
    promote_scenes_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(many_scenes_payload, ensure_ascii=False),
    )
    qa_envelopes = [
        {
            "artifact_type": "scenes_qa_batch",
            "schema_version": "2026-05-json-shards-v1",
            "job_id": "job-auto",
            "channel_id": "vida-plena-45",
            "status": "complete",
            "batch_index": index,
            "batch_total": 2,
            "data": {
                "verdict": "PASS",
                "youtube_policy": {"compliant": True, "risk_level": "none", "violations": []},
                "scene_checks": [],
                "issues": [],
                "required_changes": [],
                "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5, "youtube_policy": 5},
            },
            "warnings": [],
        }
        for index in (1, 2)
    ]
    fake = FakeBrowserClient(queue=[json.dumps(env) for env in qa_envelopes])
    monkeypatch.setenv("SCENES_SHARDED_GENERATION", "1")

    output = asyncio.run(
        auto_scenes_qa_stage(job_dir, channel_path, lambda msgs: fake.run_session("gemini", msgs))
    )

    assert output == job_dir / "operator/gemini/scenes_qa.json"
    assert (job_dir / "operator/gemini/scenes_qa_batches/scenes_qa_batch_01.json").exists()
    assert (job_dir / "operator/gemini/scenes_qa_batches/scenes_qa_batch_02.json").exists()
    merged = json.loads(output.read_text(encoding="utf-8"))
    assert merged["verdict"] == "PASS"
    assert [row["batch_index"] for row in merged["batch_results"]] == [1, 2]
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    # visual_spans (long-form report-only sidecar) now runs between scenes_qa and seo.
    assert state["current_stage"] == "visual_spans"


def test_auto_scenes_qa_sharded_reuses_existing_batches_and_logs_progress(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
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
    many_scenes_payload = {
        **valid_scenes_payload,
        "total_duration_sec": 54,
        "scenes": [
            {
                **valid_scenes_payload["scenes"][index % 2],
                "id": f"scene-{index + 1:02d}",
                "duration_sec": 6,
            }
            for index in range(9)
        ],
    }
    promote_scenes_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(many_scenes_payload, ensure_ascii=False),
    )
    qa_batch_1 = {
        "artifact_type": "scenes_qa_batch",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": 1,
        "batch_total": 2,
        "data": {
            "verdict": "PASS",
            "youtube_policy": {"compliant": True, "risk_level": "none", "violations": []},
            "scene_checks": [],
            "issues": [],
            "required_changes": [],
            "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5, "youtube_policy": 5},
        },
        "warnings": [],
    }
    qa_batch_2 = {**qa_batch_1, "batch_index": 2}
    batch_dir = job_dir / "operator/gemini/scenes_qa_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "scenes_qa_batch_01.json").write_text(
        json.dumps(qa_batch_1, ensure_ascii=False),
        encoding="utf-8",
    )
    fake = FakeBrowserClient(queue=[json.dumps(qa_batch_2)])
    monkeypatch.setenv("SCENES_SHARDED_GENERATION", "1")

    output = asyncio.run(
        auto_scenes_qa_stage(job_dir, channel_path, lambda msgs: fake.run_session("gemini", msgs))
    )

    assert output == job_dir / "operator/gemini/scenes_qa.json"
    assert len(fake.calls) == 1
    events = (job_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"SCENES_QA_PROGRESS"' in events
    assert '"step": "batch_reused"' in events
    assert '"step": "batch_saved"' in events
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    scenes_qa = next(stage for stage in state["stages"] if stage["name"] == "scenes_qa")
    assert scenes_qa["status"] == "completed"
    assert scenes_qa["started_at"] is not None


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
    # visual_spans (long-form report-only sidecar) now sits between scenes_qa and
    # seo; run it so the job advances to the seo stage.
    run_visual_spans_stage(job_dir, channel_path)
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
    tmp_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
):
    _create_job(http_client)
    http_client.post("/jobs/job-auto/idea", json=idea_payload)
    _fake_pass_stage(tmp_path / "job-auto", "idea_research")

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
    assert body["output"] == "json/script.json"
    assert body["state"]["current_stage"] == "script_qa"
    assert len(fake.calls) == 1


def test_http_auto_script_login_required_returns_409(
    http_client: TestClient,
    tmp_path: Path,
    idea_payload: dict,
):
    _create_job(http_client)
    http_client.post("/jobs/job-auto/idea", json=idea_payload)
    _fake_pass_stage(tmp_path / "job-auto", "idea_research")

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
    tmp_path: Path,
    idea_payload: dict,
):
    _create_job(http_client)
    http_client.post("/jobs/job-auto/idea", json=idea_payload)
    _fake_pass_stage(tmp_path / "job-auto", "idea_research")

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


def test_http_auto_thumbnail_image(
    http_client: TestClient,
    tmp_path: Path,
    monkeypatch,
):
    _create_job(http_client)
    job_dir = tmp_path / "job-auto"
    _set_current_stage(job_dir, "thumbnail_image")
    (job_dir / "seo.json").write_text(
        json.dumps(
            {
                "job_id": "job-auto",
                "title": "5 hábitos nocturnos para dormir mejor",
                "description": "Rutina suave y realista para descansar mejor.",
                "tags": [
                    "dormir mejor",
                    "rutina nocturna",
                    "vida plena 45",
                    "bienestar 45",
                    "descanso",
                ],
                "language": "es-ES",
                "ai_disclosure": True,
                "thumbnail_path": "",
                "thumbnail_text": "DUERME MEJOR HOY",
                "title_variants": [
                    {
                        "title": "5 hábitos nocturnos para dormir mejor",
                        "thumbnail_text": "DUERME MEJOR HOY",
                        "score": 85,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fake = FakeBrowserClient()
    app.dependency_overrides[get_browser_client] = lambda: fake
    monkeypatch.setattr("video_agent.orchestrator.stages.repo_root", lambda: tmp_path)

    try:
        r = http_client.post("/jobs/job-auto/stages/thumbnail_image/auto")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output"] == "seo.json"
    assert body["state"]["current_stage"] == "whisper_timestamps"
    seo = json.loads((job_dir / "seo.json").read_text(encoding="utf-8"))
    assert seo["thumbnail_path"].endswith("thumbnail_1.jpg")


def test_http_auto_thumbnail_image_worker_error_returns_502(
    http_client: TestClient,
    tmp_path: Path,
):
    _create_job(http_client)
    job_dir = tmp_path / "job-auto"
    _set_current_stage(job_dir, "thumbnail_image")
    (job_dir / "seo.json").write_text(
        json.dumps(
            {
                "job_id": "job-auto",
                "title": "5 hábitos nocturnos para dormir mejor",
                "description": "Rutina suave y realista para descansar mejor.",
                "tags": ["dormir mejor"],
                "language": "es-ES",
                "ai_disclosure": True,
                "thumbnail_path": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fake = FakeBrowserClient()

    async def _raise_image_error(**kwargs):
        raise BrowserClientError(
            "image driver failed", status_code=502, detail={"error": "boom"}
        )

    fake.generate_image = _raise_image_error  # type: ignore[assignment]
    app.dependency_overrides[get_browser_client] = lambda: fake
    try:
        r = http_client.post("/jobs/job-auto/stages/thumbnail_image/auto")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["browser_worker_status"] == 502


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


def test_browser_client_wraps_transport_errors_as_retryable_502():
    client = BrowserClient()

    wrapped = client._wrap_transport_error(
        "gemini/sessions",
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
    )

    assert isinstance(wrapped, BrowserClientError)
    assert wrapped.status_code == 502
    assert wrapped.detail["type"] == "RemoteProtocolError"


# ---------- /run-all -------------------------------------------------------


def _assert_pipeline_stage_order(stages: list[str], expected: list[str]) -> None:
    """Same stages completed, honouring only the orderings the DAG guarantees.

    With ``pipeline.parallel_dag: true`` the ChatGPT lane and the local
    (cpu/mps) lane interleave, so the exact position of visual_spans /
    whisper_timestamps / visual_schedule relative to the ChatGPT stages is
    scheduling-dependent. Assert set equality plus the invariant orderings.
    """
    assert sorted(stages) == sorted(expected), stages
    idx = stages.index
    chatgpt_lane = [
        s
        for s in (
            "script_promote", "script_qa", "scenes_promote", "scenes_qa",
            "seo_promote", "seo_qa", "graphic_images", "thumbnail_image",
        )
        if s in stages
    ]
    assert [s for s in stages if s in chatgpt_lane] == chatgpt_lane
    if "visual_spans" in stages and "visual_schedule" in stages:
        assert idx("visual_spans") < idx("visual_schedule")
    for dep in ("thumbnail_image", "whisper_timestamps", "visual_schedule"):
        if dep in stages and "render" in stages:
            assert idx(dep) < idx("render")
    if "render" in stages:
        assert idx("render") < idx("render_continuity_qa") < idx("review")


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
    def fake_render(job_dir, channel_path, *, notify_telegram=True):
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

    def fake_whisper(job_dir):
        out = job_dir / "whisper_timestamps.json"
        out.write_text('{"scenes":[]}', encoding="utf-8")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "whisper_timestamps", out)
        return out

    async def fake_thumbnail(job_dir, channel_path, image_fn):
        out = job_dir / "seo.json"
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "thumbnail_image", out)
        return out

    async def _noop_sleep(_):
        return

    monkeypatch.setattr(
        "video_agent.web.run_all_pipeline.run_render_stage", fake_render
    )
    monkeypatch.setattr(
        "video_agent.web.run_all_pipeline.run_review_stage", fake_review
    )
    monkeypatch.setattr(
        "video_agent.web.run_all_pipeline.run_whisper_timestamps_stage", fake_whisper
    )
    monkeypatch.setattr(
        "video_agent.web.run_all_pipeline.auto_thumbnail_image_stage", fake_thumbnail
    )
    # Long-form pipeline test: do not auto-trigger the Shorts autopilot
    # (it opens its own browser session; covered by its own tests).
    monkeypatch.setattr(
        "video_agent.shorts.trigger.should_run_autopilot_after_review",
        lambda *a, **k: False,
    )
    import asyncio as _asyncio
    monkeypatch.setattr(_asyncio, "sleep", _noop_sleep)

    try:
        r = http_client.post("/jobs/job-auto/run-all?enforce_approvals=false")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200, r.text
    body = r.json()
    stages = [c["stage"] for c in body["completed"]]
    _assert_pipeline_stage_order(
        stages,
        [
            "idea_research",
            "script_promote",
            "script_qa",
            "scenes_promote",
            "scenes_qa",
            "visual_spans",
            "seo_promote",
            "seo_qa",
            "graphic_images",
            "thumbnail_image",
            "whisper_timestamps",
            "visual_schedule",
            "render",
            "render_continuity_qa",
            "review",
        ],
    )
    assert stages[0] == "idea_research"
    assert all(s["status"] == "completed" for s in body["state"]["stages"])
    # 2 briefing sends + 3 ChatGPT task sends + 3 Gemini task sends = 8
    assert len(fake.calls) == 8
    assert fake.send_timeouts[:2] == [("chatgpt", 60_000), ("gemini", 60_000)]
    assert all(timeout == 300_000 for _, timeout in fake.send_timeouts[2:8])


def test_http_run_all_requires_idea_research_confirmation(
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
            "OK",
            "OK",
            json.dumps(valid_script_payload, ensure_ascii=False),
            qa_pass,
            json.dumps(valid_scenes_payload, ensure_ascii=False),
            qa_pass,
            json.dumps(valid_seo_payload, ensure_ascii=False),
            qa_pass,
        ]
    )
    app.dependency_overrides[get_browser_client] = lambda: fake

    def fake_render(job_dir, channel_path, *, notify_telegram=True):
        out = job_dir / "video.mp4"
        out.write_bytes(b"fake")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "render", out)
        return out

    def fake_review(job_dir):
        out = job_dir / "operator_review.html"
        out.write_text("<html/>", encoding="utf-8")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "review", out)
        return out

    def fake_whisper(job_dir):
        out = job_dir / "whisper_timestamps.json"
        out.write_text('{"scenes":[]}', encoding="utf-8")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "whisper_timestamps", out)
        return out

    async def fake_thumbnail(job_dir, channel_path, image_fn):
        out = job_dir / "seo.json"
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "thumbnail_image", out)
        return out

    async def _noop_sleep(_):
        return

    monkeypatch.setattr("video_agent.web.run_all_pipeline.run_render_stage", fake_render)
    monkeypatch.setattr("video_agent.web.run_all_pipeline.run_review_stage", fake_review)
    monkeypatch.setattr("video_agent.web.run_all_pipeline.run_whisper_timestamps_stage", fake_whisper)
    monkeypatch.setattr("video_agent.web.run_all_pipeline.auto_thumbnail_image_stage", fake_thumbnail)
    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "sleep", _noop_sleep)

    try:
        first = http_client.post("/jobs/job-auto/run-all?enforce_approvals=true")
        assert first.status_code == 409
        detail = first.json()["detail"]
        assert detail["approval_required"] == "idea_research"
        assert detail["stopped_at"] == "script"

        confirm = http_client.post("/jobs/job-auto/approvals/idea_research/confirm")
        assert confirm.status_code == 200

        second = http_client.post("/jobs/job-auto/run-all?enforce_approvals=true")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert second.status_code == 409
    detail2 = second.json()["detail"]
    assert detail2["approval_required"] == "script_promote"


def test_http_regenerate_idea_research_resets_stage(
    http_client: TestClient,
    tmp_path: Path,
):
    _create_job(http_client)
    job_dir = tmp_path / "job-auto"
    _fake_pass_stage(job_dir, "idea_research")

    r = http_client.post("/jobs/job-auto/stages/idea_research/regenerate")
    assert r.status_code == 200
    body = r.json()
    assert body["state"]["current_stage"] == "idea_research"

    approvals = http_client.get("/jobs/job-auto/approvals")
    assert approvals.status_code == 200
    assert approvals.json()["approvals"]["idea_research"] is False


def test_http_run_all_resumes_from_current_pending_stage(
    http_client: TestClient,
    tmp_path: Path,
    monkeypatch,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    valid_seo_payload: dict,
):
    _create_job(http_client)
    http_client.post("/jobs/job-auto/idea", json=idea_payload)
    job_dir = tmp_path / "job-auto"

    # Pre-complete until scenes stage so /run-all must resume there.
    _fake_pass_stage(job_dir, "idea_research")
    run_script_stage(job_dir, repo_root() / "configs/vida-plena-45/channel.yaml")
    promote_script_stage(
        job_dir,
        repo_root() / "configs/vida-plena-45/channel.yaml",
        raw_response=json.dumps(valid_script_payload, ensure_ascii=False),
    )
    _fake_pass_qa(job_dir, "script")

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
            "OK",  # qa briefing ack
            json.dumps(valid_scenes_payload, ensure_ascii=False),
            qa_pass,
            json.dumps(valid_seo_payload, ensure_ascii=False),
            qa_pass,
        ]
    )
    app.dependency_overrides[get_browser_client] = lambda: fake

    def fake_render(job_dir, channel_path, *, notify_telegram=True):
        out = job_dir / "video.mp4"
        out.write_bytes(b"fake")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "render", out)
        return out

    def fake_review(job_dir):
        out = job_dir / "operator_review.html"
        out.write_text("<html/>", encoding="utf-8")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "review", out)
        return out

    def fake_whisper(job_dir):
        out = job_dir / "whisper_timestamps.json"
        out.write_text('{"scenes":[]}', encoding="utf-8")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "whisper_timestamps", out)
        return out

    async def fake_thumbnail(job_dir, channel_path, image_fn):
        out = job_dir / "seo.json"
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "thumbnail_image", out)
        return out

    async def _noop_sleep(_):
        return

    monkeypatch.setattr("video_agent.web.run_all_pipeline.run_render_stage", fake_render)
    monkeypatch.setattr("video_agent.web.run_all_pipeline.run_review_stage", fake_review)
    monkeypatch.setattr("video_agent.web.run_all_pipeline.run_whisper_timestamps_stage", fake_whisper)
    monkeypatch.setattr("video_agent.web.run_all_pipeline.auto_thumbnail_image_stage", fake_thumbnail)
    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(
        "video_agent.shorts.trigger.should_run_autopilot_after_review",
        lambda *a, **k: False,
    )

    try:
        r = http_client.post("/jobs/job-auto/run-all?enforce_approvals=false")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200, r.text
    stages = [c["stage"] for c in r.json()["completed"]]
    _assert_pipeline_stage_order(
        stages,
        [
            "scenes_promote",
            "scenes_qa",
            "visual_spans",
            "seo_promote",
            "seo_qa",
            "graphic_images",
            "thumbnail_image",
            "whisper_timestamps",
            "visual_schedule",
            "render",
            "render_continuity_qa",
            "review",
        ],
    )
    assert len(fake.calls) == 6


def test_http_run_all_opens_writing_session_when_resuming_at_seo_qa_rework(
    http_client: TestClient,
    tmp_path: Path,
    monkeypatch,
    valid_seo_payload: dict,
):
    _create_job(http_client)
    job_dir = tmp_path / "job-auto"
    _set_current_stage(job_dir, "seo_qa")
    bad_seo = dict(valid_seo_payload, language="es-MX")
    (job_dir / "seo.json").write_text(json.dumps(bad_seo, ensure_ascii=False), encoding="utf-8")
    # Minimal scene + span artifacts so the real visual_schedule stage (which runs
    # before render) has inputs to compile from.
    (job_dir / "scenes.json").write_text(json.dumps({
        "scenes": [{"id": "scene-01", "layout": "subtitle", "duration_sec": 10,
                    "asset_refs": {"background": "assets/scene-01.mp4"}}],
    }), encoding="utf-8")
    (job_dir / "visual_spans.json").write_text(json.dumps({
        "spans": [{"id": "vs01", "scene_ids": ["scene-01"],
                   "planned_mode": "continuous_clip"}],
    }), encoding="utf-8")

    qa_pass = json.dumps(
        {
            "verdict": "PASS",
            "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5},
            "issues": [],
            "required_changes": [],
        }
    )
    fixed_seo = dict(valid_seo_payload, language="es-ES")
    fake = FakeBrowserClient(
        queue=[
            "OK",  # chatgpt briefing ack
            "OK",  # qa briefing ack
            qa_pass,
            json.dumps(fixed_seo, ensure_ascii=False),
            qa_pass,
        ]
    )
    app.dependency_overrides[get_browser_client] = lambda: fake

    def fake_render(job_dir, channel_path, *, notify_telegram=True):
        out = job_dir / "video.mp4"
        out.write_bytes(b"fake")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "render", out)
        return out

    def fake_review(job_dir):
        out = job_dir / "operator_review.html"
        out.write_text("<html/>", encoding="utf-8")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "review", out)
        return out

    def fake_whisper(job_dir):
        out = job_dir / "whisper_timestamps.json"
        out.write_text('{"scenes":[]}', encoding="utf-8")
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "whisper_timestamps", out)
        return out

    async def fake_thumbnail(job_dir, channel_path, image_fn):
        out = job_dir / "seo.json"
        from video_agent.orchestrator.stages import _complete_stage

        _complete_stage(job_dir, "thumbnail_image", out)
        return out

    async def _noop_sleep(_):
        return

    monkeypatch.setattr("video_agent.web.run_all_pipeline.run_render_stage", fake_render)
    monkeypatch.setattr("video_agent.web.run_all_pipeline.run_review_stage", fake_review)
    monkeypatch.setattr("video_agent.web.run_all_pipeline.run_whisper_timestamps_stage", fake_whisper)
    monkeypatch.setattr("video_agent.web.run_all_pipeline.auto_thumbnail_image_stage", fake_thumbnail)
    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(
        "video_agent.shorts.trigger.should_run_autopilot_after_review",
        lambda *a, **k: False,
    )

    try:
        r = http_client.post("/jobs/job-auto/run-all?enforce_approvals=false")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200, r.text
    assert "open:chatgpt" in fake.events
    assert "open:gemini" in fake.events
    assert json.loads((job_dir / "seo.json").read_text(encoding="utf-8"))["language"] == "es-ES"


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
        r = http_client.post("/jobs/job-auto/run-all?enforce_approvals=false")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 502
    detail = r.json()["detail"]
    # idea_research completes before the ChatGPT briefing fails.
    assert "idea_research" in [c["stage"] for c in detail["completed"]]
    # Persistent-session /run-all fails on the very first briefing
    # send, before run_script_stage advances the state.
    assert detail["stopped_at"] == "script"
    assert "state" in detail


def test_http_run_all_unknown_job_returns_404(http_client: TestClient):
    r = http_client.post("/jobs/missing/run-all")
    assert r.status_code == 404


def test_run_whisper_timestamps_delegates_to_audio_subprocess(
    tmp_path: Path,
    monkeypatch,
):
    from video_agent.orchestrator import create_job
    from video_agent.orchestrator.job_state import load_job
    import video_agent.orchestrator.stages as stages_mod

    job_dir = tmp_path / "job-whisper"
    create_job(
        job_dir,
        job_id="job-whisper",
        channel_id="vida-plena-45",
        idea_path="idea.json",
    )
    _set_current_stage(job_dir, "whisper_timestamps")
    (job_dir / "assets").mkdir(parents=True)
    (job_dir / "assets" / "narration.wav").write_bytes(b"fake-wav")
    (job_dir / "scenes.json").write_text(
        json.dumps(
            {
                "channel_id": "vida-plena-45",
                "job_id": "job-whisper",
                "total_duration_sec": 3,
                "scenes": [
                    {
                        "id": "scene-01",
                        "duration_sec": 3,
                        "narration": "hola mundo",
                        "on_screen_text": "Hola",
                        "caption": "Hola",
                        "visual_prompt": "warm room",
                        "motion": "slow",
                        "asset_refs": {},
                    }
                ],
                "qa": {"verdict": "PASS"},
            }
        ),
        encoding="utf-8",
    )

    calls: list[tuple[str, Path]] = []

    def fake_audio_subprocess(command: str, delegated_job_dir: Path) -> Path:
        calls.append((command, delegated_job_dir))
        output = delegated_job_dir / "whisper_timestamps.json"
        output.write_text('{"scenes":[]}', encoding="utf-8")
        stages_mod._complete_stage(delegated_job_dir, "whisper_timestamps", output)
        return output

    monkeypatch.setattr(
        stages_mod,
        "_run_audio_subprocess",
        fake_audio_subprocess,
        raising=False,
    )

    output = stages_mod.run_whisper_timestamps_stage(job_dir)

    assert output == job_dir / "whisper_timestamps.json"
    assert calls == [("whisper-timestamps", job_dir)]
    assert load_job(job_dir).stage("whisper_timestamps").status == "completed"


def test_rebase_words_to_scene_timestamps_clamps_boundary_words():
    from video_agent.orchestrator.stages import _rebase_words_to_scene_timestamps

    scenes = [
        {"id": "scene-01", "duration_sec": 10.0},
        {"id": "scene-02", "duration_sec": 10.0},
    ]
    words = [
        {"word": "hola", "start": 9.95, "end": 10.57},
        {"word": "mundo", "start": 10.6, "end": 11.0},
    ]

    scene_data = _rebase_words_to_scene_timestamps(scenes, words)

    assert scene_data[1]["word_segments"][0] == {
        "text": "hola",
        "start": 0.0,
        "end": 0.57,
    }
    assert scene_data[1]["word_segments"][1]["start"] == 0.6


# ---------- Gemini QA stages -----------------------------------------------


def _qa_pass_payload() -> dict:
    return {
        "verdict": "PASS",
        "youtube_policy": {
            "compliant": True,
            "risk_level": "none",
            "violations": [],
        },
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


def test_auto_seo_qa_prompt_uses_channel_language(
    tmp_path: Path,
    channel_path: Path,
    valid_seo_payload: dict,
):
    from video_agent.orchestrator.stages import auto_seo_qa_stage

    job_dir = tmp_path / "job-auto"
    _seed_at_stage(job_dir, "seo_qa", {"seo.json": valid_seo_payload})
    fake = FakeBrowserClient(queue=[json.dumps(_qa_pass_payload())])

    output = asyncio.run(
        auto_seo_qa_stage(
            job_dir,
            channel_path,
            lambda msgs: fake.run_session("gemini", msgs),
        )
    )

    assert output == job_dir / "operator" / "gemini" / "seo_qa.json"
    task = fake.calls[0][0]
    assert "expected language is es-ES" in task
    assert "not EXACTLY the expected language, verdict MUST be NEEDS_REWORK" in task
    assert "expected language is es-MX" not in task


def test_auto_seo_qa_forces_rework_when_language_mismatches_even_if_gemini_passes(
    tmp_path: Path,
    channel_path: Path,
    valid_seo_payload: dict,
):
    from video_agent.orchestrator.stages import auto_seo_qa_stage

    job_dir = tmp_path / "job-auto"
    seo_payload = dict(valid_seo_payload)
    seo_payload["language"] = "es-MX"
    _seed_at_stage(job_dir, "seo_qa", {"seo.json": seo_payload})
    fake = FakeBrowserClient(queue=[json.dumps(_qa_pass_payload())])

    with pytest.raises(StageInputMissingError, match="NEEDS_REWORK"):
        asyncio.run(
            auto_seo_qa_stage(
                job_dir,
                channel_path,
                lambda msgs: fake.run_session("gemini", msgs),
            )
        )

    qa = json.loads((job_dir / "operator/gemini/seo_qa.json").read_text(encoding="utf-8"))
    assert qa["verdict"] == "NEEDS_REWORK"
    assert "SEO language must be exactly es-ES" in qa["issues"][0]
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "seo_qa"


def test_auto_seo_qa_rework_repromotes_before_retrying_qa(
    tmp_path: Path,
    channel_path: Path,
    valid_seo_payload: dict,
):
    from video_agent.orchestrator.stages import auto_qa_with_rework

    job_dir = tmp_path / "job-auto"
    original = dict(valid_seo_payload, job_id="job-auto", language="es-MX")
    reworked = dict(valid_seo_payload, job_id="job-auto", language="es-ES")
    _seed_at_stage(job_dir, "seo_qa", {"seo.json": original})
    fake = FakeBrowserClient(
        queue=[
            json.dumps(_qa_pass_payload()),
            json.dumps(reworked, ensure_ascii=False),
            json.dumps(_qa_pass_payload()),
        ]
    )

    output = asyncio.run(
        auto_qa_with_rework(
            "seo",
            job_dir,
            channel_path,
            chatgpt_fn=lambda msgs: fake.run_session("chatgpt", msgs),
            qa_session_fn=lambda msgs: fake.run_session("gemini", msgs),
        )
    )

    assert output == job_dir / "operator" / "gemini" / "seo_qa.json"
    promoted = json.loads((job_dir / "seo.json").read_text(encoding="utf-8"))
    assert promoted["language"] == "es-ES"
    assert "idioma es-ES" in fake.calls[1][0]
    assert len(fake.calls) == 3


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
            qa_session_fn=lambda msgs: fake.run_session("gemini", msgs),
        )
    )
    assert output == job_dir / "operator" / "gemini" / "script_qa.json"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    script_qa = next(s for s in state["stages"] if s["name"] == "script_qa")
    assert script_qa["status"] == "completed"
    # Three sessions: failing QA, rework chat, passing QA.
    assert len(fake.calls) == 3


def test_auto_qa_with_rework_feeds_promotion_validation_errors_back_to_chatgpt(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    from video_agent.orchestrator.stages import auto_qa_with_rework

    monkeypatch.delenv("SCENES_SHARDED_GENERATION", raising=False)
    job_dir = tmp_path / "job-auto"
    _advance_through_script_promote(
        job_dir, channel_path, idea_payload, valid_script_payload
    )
    _fake_pass_qa(job_dir, "script")
    run_scenes_stage(job_dir, channel_path)
    promote_scenes_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_scenes_payload, ensure_ascii=False),
    )
    invalid_rework = {
        **valid_scenes_payload,
        "scenes": [
            {**scene, "visual_prompt": "Persona caminando en una cocina española"}
            for scene in valid_scenes_payload["scenes"]
        ],
    }
    fake = FakeBrowserClient(
        queue=[
            json.dumps(_qa_fail_payload(["visual prompts need rework"])),
            json.dumps(invalid_rework, ensure_ascii=False),
            json.dumps(valid_scenes_payload, ensure_ascii=False),
            json.dumps(_qa_pass_payload()),
        ]
    )

    output = asyncio.run(
        auto_qa_with_rework(
            "scenes",
            job_dir,
            channel_path,
            chatgpt_fn=lambda msgs: fake.run_session("chatgpt", msgs),
            qa_session_fn=lambda msgs: fake.run_session("gemini", msgs),
        )
    )

    assert output == job_dir / "operator" / "gemini" / "scenes_qa.json"
    assert len(fake.calls) == 4
    repair_prompt = fake.calls[2][0]
    assert "scenes validation failed" in repair_prompt
    assert "visual_prompt must be ENGLISH" in repair_prompt


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
                qa_session_fn=lambda msgs: fake.run_session("gemini", msgs),
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


# ---------------------------------------------------------------------------
# auto_assets_chatgpt_stage tests
# ---------------------------------------------------------------------------


def _seed_at_assets_chatgpt(job_dir: Path, scenes_doc: dict) -> None:
    """Create a job at the ``assets_chatgpt`` stage.

    All stages before ``assets_chatgpt`` are marked completed and
    ``scenes.json`` is written so the stage has something to iterate.
    """
    from video_agent.orchestrator.job_state import (
        DEFAULT_STAGES,
        JobState,
        StageStatus,
        save_job,
    )
    from video_agent.orchestrator.orchestrator import _now

    ts = _now()
    stages = []
    for name in DEFAULT_STAGES:
        if name == "whisper_timestamps":
            # inject assets_chatgpt (removed from DEFAULT_STAGES) as current stage
            stages.append(StageStatus(name="assets_chatgpt", status="pending"))
            stages.append(StageStatus(name=name, status="pending"))
        else:
            # all stages before assets_chatgpt are completed; whisper_timestamps+ remain pending
            before_assets = True
            for s in stages:
                if s.name == "assets_chatgpt":
                    before_assets = False
                    break
            if before_assets:
                stages.append(StageStatus(name=name, status="completed", started_at=ts, completed_at=ts))
            else:
                stages.append(StageStatus(name=name, status="pending"))

    state = JobState(
        job_id="job-assets",
        channel_id="vida-plena-45",
        idea_path="idea.json",
        created_at=ts,
        updated_at=ts,
        current_stage="assets_chatgpt",
        stages=stages,
    )
    job_dir.mkdir(parents=True, exist_ok=True)
    save_job(job_dir, state)
    (job_dir / "scenes.json").write_text(
        json.dumps(scenes_doc, ensure_ascii=False), encoding="utf-8"
    )


def _two_scene_doc() -> dict:
    return {
        "channel_id": "vida-plena-45",
        "job_id": "job-assets",
        "total_duration_sec": 48,
        "scenes": [
            {
                "id": "scene-01",
                "duration_sec": 24,
                "narration": "Narration one.",
                "on_screen_text": "Text one",
                "caption": "Caption one",
                "visual_prompt": "Calm bedroom with warm light",
                "motion": "slow push-in",
                "asset_refs": {},
            },
            {
                "id": "scene-02",
                "duration_sec": 24,
                "narration": "Narration two.",
                "on_screen_text": "Text two",
                "caption": "Caption two",
                "visual_prompt": "Woman reading quietly",
                "motion": "gentle pan",
                "asset_refs": {},
            },
        ],
        "qa": {"verdict": "PASS"},
    }


async def _fake_image_fn(prompt: str, *, project_name: str, out_path: str) -> dict:
    """Fake image_fn: writes a tiny PNG-like stub and returns metadata."""
    import os
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(b"\x89PNG stub")
    return {
        "src": "https://example.com/img.png",
        "local_path": out_path,
        "project_name": project_name,
        "bytes": 9,
    }


def test_auto_assets_chatgpt_gen_all_scenes(tmp_path, channel_path):
    from video_agent.orchestrator.stages import auto_assets_chatgpt_stage

    job_dir = tmp_path / "job-assets"
    _seed_at_assets_chatgpt(job_dir, _two_scene_doc())

    output = asyncio.run(
        auto_assets_chatgpt_stage(job_dir, channel_path, _fake_image_fn, throttle_sec=0)
    )

    assert output == job_dir / "scenes.json"
    scenes = json.loads(output.read_text(encoding="utf-8"))
    for scene in scenes["scenes"]:
        refs = scene.get("asset_refs", {})
        assert refs.get("primary", "").endswith(".png"), f"scene {scene['id']} missing primary"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "whisper_timestamps"
    assert any(s["name"] == "assets_chatgpt" and s["status"] == "completed" for s in state["stages"])


async def _failing_image_fn(prompt: str, *, project_name: str, out_path: str) -> dict:
    from video_agent.orchestrator.browser_client import BrowserClientError
    raise BrowserClientError("image gen failed", status_code=500, detail={})


def test_auto_assets_chatgpt_continues_on_scene_failure(tmp_path, channel_path):
    """A scene failure emits SCENE_ASSET_FAILED and stage still completes."""
    from video_agent.orchestrator.stages import auto_assets_chatgpt_stage
    from video_agent.contracts import EVENT_LOG

    job_dir = tmp_path / "job-assets"
    _seed_at_assets_chatgpt(job_dir, _two_scene_doc())

    output = asyncio.run(
        auto_assets_chatgpt_stage(job_dir, channel_path, _failing_image_fn, throttle_sec=0)
    )

    assert output == job_dir / "scenes.json"
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "whisper_timestamps"

    events_text = (job_dir / EVENT_LOG).read_text(encoding="utf-8")
    assert events_text.count("SCENE_ASSET_FAILED") == 2


def test_auto_assets_chatgpt_wrong_stage_raises(tmp_path, channel_path):
    from video_agent.orchestrator.stages import auto_assets_chatgpt_stage

    job_dir = tmp_path / "job-assets"
    _seed_at_assets_chatgpt(job_dir, _two_scene_doc())

    # Tamper: set current_stage to something else.
    from video_agent.orchestrator.job_state import load_job, save_job
    state = load_job(job_dir)
    state.current_stage = "render"
    save_job(job_dir, state)

    with pytest.raises(StageInputMissingError, match="assets_chatgpt"):
        asyncio.run(
            auto_assets_chatgpt_stage(job_dir, channel_path, _fake_image_fn, throttle_sec=0)
        )


# ---------------------------------------------------------------------------
# idea_research stage tests
# ---------------------------------------------------------------------------


def _seed_at_stage(job_dir: Path, stage_name: str, extra_files: dict | None = None) -> None:
    """Create a job with current_stage = stage_name; all prior stages completed."""
    from video_agent.orchestrator.job_state import (
        DEFAULT_STAGES,
        JobState,
        StageStatus,
        save_job,
    )
    from video_agent.orchestrator.orchestrator import _now

    ts = _now()
    stages = []
    found = False
    for name in DEFAULT_STAGES:
        if name == stage_name:
            stages.append(StageStatus(name=name, status="pending"))
            found = True
        elif not found:
            stages.append(StageStatus(name=name, status="completed", started_at=ts, completed_at=ts))
        else:
            stages.append(StageStatus(name=name, status="pending"))

    state = JobState(
        job_id="job-research",
        channel_id="vida-plena-45",
        idea_path="idea.json",
        created_at=ts,
        updated_at=ts,
        current_stage=stage_name,
        stages=stages,
    )
    job_dir.mkdir(parents=True, exist_ok=True)
    save_job(job_dir, state)
    for fname, content in (extra_files or {}).items():
        p = job_dir / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else content,
            encoding="utf-8",
        )


_IDEA_PAYLOAD = {
    "topic": "Habitos nocturnos para dormir mejor despues de los 45",
    "title_seed": "5 habitos nocturnos para dormir mejor",
    "target_duration_sec": 54,
    "key_points": ["respira", "sin pantallas"],
    "angle": "simple y realista",
}


def test_idea_research_pass(tmp_path, channel_path):
    from video_agent.orchestrator.stages import auto_idea_research_stage

    job_dir = tmp_path / "job-research"
    _seed_at_stage(job_dir, "idea_research", {"idea.json": _IDEA_PAYLOAD})

    output = asyncio.run(auto_idea_research_stage(job_dir, channel_path))

    assert output == job_dir / "research.json"
    research = json.loads(output.read_text(encoding="utf-8"))
    assert research["verdict"] == "pass"
    assert research["keywords"]
    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "script"
# ---------------------------------------------------------------------------
# run-batch endpoint tests
# ---------------------------------------------------------------------------


def _make_completed_job(job_dir: Path, job_id: str) -> None:
    """Seed a fully-completed job so run-all returns immediately."""
    from video_agent.orchestrator.job_state import (
        DEFAULT_STAGES,
        JobState,
        StageStatus,
        save_job,
    )
    from video_agent.orchestrator.orchestrator import _now

    ts = _now()
    stages = [
        StageStatus(name=name, status="completed", started_at=ts, completed_at=ts)
        for name in DEFAULT_STAGES
    ]
    state = JobState(
        job_id=job_id,
        channel_id="vida-plena-45",
        idea_path="idea.json",
        created_at=ts,
        updated_at=ts,
        current_stage="review",
        stages=stages,
    )
    job_dir.mkdir(parents=True, exist_ok=True)
    save_job(job_dir, state)


def test_http_run_batch_all_succeed(
    http_client: TestClient,
    tmp_path: Path,
):
    """POST /run-batch with 2 fully-completed jobs returns succeeded=2."""
    for jid in ("batch-1", "batch-2"):
        _make_completed_job(tmp_path / jid, jid)

    app.dependency_overrides[get_browser_client] = lambda: FakeBrowserClient()
    try:
        r = http_client.post(
            "/run-batch",
            json={"job_ids": ["batch-1", "batch-2"], "enforce_approvals": False},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    job_ids_in_results = [entry["job_id"] for entry in body["results"]]
    assert "batch-1" in job_ids_in_results
    assert "batch-2" in job_ids_in_results


def test_http_run_batch_unknown_job_is_soft_failure(
    http_client: TestClient,
    tmp_path: Path,
):
    """Unknown job_id is recorded as error; run-batch still returns 200."""
    _make_completed_job(tmp_path / "batch-ok", "batch-ok")

    app.dependency_overrides[get_browser_client] = lambda: FakeBrowserClient()
    try:
        r = http_client.post(
            "/run-batch",
            json={"job_ids": ["batch-ok", "no-such-job"], "enforce_approvals": False},
        )
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert body["succeeded"] == 1
    assert body["failed"] == 1
    errors = {e["job_id"]: e.get("error") for e in body["results"]}
    assert errors["no-such-job"] is not None


def test_http_run_batch_empty_list(http_client: TestClient):
    """Empty job_ids list returns total=0."""
    app.dependency_overrides[get_browser_client] = lambda: FakeBrowserClient()
    try:
        r = http_client.post("/run-batch", json={"job_ids": []})
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 0
    assert body["succeeded"] == 0
    assert body["failed"] == 0


def test_auto_qa_with_rework_dag_mode_does_not_repromote_completed_promote(
    tmp_path: Path,
    channel_path: Path,
    valid_seo_payload: dict,
):
    """DAG mode: when seo_promote is already completed, the QA retry loop
    must not re-run the promoter. The old unconditional ``dag_mode() or …``
    gate re-promoted on every attempt, emitting duplicate STAGE_COMPLETED
    events for seo_promote (one from auto_seo_stage, one from the loop)."""
    from video_agent.orchestrator.stages import auto_qa_with_rework
    from video_agent.orchestrator.stages._shared import set_dag_mode

    job_dir = tmp_path / "job-auto"
    payload = dict(valid_seo_payload, job_id="job-auto")
    _seed_at_stage(job_dir, "seo_qa", {"seo.json": payload})
    # Raw response on disk, as after a normal auto_seo_stage run — the buggy
    # path used it to re-promote unconditionally whenever dag_mode() was on.
    raw_path = job_dir / "operator" / "chatgpt" / "seo.raw.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    fake = FakeBrowserClient(queue=[json.dumps(_qa_pass_payload())])

    set_dag_mode(True)
    try:
        output = asyncio.run(
            auto_qa_with_rework(
                "seo",
                job_dir,
                channel_path,
                chatgpt_fn=lambda msgs: fake.run_session("chatgpt", msgs),
                qa_session_fn=lambda msgs: fake.run_session("gemini", msgs),
            )
        )
    finally:
        set_dag_mode(False)

    assert output == job_dir / "operator" / "gemini" / "seo_qa.json"
    # Only the QA session ran; no rework chat, no re-promotion.
    assert len(fake.calls) == 1
    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    promote_completions = [
        e
        for e in events
        if e["event"] == "STAGE_COMPLETED"
        and e["data"].get("stage") == "seo_promote"
    ]
    assert promote_completions == []


def test_auto_scenes_sharded_retries_wrong_batch_index_before_failing(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    """ChatGPT re-answering a previous batch (wrong batch_index) must trigger
    a repair-prompt retry, not fail the whole scenes_promote stage (the
    'Expected batch_index 13, got 12' production failure)."""
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    run_script_stage(job_dir, channel_path)
    promote_script_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_script_payload, ensure_ascii=False),
    )
    _fake_pass_qa(job_dir, "script")
    plan = {
        "artifact_type": "scenes_plan",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": None,
        "batch_total": None,
        "data": {
            "target_scene_count": 2,
            "target_total_duration_sec": 48,
            "batch_size": 2,
            "batches": [
                {
                    "batch_index": 1,
                    "scene_start": "scene-01",
                    "scene_end": "scene-02",
                    "purpose": "full test batch",
                    "script_sections": ["section-01"],
                }
            ],
        },
        "warnings": [],
    }
    good_batch = {
        "artifact_type": "scenes_batch",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": 1,
        "batch_total": 1,
        "data": {
            "scene_start": "scene-01",
            "scene_end": "scene-02",
            "scenes": valid_scenes_payload["scenes"],
        },
        "warnings": [],
    }
    # Valid envelope shape, wrong batch_index — the model re-answered
    # another batch, as in the production failure.
    wrong_batch = dict(good_batch, batch_index=2)
    fake = FakeBrowserClient(
        queue=[json.dumps(plan), json.dumps(wrong_batch), json.dumps(good_batch)]
    )
    monkeypatch.setenv("SCENES_SHARDED_GENERATION", "1")

    output = asyncio.run(
        auto_scenes_stage(
            job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs)
        )
    )

    assert output == job_dir / "scenes.json"
    scenes = json.loads((job_dir / "scenes.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in scenes["scenes"]] == ["scene-01", "scene-02"]
    # plan + wrong batch + repaired batch = 3 sessions
    assert len(fake.calls) == 3
    events = [
        json.loads(line)
        for line in (job_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    retries = [
        e
        for e in events
        if e["event"] == "SCENES_PROMOTE_PROGRESS"
        and e["data"].get("step") == "batch_retry"
    ]
    assert len(retries) == 1
    assert "batch_index" in retries[0]["data"]["reason"] or "Expected" in retries[0]["data"]["reason"]


def test_local_precheck_blocks_gemini_when_seo_language_wrong(
    tmp_path: Path,
    channel_path: Path,
    valid_seo_payload: dict,
):
    """Mechanical failures must be caught locally and routed to ChatGPT
    rework WITHOUT spending a Gemini QA round-trip: the first browser send
    must be the chatgpt rework, and only the clean artifact reaches Gemini."""
    from video_agent.orchestrator.stages import auto_qa_with_rework

    job_dir = tmp_path / "job-auto"
    bad = dict(valid_seo_payload, job_id="job-auto", language="es-MX")
    fixed = dict(valid_seo_payload, job_id="job-auto", language="es-ES")
    _seed_at_stage(job_dir, "seo_qa", {"seo.json": bad})
    fake = FakeBrowserClient(
        queue=[
            json.dumps(fixed, ensure_ascii=False),  # chatgpt rework response
            json.dumps(_qa_pass_payload()),          # gemini QA on clean artifact
        ]
    )

    output = asyncio.run(
        auto_qa_with_rework(
            "seo",
            job_dir,
            channel_path,
            chatgpt_fn=lambda msgs: fake.run_session("chatgpt", msgs),
            qa_session_fn=lambda msgs: fake.run_session("gemini", msgs),
        )
    )

    assert output == job_dir / "operator" / "gemini" / "seo_qa.json"
    # Send order proves the gate: rework first (chatgpt), Gemini only after.
    assert fake.events == ["send:chatgpt", "send:gemini"]
    promoted = json.loads((job_dir / "seo.json").read_text(encoding="utf-8"))
    assert promoted["language"] == "es-ES"


def test_sharded_batch_prompt_carries_anti_drift_contract_header(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    """Every sharded batch message must open with the current-batch contract
    (bug-438 anti-drift guard)."""
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    run_script_stage(job_dir, channel_path)
    promote_script_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_script_payload, ensure_ascii=False),
    )
    _fake_pass_qa(job_dir, "script")
    plan = {
        "artifact_type": "scenes_plan",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": None,
        "batch_total": None,
        "data": {
            "target_scene_count": 2,
            "target_total_duration_sec": 48,
            "batch_size": 2,
            "batches": [
                {
                    "batch_index": 1,
                    "scene_start": "scene-01",
                    "scene_end": "scene-02",
                    "purpose": "full test batch",
                    "script_sections": ["section-01"],
                }
            ],
        },
        "warnings": [],
    }
    batch = {
        "artifact_type": "scenes_batch",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": 1,
        "batch_total": 1,
        "data": {
            "scene_start": "scene-01",
            "scene_end": "scene-02",
            "scenes": valid_scenes_payload["scenes"],
        },
        "warnings": [],
    }
    fake = FakeBrowserClient(queue=[json.dumps(plan), json.dumps(batch)])
    monkeypatch.setenv("SCENES_SHARDED_GENERATION", "1")

    asyncio.run(
        auto_scenes_stage(
            job_dir, channel_path, lambda msgs: fake.run_session("chatgpt", msgs)
        )
    )

    batch_msg = fake.calls[1][0]
    assert batch_msg.startswith("# LOTE ACTUAL: 1 de 1")
    assert "batch_index=1" in batch_msg
    assert "scene-01" in batch_msg and "scene-02" in batch_msg


def test_scenes_qa_prewarm_overlaps_generation_and_qa_stage_reuses(
    tmp_path: Path,
    channel_path: Path,
    idea_payload: dict,
    valid_script_payload: dict,
    valid_scenes_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    """With qa_session_fn provided, sharded generation prewarms Gemini QA per
    scene slice (hash-stamped envelope on disk); the scenes_qa stage then
    reuses those verdicts without any further Gemini round-trip."""
    job_dir = tmp_path / "job-auto"
    _seed_script(job_dir, channel_path, idea_payload)
    run_script_stage(job_dir, channel_path)
    promote_script_stage(
        job_dir,
        channel_path,
        raw_response=json.dumps(valid_script_payload, ensure_ascii=False),
    )
    _fake_pass_qa(job_dir, "script")
    plan = {
        "artifact_type": "scenes_plan",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": None,
        "batch_total": None,
        "data": {
            "target_scene_count": 2,
            "target_total_duration_sec": 48,
            "batch_size": 2,
            "batches": [
                {
                    "batch_index": 1,
                    "scene_start": "scene-01",
                    "scene_end": "scene-02",
                    "purpose": "full test batch",
                    "script_sections": ["section-01"],
                }
            ],
        },
        "warnings": [],
    }
    batch = {
        "artifact_type": "scenes_batch",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": 1,
        "batch_total": 1,
        "data": {
            "scene_start": "scene-01",
            "scene_end": "scene-02",
            "scenes": valid_scenes_payload["scenes"],
        },
        "warnings": [],
    }
    prewarm_qa = {
        "artifact_type": "scenes_qa_batch",
        "schema_version": "2026-05-json-shards-v1",
        "job_id": "job-auto",
        "channel_id": "vida-plena-45",
        "status": "complete",
        "batch_index": 1,
        "batch_total": 1,
        "data": {
            "verdict": "PASS",
            "youtube_policy": {"compliant": True, "risk_level": "none", "violations": []},
            "scene_checks": [],
            "issues": [],
            "required_changes": [],
            "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5, "youtube_policy": 5},
        },
        "warnings": [],
    }
    fake = FakeBrowserClient(
        queue=[json.dumps(plan), json.dumps(batch), json.dumps(prewarm_qa)]
    )
    monkeypatch.setenv("SCENES_SHARDED_GENERATION", "1")

    output = asyncio.run(
        auto_scenes_stage(
            job_dir,
            channel_path,
            lambda msgs: fake.run_session("chatgpt", msgs),
            qa_session_fn=lambda msgs: fake.run_session("gemini", msgs),
        )
    )

    assert output == job_dir / "scenes.json"
    # plan + batch on chatgpt, prewarm QA on gemini.
    assert fake.events == ["send:chatgpt", "send:chatgpt", "send:gemini"]
    qa_batch_path = job_dir / "operator/gemini/scenes_qa_batches/scenes_qa_batch_01.json"
    assert qa_batch_path.exists()
    saved = json.loads(qa_batch_path.read_text(encoding="utf-8"))
    assert saved.get("source_content_hash")

    # scenes_qa stage must reuse the prewarmed verdict: empty queue proves
    # no further Gemini round-trip happens.
    fake_qa = FakeBrowserClient(queue=[])
    output_qa = asyncio.run(
        auto_scenes_qa_stage(
            job_dir, channel_path, lambda msgs: fake_qa.run_session("gemini", msgs)
        )
    )
    assert output_qa == job_dir / "operator/gemini/scenes_qa.json"
    merged = json.loads(output_qa.read_text(encoding="utf-8"))
    assert merged["verdict"] == "PASS"
    assert fake_qa.calls == []
