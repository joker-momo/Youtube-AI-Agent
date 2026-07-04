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
    if not width or not height:
        return "unknown"
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
        # bug-470: is_file_valid() used to re-hash the full file on EVERY call.
        # Asset selection re-validates the same handful of popular library
        # videos once per scene (85 scenes x up to 10 candidates), so the same
        # file was being SHA256'd dozens of times per render -- a multi-hundred-MB
        # library and 85 scenes turned this into 20+ minutes of pure re-hashing
        # before Remotion even started. Cache the validation result per absolute
        # path, keyed by (size, mtime_ns) so a genuine on-disk change (re-download,
        # corruption, edit) still forces a real re-hash, but repeat checks of an
        # unchanged file within this instance's lifetime (one asset-prep run) are
        # instant.
        self._valid_cache: dict[str, tuple[int, int, bool]] = {}

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
            self._migrate_visual_diversity(db)

    @staticmethod
    def _columns(db: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}

    def _migrate_visual_diversity(self, db: sqlite3.Connection) -> None:
        """Idempotent schema additions for spec v5.4. Safe to call repeatedly."""
        asset_cols = self._columns(db, "assets")
        for col, ddl in (
            ("visual_bucket", "visual_bucket TEXT"),
            ("shot_type", "shot_type TEXT"),
            ("mood", "mood TEXT"),
            ("locale_feel", "locale_feel TEXT"),
            ("creator_key", "creator_key TEXT"),
            ("duration_sec", "duration_sec REAL"),
            ("quality_score", "quality_score REAL"),
            ("metadata_json", "metadata_json TEXT"),
        ):
            if col not in asset_cols:
                db.execute(f"ALTER TABLE assets ADD COLUMN {ddl}")

        usage_cols = self._columns(db, "asset_usage")
        for col, ddl in (
            ("visual_bucket", "visual_bucket TEXT"),
            ("shot_type", "shot_type TEXT"),
            ("duration_used_sec", "duration_used_sec REAL"),
            ("topic", "topic TEXT"),
        ):
            if col not in usage_cols:
                db.execute(f"ALTER TABLE asset_usage ADD COLUMN {ddl}")

        db.execute("CREATE INDEX IF NOT EXISTS idx_assets_creator_key ON assets(creator_key)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_assets_visual_bucket ON assets(visual_bucket)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_assets_shot_type ON assets(shot_type)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_usage_asset_used_at ON asset_usage(asset_id, used_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_usage_job_scene ON asset_usage(job_id, scene_id)")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_reservations (
                reservation_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                scene_id TEXT NOT NULL,
                reserved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_reservations_asset ON asset_reservations(asset_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_reservations_expires ON asset_reservations(expires_at)")

    def list_usage_for_asset(self, asset_id: str) -> list[dict[str, Any]]:
        """Return usage rows sorted ascending by used_at."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM asset_usage WHERE asset_id = ? ORDER BY used_at ASC",
                (asset_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_visual_metadata(
        self,
        asset_id: str,
        *,
        visual_bucket: str | None = None,
        shot_type: str | None = None,
        mood: str | None = None,
        locale_feel: str | None = None,
        creator_key: str | None = None,
        duration_sec: float | None = None,
        quality_score: float | None = None,
        metadata_json: str | None = None,
    ) -> None:
        """Lazy backfill for spec v5.4 columns. Only writes provided fields."""
        sets: list[str] = []
        values: list[Any] = []
        for col, val in (
            ("visual_bucket", visual_bucket),
            ("shot_type", shot_type),
            ("mood", mood),
            ("locale_feel", locale_feel),
            ("creator_key", creator_key),
            ("duration_sec", duration_sec),
            ("quality_score", quality_score),
            ("metadata_json", metadata_json),
        ):
            if val is not None:
                sets.append(f"{col} = ?")
                values.append(val)
        if not sets:
            return
        values.append(asset_id)
        with self._connect() as db:
            db.execute(
                f"UPDATE assets SET {', '.join(sets)} WHERE asset_id = ?",
                values,
            )

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
        try:
            stat = path.stat()
        except OSError:
            return False
        cache_key = str(path)
        cached = self._valid_cache.get(cache_key)
        if cached is not None and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
            return cached[2]
        is_valid = _sha256(path) == asset["file_hash"]
        self._valid_cache[cache_key] = (stat.st_size, stat.st_mtime_ns, is_valid)
        return is_valid

    def search_by_query(
        self,
        query: str,
        media_type: str | None = None,
        exclude_asset_ids: set[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return cached assets whose original_query or tags overlap with query terms.

        Used to avoid re-downloading when a suitable asset already exists in the library.
        Scored by term overlap; caller picks the best match.
        """
        import re

        def _tokens(text: str) -> set[str]:
            return {t for t in re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ0-9]+", text.lower()) if len(t) > 2}

        query_terms = _tokens(query)
        if not query_terms:
            return []

        params: list[Any] = []
        if media_type:
            sql = (
                "SELECT * FROM assets WHERE is_banned = 0 AND media_type = ? "
                "ORDER BY use_count ASC"
            )
            params.append(media_type)
        else:
            sql = "SELECT * FROM assets WHERE is_banned = 0 ORDER BY use_count ASC"

        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()

        exclude = exclude_asset_ids or set()
        # bug-470 follow-up: is_file_valid (full SHA256) used to run on EVERY
        # non-excluded row BEFORE scoring -- the first search_by_query call in
        # a run swept the entire library (1000+ videos, ~15GB) through the
        # hasher and stalled asset prep for ~10 minutes on a USB disk. Score
        # first (pure string work, no I/O), then validate only the ranked
        # candidates top-down, stopping once `limit` valid ones are collected.
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            asset = dict(row)
            if asset["asset_id"] in exclude:
                continue
            # Score: overlap between query terms and stored query + tags
            stored_text = " ".join(filter(None, [
                asset.get("original_query") or "",
                asset.get("provider_tags_json") or "",
            ]))
            stored_terms = _tokens(stored_text)
            overlap = len(query_terms & stored_terms)
            if overlap > 0:
                scored.append((overlap, asset))

        scored.sort(key=lambda x: (-x[0], x[1]["use_count"]))
        results: list[dict[str, Any]] = []
        for _, asset in scored:
            if not self.is_file_valid(asset):
                continue
            results.append(asset)
            if len(results) >= limit:
                break
        return results

    def store_photo(self, candidate: dict[str, Any], downloaded_path: Path, original_query: str) -> dict[str, Any]:
        provider = candidate["provider"]
        provider_asset_id = str(candidate["provider_asset_id"])
        existing = self.get_by_provider_id(provider, provider_asset_id)
        quality = candidate.get("quality") or "photo"
        now = _now_iso()
        relative_dir = Path("photos") / provider / now[:4] / now[5:7]
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

    def store_video(self, candidate: dict[str, Any], downloaded_path: Path, original_query: str) -> dict[str, Any]:
        """Store a video asset (MP4) in the library without PIL processing."""
        provider = candidate["provider"].replace("_video", "")  # pexels_video → pexels
        provider_asset_id = str(candidate["provider_asset_id"])
        existing = self.get_by_provider_id(provider, provider_asset_id)
        quality = candidate.get("quality") or "hd"
        now = _now_iso()
        relative_dir = Path("videos") / provider / now[:4] / now[5:7]
        relative_path = relative_dir / f"{provider}_{provider_asset_id}_{quality}.mp4"
        absolute_path = self.root / (existing["file_path"] if existing else relative_path)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)

        if existing and self.is_file_valid(existing):
            return existing

        shutil.copy2(downloaded_path, absolute_path)
        file_hash = _sha256(absolute_path)
        width = int(candidate.get("width") or 1920)
        height = int(candidate.get("height") or 1080)
        values = {
            "asset_id": existing["asset_id"] if existing else str(uuid.uuid4()),
            "provider": provider,
            "provider_asset_id": provider_asset_id,
            "media_type": "video",
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
        *,
        visual_bucket: str | None = None,
        shot_type: str | None = None,
        duration_used_sec: float | None = None,
        topic: str | None = None,
    ) -> None:
        now = _now_iso()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO asset_usage (
                    asset_id, channel_id, job_id, scene_id, scene_intent, used_at,
                    visual_bucket, shot_type, duration_used_sec, topic
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    channel_id,
                    job_id,
                    scene_id,
                    scene_intent,
                    now,
                    visual_bucket,
                    shot_type,
                    duration_used_sec,
                    topic,
                ),
            )
            db.execute(
                "UPDATE assets SET use_count = use_count + 1, last_used_at = ? WHERE asset_id = ?",
                (now, asset_id),
            )
            db.execute(
                "DELETE FROM asset_reservations WHERE asset_id = ? AND job_id = ? AND scene_id = ?",
                (asset_id, job_id, scene_id),
            )

    def list_usage(self, asset_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM asset_usage WHERE asset_id = ? ORDER BY usage_id", (asset_id,)).fetchall()
            return [dict(row) for row in rows]
