"""Spec §12–§16 scoring + eligibility + negative match + penalty cap."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml
from pathlib import Path

from video_agent.assets.visual_diversity.creator import creator_key
from video_agent.assets.visual_diversity.eligibility import (
    candidate_eligibility,
    is_pexels_provider,
)
from video_agent.assets.visual_diversity.quality import (
    lifetime_novelty,
    negative_match_score,
    quality_score,
    recent_freshness,
)
from video_agent.assets.visual_diversity.recency import (
    last_used_older_than_days,
    used_in_last_n_days,
)
from video_agent.assets.visual_diversity.scoring import (
    CandidateScore,
    candidate_sort_key,
    score_candidate,
)
from video_agent.assets.visual_diversity.semantic import (
    passes_semantic_gate,
    semantic_match_score,
)


def _dna() -> dict:
    return yaml.safe_load(
        Path("configs/vida-plena-45/visual-dna.yaml").read_text(encoding="utf-8")
    )


def _visual_config() -> dict:
    return {
        "diversity": {
            "enabled": True,
            "rollout_mode": "report_only",
            "lifetime_novelty_saturation_count": 6,
            "duplicate_asset_escape_hatch": "warn_if_no_alternatives",
        }
    }


def test_semantic_match_is_deterministic_and_config_driven():
    dna = _dna()
    a, *_ = semantic_match_score("woman 50 morning kitchen", "mature adult woman 50 coffee morning", dna)
    b, *_ = semantic_match_score("woman 50 morning kitchen", "mature adult woman 50 coffee morning", dna)
    assert a == b
    assert a > 0.0


def test_phrase_synonyms_are_capped():
    dna = _dna()
    # v5.6: phrase counts only when BOTH query and candidate contain a phrase
    # belonging to the same label. Query carries the "middle aged" phrase.
    candidate_text = "middle aged middle-aged mature adult midlife woman 50 man 55 45 plus 45+"
    score, _d, _s, phrase_count, _matched = semantic_match_score(
        "middle aged 45+", candidate_text, dna
    )
    assert phrase_count == 1  # one label hits on both sides
    assert score <= 1.0


def test_phrase_match_requires_both_query_and_candidate():
    dna = _dna()
    # Query has no phrase: phrase_count must be zero even when candidate is full of them.
    _s, _d, _sy, phrase_count, _m = semantic_match_score(
        "morning kitchen", "middle aged mature adult midlife", dna
    )
    assert phrase_count == 0


def test_passes_semantic_gate_requires_score_and_hits():
    assert passes_semantic_gate(0.50, 1, 1, 0) is True
    assert passes_semantic_gate(0.20, 5, 0, 0) is False


def test_quality_score_uses_resolution_aspect_duration_label():
    full_hd = {"width": 1920, "height": 1080, "duration_sec": 30, "quality": "hd"}
    low_res = {"width": 640, "height": 360, "duration_sec": 5, "quality": ""}
    assert quality_score(full_hd) > quality_score(low_res)


def test_strong_negative_pattern_hard_rejects():
    dna = _dna()
    score, hits = negative_match_score("", "hospital bed patient", "", dna)
    assert score == 1.0 and hits


def test_weak_negative_pattern_applies_partial_penalty():
    dna = _dna()
    score, hits = negative_match_score("", "doctor visit", "", dna)
    assert 0.0 < score < 1.0


def test_consult_context_relaxes_only_doctor_and_medicine():
    dna = _dna()
    scene_text = "Consulta con tu médico antes de empezar."
    score, _ = negative_match_score("", "doctor", scene_text, dna)
    assert score == 0.0
    score, _ = negative_match_score("", "hospital bed", scene_text, dna)
    assert score >= 1.0  # strong phrase still rejects


def test_recent_freshness_and_lifetime_novelty_are_independent():
    now = datetime.now(timezone.utc)
    usage_recent = [{"used_at": (now - timedelta(days=2)).isoformat()}]
    assert recent_freshness(usage_recent) == 0.1
    # Lifetime novelty depends on use_count, not recency.
    assert lifetime_novelty(0, _visual_config()) == 1.0
    assert lifetime_novelty(6, _visual_config()) == 0.0


def test_lifetime_novelty_requires_visual_config():
    cfg = {"diversity": {"lifetime_novelty_saturation_count": 3}}
    assert lifetime_novelty(0, cfg) == 1.0
    assert lifetime_novelty(3, cfg) == 0.0


def test_penalty_cap_reports_raw_and_capped():
    dna = _dna()
    score = score_candidate(
        {
            "provider": "pexels",
            "provider_asset_id": "1",
            "width": 1920,
            "height": 1080,
            "duration_sec": 20,
            "tags": ["morning", "kitchen"],
            "photographer": "X",
        },
        scene={"id": "s1"},
        query="morning kitchen",
        base_seed="seed",
        visual_config=_visual_config(),
        visual_dna=dna,
        asset_usage=None,
        use_count=0,
        creator_used_current_job=True,
        creator_ratio_after_pick=0.9,
        would_make_more_than_two_consecutive_shot_type=True,
        would_exceed_bucket_ratio=True,
        actively_reserved_by_other_job=True,
        negative_score=0.5,
    )
    assert score.penalty_total_raw > score.penalty_total_capped
    assert score.penalty_total_capped == 1.0


def test_duplicate_in_current_job_is_eligibility_rejected():
    dna = _dna()
    cfg = _visual_config()
    result = candidate_eligibility(
        {"provider": "pexels", "provider_asset_id": "1"},
        scene={"id": "s1"},
        job_state={"used_provider_asset_ids": {"1"}},
        visual_config=cfg,
        visual_dna=dna,
    )
    assert result.eligible is False
    assert result.hard_reject_reason == "duplicate_asset_current_job"
    assert result.can_escape_hatch is True  # escape hatch enabled in config


def test_non_pexels_provider_is_eligibility_rejected():
    dna = _dna()
    cfg = _visual_config()
    result = candidate_eligibility(
        {"provider": "pixabay", "provider_asset_id": "1"},
        scene={},
        job_state={},
        visual_config=cfg,
        visual_dna=dna,
    )
    assert result.eligible is False
    assert result.hard_reject_reason == "non_pexels_provider"


def test_creator_key_prefers_user_id():
    assert creator_key({"user_id": 42}) == "pexels:42"
    assert creator_key({"photographer_url": "https://www.pexels.com/@example"}).startswith(
        "pexels:url:"
    )
    assert creator_key({"photographer": "Anna García"}).startswith("pexels:namehash:")


def test_candidate_sort_key_breaks_ties_with_stable_hash():
    dna = _dna()
    cfg = _visual_config()
    candidate = {"provider": "pexels", "provider_asset_id": "abc", "width": 1920, "height": 1080}
    score = score_candidate(
        candidate,
        scene={},
        query="",
        base_seed="seed",
        visual_config=cfg,
        visual_dna=dna,
    )
    key = candidate_sort_key(score, candidate)
    assert key[0] == -score.total
    assert isinstance(key[-1], int)


def test_recency_helpers_inequality_semantics():
    now = datetime.now(timezone.utc)
    usage = [{"used_at": (now - timedelta(days=30)).isoformat()}]
    # Exactly 30 days old is NOT older than 30 days.
    assert last_used_older_than_days(usage, 30, now=now) is False
    usage_old = [{"used_at": (now - timedelta(days=31)).isoformat()}]
    assert last_used_older_than_days(usage_old, 30, now=now) is True
    assert used_in_last_n_days(usage, 30, now=now) == 1


def test_is_pexels_provider_handles_variants():
    dna = _dna()
    assert is_pexels_provider("pexels", dna)
    assert is_pexels_provider("pexels_video", dna)  # alias → "pexels"
    assert not is_pexels_provider("pixabay", dna)
    assert not is_pexels_provider(None, dna)
    # Backward-compat: no visual_dna falls back to the old prefix check.
    assert is_pexels_provider("pexels")
    assert is_pexels_provider("pexels_video")
