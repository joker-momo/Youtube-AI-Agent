"""Optional backfill of visual-diversity columns for existing assets (spec §19)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .creator import creator_key as _creator_key
from .helpers import normalize_text
from .quality import quality_score as _quality_score


_SHOT_HEURISTICS = [
    ("graphic", ["graphic card", "checklist", "timeline", "habit matrix"]),
    ("macro", ["macro", "close up of", "extreme close", "tea steam", "sunlight through"]),
    ("closeup", ["close up", "close-up", "closeup", "hands"]),
    ("wide", ["street", "park", "wide", "landscape", "city"]),
    ("medium_closeup", ["portrait", "smiling"]),
]


def infer_shot_type(text: str) -> str | None:
    text = normalize_text(text)
    if not text:
        return None
    for shot, terms in _SHOT_HEURISTICS:
        for term in terms:
            if normalize_text(term) in text:
                return shot
    return None


def infer_visual_bucket(text: str, visual_dna: dict[str, Any]) -> str | None:
    """Pick the bucket whose keyword triggers (es + en) best match the text."""
    text = normalize_text(text)
    if not text:
        return None
    best_bucket = None
    best_score = 0
    for bucket_id, cfg in (visual_dna.get("visual_buckets") or {}).items():
        score = 0
        for term in (cfg.get("keyword_triggers", {}).get("es") or []):
            if normalize_text(term) in text:
                score += 2
        for term in (cfg.get("keyword_triggers", {}).get("en") or []):
            if normalize_text(term) in text:
                score += 1
        if score > best_score:
            best_bucket = bucket_id
            best_score = score
    return best_bucket


def _has_duration(asset: dict[str, Any]) -> float | None:
    for key in ("duration_sec", "duration"):
        value = asset.get(key)
        if value:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def backfill_asset(
    asset: dict[str, Any],
    visual_dna: dict[str, Any],
) -> dict[str, Any]:
    """Infer missing v5.4 columns. Returns the patch dict (only changed fields)."""
    text = " ".join(
        filter(
            None,
            [
                asset.get("original_query") or "",
                asset.get("provider_tags_json") or "",
                asset.get("attribution") or "",
            ],
        )
    )
    patch: dict[str, Any] = {}

    if not asset.get("visual_bucket"):
        bucket = infer_visual_bucket(text, visual_dna)
        if bucket:
            patch["visual_bucket"] = bucket

    if not asset.get("shot_type"):
        shot = infer_shot_type(text)
        if shot:
            patch["shot_type"] = shot

    if not asset.get("creator_key"):
        ck = _creator_key(asset)
        if ck:
            patch["creator_key"] = ck

    if not asset.get("duration_sec"):
        dur = _has_duration(asset)
        if dur is not None:
            patch["duration_sec"] = dur

    if not asset.get("quality_score"):
        patch["quality_score"] = round(_quality_score(asset), 4)

    if patch:
        existing_meta_raw = asset.get("metadata_json") or "{}"
        try:
            existing_meta = json.loads(existing_meta_raw) if existing_meta_raw else {}
        except (TypeError, ValueError):
            existing_meta = {}
        existing_meta["backfill"] = {
            "confidence": "low",
            "backfilled_at": datetime.now(timezone.utc).isoformat(),
            "source": "query_tags_heuristic",
        }
        patch["metadata_json"] = json.dumps(existing_meta, ensure_ascii=False)

    return patch
