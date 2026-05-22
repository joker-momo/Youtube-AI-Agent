from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class JobQueue:
    def __init__(self, db_path: Path):
        self.db_path = db_path
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
                    error TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON job_queue(status)")

    def enqueue(self, job_id: str, enforce_approvals: bool) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO job_queue (job_id, status, enforce_approvals) VALUES (?, 'pending', ?)",
                    (job_id, 1 if enforce_approvals else 0)
                )
                return True
            except sqlite3.IntegrityError:
                conn.execute(
                    "UPDATE job_queue SET status = 'pending', enforce_approvals = ?, error = NULL, started_at = NULL, completed_at = NULL WHERE job_id = ?",
                    (1 if enforce_approvals else 0, job_id)
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

    def mark_running(self, job_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE job_queue SET status = 'running', started_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                (job_id,)
            )

    def mark_completed(self, job_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE job_queue SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                (job_id,)
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE job_queue SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error = ? WHERE job_id = ?",
                (error, job_id)
            )

    def requeue_running_jobs(self) -> int:
        """Recover jobs left in 'running' after worker crash/restart."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE job_queue SET status = 'pending', started_at = NULL WHERE status = 'running'"
            )
            return int(cursor.rowcount or 0)
