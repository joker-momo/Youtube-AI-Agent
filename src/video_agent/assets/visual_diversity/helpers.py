"""Deterministic helpers shared across the visual diversity layer."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any


def normalize_text(value: Any) -> str:
    """Lowercase, strip accents, normalize whitespace; keep alnum, hyphen, plus."""
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9+ -]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def stable_hash(value: str) -> int:
    """Stable cross-process hash. Python built-in hash() is salted; do not use it."""
    return int(hashlib.sha1(value.encode("utf-8")).hexdigest()[:12], 16)


def deterministic_argmax(scores: dict[str, float], seed: str) -> str:
    """Pick the highest-scoring key; tie-break by stable_hash(seed:key) then key."""
    if not scores:
        raise ValueError("deterministic_argmax requires at least one score")
    return sorted(
        scores.items(),
        key=lambda item: (-item[1], stable_hash(f"{seed}:{item[0]}"), item[0]),
    )[0][0]


def stable_dedupe(values: list[str]) -> list[str]:
    """Preserve first occurrence. Query order controls API budget."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def resolve_video_topic(scene_doc: dict, job_metadata: dict | None = None) -> str:
    """Resolve topic for seeding; chained fallback through scene_doc and job metadata."""
    job_metadata = job_metadata or {}
    return (
        str(scene_doc.get("topic") or "")
        or str(scene_doc.get("title") or "")
        or str(job_metadata.get("topic") or "")
        or str(job_metadata.get("title") or "")
        or str(job_metadata.get("youtube_title") or "")
        or ""
    )


def visual_seed(
    channel_id: str,
    job_id: str,
    scene: dict,
    scene_index: int,
    topic: str | None = None,
) -> str:
    """Deterministic seed per scene: channel + job + scene id + index + topic hash."""
    topic_hash = hashlib.sha1((topic or "").encode("utf-8")).hexdigest()[:12]
    return f"{channel_id}:{job_id}:{scene.get('id', 'scene')}:{scene_index}:{topic_hash}"


def candidate_tiebreak_seed(base_seed: str, provider_asset_id: str) -> str:
    return f"{base_seed}:{provider_asset_id}"
