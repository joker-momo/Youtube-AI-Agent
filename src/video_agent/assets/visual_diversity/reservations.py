"""Cross-job race-safe asset reservations (spec §20).

Uses the existing asset_library/metadata.db. Same-job duplicate prevention
remains in StockAssetService job state; this layer protects against two
concurrent jobs racing on the same Pexels asset.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def resolve_reservation_db_path(visual_config: dict[str, Any], repo_root: Path | None = None) -> Path:
    asset_library_path = (visual_config or {}).get("asset_library_path") or "asset_library"
    base = Path(asset_library_path)
    if not base.is_absolute() and repo_root is not None:
        base = Path(repo_root) / base
    return base / "metadata.db"


def try_reserve_asset(
    db_path: Path,
    asset_id: str,
    channel_id: str,
    job_id: str,
    scene_id: str,
    ttl_minutes: int,
    now: datetime | None = None,
) -> bool:
    """Atomically check + insert reservation. Returns False if another job holds it."""
    now = now or _utc_now()
    expires = now + timedelta(minutes=max(1, ttl_minutes))

    with sqlite3.connect(str(db_path), timeout=30, isolation_level="IMMEDIATE") as db:
        db.row_factory = sqlite3.Row
        db.execute(
            "DELETE FROM asset_reservations WHERE expires_at <= ?",
            (_iso(now),),
        )
        active = db.execute(
            """
            SELECT 1
            FROM asset_reservations
            WHERE asset_id = ?
              AND expires_at > ?
              AND job_id != ?
            LIMIT 1
            """,
            (asset_id, _iso(now), job_id),
        ).fetchone()
        if active:
            return False

        db.execute(
            """
            INSERT INTO asset_reservations (
                reservation_id, asset_id, channel_id, job_id, scene_id, reserved_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                asset_id,
                channel_id,
                job_id,
                scene_id,
                _iso(now),
                _iso(expires),
            ),
        )
        return True


def is_actively_reserved(
    db_path: Path,
    asset_id: str,
    job_id: str,
    now: datetime | None = None,
) -> bool:
    now = now or _utc_now()
    with sqlite3.connect(str(db_path), timeout=10) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """
            SELECT 1 FROM asset_reservations
            WHERE asset_id = ? AND expires_at > ? AND job_id != ?
            LIMIT 1
            """,
            (asset_id, _iso(now), job_id),
        ).fetchone()
        return row is not None


def release_reservation(db_path: Path, asset_id: str, job_id: str, scene_id: str) -> None:
    with sqlite3.connect(str(db_path), timeout=10) as db:
        db.execute(
            "DELETE FROM asset_reservations WHERE asset_id = ? AND job_id = ? AND scene_id = ?",
            (asset_id, job_id, scene_id),
        )
