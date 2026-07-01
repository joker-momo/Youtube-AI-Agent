"""Regression coverage for timeline status derivation during a DAG-mode run
(bridge P2/P3 report: current_stage/approval_blocked_by stale, graphic_images
sub_progress undercounted, shorts_autopilot shown in_progress prematurely).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_agent.orchestrator.job_state import DEFAULT_STAGES, JobState, StageStatus, save_job
from video_agent.orchestrator.queue import JobQueue
from video_agent.web.routes.timeline import job_timeline


def _make_job(tmp_path: Path, job_id: str = "job-active") -> Path:
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "json").mkdir()
    # Simulate a DAG-mode run: everything through visual_schedule is completed,
    # render is in_progress, but state.current_stage is FROZEN at "visual_spans"
    # (the pointer stops advancing once DAG mode takes over post-scenes stages —
    # see _apply_stage_completion's "if not dag_mode()" guard).
    stages = []
    frozen_at = "visual_spans"
    completed_before_render = [
        "idea_research", "script", "script_promote", "script_qa", "scenes",
        "scenes_promote", "scenes_qa", "visual_spans", "seo", "seo_promote",
        "seo_qa", "graphic_images", "thumbnail_image", "whisper_timestamps",
        "visual_schedule",
    ]
    for name in DEFAULT_STAGES:
        if name in completed_before_render:
            stages.append(StageStatus(name=name, status="completed", completed_at="2026-07-01T12:00:00+00:00"))
        elif name == "render":
            stages.append(StageStatus(name=name, status="in_progress", started_at="2026-07-01T12:28:00+00:00"))
        else:
            stages.append(StageStatus(name=name))
    state = JobState(
        job_id=job_id,
        channel_id="vida-plena-45",
        idea_path="idea.json",
        created_at="2026-07-01T11:00:00+00:00",
        updated_at="2026-07-01T12:53:00+00:00",
        current_stage=frozen_at,
        stages=stages,
    )
    save_job(job_dir, state)
    return job_dir


def _make_completed_job(tmp_path: Path, job_id: str = "job-done") -> Path:
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "json").mkdir()
    # DAG-mode run that finished: EVERY stage completed, so nothing is
    # in_progress anymore. state.current_stage is still frozen at "visual_spans"
    # (the pointer never advances under DAG mode — see _make_job above). An
    # in-progress-only fallback would keep returning the stale pointer forever,
    # even for a fully finished job (bug found after fb5684c's initial fix:
    # Codex confirmed the API still showed current_stage=visual_spans and
    # approval_blocked_by=idea_research on a completed job after a dashboard
    # restart, ruling out "stale process" as the cause).
    stages = [
        StageStatus(name=name, status="completed", completed_at="2026-07-01T13:58:34+00:00")
        for name in DEFAULT_STAGES
    ]
    state = JobState(
        job_id=job_id,
        channel_id="vida-plena-45",
        idea_path="idea.json",
        created_at="2026-07-01T11:00:00+00:00",
        updated_at="2026-07-01T13:58:34+00:00",
        current_stage="visual_spans",
        stages=stages,
    )
    save_job(job_dir, state)
    return job_dir


@pytest.fixture
def jobs_root(tmp_path: Path) -> Path:
    return tmp_path


def test_current_stage_reflects_in_progress_stage_not_stale_pointer(jobs_root: Path):
    job_dir = _make_job(jobs_root)
    result = job_timeline("job-active", jobs_root)
    assert result["current_stage"] == "render"
    assert result["current_stage"] != "visual_spans"


def test_approval_blocked_by_uses_live_stage_not_stale_pointer(jobs_root: Path):
    # idea_research is a completed, already-approved stage far behind "render".
    # The stale pointer bug made approval_blocked_by report idea_research as
    # still blocking, even though render (way past it) is actively running.
    job_dir = _make_job(jobs_root)
    result = job_timeline("job-active", jobs_root)
    assert result["approval_blocked_by"] is None


def test_graphic_images_sub_progress_counts_all_graphic_layouts(jobs_root: Path):
    job_dir = _make_job(jobs_root)
    scenes = {
        "scenes": [
            {"id": "scene-01", "layout": "checklist"},
            {"id": "scene-02", "layout": "plate_map"},
            {"id": "scene-03", "layout": "quote_portrait"},
            {"id": "scene-04", "layout": "myth"},
            {"id": "scene-05", "layout": "subtitle"},  # no card needed
        ]
    }
    (job_dir / "json" / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")
    assets_dir = job_dir / "assets"
    assets_dir.mkdir()
    for name in ("graphic-scene-01.png", "graphic-scene-02.png"):
        (assets_dir / name).write_bytes(b"fake")

    result = job_timeline("job-active", jobs_root)
    graphic_stage = next(s for s in result["stages"] if s["name"] == "graphic_images")
    sub = graphic_stage["sub_progress"]
    # Previously hardcoded to only checklist/warning/quote/cta -> total=1,
    # done=2 -> "Image 2/1" (done > total, nonsensical). Fixed: all 4
    # graphic-needing scenes counted.
    assert sub["total"] == 4
    assert sub["done"] == 2


def test_shorts_autopilot_not_in_progress_before_review_completes(jobs_root: Path):
    job_dir = _make_job(jobs_root)
    # Parent job's queue row is "running" (render stage active) — the raw
    # short_owner_is_alive signal would report shorts as running too, even
    # though shorts autopilot only fires after "review" completes and no
    # shorts manifest exists yet.
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(job_id="job-active", command="run_all", enforce_approvals=False)
    queue.mark_running("job-active")

    result = job_timeline("job-active", jobs_root)
    shorts_stage = next((s for s in result["stages"] if s.get("name") == "shorts_autopilot"), None)
    assert shorts_stage is None


def test_shorts_autopilot_shows_in_progress_once_review_completes(jobs_root: Path):
    job_dir = _make_job(jobs_root)
    state_path = job_dir / "job.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for s in state["stages"]:
        if s["name"] == "review":
            s["status"] = "completed"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(job_id="job-active", command="run_all", enforce_approvals=False)
    queue.mark_running("job-active")

    result = job_timeline("job-active", jobs_root)
    shorts_stage = next((s for s in result["stages"] if s.get("name") == "shorts_autopilot"), None)
    assert shorts_stage is not None
    assert shorts_stage["status"] == "in_progress"


def test_completed_dag_job_reports_last_stage_not_frozen_pointer(jobs_root: Path):
    job_dir = _make_completed_job(jobs_root)
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(job_id="job-done", command="run_all", enforce_approvals=False)
    queue.mark_running("job-done")
    queue.mark_completed("job-done")

    result = job_timeline("job-done", jobs_root)
    assert result["current_stage"] == DEFAULT_STAGES[-1]
    assert result["current_stage"] != "visual_spans"


def test_completed_dag_job_approval_blocked_by_is_none(jobs_root: Path):
    job_dir = _make_completed_job(jobs_root)
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(job_id="job-done", command="run_all", enforce_approvals=False)
    queue.mark_running("job-done")
    queue.mark_completed("job-done")

    result = job_timeline("job-done", jobs_root)
    assert result["approval_blocked_by"] is None
