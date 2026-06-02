"""Pexels API budget, 429 handling, and cross-job backoff state (spec §21)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApiBudget:
    """Per-video budget tracker. Hourly budget is best handled by an orchestrator."""

    def __init__(self, max_per_video: int) -> None:
        self.max_per_video = int(max_per_video)
        self.used = 0
        self.rate_limited = False
        self.query_cache_hits = 0
        self.library_cache_hits = 0

    def can_call(self) -> bool:
        return not self.rate_limited and self.used < self.max_per_video

    def record_call(self) -> None:
        self.used += 1

    def record_429(self) -> None:
        self.rate_limited = True

    def record_query_cache_hit(self) -> None:
        self.query_cache_hits += 1

    def record_library_cache_hit(self) -> None:
        self.library_cache_hits += 1

    def stats(self, *, hourly_budget_enforced: bool = False, hourly_budget_warning: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "max_api_requests_per_video": self.max_per_video,
            "api_requests_used": self.used,
            "rate_limited": self.rate_limited,
            "query_cache_hits": self.query_cache_hits,
            "library_cache_hits": self.library_cache_hits,
            "hourly_budget_enforced": hourly_budget_enforced,
        }
        if hourly_budget_warning:
            out["hourly_budget_warning"] = hourly_budget_warning
        return out


def write_backoff_state(
    path: Path,
    *,
    job_id: str,
    backoff_seconds: int,
    now: datetime | None = None,
) -> None:
    now = now or _utc_now()
    backoff_until = now + timedelta(seconds=max(1, backoff_seconds))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": "pexels",
        "rate_limited_at": now.isoformat(),
        "backoff_until": backoff_until.isoformat(),
        "source_job_id": job_id,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_backoff_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_backoff_active(path: Path, now: datetime | None = None) -> bool:
    state = read_backoff_state(path)
    if not state:
        return False
    until_raw = state.get("backoff_until")
    if not until_raw:
        return False
    try:
        until = datetime.fromisoformat(str(until_raw))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    now = now or _utc_now()
    return now < until
