"""CTR-first title formula (2026-06-29, option b).

Titles open with a curiosity/contrarian hook; keyword comes after. 3 variants use
distinct devices (curiosity / contrarian / keyword-first SEO net). Honest CTR only
— no fake authority, fear, or cure claims. See docs/competitor_teardown_2026-06-29.md.
"""

from __future__ import annotations

from video_agent.operator import _chatgpt_seo_prompt

CFG = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "audience": {"language": "es-ES", "age_range": [45, 75]},
    "seo": {"language": "es-ES", "min_tags": 5, "max_tags": 8},
}
SCRIPT = {"job_id": "j1", "hook": "h", "sections": [], "narration": "n", "cta": "c"}
SCENES = {"total_duration_sec": 600, "scenes": [{"visual_prompt": "kitchen"}]}


def _p() -> str:
    return _chatgpt_seo_prompt(CFG, SCRIPT, SCENES)


def test_title_is_ctr_first_hook_then_keyword():
    p = _p()
    assert "CTR-FIRST" in p
    assert "curiosity or contrarian HOOK" in p
    assert "keyword may come second" in p


def test_three_variants_use_distinct_devices():
    p = _p()
    assert "variant 1 = curiosity-gap" in p
    assert "variant 2 = contrarian" in p
    assert "variant 3 = keyword-first searchable" in p


def test_honest_ctr_guardrails_present():
    p = _p()
    assert "HONEST CTR ONLY" in p
    assert "fake authority" in p
    assert "milagro/cura/garantiza" in p


def test_keeps_same_pain_angle_and_complementary_rules():
    p = _p()
    assert "SAME PAIN ANGLE" in p
    assert "COMPLEMENTARY, NOT REPETITIVE" in p
