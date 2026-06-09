from __future__ import annotations

import fcntl
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_agent.orchestrator.queue import JobQueue
from video_agent.shorts import paths as shorts_paths
from video_agent.web.app import app, get_jobs_root


@pytest.fixture
def client(tmp_path: Path):
    app.dependency_overrides[get_jobs_root] = lambda: tmp_path
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _write_job(
    root: Path,
    job_id: str,
    *,
    channel_id: str = "vida-plena-45",
    title: str = "Dormir mejor",
    with_required_artifacts: bool = True,
) -> Path:
    job_dir = root / job_id
    (job_dir / "json").mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "channel_id": channel_id,
                "created_at": "2026-06-02T00:00:00Z",
                "updated_at": "2026-06-02T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "json" / "idea.json").write_text(
        json.dumps({"title_seed": title}),
        encoding="utf-8",
    )
    if with_required_artifacts:
        for rel in ("video.mp4", "script.json", "scenes.json", "seo.json"):
            (job_dir / rel).write_text("{}", encoding="utf-8")
    return job_dir


def test_job_queue_active_jobs_returns_pending_and_running(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.db")
    queue.enqueue("job-pending", enforce_approvals=False)
    queue.enqueue("job-running", enforce_approvals=False)
    queue.mark_running("job-running")
    queue.enqueue("job-complete", enforce_approvals=False)
    queue.mark_completed("job-complete")

    active = queue.active_jobs()

    assert {(row["job_id"], row["status"]) for row in active} == {
        ("job-pending", "pending"),
        ("job-running", "running"),
    }


@pytest.mark.parametrize("html_name", ["dashboard.html", "shorts_studio.html"])
def test_rendered_shorts_copy_hashtags_with_commas(html_name: str):
    html = (Path(__file__).parents[1] / "src" / "video_agent" / "web" / html_name).read_text(encoding="utf-8")

    assert "shortTagsList.join(',')" in html
    assert "shortTagsList.join(', ')" not in html
    assert "shortTagsList.join(' ')" not in html


def test_shorts_studio_renders_cache_does_not_keep_loading_placeholder():
    html = (Path(__file__).parents[1] / "src" / "video_agent" / "web" / "shorts_studio.html").read_text(encoding="utf-8")

    assert "rendersStillLoading" in html
    assert "stateKey === LAST_RENDERS_JSON_BY_JOB[jobId] && !rendersStillLoading" in html


def test_shorts_studio_displays_qa_scenes_attempts():
    html = (Path(__file__).parents[1] / "src" / "video_agent" / "web" / "shorts_studio.html").read_text(encoding="utf-8")

    assert "function renderQaScenesAttempts" in html
    assert "QA Scenes:" in html
    assert "qa_scenes_attempts" in html


def test_shorts_studio_state_uses_busy_guard_for_queue_jobs(client: TestClient, tmp_path: Path):
    _write_job(tmp_path, "job-1")
    queue = JobQueue(tmp_path / "queue.db")
    queue.enqueue("job-1", enforce_approvals=False)

    response = client.get("/shorts-studio/state")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_start"] is False
    assert any(item["kind"] == "queue" and item["job_id"] == "job-1" for item in body["active_jobs"])


def test_shorts_studio_state_detects_run_and_shorts_locks(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    shorts_root = job_dir / "shorts"
    shorts_root.mkdir(parents=True, exist_ok=True)
    short_dir = shorts_paths.short_dir(job_dir, "short-01")
    short_dir.mkdir(parents=True, exist_ok=True)
    held = []
    for path in (
        job_dir / ".run.lock",
        shorts_paths.autopilot_lock_path(job_dir),
        shorts_paths.short_lock_path(job_dir, "short-01"),
    ):
        path.write_text("", encoding="utf-8")
        fd = path.open("a+")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(fd)
    try:
        response = client.get("/shorts-studio/state")
    finally:
        for fd in held:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_start"] is False
    kinds = {item["kind"] for item in body["active_jobs"]}
    assert {"run_lock", "shorts_autopilot_lock", "short_lock"} <= kinds


def test_shorts_studio_state_lists_all_jobs_and_marks_ineligible(client: TestClient, tmp_path: Path):
    _write_job(tmp_path, "job-eligible", with_required_artifacts=True)
    _write_job(tmp_path, "job-missing", with_required_artifacts=False)

    response = client.get("/shorts-studio/state")

    assert response.status_code == 200, response.text
    body = response.json()
    jobs = {item["job_id"]: item for item in body["jobs"]}
    assert set(jobs) == {"job-eligible", "job-missing"}
    assert jobs["job-eligible"]["eligible"] is True
    assert jobs["job-eligible"]["missing"] == []
    assert jobs["job-missing"]["eligible"] is False
    assert "video.mp4" in jobs["job-missing"]["missing"]


def test_shorts_studio_drafts_reads_manifest_and_short_status(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    short_dir = shorts_paths.short_dir(job_dir, "short-01")
    short_dir.mkdir(parents=True, exist_ok=True)
    (short_dir / shorts_paths.SHORT_SCRIPT_FILE).write_text(json.dumps({"hook": "A"}), encoding="utf-8")
    (short_dir / shorts_paths.SHORT_SCENES_FILE).write_text(json.dumps({"scenes": []}), encoding="utf-8")
    (short_dir / shorts_paths.SHORT_QA_FILE).write_text(json.dumps({"qa_verdict": "PASS"}), encoding="utf-8")
    (short_dir / shorts_paths.SHORT_SOURCE_MAP_FILE).write_text(
        json.dumps({"source_scene_ids": ["scene-12", "scene-13"]}),
        encoding="utf-8",
    )
    (short_dir / shorts_paths.SHORT_STATUS_FILE).write_text(
        json.dumps(
            {
                "short_id": "short-01",
                "status": "ready_for_render",
                "rendered": False,
                "requires_render_confirmation": True,
                "qa_verdict": "PASS",
                "duration_sec": 37.5,
                "hook": "Dormir mejor",
                "cover_text": "3 cambios",
            }
        ),
        encoding="utf-8",
    )
    shorts_paths.manifest_path(job_dir).parent.mkdir(parents=True, exist_ok=True)
    shorts_paths.manifest_path(job_dir).write_text(
        json.dumps(
            {
                "status": "drafts_ready",
                "shorts": [
                    {
                        "short_id": "short-01",
                        "status": "ready_for_render",
                        "rendered": False,
                        "requires_render_confirmation": True,
                        "qa_verdict": "PASS",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/shorts-studio/jobs/job-1/drafts")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == "job-1"
    assert body["drafts"][0]["short_id"] == "short-01"
    assert body["drafts"][0]["status"] == "ready_for_render"
    assert body["drafts"][0]["source_scene_ids"] == ["scene-12", "scene-13"]


def test_shorts_studio_drafts_surfaces_qa_decision_and_failure(client: TestClient, tmp_path: Path):
    """A needs_review Short must surface its structured terminal decision +
    explicit failure reason so the UI can avoid the generic max-regen message."""
    job_dir = _write_job(tmp_path, "job-d")
    short_dir = shorts_paths.short_dir(job_dir, "short-01")
    (short_dir / "json").mkdir(parents=True, exist_ok=True)
    (short_dir / "json" / shorts_paths.SHORT_QA_DECISION_SUMMARY_FILE).write_text(
        json.dumps({
            "stage": "qa_scenes",
            "decision": "failed_hard_blocker",
            "renderable": False,
            "continued_to_render": False,
            "remaining_blockers": [{"detail": "unsupported health claim"}],
            "remaining_warnings": [],
            "attempts_used": 3,
            "max_attempts": 3,
        }),
        encoding="utf-8",
    )
    (short_dir / shorts_paths.SHORT_STATUS_FILE).write_text(
        json.dumps({
            "short_id": "short-01",
            "status": "needs_review",
            "qa_verdict": "FAIL",
            "failure_stage": "qa_scenes",
            "failure_reason": "qa_scenes hard blocker: unsupported health claim",
            "requires_user_review": True,
        }),
        encoding="utf-8",
    )
    shorts_paths.manifest_path(job_dir).parent.mkdir(parents=True, exist_ok=True)
    shorts_paths.manifest_path(job_dir).write_text(
        json.dumps({"status": "drafts_ready", "shorts": [{"short_id": "short-01", "status": "needs_review"}]}),
        encoding="utf-8",
    )

    body = client.get("/shorts-studio/jobs/job-d/drafts").json()
    draft = body["drafts"][0]
    assert draft["qa_decision"]["decision"] == "failed_hard_blocker"
    assert draft["qa_decision"]["remaining_blockers"][0]["detail"] == "unsupported health claim"
    assert draft["failure_stage"] == "qa_scenes"
    assert "unsupported health claim" in draft["failure_reason"]


def test_shorts_studio_prepare_returns_409_when_busy(client: TestClient, tmp_path: Path):
    _write_job(tmp_path, "job-1")
    queue = JobQueue(tmp_path / "queue.db")
    queue.enqueue("job-1", enforce_approvals=False)

    response = client.post("/shorts-studio/jobs/job-1/prepare", json={"force": False})

    assert response.status_code == 409


def test_shorts_studio_prepare_enqueues_new_command(client: TestClient, tmp_path: Path):
    _write_job(tmp_path, "job-1")

    response = client.post("/shorts-studio/jobs/job-1/prepare", json={"force": True})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["command"] == "shorts_prepare_drafts"
    row = JobQueue(tmp_path / "queue.db").get_job("job-1")
    assert row is not None
    assert row["command"] == "shorts_prepare_drafts"


def test_shorts_studio_confirm_render_rejects_non_pass_short(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    short_dir = shorts_paths.short_dir(job_dir, "short-01")
    short_dir.mkdir(parents=True, exist_ok=True)
    (short_dir / shorts_paths.SHORT_STATUS_FILE).write_text(
        json.dumps({"short_id": "short-01", "status": "needs_review", "qa_verdict": "FAIL"}),
        encoding="utf-8",
    )

    response = client.post("/shorts-studio/jobs/job-1/confirm-render", json={"short_ids": ["short-01"]})

    assert response.status_code == 400


def test_shorts_studio_confirm_render_enqueues_new_command(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    short_dir = shorts_paths.short_dir(job_dir, "short-01")
    short_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        (shorts_paths.SHORT_STATUS_FILE, {"short_id": "short-01", "status": "ready_for_render", "qa_verdict": "PASS"}),
        (shorts_paths.SHORT_SCRIPT_FILE, {"hook": "Dormir mejor"}),
        (shorts_paths.SHORT_SCENES_FILE, {"scenes": []}),
        (shorts_paths.SHORT_SEO_FILE, {"title": "Dormir mejor"}),
        (shorts_paths.SHORT_QA_FILE, {"qa_verdict": "PASS"}),
    ):
        (short_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    response = client.post("/shorts-studio/jobs/job-1/confirm-render", json={"short_ids": ["short-01"]})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["command"] == "shorts_confirm_render"
    row = JobQueue(tmp_path / "queue.db").get_job("job-1")
    assert row is not None
    assert row["command"] == "shorts_confirm_render"


def test_shorts_studio_state_maps_generate_ideas_queue_command(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    shorts_dir = job_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    queue = JobQueue(tmp_path / "queue.db")
    queue.enqueue("job-1", enforce_approvals=False, command="shorts_generate_ideas", payload={"target_count": 10})

    response = client.get("/shorts-studio/state")

    assert response.status_code == 200, response.text
    jobs = {item["job_id"]: item for item in response.json()["jobs"]}
    assert jobs["job-1"]["shorts_status"] == "ideas_generating"


def test_shorts_studio_state_failed_idea_run_overrides_old_ideas_ready(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    shorts_dir = job_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    (shorts_dir / "short_ideas.json").write_text(
        json.dumps({"generation_id": "ideas-old", "ideas": [{"idea_id": "idea-01"}]}),
        encoding="utf-8",
    )
    (shorts_dir / "idea_generation_run.json").write_text(
        json.dumps({"generation_id": "ideas-new", "status": "failed", "errors": ["boom"]}),
        encoding="utf-8",
    )

    response = client.get("/shorts-studio/state")

    assert response.status_code == 200, response.text
    jobs = {item["job_id"]: item for item in response.json()["jobs"]}
    assert jobs["job-1"]["shorts_status"] == "failed"


def test_shorts_studio_generate_ideas_enqueues_new_command(client: TestClient, tmp_path: Path):
    _write_job(tmp_path, "job-1")

    response = client.post("/shorts-studio/jobs/job-1/ideas/generate", json={"target_count": 8, "force": True})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["command"] == "shorts_generate_ideas"
    row = JobQueue(tmp_path / "queue.db").get_job("job-1")
    assert row is not None
    assert row["command"] == "shorts_generate_ideas"


def test_shorts_studio_get_ideas_returns_short_ideas_doc(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    shorts_dir = job_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    (shorts_dir / "short_ideas.json").write_text(
        json.dumps({"generation_id": "ideas-1", "ideas": [{"idea_id": "idea-01"}]}),
        encoding="utf-8",
    )

    response = client.get("/shorts-studio/jobs/job-1/ideas")

    assert response.status_code == 200, response.text
    assert response.json()["ideas"][0]["idea_id"] == "idea-01"


def test_shorts_studio_render_selected_enqueues_new_command(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    shorts_dir = job_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    (shorts_dir / "short_ideas.json").write_text(
        json.dumps({"generation_id": "ideas-1", "ideas": [{"idea_id": "idea-01"}]}),
        encoding="utf-8",
    )

    response = client.post("/shorts-studio/jobs/job-1/ideas/render", json={"idea_ids": ["idea-01"], "force": False})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["command"] == "shorts_render_selected_ideas"
    row = JobQueue(tmp_path / "queue.db").get_job("job-1")
    assert row is not None
    assert row["command"] == "shorts_render_selected_ideas"


def test_shorts_studio_get_ideas_returns_associated_short(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    shorts_dir = job_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    (shorts_dir / "short_ideas.json").write_text(
        json.dumps({"generation_id": "ideas-1", "ideas": [{"idea_id": "idea-01"}]}),
        encoding="utf-8",
    )
    (shorts_dir / "shorts_manifest.json").write_text(
        json.dumps({
            "shorts": [
                {
                    "short_id": "short-01",
                    "idea_id": "idea-01",
                    "status": "completed",
                    "rendered": True,
                }
            ]
        }),
        encoding="utf-8",
    )

    response = client.get("/shorts-studio/jobs/job-1/ideas")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ideas"][0]["associated_short"]["short_id"] == "short-01"
    assert data["ideas"][0]["associated_short"]["status"] == "completed"
    assert data["ideas"][0]["associated_short"]["rendered"] is True


def test_shorts_studio_render_selected_rejects_already_rendered(client: TestClient, tmp_path: Path):
    job_dir = _write_job(tmp_path, "job-1")
    shorts_dir = job_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    (shorts_dir / "short_ideas.json").write_text(
        json.dumps({"generation_id": "ideas-1", "ideas": [{"idea_id": "idea-01"}]}),
        encoding="utf-8",
    )
    (shorts_dir / "shorts_manifest.json").write_text(
        json.dumps({
            "shorts": [
                {
                    "short_id": "short-01",
                    "idea_id": "idea-01",
                    "status": "completed",
                    "rendered": True,
                }
            ]
        }),
        encoding="utf-8",
    )

    # Render without force should be rejected with 400
    response = client.post("/shorts-studio/jobs/job-1/ideas/render", json={"idea_ids": ["idea-01"], "force": False})
    assert response.status_code == 400
    assert "already_rendered" in response.json()["detail"]["error"]

    # Render with force=True should still be accepted
    response = client.post("/shorts-studio/jobs/job-1/ideas/render", json={"idea_ids": ["idea-01"], "force": True})
    assert response.status_code == 202
