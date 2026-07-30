from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from video_agent.localized_v2.job_state import JobInput, PromotedArtifact

ACTIVE_ATTEMPT = "RUNNING"
TERMINAL_JOB_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class QueueBusyError(RuntimeError):
    code = "QUEUE_BUSY"
    retryable = True


@dataclass(frozen=True, slots=True)
class AttemptLease:
    attempt_id: str
    job_id: str
    number: int
    owner_id: str
    lease_expires_at: str


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class LocalizedQueue:
    def __init__(self, db_path: Path, *, busy_timeout_ms: int = 2500):
        self.db_path = db_path.resolve()
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        channel_id TEXT NOT NULL,
                        locale TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        input_json TEXT NOT NULL,
                        current_stage TEXT,
                        current_attempt_id TEXT,
                        cancel_requested_at TEXT,
                        terminal_result_json TEXT,
                        failure_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS attempts (
                        attempt_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL REFERENCES jobs(job_id),
                        number INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        lease_expires_at TEXT NOT NULL,
                        heartbeat_at TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        failure_json TEXT,
                        UNIQUE(job_id, number)
                    );
                    CREATE TABLE IF NOT EXISTS completed_stages (
                        job_id TEXT NOT NULL REFERENCES jobs(job_id),
                        stage TEXT NOT NULL,
                        attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                        ordinal INTEGER NOT NULL,
                        completed_at TEXT NOT NULL,
                        PRIMARY KEY(job_id, stage)
                    );
                    CREATE TABLE IF NOT EXISTS artifacts (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL REFERENCES jobs(job_id),
                        stage TEXT NOT NULL,
                        name TEXT NOT NULL,
                        path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        promoted_at TEXT NOT NULL,
                        UNIQUE(job_id, stage, name)
                    );
                    CREATE TABLE IF NOT EXISTS events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL REFERENCES jobs(job_id),
                        attempt_id TEXT,
                        type TEXT NOT NULL,
                        stage TEXT,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS jobs_status_created
                    ON jobs(status, created_at);
                    CREATE INDEX IF NOT EXISTS attempts_lease
                    ON attempts(status, lease_expires_at);
                    """
                )
        except sqlite3.OperationalError as exc:
            self._raise_busy(exc)
        self._schema_ready = True

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        self._ensure_schema()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.OperationalError as exc:
            connection.rollback()
            self._raise_busy(exc)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _raise_busy(exc: sqlite3.OperationalError) -> None:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise QueueBusyError("localized V2 queue is busy; retry later") from exc
        raise exc

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        *,
        attempt_id: str | None = None,
        stage: str | None = None,
        payload: dict | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(job_id, attempt_id, type, stage, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, attempt_id, event_type, stage, _json(payload or {}), _timestamp()),
        )

    def create_job(self, job_input: JobInput) -> None:
        now = _timestamp()
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, status, channel_id, locale, topic, input_json,
                    created_at, updated_at
                ) VALUES (?, 'PENDING', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_input.job_id,
                    job_input.channel_id,
                    job_input.locale,
                    job_input.topic,
                    _json(job_input.to_dict()),
                    now,
                    now,
                ),
            )
            self._event(connection, job_input.job_id, "JOB_CREATED")

    def delete_unstarted(self, job_id: str) -> None:
        with self._write() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row and row["status"] == "PENDING":
                connection.execute("DELETE FROM events WHERE job_id = ?", (job_id,))
                connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))

    def _job_snapshot(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        attempts = connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE job_id = ?", (row["job_id"],)
        ).fetchone()[0]
        return {
            "jobId": row["job_id"],
            "status": row["status"],
            "channelId": row["channel_id"],
            "locale": row["locale"],
            "topic": row["topic"],
            "input": json.loads(row["input_json"]),
            "currentStage": row["current_stage"],
            "currentAttemptId": row["current_attempt_id"],
            "attemptCount": attempts,
            "cancelRequestedAt": row["cancel_requested_at"],
            "terminalResult": (
                json.loads(row["terminal_result_json"])
                if row["terminal_result_json"]
                else None
            ),
            "failure": json.loads(row["failure_json"]) if row["failure_json"] else None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        if not self.db_path.exists():
            return None
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._job_snapshot(connection, row) if row else None

    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        if not self.db_path.exists():
            return None
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            return dict(row) if row else None

    def _recover_expired_conn(self, connection: sqlite3.Connection) -> int:
        expired = connection.execute(
            """
            SELECT a.attempt_id, a.job_id, j.status AS job_status
            FROM attempts a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.status = 'RUNNING' AND a.lease_expires_at <= ?
            """,
            (_timestamp(),),
        ).fetchall()
        for row in expired:
            cancelled = row["job_status"] == "CANCEL_REQUESTED"
            attempt_status = "CANCELLED" if cancelled else "INTERRUPTED"
            job_status = "CANCELLED" if cancelled else "INTERRUPTED"
            now = _timestamp()
            connection.execute(
                "UPDATE attempts SET status = ?, completed_at = ? WHERE attempt_id = ?",
                (attempt_status, now, row["attempt_id"]),
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, current_attempt_id = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (job_status, now, row["job_id"]),
            )
            self._event(
                connection,
                row["job_id"],
                "LEASE_EXPIRED",
                attempt_id=row["attempt_id"],
                payload={"status": job_status},
            )
        return len(expired)

    def recover_expired_leases(self) -> int:
        with self._write() as connection:
            return self._recover_expired_conn(connection)

    def claim_next(self, owner_id: str, *, lease_seconds: int) -> AttemptLease | None:
        with self._write() as connection:
            self._recover_expired_conn(connection)
            job = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'PENDING'
                ORDER BY created_at ASC, job_id ASC
                LIMIT 1
                """
            ).fetchone()
            if job is None:
                return None
            number = connection.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM attempts WHERE job_id = ?",
                (job["job_id"],),
            ).fetchone()[0]
            attempt_id = str(uuid.uuid4())
            now = _now()
            lease_expiry = _timestamp(now + timedelta(seconds=max(1, lease_seconds)))
            now_text = _timestamp(now)
            connection.execute(
                """
                INSERT INTO attempts(
                    attempt_id, job_id, number, status, owner_id,
                    lease_expires_at, heartbeat_at, started_at
                ) VALUES (?, ?, ?, 'RUNNING', ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    job["job_id"],
                    number,
                    owner_id,
                    lease_expiry,
                    now_text,
                    now_text,
                ),
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'RUNNING', current_attempt_id = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (attempt_id, now_text, job["job_id"]),
            )
            self._event(
                connection,
                job["job_id"],
                "ATTEMPT_CLAIMED",
                attempt_id=attempt_id,
                payload={"ownerId": owner_id, "number": number},
            )
            return AttemptLease(
                attempt_id=attempt_id,
                job_id=job["job_id"],
                number=number,
                owner_id=owner_id,
                lease_expires_at=lease_expiry,
            )

    def _owned_attempt(
        self, connection: sqlite3.Connection, attempt_id: str, owner_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown attempt: {attempt_id}")
        if row["owner_id"] != owner_id:
            raise PermissionError(f"attempt {attempt_id} is owned by another worker")
        if row["status"] != ACTIVE_ATTEMPT:
            raise ValueError(f"attempt {attempt_id} is not running")
        return row

    def heartbeat(
        self, attempt_id: str, owner_id: str, *, lease_seconds: int
    ) -> AttemptLease:
        with self._write() as connection:
            row = self._owned_attempt(connection, attempt_id, owner_id)
            now = _now()
            expiry = _timestamp(now + timedelta(seconds=max(1, lease_seconds)))
            connection.execute(
                """
                UPDATE attempts SET heartbeat_at = ?, lease_expires_at = ?
                WHERE attempt_id = ?
                """,
                (_timestamp(now), expiry, attempt_id),
            )
            return AttemptLease(
                attempt_id=attempt_id,
                job_id=row["job_id"],
                number=row["number"],
                owner_id=owner_id,
                lease_expires_at=expiry,
            )

    def record_stage(
        self,
        attempt_id: str,
        owner_id: str,
        stage: str,
        *,
        completed: bool,
    ) -> None:
        with self._write() as connection:
            attempt = self._owned_attempt(connection, attempt_id, owner_id)
            now = _timestamp()
            connection.execute(
                "UPDATE jobs SET current_stage = ?, updated_at = ? WHERE job_id = ?",
                (stage, now, attempt["job_id"]),
            )
            event_type = "STAGE_COMPLETED" if completed else "STAGE_STARTED"
            if completed:
                ordinal = connection.execute(
                    """
                    SELECT COALESCE(MAX(ordinal), 0) + 1
                    FROM completed_stages WHERE job_id = ?
                    """,
                    (attempt["job_id"],),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO completed_stages(
                        job_id, stage, attempt_id, ordinal, completed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (attempt["job_id"], stage, attempt_id, ordinal, now),
                )
            self._event(
                connection,
                attempt["job_id"],
                event_type,
                attempt_id=attempt_id,
                stage=stage,
            )

    def completed_stages(self, job_id: str) -> tuple[str, ...]:
        if not self.db_path.exists():
            return ()
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT stage FROM completed_stages
                WHERE job_id = ? ORDER BY ordinal ASC
                """,
                (job_id,),
            ).fetchall()
            return tuple(row["stage"] for row in rows)

    def request_cancel(self, job_id: str) -> None:
        with self._write() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown job: {job_id}")
            if row["status"] in {"CANCELLED", "CANCEL_REQUESTED"}:
                return
            if row["status"] == "COMPLETED":
                raise ValueError("completed jobs cannot be cancelled")
            now = _timestamp()
            status = "CANCEL_REQUESTED" if row["status"] == "RUNNING" else "CANCELLED"
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, cancel_requested_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, now, now, job_id),
            )
            self._event(connection, job_id, status)

    def is_cancel_requested(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return bool(job and job["status"] == "CANCEL_REQUESTED")

    def acknowledge_cancel(self, attempt_id: str, owner_id: str) -> None:
        with self._write() as connection:
            attempt = self._owned_attempt(connection, attempt_id, owner_id)
            now = _timestamp()
            connection.execute(
                """
                UPDATE attempts SET status = 'CANCELLED', completed_at = ?
                WHERE attempt_id = ?
                """,
                (now, attempt_id),
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'CANCELLED', current_attempt_id = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (now, attempt["job_id"]),
            )
            self._event(
                connection,
                attempt["job_id"],
                "CANCELLED",
                attempt_id=attempt_id,
            )

    def complete_attempt(
        self, attempt_id: str, owner_id: str, result: dict | None = None
    ) -> None:
        with self._write() as connection:
            attempt = self._owned_attempt(connection, attempt_id, owner_id)
            now = _timestamp()
            connection.execute(
                """
                UPDATE attempts SET status = 'COMPLETED', completed_at = ?
                WHERE attempt_id = ?
                """,
                (now, attempt_id),
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'COMPLETED', current_attempt_id = NULL,
                    terminal_result_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (_json(result or {}), now, attempt["job_id"]),
            )
            self._event(
                connection,
                attempt["job_id"],
                "COMPLETED",
                attempt_id=attempt_id,
            )

    def fail_attempt(self, attempt_id: str, owner_id: str, failure: dict) -> None:
        with self._write() as connection:
            attempt = self._owned_attempt(connection, attempt_id, owner_id)
            now = _timestamp()
            failure_json = _json(failure)
            connection.execute(
                """
                UPDATE attempts
                SET status = 'FAILED', completed_at = ?, failure_json = ?
                WHERE attempt_id = ?
                """,
                (now, failure_json, attempt_id),
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'FAILED', current_attempt_id = NULL,
                    failure_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (failure_json, now, attempt["job_id"]),
            )
            self._event(
                connection,
                attempt["job_id"],
                "FAILED",
                attempt_id=attempt_id,
                payload=failure,
            )

    def _requeue(self, job_id: str, allowed: set[str]) -> None:
        with self._write() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown job: {job_id}")
            if row["status"] not in allowed:
                raise ValueError(f"job {job_id} cannot be requeued from {row['status']}")
            connection.execute(
                """
                UPDATE jobs
                SET status = 'PENDING', current_attempt_id = NULL,
                    cancel_requested_at = NULL, failure_json = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (_timestamp(), job_id),
            )
            self._event(connection, job_id, "REQUEUED")

    def retry(self, job_id: str) -> None:
        self._requeue(job_id, {"FAILED"})

    def resume(self, job_id: str, *, allow_cancelled: bool = False) -> None:
        allowed = {"INTERRUPTED"}
        if allow_cancelled:
            allowed.add("CANCELLED")
        self._requeue(job_id, allowed)

    def register_artifacts(
        self,
        attempt_id: str,
        owner_id: str,
        stage: str,
        artifacts: tuple[PromotedArtifact, ...],
    ) -> None:
        with self._write() as connection:
            attempt = self._owned_attempt(connection, attempt_id, owner_id)
            for artifact in artifacts:
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        job_id, stage, name, path, sha256, promoted_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt["job_id"],
                        stage,
                        artifact.name,
                        str(artifact.path),
                        artifact.sha256,
                        _timestamp(),
                    ),
                )

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT stage, name, path, sha256, promoted_at
                FROM artifacts WHERE job_id = ? ORDER BY sequence ASC
                """,
                (job_id,),
            ).fetchall()
            return [dict(row) for row in rows]
