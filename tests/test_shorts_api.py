"""Shorts Autopilot v5 — Phase 7: backend API + status/source-url."""
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


def _make_job(tmp_path: Path, with_shorts=True) -> Path:
    job = tmp_path / "job-1"
    job.mkdir()
    (job / "job.json").write_text(json.dumps({"job_id": "job-1", "channel_id": "vida-plena-45"}), encoding="utf-8")
    if with_shorts:
        sdir = job / "shorts"
        (sdir / "short-01").mkdir(parents=True)
        (sdir / "short-02").mkdir(parents=True)
        (sdir / "shorts_manifest.json").write_text(json.dumps({
            "source_long_job_id": "job-1", "status": "completed_with_warnings",
            "shorts": [
                {"short_id": "short-01", "status": "rendered", "qa_verdict": "PASS"},
                {"short_id": "short-02", "status": "needs_review", "qa_verdict": "FAIL"},
            ],
        }), encoding="utf-8")
        (sdir / "autopilot_run.json").write_text(json.dumps({"status": "completed_with_warnings"}), encoding="utf-8")
        (sdir / "short-01" / "short_status.json").write_text(json.dumps({"short_id": "short-01", "status": "rendered", "uploaded": False}), encoding="utf-8")
        (sdir / "short-01" / "short_seo.json").write_text(json.dumps({"short_id": "short-01", "title": "t", "long_video_url": "", "pinned_comment": "old"}), encoding="utf-8")
    return job


# --- status summary --------------------------------------------------------

def test_status_summary_label_mixed(tmp_path: Path):
    from video_agent.shorts import status
    job = _make_job(tmp_path)
    s = status.summarize_shorts(job)
    assert s["counts"]["rendered"] == 1
    assert s["counts"]["needs_review"] == 1
    assert s["label"] == "1 rendered · 1 needs review"


def test_status_summary_none_when_no_manifest(tmp_path: Path):
    from video_agent.shorts import status
    job = _make_job(tmp_path, with_shorts=False)
    assert status.summarize_shorts(job)["state"] == "none"


# --- GET /jobs/{id}/shorts -------------------------------------------------

def test_get_shorts_returns_summary(client: TestClient, tmp_path: Path):
    _make_job(tmp_path)
    r = client.get("/jobs/job-1/shorts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "1 rendered · 1 needs review"
    assert len(body["shorts"]) == 2


def test_get_shorts_unknown_job_404(client: TestClient, tmp_path: Path):
    assert client.get("/jobs/missing/shorts").status_code == 404


# --- source-url ------------------------------------------------------------

def test_source_url_updates_manifest_and_seo(client: TestClient, tmp_path: Path):
    _make_job(tmp_path)
    r = client.post("/jobs/job-1/shorts/source-url", json={"long_video_url": "https://youtu.be/abc"})
    assert r.status_code == 200, r.text
    assert r.json()["updated_shorts"] == 1
    seo = json.loads((tmp_path / "job-1" / "shorts" / "short-01" / "short_seo.json").read_text())
    assert seo["long_video_url"] == "https://youtu.be/abc"
    manifest = json.loads((tmp_path / "job-1" / "shorts" / "shorts_manifest.json").read_text())
    assert manifest["source_video_url"] == "https://youtu.be/abc"


# --- uploaded --------------------------------------------------------------

def test_mark_uploaded_sets_status(client: TestClient, tmp_path: Path):
    _make_job(tmp_path)
    r = client.post("/jobs/job-1/shorts/short-01/uploaded", json={"youtube_url": "https://youtu.be/xyz"})
    assert r.status_code == 200, r.text
    st = json.loads((tmp_path / "job-1" / "shorts" / "short-01" / "short_status.json").read_text())
    assert st["uploaded"] is True
    assert st["youtube_url"] == "https://youtu.be/xyz"


# --- autopilot POST (monkeypatched, no browser) ----------------------------

def test_autopilot_post_enqueues(client: TestClient, tmp_path: Path, monkeypatch):
    _make_job(tmp_path, with_shorts=False)
    calls = {}
    import video_agent.web.routes.shorts as shorts_route

    def fake_enqueue(job_dir, channel_config, *, force, client):
        calls["force"] = force
        calls["job"] = job_dir.name

    monkeypatch.setattr(shorts_route, "enqueue_shorts_autopilot", fake_enqueue)
    # browser client dependency → dummy
    from video_agent.web.app import get_browser_client
    app.dependency_overrides[get_browser_client] = lambda: object()
    try:
        r = client.post("/jobs/job-1/shorts/autopilot", json={"force": True})
    finally:
        app.dependency_overrides.pop(get_browser_client, None)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "enqueued"
    assert calls == {"force": True, "job": "job-1"}


# --- trigger gate ----------------------------------------------------------

def _cfg_enabled():
    return {"shorts": {"enabled": True, "autopilot": {"enabled": True, "run_after_long_review_pass": True}}}


def test_trigger_true_when_review_pass(tmp_path: Path):
    from video_agent.shorts.trigger import should_run_autopilot_after_review
    job = tmp_path / "j"; job.mkdir()
    (job / "review.json").write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    assert should_run_autopilot_after_review(job, _cfg_enabled()) is True


def test_trigger_false_when_review_fail(tmp_path: Path):
    from video_agent.shorts.trigger import should_run_autopilot_after_review
    job = tmp_path / "j"; job.mkdir()
    (job / "review.json").write_text(json.dumps({"verdict": "FAIL"}), encoding="utf-8")
    assert should_run_autopilot_after_review(job, _cfg_enabled()) is False


def test_trigger_false_when_disabled(tmp_path: Path):
    from video_agent.shorts.trigger import should_run_autopilot_after_review
    job = tmp_path / "j"; job.mkdir()
    (job / "review.json").write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    assert should_run_autopilot_after_review(job, {"shorts": {"enabled": False}}) is False
