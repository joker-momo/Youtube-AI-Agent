from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from typing import Any


class JobQueue:
    def __init__(
        self,
        db_path: Path,
        max_attempts: int = 3,
        stale_running_seconds: int = 180,
    ):
        self.db_path = db_path
        self.max_attempts = max(1, int(max_attempts))
        self.stale_running_seconds = max(30, int(stale_running_seconds))
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_queue (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    enforce_approvals INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    command TEXT NOT NULL DEFAULT 'run_all',
                    payload TEXT,
                    heartbeat_at TIMESTAMP,
                    error TEXT
                )
            """)
            existing = {
                row[1] for row in conn.execute("PRAGMA table_info(job_queue)").fetchall()
            }
            if "attempts" not in existing:
                conn.execute(
                    "ALTER TABLE job_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
                )
            if "command" not in existing:
                conn.execute(
                    "ALTER TABLE job_queue ADD COLUMN command TEXT NOT NULL DEFAULT 'run_all'"
                )
            if "payload" not in existing:
                conn.execute("ALTER TABLE job_queue ADD COLUMN payload TEXT")
            if "heartbeat_at" not in existing:
                conn.execute("ALTER TABLE job_queue ADD COLUMN heartbeat_at TIMESTAMP")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON job_queue(status)")

    def _is_running_stale_conn(self, conn: sqlite3.Connection, job_id: str) -> bool:
        row = conn.execute(
            """
            SELECT status,
                   CAST(strftime('%s', 'now') AS INTEGER)
                   - CAST(strftime('%s', COALESCE(heartbeat_at, started_at)) AS INTEGER) AS age_seconds
            FROM job_queue
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None or row[0] != "running":
            return False
        age = row[1]
        return age is None or int(age) > self.stale_running_seconds

    def is_running_stale(self, job_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            return self._is_running_stale_conn(conn, job_id)

    def enqueue(
        self,
        job_id: str,
        enforce_approvals: bool,
        *,
        command: str = "run_all",
        payload: dict[str, Any] | None = None,
    ) -> bool:
        payload_text = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO job_queue (job_id, status, enforce_approvals, command, payload) VALUES (?, 'pending', ?, ?, ?)",
                    (job_id, 1 if enforce_approvals else 0, command or "run_all", payload_text)
                )
                return True
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT status FROM job_queue WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row and row[0] == "running":
                    if not self._is_running_stale_conn(conn, job_id):
                        return False
                conn.execute(
                    "UPDATE job_queue SET status = 'pending', enforce_approvals = ?, command = ?, payload = ?, attempts = 0, error = NULL, started_at = NULL, completed_at = NULL, heartbeat_at = NULL WHERE job_id = ?",
                    (1 if enforce_approvals else 0, command or "run_all", payload_text, job_id)
                )
                return True

    def get_next_job(self) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM job_queue WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM job_queue WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def mark_running(self, job_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE job_queue SET status = 'running', started_at = CURRENT_TIMESTAMP, heartbeat_at = CURRENT_TIMESTAMP, completed_at = NULL WHERE job_id = ?",
                (job_id,)
            )

    def touch_running(self, job_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE job_queue SET heartbeat_at = CURRENT_TIMESTAMP WHERE job_id = ? AND status = 'running'",
                (job_id,),
            )

    def mark_completed(self, job_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE job_queue SET status = 'completed', completed_at = CURRENT_TIMESTAMP, heartbeat_at = NULL WHERE job_id = ?",
                (job_id,)
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE job_queue SET status = 'failed', completed_at = CURRENT_TIMESTAMP, heartbeat_at = NULL, error = ? WHERE job_id = ?",
                (error, job_id)
            )

    def mark_retry(self, job_id: str, error: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT attempts FROM job_queue WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return False
            attempts = int(row["attempts"]) + 1
            if attempts >= self.max_attempts:
                conn.execute(
                    "UPDATE job_queue SET status = 'failed', attempts = ?, completed_at = CURRENT_TIMESTAMP, heartbeat_at = NULL, error = ? WHERE job_id = ?",
                    (attempts, error, job_id),
                )
                return False
            conn.execute(
                "UPDATE job_queue SET status = 'pending', attempts = ?, started_at = NULL, completed_at = NULL, heartbeat_at = NULL, error = ? WHERE job_id = ?",
                (attempts, error, job_id),
            )
            return True

    def requeue_running_jobs(self) -> int:
        """Recover jobs left in 'running' after worker crash/restart."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE job_queue SET status = 'pending', started_at = NULL, heartbeat_at = NULL WHERE status = 'running'"
            )
            return int(cursor.rowcount or 0)

    def requeue_stale_running_jobs(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE job_queue
                SET status = 'pending', started_at = NULL, heartbeat_at = NULL
                WHERE status = 'running'
                  AND (
                    COALESCE(heartbeat_at, started_at) IS NULL
                    OR CAST(strftime('%s', 'now') AS INTEGER)
                       - CAST(strftime('%s', COALESCE(heartbeat_at, started_at)) AS INTEGER) > ?
                  )
                """,
                (self.stale_running_seconds,),
            )
            return int(cursor.rowcount or 0)
