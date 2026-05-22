from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

STOP_WORDS = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "with"}


def normalize_query(query: str) -> str:
    cleaned = query.lower().strip()
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    words = [word for word in cleaned.split() if word not in STOP_WORDS]
    return " ".join(sorted(words))


def _filters_hash(filters: dict[str, Any]) -> str:
    payload = json.dumps(filters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def query_cache_key(provider: str, query: str, filters: dict[str, Any]) -> str:
    return f"{provider}:{normalize_query(query)}:{_filters_hash(filters)}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class QueryCache:
    def __init__(self, db_path: Path, now: Callable[[], datetime] = _utc_now) -> None:
        self.db_path = db_path
        self.now = now
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS query_cache (
                    cache_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    query_original TEXT NOT NULL,
                    query_normalized TEXT NOT NULL,
                    filters_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    results_count INTEGER NOT NULL,
                    cached_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_query_cache_provider_query ON query_cache(provider, query_normalized)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_query_cache_expires ON query_cache(expires_at)")

    def set(self, provider: str, query: str, filters: dict[str, Any], response: dict[str, Any], ttl_hours: int = 24) -> str:
        key = query_cache_key(provider, query, filters)
        cached_at = self.now()
        expires_at = cached_at + timedelta(hours=ttl_hours)
        if provider == "pexels":
            results = response.get("photos")
        elif provider == "pexels_video":
            results = response.get("videos")
        else:
            results = response.get("hits")
        results_count = len(results or [])
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO query_cache (
                    cache_key, provider, query_original, query_normalized, filters_json,
                    response_json, results_count, cached_at, expires_at, hit_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT hit_count FROM query_cache WHERE cache_key = ?), 0))
                """,
                (
                    key,
                    provider,
                    query,
                    normalize_query(query),
                    json.dumps(filters, sort_keys=True),
                    json.dumps(response),
                    results_count,
                    cached_at.isoformat(),
                    expires_at.isoformat(),
                    key,
                ),
            )
        return key

    def get(self, provider: str, query: str, filters: dict[str, Any]) -> dict[str, Any] | None:
        key = query_cache_key(provider, query, filters)
        with self._connect() as db:
            row = db.execute("SELECT * FROM query_cache WHERE cache_key = ?", (key,)).fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= self.now():
                return None
            db.execute("UPDATE query_cache SET hit_count = hit_count + 1 WHERE cache_key = ?", (key,))
            return json.loads(row["response_json"])

    def get_entry(self, cache_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM query_cache WHERE cache_key = ?", (cache_key,)).fetchone()
            return dict(row) if row else None
