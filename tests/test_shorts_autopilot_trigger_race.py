"""Long-form completion must not auto-enqueue Shorts autopilot.

Shorts autopilot can still be run explicitly through its routes/queue command,
but a completed long ``run_all`` job should stop at the long video. This keeps
the long-video pipeline from starting a second browser/render workload under
the same job id.
"""
from __future__ import annotations

from pathlib import Path

from video_agent.orchestrator import worker
from video_agent.orchestrator.queue import JobQueue


def test_enqueue_during_still_running_row_is_dropped(tmp_path: Path):
    """Reproduces the ROOT bug: enqueuing a new command for a job_id that is
    still 'running' (the state execute_run_all's inline trigger runs in,
    since mark_completed hasn't fired yet) is a silent no-op."""
    queue = JobQueue(tmp_path / "queue.db")
    queue.enqueue("job-a", enforce_approvals=False, command="run_all")
    queue.mark_running("job-a")  # simulates the worker mid-dispatch, pre-mark_completed

    ok = queue.enqueue("job-a", enforce_approvals=False, command="shorts_autopilot")

    assert ok is False
    row = queue.get_job("job-a")
    assert row["command"] == "run_all"  # untouched -- autopilot dispatch was dropped


def test_maybe_trigger_shorts_autopilot_noop_after_mark_completed(tmp_path: Path, monkeypatch):
    """A completed long ``run_all`` must not enqueue Shorts automatically."""
    queue = JobQueue(tmp_path / "queue.db")
    queue.enqueue("job-a", enforce_approvals=False, command="run_all")
    queue.mark_running("job-a")
    queue.mark_completed("job-a")

    job_dir = tmp_path / "job-a"
    job_dir.mkdir()
    channel_path = tmp_path / "channel.yaml"
    channel_path.write_text("shorts:\n  enabled: true\n", encoding="utf-8")

    calls = []

    def _fake_enqueue(jd, cfg, *, force, client=None, short_id=None):
        calls.append(jd)
        queue.enqueue(jd.name, enforce_approvals=False, command="shorts_autopilot")

    monkeypatch.setattr("video_agent.web.routes.shorts.enqueue_shorts_autopilot", _fake_enqueue)

    worker._maybe_trigger_shorts_autopilot(
        job_id="job-a", job_dir=job_dir, channel_path=channel_path,
        client=None, command="run_all",
    )

    assert calls == []
    row = queue.get_job("job-a")
    assert row["command"] == "run_all"
    assert row["status"] == "completed"


def test_maybe_trigger_shorts_autopilot_noop_for_other_commands(tmp_path: Path, monkeypatch):
    called = False

    def _fake_should_run(jd, cfg):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(
        "video_agent.shorts.trigger.should_run_autopilot_after_review", _fake_should_run
    )

    worker._maybe_trigger_shorts_autopilot(
        job_id="job-a", job_dir=tmp_path / "job-a", channel_path=tmp_path / "channel.yaml",
        client=None, command="stage_render",
    )

    assert called is False  # non-run_all commands never even check the trigger


def test_maybe_trigger_shorts_autopilot_noop_when_review_not_passed(tmp_path: Path, monkeypatch):
    job_dir = tmp_path / "job-a"
    job_dir.mkdir()
    channel_path = tmp_path / "channel.yaml"
    channel_path.write_text("shorts:\n  enabled: true\n", encoding="utf-8")

    monkeypatch.setattr(
        "video_agent.shorts.trigger.should_run_autopilot_after_review",
        lambda jd, cfg: False,
    )
    called = False

    def _fake_enqueue(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr("video_agent.web.routes.shorts.enqueue_shorts_autopilot", _fake_enqueue)

    worker._maybe_trigger_shorts_autopilot(
        job_id="job-a", job_dir=job_dir, channel_path=channel_path,
        client=None, command="run_all",
    )

    assert called is False
