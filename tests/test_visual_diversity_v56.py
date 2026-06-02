"""Spec v5.6 delta tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from video_agent.assets.visual_diversity.eligibility import (
    graphic_card_fallback_available,
    is_pexels_provider,
    normalize_provider_id,
)
from video_agent.assets.visual_diversity.planner import (
    allowed_visual_buckets,
    choose_shot_type,
    graphic_card_bucket_renderable,
    graphic_card_bucket_report_plannable,
    graphic_card_target_for_report,
    plan_report_only_graphic_cards,
    would_exceed_shot_type_ratio,
)
from video_agent.assets.visual_diversity.quality import (
    duration_fit_score,
    required_min_duration_sec,
)
from video_agent.assets.visual_diversity.semantic import semantic_match_score


def _dna() -> dict:
    return yaml.safe_load(
        Path("configs/vida-plena-45/visual-dna.yaml").read_text(encoding="utf-8")
    )


# §15 provider aliases ------------------------------------------------------

def test_provider_aliases_map_pexels_video_to_pexels():
    dna = _dna()
    assert normalize_provider_id("pexels_video", dna) == "pexels"
    assert normalize_provider_id("pixabay", dna) == "pixabay"
    assert is_pexels_provider("pexels_video", dna)
    assert not is_pexels_provider("pixabay", dna)


# §9 graphic card bucket gating --------------------------------------------

def test_graphic_card_bucket_renderable_matrix():
    cfg = {"graphic_cards": {"enabled": True, "rollout_mode": "auto_if_supported"}}
    assert graphic_card_bucket_renderable(cfg, {"graphic_cards": True})
    assert not graphic_card_bucket_renderable(cfg, {"graphic_cards": False})
    cfg["graphic_cards"]["rollout_mode"] = "report_only"
    assert not graphic_card_bucket_renderable(cfg, {"graphic_cards": True})
    cfg["graphic_cards"]["enabled"] = False
    assert not graphic_card_bucket_renderable(cfg, {"graphic_cards": True})


def test_graphic_card_bucket_report_plannable():
    cfg = {"graphic_cards": {"enabled": True, "rollout_mode": "report_only"}}
    assert graphic_card_bucket_report_plannable(cfg)
    cfg["graphic_cards"]["rollout_mode"] = "auto_if_supported"
    assert not graphic_card_bucket_report_plannable(cfg)


def test_allowed_visual_buckets_drops_card_bucket_when_report_only():
    dna = _dna()
    cfg = {"graphic_cards": {"enabled": True, "rollout_mode": "report_only"}}
    buckets = allowed_visual_buckets(dna, cfg, {"graphic_cards": True})
    assert "local_graphic_card" not in buckets


# §9 report-only card planning ----------------------------------------------

def test_plan_report_only_graphic_cards_does_not_change_render():
    dna = _dna()
    cfg = {
        "graphic_cards": {
            "enabled": True,
            "rollout_mode": "report_only",
            "min_per_long_video": 4,
        }
    }
    scenes = [
        {"id": f"s{i}", "narration_text": f"tres pasos para mañana lista numero {i}"}
        for i in range(30)
    ]
    plans = plan_report_only_graphic_cards(scenes, dna, cfg, {"graphic_cards": False})
    assert len(plans) <= 4
    # Scenes must be left untouched.
    assert all(scene.get("visual_bucket") is None for scene in scenes)


def test_graphic_card_target_zero_when_disabled():
    cfg = {"graphic_cards": {"enabled": False}}
    assert graphic_card_target_for_report(30, "long", cfg, {}) == 0


# §10 shot-type ratio + config-driven consecutive ---------------------------

def test_would_exceed_shot_type_ratio_only_in_long():
    dna = _dna()
    assert would_exceed_shot_type_ratio("medium", {"medium": 10}, 20, dna, "short") is False
    # Long: medium max ratio is 0.45 → ceil 9 at 20 scenes.
    assert would_exceed_shot_type_ratio("medium", {"medium": 9}, 20, dna, "long") is True


def test_choose_shot_type_respects_configured_consecutive_limit():
    dna = _dna()
    cfg = {"diversity": {"max_same_shot_type_consecutive": 1}}
    shot = choose_shot_type(
        {},
        "persona_moment",
        scene_index=2,
        previous_shot_types=["medium"],
        visual_dna=dna,
        renderer_caps={"graphic_cards": False},
        scene_count=30,
        current_shot_counts={"medium": 1},
        visual_config=cfg,
    )
    assert shot != "medium"


def test_choose_shot_type_legacy_signature_still_works():
    dna = _dna()
    shot = choose_shot_type(
        {}, "macro_texture", 0, [], dna, {"graphic_cards": False}
    )
    assert shot in {"macro", "closeup"}


# §15 graphic-card fallback availability -----------------------------------

def test_graphic_card_fallback_available_requires_render_mode_and_supported_type():
    cfg = {
        "graphic_cards": {
            "enabled": True,
            "rollout_mode": "auto_if_supported",
            "supported_card_types": ["checklist", "timeline", "habit_matrix"],
        }
    }
    caps = {"graphic_cards": True}
    assert graphic_card_fallback_available({}, cfg, caps)
    # When renderer is missing the fallback must not be advertised.
    assert not graphic_card_fallback_available({}, cfg, {"graphic_cards": False})
    # Scene asks for an unsupported card type.
    scene = {"graphic_card": {"type": "body_area_map"}}
    assert not graphic_card_fallback_available(scene, cfg, caps)


# §24 shared duration helper -----------------------------------------------

def test_required_min_duration_sec_uses_10s_floor():
    assert required_min_duration_sec(0) == 10.0
    assert required_min_duration_sec(None) == 10.0
    assert required_min_duration_sec(25) == 25.0


def test_duration_fit_score_buckets():
    # Equal or longer than required → no penalty.
    assert duration_fit_score(20, 20) == (1.0, 0.0)
    # Unknown duration is soft, not hard-rejected.
    assert duration_fit_score(None, 20)[1] > 0
    # Below 50% of required gets the biggest penalty bucket.
    score, penalty = duration_fit_score(4, 20)
    assert penalty == 0.30
    assert score == 0.20


# Phrase-synonym semantics --------------------------------------------------

def test_phrase_hits_require_query_and_candidate_side():
    dna = _dna()
    # Query carries the label phrase, candidate echoes another variant.
    _s, _d, _sy, phc, _m = semantic_match_score(
        "middle-aged morning kitchen",
        "mature adult morning kitchen",
        dna,
    )
    assert phc == 1
    # Neither side has the phrase: zero.
    _s, _d, _sy, phc, _m = semantic_match_score("morning kitchen", "coffee cup", dna)
    assert phc == 0
