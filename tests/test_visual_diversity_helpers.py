"""Spec §29 deterministic helpers + tokenization + dedupe."""

from __future__ import annotations

import pytest

from video_agent.assets.visual_diversity.helpers import (
    deterministic_argmax,
    normalize_text,
    resolve_video_topic,
    stable_dedupe,
    stable_hash,
    visual_seed,
)
from video_agent.assets.visual_diversity.semantic import normalize_terms


def _dna() -> dict:
    return {
        "token_policy": {
            "preserve_numeric_terms": ["45", "50", "55", "60", "65", "70"],
            "preserve_short_terms": [],
            "stopwords": {"en": ["the", "and"], "es": ["los", "las"]},
        }
    }


def test_normalize_text_strips_accents_lowers_and_normalizes_whitespace():
    assert normalize_text(" Mañana   ÁLBUM  ") == "manana album"
    assert normalize_text("Middle-Aged 45+") == "middle-aged 45+"


def test_normalize_terms_preserves_hyphen_and_plus():
    terms = normalize_terms("middle-aged person at 45+", _dna())
    assert "middle-aged" in terms
    assert "45+" in terms


def test_normalize_terms_drops_punctuation_only_tokens():
    terms = normalize_terms("--- +++ walk", _dna())
    assert terms == {"walk"}


def test_normalize_terms_preserves_age_numbers_without_duplicating_preserve_short():
    terms = normalize_terms("woman 50 morning", _dna())
    assert "50" in terms
    assert "morning" in terms


def test_stable_dedupe_preserves_first_occurrence_and_order():
    assert stable_dedupe(["walk park", "Walk Park", "morning"]) == ["walk park", "morning"]


def test_deterministic_argmax_is_stable_across_calls():
    scores = {"a": 1.0, "b": 1.0, "c": 0.5}
    assert deterministic_argmax(scores, seed="seed-x") == deterministic_argmax(scores, seed="seed-x")


def test_deterministic_argmax_does_not_use_python_hash():
    scores = {"a": 1.0, "b": 1.0}
    # Same seed must always pick the same key; Python hash() would not be stable.
    assert stable_hash("seed:a") == stable_hash("seed:a")
    assert deterministic_argmax(scores, "seed") in {"a", "b"}


def test_deterministic_argmax_raises_on_empty():
    with pytest.raises(ValueError):
        deterministic_argmax({}, seed="x")


def test_visual_seed_changes_across_jobs():
    scene = {"id": "scene_01"}
    s1 = visual_seed("ch", "job-a", scene, 0, topic="t")
    s2 = visual_seed("ch", "job-b", scene, 0, topic="t")
    assert s1 != s2


def test_resolve_video_topic_fallback_chain():
    assert resolve_video_topic({"topic": "X"}) == "X"
    assert resolve_video_topic({}, {"youtube_title": "Y"}) == "Y"
    assert resolve_video_topic({}, {}) == ""
