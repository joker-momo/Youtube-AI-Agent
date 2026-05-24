from __future__ import annotations

from pathlib import Path

from video_agent.orchestrator.queue import JobQueue
import video_agent.orchestrator.worker as worker
from video_agent.orchestrator.worker import _dispatch_queue_job, _is_retryable_exception
from fastapi import HTTPException


def test_queue_retries_failed_job_until_max_attempts(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.db", max_attempts=3)
    queue.enqueue("job-a", enforce_approvals=False)

    job = queue.get_next_job()
    assert job is not None
    assert job["attempts"] == 0

    queue.mark_running("job-a")
    assert queue.mark_retry("job-a", "temporary browser error") is True

    retried = queue.get_next_job()
    assert retried is not None
    assert retried["job_id"] == "job-a"
    assert retried["attempts"] == 1

    queue.mark_running("job-a")
    assert queue.mark_retry("job-a", "temporary browser error") is True

    queue.mark_running("job-a")
    assert queue.mark_retry("job-a", "temporary browser error") is False
    assert queue.get_next_job() is None

    failed = queue.get_job("job-a")
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["attempts"] == 3


def test_requeue_running_jobs_preserves_attempt_count(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.db", max_attempts=3)
    queue.enqueue("job-a", enforce_approvals=False)
    queue.mark_running("job-a")
    assert queue.mark_retry("job-a", "temporary browser error") is True
    queue.mark_running("job-a")

    recovered = queue.requeue_running_jobs()

    assert recovered == 1
    job = queue.get_next_job()
    assert job is not None
    assert job["job_id"] == "job-a"
    assert job["attempts"] == 1


def test_enqueue_records_command_and_payload(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.db", max_attempts=3)

    queue.enqueue(
        "job-a",
        enforce_approvals=False,
        command="stage_render",
        payload={"reason": "manual"},
    )

    job = queue.get_next_job()
    assert job is not None
    assert job["command"] == "stage_render"
    assert job["payload"] == '{"reason": "manual"}'


def test_existing_queue_rows_default_to_run_all(tmp_path: Path):
    queue = JobQueue(tmp_path / "queue.db", max_attempts=3)
    queue.enqueue("job-a", enforce_approvals=False)

    job = queue.get_next_job()

    assert job is not None
    assert job["command"] == "run_all"
    assert job["payload"] is None


def test_worker_dispatches_render_command(monkeypatch, tmp_path: Path):
    calls: list[tuple[Path, Path]] = []

    def fake_render(job_dir: Path, channel_path: Path) -> Path:
        calls.append((job_dir, channel_path))
        return job_dir / "video.mp4"

    monkeypatch.setattr(worker, "run_render_stage", fake_render)

    _dispatch_queue_job(
        {"job_id": "job-a", "command": "stage_render", "enforce_approvals": 0},
        jobs_root=tmp_path,
        channel_path=tmp_path / "channel.yaml",
        client=object(),
    )

    assert calls == [(tmp_path / "job-a", tmp_path / "channel.yaml")]


def test_worker_retry_classification_skips_operator_blocks():
    assert _is_retryable_exception(RuntimeError("temporary")) is True
    assert (
        _is_retryable_exception(
            HTTPException(status_code=409, detail={"approval_required": "seo"})
        )
        is False
    )
    assert (
        _is_retryable_exception(
            HTTPException(status_code=409, detail={"login_required": True})
        )
        is False
    )
    assert (
        _is_retryable_exception(
            HTTPException(status_code=409, detail={"stop_requested": True})
        )
        is False
    )
