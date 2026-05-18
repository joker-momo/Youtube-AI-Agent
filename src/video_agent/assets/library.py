from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aspect_ratio(width: int, height: int) -> str:
    ratio = width / height
    if abs(ratio - 16 / 9) < 0.08:
        return "16:9"
    if abs(ratio - 4 / 3) < 0.08:
        return "4:3"
    if abs(ratio - 1) < 0.08:
        return "1:1"
    return f"{width}:{height}"


class AssetLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.db_path = root / "metadata.db"
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_asset_id TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    file_hash TEXT NOT NULL,
                    perceptual_hash TEXT,
                    width INTEGER,
                    height INTEGER,
                    aspect_ratio TEXT,
                    original_url TEXT,
                    original_query TEXT,
                    provider_tags_json TEXT,
                    photographer TEXT,
                    photographer_url TEXT,
                    attribution TEXT,
                    license TEXT,
                    downloaded_at TEXT NOT NULL,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    is_banned INTEGER NOT NULL DEFAULT 0,
                    banned_reason TEXT
                )
                """
            )
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_provider_id ON assets(provider, provider_asset_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_assets_phash ON assets(perceptual_hash)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_assets_use_count ON assets(use_count)")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_usage (
                    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    scene_id TEXT NOT NULL,
                    scene_intent TEXT,
                    used_at TEXT NOT NULL,
                    FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_usage_asset ON asset_usage(asset_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_usage_channel ON asset_usage(channel_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_usage_job ON asset_usage(job_id)")

    def get_by_provider_id(self, provider: str, provider_asset_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM assets WHERE provider = ? AND provider_asset_id = ?",
                (provider, str(provider_asset_id)),
            ).fetchone()
            return dict(row) if row else None

    def get_by_asset_id(self, asset_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
            return dict(row) if row else None

    def is_file_valid(self, asset: dict[str, Any]) -> bool:
        path = self.root / asset["file_path"]
        return path.exists() and _sha256(path) == asset["file_hash"]

    def store_photo(self, candidate: dict[str, Any], downloaded_path: Path, original_query: str) -> dict[str, Any]:
        provider = candidate["provider"]
        provider_asset_id = str(candidate["provider_asset_id"])
        existing = self.get_by_provider_id(provider, provider_asset_id)
        quality = candidate.get("quality") or "photo"
        relative_dir = Path("photos") / provider / _now_iso()[:4] / _now_iso()[5:7]
        relative_path = relative_dir / f"{provider}_{provider_asset_id}_{quality}.jpg"
        absolute_path = self.root / (existing["file_path"] if existing else relative_path)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)

        if existing and self.is_file_valid(existing):
            return existing

        shutil.copy2(downloaded_path, absolute_path)
        file_hash = _sha256(absolute_path)
        with Image.open(absolute_path) as image:
            width, height = image.size
        values = {
            "asset_id": existing["asset_id"] if existing else str(uuid.uuid4()),
            "provider": provider,
            "provider_asset_id": provider_asset_id,
            "media_type": "photo",
            "file_path": str(absolute_path.relative_to(self.root)),
            "file_size_bytes": absolute_path.stat().st_size,
            "file_hash": file_hash,
            "perceptual_hash": None,
            "width": width,
            "height": height,
            "aspect_ratio": _aspect_ratio(width, height),
            "original_url": candidate.get("source_url"),
            "original_query": original_query,
            "provider_tags_json": json.dumps(candidate.get("tags", [])),
            "photographer": candidate.get("photographer"),
            "photographer_url": candidate.get("photographer_url"),
            "attribution": candidate.get("attribution"),
            "license": candidate.get("license"),
            "downloaded_at": existing["downloaded_at"] if existing else _now_iso(),
            "last_used_at": existing["last_used_at"] if existing else None,
            "use_count": existing["use_count"] if existing else 0,
            "is_banned": existing["is_banned"] if existing else 0,
            "banned_reason": existing["banned_reason"] if existing else None,
        }
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO assets (
                    asset_id, provider, provider_asset_id, media_type, file_path, file_size_bytes,
                    file_hash, perceptual_hash, width, height, aspect_ratio, original_url,
                    original_query, provider_tags_json, photographer, photographer_url,
                    attribution, license, downloaded_at, last_used_at, use_count, is_banned, banned_reason
                ) VALUES (
                    :asset_id, :provider, :provider_asset_id, :media_type, :file_path, :file_size_bytes,
                    :file_hash, :perceptual_hash, :width, :height, :aspect_ratio, :original_url,
                    :original_query, :provider_tags_json, :photographer, :photographer_url,
                    :attribution, :license, :downloaded_at, :last_used_at, :use_count, :is_banned, :banned_reason
                )
                """,
                values,
            )
        return self.get_by_asset_id(values["asset_id"]) or values

    def record_usage(
        self,
        asset_id: str,
        channel_id: str,
        job_id: str,
        scene_id: str,
        scene_intent: str | None = None,
    ) -> None:
        now = _now_iso()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO asset_usage (asset_id, channel_id, job_id, scene_id, scene_intent, used_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (asset_id, channel_id, job_id, scene_id, scene_intent, now),
            )
            db.execute(
                "UPDATE assets SET use_count = use_count + 1, last_used_at = ? WHERE asset_id = ?",
                (now, asset_id),
            )

    def list_usage(self, asset_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM asset_usage WHERE asset_id = ? ORDER BY usage_id", (asset_id,)).fetchall()
            return [dict(row) for row in rows]
