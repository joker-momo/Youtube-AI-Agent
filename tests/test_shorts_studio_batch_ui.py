"""API and HTML acceptance tests for multi-select Short render batches."""

from __future__ import annotations

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


def _write_job(root: Path) -> Path:
    job_dir = root / "job-1"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "job.json").write_text(
        json.dumps({"job_id": "job-1", "channel_id": "vida-plena-45"}),
        encoding="utf-8",
    )
    (job_dir / "json" / "idea.json").write_text("{}", encoding="utf-8")
    for rel in ("video.mp4", "script.json", "scenes.json", "seo.json"):
        (job_dir / rel).write_text("{}", encoding="utf-8")
    shorts_paths.shorts_dir(job_dir).mkdir(parents=True)
    shorts_paths.short_ideas_path(job_dir).write_text(
        json.dumps(
            {
                "generation_id": "ideas-1",
                "ideas": [
                    {"idea_id": "idea-03", "title": "Tercera"},
                    {"idea_id": "idea-01", "title": "Primera"},
                    {"idea_id": "idea-08", "title": "Octava"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return job_dir


def test_post_multiple_ideas_persists_ordered_batch_before_enqueue(client, tmp_path):
    job_dir = _write_job(tmp_path)

    response = client.post(
        "/shorts-studio/jobs/job-1/ideas/render",
        json={"idea_ids": ["idea-03", "idea-01", "idea-08"], "short_type": "infographic", "force": False},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["batch_id"].startswith("srb-")
    assert body["idea_ids"] == ["idea-03", "idea-01", "idea-08"]
    assert body["total_count"] == 3
    assert body["remaining_count"] == 3

    batch = json.loads(shorts_paths.render_batch_path(job_dir).read_text(encoding="utf-8"))
    assert batch["batch_id"] == body["batch_id"]
    assert [item["idea_id"] for item in batch["items"]] == body["idea_ids"]
    row = JobQueue(tmp_path / "queue.db").get_job("job-1")
    payload = json.loads(row["payload"])
    assert row["command"] == "shorts_render_infographic"
    assert payload["batch_id"] == body["batch_id"]
    assert payload["idea_ids"] == body["idea_ids"]


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"idea_ids": ["idea-03", "idea-03"], "short_type": "infographic"}, "duplicate_idea_ids"),
        ({"idea_ids": ["idea-missing"], "short_type": "infographic"}, "invalid_idea_ids"),
        ({"idea_ids": ["idea-03"], "short_type": "unknown"}, "invalid_short_type"),
    ],
)
def test_post_batch_rejects_invalid_contract(client, tmp_path, payload, error):
    _write_job(tmp_path)

    response = client.post("/shorts-studio/jobs/job-1/ideas/render", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == error


def test_active_non_terminal_batch_rejects_second_submission(client, tmp_path):
    _write_job(tmp_path)
    first = client.post(
        "/shorts-studio/jobs/job-1/ideas/render",
        json={"idea_ids": ["idea-03", "idea-01"], "short_type": "infographic"},
    )
    assert first.status_code == 202

    second = client.post(
        "/shorts-studio/jobs/job-1/ideas/render",
        json={"idea_ids": ["idea-08"], "short_type": "infographic"},
    )

    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "active_render_batch"


def test_get_render_batch_returns_idle_then_durable_snapshot(client, tmp_path):
    _write_job(tmp_path)
    idle = client.get("/shorts-studio/jobs/job-1/ideas/render-batch")
    assert idle.status_code == 200
    assert idle.json() == {
        "status": "idle",
        "total_count": 0,
        "completed_count": 0,
        "failed_count": 0,
        "remaining_count": 0,
        "items": [],
    }

    created = client.post(
        "/shorts-studio/jobs/job-1/ideas/render",
        json={"idea_ids": ["idea-01"], "short_type": "infographic"},
    )
    snapshot = client.get("/shorts-studio/jobs/job-1/ideas/render-batch")
    assert snapshot.status_code == 200
    assert snapshot.json()["batch_id"] == created.json()["batch_id"]
    assert snapshot.json()["total_count"] == 1


def test_single_idea_request_remains_a_valid_one_item_batch(client, tmp_path):
    _write_job(tmp_path)

    response = client.post(
        "/shorts-studio/jobs/job-1/ideas/render",
        json={"idea_ids": ["idea-03"], "force": False},
    )

    assert response.status_code == 202
    assert response.json()["command"] == "shorts_render_infographic"
    assert response.json()["total_count"] == 1


def test_shorts_studio_html_exposes_multi_select_and_batch_progress_contract():
    html = (Path(__file__).parents[1] / "src" / "video_agent" / "web" / "shorts_studio.html").read_text(
        encoding="utf-8"
    )

    for marker in (
        "shorts-idea-checkbox",
        "sp-select-all-ideas",
        "sp-selected-count",
        "sp-render-selected",
        "shorts-batch-progress",
        "shorts-batch-current",
        "shorts-batch-remaining",
        "/ideas/render-batch",
    ):
        assert marker in html

    assert "idea_ids: selectedIdeaIds" in html
    assert "Render selected" in html

