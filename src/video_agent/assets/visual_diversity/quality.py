"""Candidate quality, negative-pattern, locale, and metadata scoring (spec §13–§14, §16)."""

from __future__ import annotations

from typing import Any

from .helpers import normalize_text


_CONSULT_PHRASES = [
    "consulta con tu medico",
    "consulta a tu medico",
    "acude al medico",
    "seek medical advice",
    "talk to your doctor",
]


def quality_score(candidate: dict[str, Any], required_orientation: str = "landscape") -> float:
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    duration = float(candidate.get("duration_sec") or candidate.get("duration") or 0)
    quality_label = str(candidate.get("quality") or "").lower()

    score = 0.0
    if width >= 1920 and height >= 1080:
        score += 0.40
    elif width >= 1280 and height >= 720:
        score += 0.25
    elif width and height:
        score += 0.10

    if width and height:
        ratio = width / height
        if required_orientation == "landscape" and abs(ratio - 16 / 9) < 0.12:
            score += 0.25
        elif required_orientation == "landscape" and ratio > 1.3:
            score += 0.15

    if duration >= 20:
        score += 0.20
    elif duration >= 10:
        score += 0.12
    elif duration > 0:
        score += 0.05

    if quality_label in {"large2x", "fullhd", "hd", "original"}:
        score += 0.15

    return min(1.0, score)


def negative_match_score(
    query: str,
    candidate_text: str,
    scene_text_value: str,
    visual_dna: dict[str, Any],
) -> tuple[float, list[str]]:
    """Return (penalty_score, hit_terms). 1.0 = hard reject."""
    text = normalize_text(" ".join([query, candidate_text]))
    negative_patterns = visual_dna.get("negative_patterns", {}) or {}

    strong_hits = [
        phrase
        for phrase in negative_patterns.get("strong_phrases_en", []) or []
        if normalize_text(phrase) in text
    ]
    weak_hits = [
        term
        for term in negative_patterns.get("weak_terms_en", []) or []
        if normalize_text(term) in text
    ]

    consult_context = any(
        phrase in normalize_text(scene_text_value) for phrase in _CONSULT_PHRASES
    )
    if consult_context:
        weak_hits = [h for h in weak_hits if h not in {"doctor", "medicine"}]

    if strong_hits:
        return 1.0, strong_hits
    if len(weak_hits) >= 2:
        return 0.5, weak_hits
    if len(weak_hits) == 1:
        return 0.20, weak_hits
    return 0.0, []


def strong_negative_match(candidate_text: str, scene_text_value: str, visual_dna: dict[str, Any]) -> bool:
    score, _ = negative_match_score("", candidate_text, scene_text_value, visual_dna)
    return score >= 1.0


def locale_fit_score(scene: dict[str, Any], candidate_text: str) -> float:
    text = normalize_text(candidate_text)
    locale_terms = ["spain", "spanish", "madrid", "barcelona", "mediterranean", "european"]
    if any(term in text for term in locale_terms):
        return 1.0
    if scene.get("locale_feel") in {None, "", "Generic"}:
        return 0.6
    return 0.35


def metadata_completeness_score(asset: dict[str, Any]) -> float:
    """Source/audit completeness; photographer and photographer_url share one slot."""
    checks = [
        bool(asset.get("provider")),
        bool(asset.get("provider_asset_id")),
        bool(asset.get("original_url") or asset.get("source_url")),
        bool(asset.get("photographer") or asset.get("photographer_url")),
        bool(asset.get("license")),
        bool(asset.get("width")),
        bool(asset.get("height")),
    ]
    return sum(1 for value in checks if value) / len(checks)


def semantic_metadata_present(asset: dict[str, Any]) -> dict[str, bool]:
    return {
        "original_query": bool(asset.get("original_query")),
        "provider_tags_json": bool(asset.get("provider_tags_json")),
    }


def recent_freshness(asset_usage: list[dict[str, Any]] | None) -> float:
    """0.1 for recent use, 1.0 for never used. Bucketed at 30/90 days."""
    from .recency import last_used_older_than_days

    if not asset_usage:
        return 1.0
    if last_used_older_than_days(asset_usage, 90):
        return 0.8
    if last_used_older_than_days(asset_usage, 30):
        return 0.5
    return 0.1


def required_min_duration_sec(scene_duration_sec: float | int | None) -> float:
    """Shared minimum-duration requirement (spec §24). 10s floor or scene length."""
    return max(10.0, float(scene_duration_sec or 0))


def duration_fit_score(
    candidate_duration_sec: float | int | None,
    scene_duration_sec: float | int,
) -> tuple[float, float]:
    """Return ``(score, penalty)`` for a candidate vs scene duration.

    Score is for the score component (currently unused by the main formula
    but useful for tooling); penalty plugs into the candidate scoring penalty
    bag so retrieval and scoring share a single source of truth.
    """
    required = required_min_duration_sec(scene_duration_sec)
    if candidate_duration_sec is None or float(candidate_duration_sec) <= 0:
        return 0.5, 0.10
    cd = float(candidate_duration_sec)
    if cd >= required:
        return 1.0, 0.0
    ratio = cd / required if required else 1.0
    if ratio >= 0.75:
        return 0.75, 0.08
    if ratio >= 0.50:
        return 0.45, 0.18
    return 0.20, 0.30


def lifetime_novelty(use_count: int, visual_config: dict[str, Any]) -> float:
    """Channel-configurable saturation; default 6 uses → 0.0 novelty."""
    diversity = (visual_config or {}).get("diversity", {}) if visual_config else {}
    saturation_count = max(1, int(diversity.get("lifetime_novelty_saturation_count", 6) or 6))
    return max(0.0, 1.0 - min(1.0, use_count / saturation_count))
