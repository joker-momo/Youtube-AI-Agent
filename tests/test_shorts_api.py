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


def test_status_summary_maps_ready_for_render_to_drafts_ready(tmp_path: Path):
    from video_agent.shorts import status

    job = _make_job(tmp_path, with_shorts=False)
    sdir = job / "shorts"
    (sdir / "short-01").mkdir(parents=True)
    (sdir / "shorts_manifest.json").write_text(
        json.dumps(
            {
                "source_long_job_id": "job-1",
                "status": "drafts_ready",
                "shorts": [
                    {
                        "short_id": "short-01",
                        "status": "ready_for_render",
                        "qa_verdict": "PASS",
                        "requires_render_confirmation": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (sdir / "autopilot_run.json").write_text(
        json.dumps({"status": "drafts_ready", "mode": "prepare_drafts"}),
        encoding="utf-8",
    )

    summary = status.summarize_shorts(job)

    assert summary["state"] == "drafts_ready"
    assert summary["counts"]["ready_for_render"] == 1
    assert summary["counts"]["rendered"] == 0
    assert summary["label"] == "1 ready for render"


# --- GET /jobs/{id}/shorts -------------------------------------------------

def test_get_shorts_returns_summary(client: TestClient, tmp_path: Path):
    _make_job(tmp_path)
    r = client.get("/jobs/job-1/shorts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "1 rendered · 1 needs review"
    assert len(body["shorts"]) == 2


def test_get_shorts_returns_draft_ready_summary(client: TestClient, tmp_path: Path):
    _make_job(tmp_path, with_shorts=False)
    sdir = tmp_path / "job-1" / "shorts"
    (sdir / "short-01").mkdir(parents=True)
    (sdir / "shorts_manifest.json").write_text(
        json.dumps(
            {
                "source_long_job_id": "job-1",
                "status": "drafts_ready",
                "shorts": [
                    {
                        "short_id": "short-01",
                        "status": "ready_for_render",
                        "qa_verdict": "PASS",
                        "requires_render_confirmation": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (sdir / "autopilot_run.json").write_text(
        json.dumps({"status": "drafts_ready", "mode": "prepare_drafts"}),
        encoding="utf-8",
    )

    r = client.get("/jobs/job-1/shorts")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "drafts_ready"
    assert body["counts"]["ready_for_render"] == 1
    assert body["shorts"][0]["status"] == "ready_for_render"


def test_get_shorts_unknown_job_404(client: TestClient, tmp_path: Path):
    assert client.get("/jobs/missing/shorts").status_code == 404


def test_timeline_appends_shorts_autopilot_virtual_stage(client: TestClient, tmp_path: Path):
    from video_agent.orchestrator.job_state import DEFAULT_STAGES

    job = _make_job(tmp_path)
    payload = {
        "job_id": "job-1",
        "channel_id": "vida-plena-45",
        "idea_path": "idea.json",
        "created_at": "2026-05-31T00:00:00Z",
        "updated_at": "2026-05-31T00:10:00Z",
        "current_stage": "review",
        "stages": [
            {
                "name": name,
                "status": "completed",
                "started_at": "2026-05-31T00:00:00Z",
                "completed_at": "2026-05-31T00:01:00Z",
                "error": None,
            }
            for name in DEFAULT_STAGES
        ],
    }
    (job / "job.json").write_text(json.dumps(payload), encoding="utf-8")

    r = client.get("/jobs/job-1/timeline")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stages"][-1]["name"] == "shorts_autopilot"
    assert body["stages"][-1]["label"] == "Shorts Autopilot"
    assert body["stages"][-1]["status"] == "completed"
    assert body["stages"][-1]["sub_progress"]["kind"] == "shorts_autopilot"
    assert [s["short_id"] for s in body["stages"][-1]["sub_progress"]["shorts"]] == ["short-01", "short-02"]
    assert body["stages_total"] == len(DEFAULT_STAGES) + 1
    assert body["stages_done"] == len(DEFAULT_STAGES) + 1


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


def test_worker_dispatches_shorts_prepare_drafts_command(tmp_path: Path, monkeypatch):
    from video_agent.orchestrator import worker

    _make_job(tmp_path, with_shorts=False)
    called = {}

    def fake_prepare(job, *, job_dir, channel_path, client):
        called["job_id"] = job["job_id"]
        called["job_dir"] = job_dir.name

    monkeypatch.setattr(worker, "_run_shorts_prepare_drafts_job", fake_prepare)
    worker._dispatch_queue_job(
        {"job_id": "job-1", "enforce_approvals": 0, "command": "shorts_prepare_drafts", "payload": "{}"},
        jobs_root=tmp_path,
        channel_path=Path("configs/vida-plena-45/channel.yaml"),
        client=object(),
    )

    assert called == {"job_id": "job-1", "job_dir": "job-1"}


def test_worker_dispatches_shorts_confirm_render_command(tmp_path: Path, monkeypatch):
    from video_agent.orchestrator import worker

    _make_job(tmp_path, with_shorts=False)
    called = {}

    def fake_confirm(job, *, job_dir, channel_path):
        called["job_id"] = job["job_id"]
        called["job_dir"] = job_dir.name

    monkeypatch.setattr(worker, "_run_shorts_confirm_render_job", fake_confirm)
    worker._dispatch_queue_job(
        {"job_id": "job-1", "enforce_approvals": 0, "command": "shorts_confirm_render", "payload": "{\"short_ids\":[\"short-01\"]}"},
        jobs_root=tmp_path,
        channel_path=Path("configs/vida-plena-45/channel.yaml"),
        client=object(),
    )

    assert called == {"job_id": "job-1", "job_dir": "job-1"}


def test_worker_dispatches_shorts_generate_ideas_command(tmp_path: Path, monkeypatch):
    from video_agent.orchestrator import worker

    _make_job(tmp_path, with_shorts=False)
    called = {}

    def fake_generate(job, *, job_dir, channel_path, client):
        called["job_id"] = job["job_id"]
        called["job_dir"] = job_dir.name

    monkeypatch.setattr(worker, "_run_shorts_generate_ideas_job", fake_generate)
    worker._dispatch_queue_job(
        {"job_id": "job-1", "enforce_approvals": 0, "command": "shorts_generate_ideas", "payload": "{\"target_count\":10}"},
        jobs_root=tmp_path,
        channel_path=Path("configs/vida-plena-45/channel.yaml"),
        client=object(),
    )

    assert called == {"job_id": "job-1", "job_dir": "job-1"}


def test_worker_dispatches_shorts_render_selected_ideas_command(tmp_path: Path, monkeypatch):
    from video_agent.orchestrator import worker

    _make_job(tmp_path, with_shorts=False)
    called = {}

    def fake_render_selected(job, *, job_dir, channel_path, client):
        called["job_id"] = job["job_id"]
        called["job_dir"] = job_dir.name

    monkeypatch.setattr(worker, "_run_shorts_render_selected_ideas_job", fake_render_selected)
    worker._dispatch_queue_job(
        {"job_id": "job-1", "enforce_approvals": 0, "command": "shorts_render_selected_ideas", "payload": "{\"idea_ids\":[\"idea-01\"]}"},
        jobs_root=tmp_path,
        channel_path=Path("configs/vida-plena-45/channel.yaml"),
        client=object(),
    )

    assert called == {"job_id": "job-1", "job_dir": "job-1"}


def test_short_render_job_updates_draft_ready_manifest_and_run(tmp_path: Path, monkeypatch):
    from video_agent.orchestrator import worker

    job = _make_job(tmp_path, with_shorts=False)
    sdir = job / "shorts" / "short-01"
    sdir.mkdir(parents=True, exist_ok=True)
    for name in ("short_script.json", "short_scenes.json", "short_seo.json", "short_script_qa.json", "short_scenes_qa.json", "short_render_props.json"):
        (sdir / name).write_text("{}", encoding="utf-8")
    (job / "shorts" / "shorts_manifest.json").write_text(
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
    (job / "shorts" / "autopilot_run.json").write_text(
        json.dumps(
            {
                "status": "drafts_ready",
                "mode": "prepare_drafts",
                "ready_for_render_count": 1,
                "rendered_count": 0,
                "needs_review_count": 0,
                "failed_count": 0,
            }
        ),
        encoding="utf-8",
    )
    (sdir / "short_status.json").write_text(
        json.dumps({"short_id": "short-01", "status": "ready_for_render", "qa_verdict": "PASS"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(worker, "render_short_video", lambda short_dir, channel_config: short_dir / "short.mp4", raising=False)
    monkeypatch.setattr(worker, "render_short_cover", lambda short_dir, channel_config: short_dir / "short_cover.jpg", raising=False)

    def fake_video(short_dir, channel_config):
        (short_dir / "short.mp4").write_bytes(b"v")
        return short_dir / "short.mp4"

    def fake_cover(short_dir, channel_config):
        (short_dir / "short_cover.jpg").write_bytes(b"j")
        return short_dir / "short_cover.jpg"

    monkeypatch.setattr("video_agent.shorts.renderer.render_short_video", fake_video)
    monkeypatch.setattr("video_agent.shorts.renderer.render_short_cover", fake_cover)

    worker._run_short_render_job(
        {"job_id": "job-1", "payload": json.dumps({"short_id": "short-01"})},
        job_dir=job,
        channel_path=Path("configs/vida-plena-45/channel.yaml"),
    )

    status_doc = json.loads((sdir / "short_status.json").read_text(encoding="utf-8"))
    manifest_doc = json.loads((job / "shorts" / "shorts_manifest.json").read_text(encoding="utf-8"))
    run_doc = json.loads((job / "shorts" / "autopilot_run.json").read_text(encoding="utf-8"))
    assert status_doc["status"] == "rendered"
    assert manifest_doc["shorts"][0]["status"] == "rendered"
    assert run_doc["rendered_count"] == 1
    assert run_doc["ready_for_render_count"] == 0


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


# --- §18.4 regenerate ONE short --------------------------------------------

def test_regenerate_one_short_archives_only_that_short_dir(tmp_path: Path, monkeypatch):
    """POST /regenerate must archive only the specified short folder and
    enqueue a rebuild for that short, NOT skip due to manifest existence."""
    from video_agent.shorts import paths, manifest as manifest_mod
    job = _make_job(tmp_path)
    sd = paths.short_dir(job, "short-01")
    (sd / "marker.json").write_text("{}", encoding="utf-8")  # legacy file in short-01

    enqueued = {}
    import video_agent.web.routes.shorts as shorts_route

    def fake_enqueue(job_dir, channel_config, *, force, client, short_id=None):
        enqueued["force"] = force
        enqueued["short_id"] = short_id
        enqueued["job"] = job_dir.name

    monkeypatch.setattr(shorts_route, "enqueue_shorts_autopilot", fake_enqueue)
    from video_agent.web.app import get_browser_client
    app.dependency_overrides[get_browser_client] = lambda: object()
    try:
        c = TestClient(app)
        app.dependency_overrides[get_jobs_root] = lambda: tmp_path
        r = c.post("/jobs/job-1/shorts/short-01/regenerate")
    finally:
        app.dependency_overrides.pop(get_browser_client, None)

    assert r.status_code == 202, r.text
    # short-01 dir archived
    arch = paths.archive_dir(job)
    assert arch.exists()
    # original short-01 dir moved away
    assert not (sd / "marker.json").exists()
    # autopilot enqueued for that short
    assert enqueued["short_id"] == "short-01"


def test_worker_shorts_autopilot_with_short_id_rebuilds_only_that_short(tmp_path: Path):
    """When the autopilot is invoked with target_short_id, the plan must be
    filtered to just that short so its rebuild succeeds even when a manifest
    exists for other rendered shorts."""
    from video_agent.shorts import autopilot, manifest
    job = tmp_path / "long-job"
    job.mkdir()
    for f in ("script.json", "scenes.json", "seo.json"):
        (job / f).write_text("{}", encoding="utf-8")
    (job / "video.mp4").write_bytes(b"x")

    # Pre-existing manifest with short-01 rendered, short-02 rendered.
    manifest.write_manifest(job, {
        "source_long_job_id": "long-job", "status": "completed",
        "shorts": [
            {"short_id": "short-01", "status": "rendered"},
            {"short_id": "short-02", "status": "rendered"},
        ],
    })

    built: list[str] = []

    def fake_plan(long_job_dir, channel_config, requested_count=None):
        return {"selected_shorts": [
            {"short_id": "short-01", "format": "pain_to_tip"},
            {"short_id": "short-02", "format": "mistake_to_avoid"},
        ], "warnings": []}

    def fake_build(long_job_dir, short_plan, cfg):
        built.append(short_plan["short_id"])
        return {"short_id": short_plan["short_id"], "status": "rendered", "qa_verdict": "PASS"}

    cfg = {"shorts": {"autopilot": {"skip_if_shorts_already_exist": True}}}
    # target_short_id=short-02 + force=True → archive short-02 only, rebuild it
    result = autopilot.run_shorts_autopilot(
        job, cfg, plan_fn=fake_plan, build_short_fn=fake_build,
        force=True, target_short_id="short-02",
    )
    assert built == ["short-02"]
    assert result["status"] in ("completed", "completed_with_warnings")


# --- §19.5 Short detail drawer (7 tabs) ------------------------------------

def test_dashboard_html_has_short_detail_drawer_with_seven_tabs():
    """Dashboard must include a Short detail drawer with 7 tabs per spec
    §19.5: Overview | Script | Scenes | Source Map | SEO | QA | Render."""
    from pathlib import Path as _P
    html = _P("src/video_agent/web/dashboard.html").read_text(encoding="utf-8")
    # drawer DOM marker
    assert 'id="short-drawer"' in html, "missing #short-drawer"
    # opener wired from short cards
    assert "openShortDrawer" in html, "missing openShortDrawer() JS"
    # 7 tab names appear as tab buttons
    for tab in ("Overview", "Script", "Scenes", "Source Map", "SEO", "QA", "Render"):
        assert f'data-tab="{tab.lower().replace(" ", "-")}"' in html, f"missing tab: {tab}"


# --- §2.1 dead-code cleanup -------------------------------------------------

def test_legacy_shorts_render_progress_route_removed():
    """Spec §2.1: 'shorts_render' references must be removed/deprecated.
    The legacy GET /jobs/{id}/stages/shorts_render/progress endpoint is dead."""
    from pathlib import Path as _P
    src = _P("src/video_agent/web/routes/_legacy.py").read_text(encoding="utf-8")
    assert "/stages/shorts_render/progress" not in src, "dead shorts_render route still present"


def test_legacy_chatgpt_shorts_prompt_removed():
    """Spec §2.1 lists _chatgpt_shorts_script_prompt for removal/deprecation."""
    from pathlib import Path as _P
    src = _P("src/video_agent/operator.py").read_text(encoding="utf-8")
    assert "_chatgpt_shorts_script_prompt" not in src, "dead legacy shorts prompt still present"
