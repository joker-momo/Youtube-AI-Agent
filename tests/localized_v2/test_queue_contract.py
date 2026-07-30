from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from video_agent.localized_v2.job_state import JobInput
from video_agent.localized_v2.queue import LocalizedQueue, QueueBusyError


def _input(job_id: str = "job-a") -> JobInput:
    return JobInput(
        job_id=job_id,
        channel_id="healthy-life-en",
        locale="en-US",
        topic="A calm daily habit",
        channel_snapshot={"channelId": "healthy-life-en"},
        locale_snapshot={"locale": "en-US"},
    )


def test_two_workers_cannot_own_same_attempt(tmp_path: Path) -> None:
    queue = LocalizedQueue(tmp_path / "queue.db")
    queue.create_job(_input())

    first = queue.claim_next("worker-a", lease_seconds=30)
    second = queue.claim_next("worker-b", lease_seconds=30)

    assert first is not None
    assert first.owner_id == "worker-a"
    assert second is None
    assert queue.get_job("job-a")["attemptCount"] == 1


def test_heartbeat_requires_owner_and_extends_lease(tmp_path: Path) -> None:
    queue = LocalizedQueue(tmp_path / "queue.db")
    queue.create_job(_input())
    lease = queue.claim_next("worker-a", lease_seconds=1)
    assert lease is not None

    with pytest.raises(PermissionError):
        queue.heartbeat(lease.attempt_id, "worker-b", lease_seconds=30)
    renewed = queue.heartbeat(lease.attempt_id, "worker-a", lease_seconds=30)

    assert renewed.lease_expires_at > lease.lease_expires_at


def test_expired_lease_interrupts_and_resume_creates_one_attempt(tmp_path: Path) -> None:
    queue = LocalizedQueue(tmp_path / "queue.db")
    queue.create_job(_input())
    first = queue.claim_next("worker-a", lease_seconds=30)
    assert first is not None
    queue.record_stage(first.attempt_id, "worker-a", "script", completed=True)
    with sqlite3.connect(queue.db_path) as connection:
        connection.execute(
            "UPDATE attempts SET lease_expires_at = '2000-01-01T00:00:00+00:00'"
        )

    assert queue.recover_expired_leases() == 1
    assert queue.get_job("job-a")["status"] == "INTERRUPTED"
    queue.resume("job-a")
    second = queue.claim_next("worker-b", lease_seconds=30)

    assert second is not None
    assert second.number == 2
    assert queue.completed_stages("job-a") == ("script",)
    assert queue.claim_next("worker-c", lease_seconds=30) is None


def test_cancel_requested_job_finalizes_after_lease_expiry(tmp_path: Path) -> None:
    queue = LocalizedQueue(tmp_path / "queue.db")
    queue.create_job(_input())
    lease = queue.claim_next("worker-a", lease_seconds=30)
    assert lease is not None
    queue.request_cancel("job-a")
    with sqlite3.connect(queue.db_path) as connection:
        connection.execute(
            "UPDATE attempts SET lease_expires_at = '2000-01-01T00:00:00+00:00'"
        )

    queue.recover_expired_leases()

    assert queue.get_job("job-a")["status"] == "CANCELLED"
    assert queue.get_attempt(lease.attempt_id)["status"] == "CANCELLED"


def test_pending_cancel_never_becomes_claimable(tmp_path: Path) -> None:
    queue = LocalizedQueue(tmp_path / "queue.db")
    queue.create_job(_input())

    queue.request_cancel("job-a")

    assert queue.get_job("job-a")["status"] == "CANCELLED"
    assert queue.claim_next("worker-a", lease_seconds=30) is None


def test_retry_is_explicit_and_preserves_attempt_history(tmp_path: Path) -> None:
    queue = LocalizedQueue(tmp_path / "queue.db")
    queue.create_job(_input())
    first = queue.claim_next("worker-a", lease_seconds=30)
    assert first is not None
    queue.fail_attempt(
        first.attempt_id,
        "worker-a",
        {"code": "PROVIDER_TIMEOUT", "retryable": True},
    )

    queue.retry("job-a")
    second = queue.claim_next("worker-b", lease_seconds=30)

    assert second is not None
    assert second.number == 2
    assert queue.get_job("job-a")["attemptCount"] == 2


def test_sqlite_contention_is_bounded_and_structured(tmp_path: Path) -> None:
    queue = LocalizedQueue(tmp_path / "queue.db", busy_timeout_ms=25)
    queue.create_job(_input())
    blocker = sqlite3.connect(queue.db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(QueueBusyError) as error:
            queue.claim_next("worker-a", lease_seconds=30)
    finally:
        blocker.rollback()
        blocker.close()

    assert error.value.code == "QUEUE_BUSY"
    assert error.value.retryable is True
