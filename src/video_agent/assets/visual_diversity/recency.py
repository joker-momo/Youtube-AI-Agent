"""Recency helpers for asset_usage rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _latest_used_at(asset_usage: list[dict[str, Any]] | None) -> datetime | None:
    if not asset_usage:
        return None
    candidates: list[datetime] = []
    for row in asset_usage:
        parsed = _parse_iso(row.get("used_at"))
        if parsed is not None:
            candidates.append(parsed)
    return max(candidates) if candidates else None


def last_used_within_days(asset_usage: list[dict[str, Any]] | None, days: int, now: datetime | None = None) -> bool:
    """True if the latest use is within the last `days` (strictly less than days * 24h)."""
    latest = _latest_used_at(asset_usage)
    if latest is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - latest) < timedelta(days=days)


def last_used_older_than_days(asset_usage: list[dict[str, Any]] | None, days: int, now: datetime | None = None) -> bool:
    """True if the latest use is strictly older than `days * 24h`."""
    latest = _latest_used_at(asset_usage)
    if latest is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - latest) > timedelta(days=days)


def used_in_last_n_days(asset_usage: list[dict[str, Any]] | None, days: int, now: datetime | None = None) -> int:
    if not asset_usage:
        return 0
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    count = 0
    for row in asset_usage:
        parsed = _parse_iso(row.get("used_at"))
        if parsed is not None and parsed >= cutoff:
            count += 1
    return count
