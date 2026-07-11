"""SEO deterministic-fallback quality (live repro 2026-07-11, Mundial idea-02).

After exhausting retries the builder shipped title 'Si tienes más de 45: Mide'
(one lonely hook word) and a description that was ONLY hashtags. Fallbacks are
allowed to be deterministic — never skeletal.
"""
from __future__ import annotations

import json

from video_agent.shorts.short_seo_builder import (
    _fallback_title_from_hook,
    _title_issues,
    build_short_seo,
)

HOOK = "Mide tu cansancio acumulado"


def test_fallback_title_carries_the_hook_phrase_not_one_word():
    fb = _fallback_title_from_hook(HOOK, 45)
    assert len(fb) <= 40
    assert _title_issues(fb, HOOK) == []
    # More than a single content word: the phrase fits the budget, use it.
    assert "cansancio" in fb.lower()


def test_fallback_title_still_degrades_gracefully_for_long_hooks():
    long_hook = "Independientemente circunstancias extraordinarias imprevisibles"
    fb = _fallback_title_from_hook(long_hook, 45)
    assert len(fb) <= 40


def test_exhausted_retries_never_ship_a_hashtags_only_description(tmp_path):
    """When every LLM attempt returns an empty description, the final artifact
    must still carry a real Spanish body ending in an engagement question —
    not a bare hashtag string."""
    (tmp_path / "shorts" / "short-01" / "json").mkdir(parents=True)

    def llm_fn(prompt):
        return json.dumps({
            "title": "x" * 60,  # always invalid -> exhausts retries
            "description": "",
            "hashtags": ["#partido", "#bienestar", "#shorts"],
            "pinned_comment": "¿Te pasa?",
        })

    seo = build_short_seo(
        tmp_path,
        "short-01",
        {"short_id": "short-01", "format": "infographic",
         "title": "¿Llegas fresco al partido?",
         "viewer_pain": HOOK, "practical_payoff": "Chequeo rápido"},
        {"hook": HOOK,
         "narration": "Mide tu cansancio acumulado antes de jugar. Cuatro señales bastan.",
         "cta": "Sigue", "short_format": "infographic"},
        {"channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
         "audience": {"language": "es-ES", "age_range": [45, 75]}},
        llm_fn,
    )

    body = seo["description"]
    for tag in seo["hashtags"]:
        body = body.replace(tag, "")
    body = body.strip()
    assert body, f"description must not be hashtags-only: {seo['description']!r}"
    assert body.endswith("?")  # engagement question before the hashtags
    assert len(seo["title"]) <= 40
