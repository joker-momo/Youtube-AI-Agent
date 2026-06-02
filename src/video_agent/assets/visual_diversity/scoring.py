"""Structured candidate scoring with penalty cap (spec §16).

`CandidateScore.total` already subtracts the capped penalty.
Callers must pass `visual_config` so `lifetime_novelty` honours
`lifetime_novelty_saturation_count`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .helpers import candidate_tiebreak_seed, stable_hash
from .quality import (
    lifetime_novelty,
    locale_fit_score,
    metadata_completeness_score,
    quality_score,
    recent_freshness,
)
from .recency import used_in_last_n_days
from .semantic import semantic_match_score


# Component weights — must sum to 1.0.
W_SEMANTIC = 0.34
W_BUCKET = 0.12
W_SHOT = 0.08
W_LOCALE = 0.04
W_RECENT_FRESHNESS = 0.10
W_LIFETIME_NOVELTY = 0.10
W_QUALITY = 0.18
W_METADATA = 0.04


@dataclass(frozen=True)
class CandidateScore:
    total: float
    semantic_match: float
    bucket_match: float
    shot_type_match: float
    locale_fit: float
    recent_freshness: float
    lifetime_novelty: float
    quality: float
    metadata_completeness: float
    penalty_total_raw: float
    penalty_total_capped: float
    penalties: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)
    tie_break_seed: str = ""


def _component_match(value: str | None, target: str | None) -> float:
    if value is None or target is None:
        return 0.5
    return 1.0 if value == target else 0.0


def _candidate_text_for_match(candidate: dict[str, Any]) -> str:
    return " ".join([
        str(candidate.get("source_url") or ""),
        str(candidate.get("photographer") or ""),
        " ".join(candidate.get("tags") or []),
        str(candidate.get("attribution") or ""),
        str(candidate.get("original_query") or ""),
        str(candidate.get("provider_tags_json") or ""),
    ])


def score_candidate(
    candidate: dict[str, Any],
    *,
    scene: dict[str, Any],
    query: str,
    base_seed: str,
    visual_config: dict[str, Any],
    visual_dna: dict[str, Any],
    asset_usage: list[dict[str, Any]] | None = None,
    use_count: int = 0,
    creator_used_current_job: bool = False,
    creator_ratio_after_pick: float = 0.0,
    max_same_creator_ratio: float = 0.25,
    would_make_more_than_two_consecutive_shot_type: bool = False,
    would_exceed_bucket_ratio: bool = False,
    actively_reserved_by_other_job: bool = False,
    negative_score: float = 0.0,
) -> CandidateScore:
    """Compute the full structured score for a Pexels candidate."""
    candidate_text = _candidate_text_for_match(candidate)
    sem_score, _direct, _syn, _phrase, matched_terms = semantic_match_score(
        query, candidate_text, visual_dna
    )

    bucket_match = _component_match(
        candidate.get("visual_bucket"), scene.get("visual_bucket")
    )
    shot_match = _component_match(
        candidate.get("shot_type"), scene.get("shot_type")
    )
    locale = locale_fit_score(scene, candidate_text)
    fresh = recent_freshness(asset_usage)
    novelty = lifetime_novelty(use_count, visual_config)
    quality = quality_score(candidate)
    metadata = metadata_completeness_score(candidate)

    used_30 = used_in_last_n_days(asset_usage, 30) > 0
    used_90 = used_in_last_n_days(asset_usage, 90) > 0

    penalties: dict[str, float] = {
        "same_creator_current_job": 0.12 if creator_used_current_job else 0.0,
        "same_creator_ratio": 0.20 if creator_ratio_after_pick > max_same_creator_ratio else 0.0,
        "reuse_last_30_days": 0.20 if used_30 else 0.0,
        "reuse_last_90_days": 0.08 if used_90 else 0.0,
        "consecutive_shot_type": 0.20 if would_make_more_than_two_consecutive_shot_type else 0.0,
        "bucket_overuse": 0.25 if would_exceed_bucket_ratio else 0.0,
        "negative_pattern": float(negative_score),
        "active_reservation": 0.20 if actively_reserved_by_other_job else 0.0,
    }
    raw_penalty = sum(penalties.values())
    capped_penalty = min(1.0, raw_penalty)

    total = (
        sem_score * W_SEMANTIC
        + bucket_match * W_BUCKET
        + shot_match * W_SHOT
        + locale * W_LOCALE
        + fresh * W_RECENT_FRESHNESS
        + novelty * W_LIFETIME_NOVELTY
        + quality * W_QUALITY
        + metadata * W_METADATA
        - capped_penalty
    )

    tie_break_seed = candidate_tiebreak_seed(
        base_seed, str(candidate.get("provider_asset_id") or "")
    )

    reasons: list[str] = []
    if used_30:
        reasons.append("reuse_last_30_days")
    if used_90:
        reasons.append("reuse_last_90_days")
    if negative_score >= 0.5:
        reasons.append("negative_pattern_strong")
    elif negative_score > 0:
        reasons.append("negative_pattern_weak")

    return CandidateScore(
        total=round(total, 4),
        semantic_match=round(sem_score, 4),
        bucket_match=round(bucket_match, 4),
        shot_type_match=round(shot_match, 4),
        locale_fit=round(locale, 4),
        recent_freshness=round(fresh, 4),
        lifetime_novelty=round(novelty, 4),
        quality=round(quality, 4),
        metadata_completeness=round(metadata, 4),
        penalty_total_raw=round(raw_penalty, 4),
        penalty_total_capped=round(capped_penalty, 4),
        penalties=penalties,
        reasons=reasons,
        matched_terms=matched_terms,
        tie_break_seed=tie_break_seed,
    )


def candidate_sort_key(score: CandidateScore, asset: dict[str, Any]) -> tuple:
    """Stable tie-breaker chain for ranked selection (spec §16)."""
    width = int(asset.get("width") or 0)
    height = int(asset.get("height") or 0)
    use_count = int(asset.get("use_count") or 0)
    return (
        -score.total,
        -score.metadata_completeness,
        use_count,
        -(width * height),
        stable_hash(score.tie_break_seed),
    )
