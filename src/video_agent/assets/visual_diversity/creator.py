"""Stable Pexels creator identity (spec §17)."""

from __future__ import annotations

import hashlib
import re
from typing import Any


def _normalize_url(value: str) -> str:
    value = re.sub(r"^https?://(www\.)?", "", value.strip().lower())
    return value.rstrip("/")


def creator_key(candidate_or_asset: dict[str, Any]) -> str | None:
    """Prefer numeric user_id; fall back to URL hash, then name hash."""
    if not candidate_or_asset:
        return None
    user_id = candidate_or_asset.get("user_id") or candidate_or_asset.get("photographer_id")
    if user_id:
        return f"pexels:{user_id}"

    photographer_url = candidate_or_asset.get("photographer_url")
    if photographer_url:
        return "pexels:url:" + _normalize_url(str(photographer_url))

    photographer = candidate_or_asset.get("photographer")
    if photographer:
        digest = hashlib.sha1(str(photographer).strip().lower().encode("utf-8")).hexdigest()
        return "pexels:namehash:" + digest[:12]
    return None
