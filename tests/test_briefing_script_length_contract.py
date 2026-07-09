"""Regression: the script + script_qa BRIEFING length contract is content-driven.

bug-495 (Codex 20260707-173112): briefing.py hardcoded a "2900-4350 palabras
(~20-30 min a 145 wpm)" range for the Gemini script_qa gate, ignoring the
channel's real 120 wpm pace and 11-min (660s) floor. That phantom upper bound
rejected correctly-sized long scripts (~1700 words) that satisfy the current
floor. The QA contract must derive its floor from
``content_format.duration_sec_min × tts.pace_wpm`` — exactly like the
generator prompt — and impose NO maximum.

``test_script_length_quality_first.py`` guards the operator_prompts generator;
this file guards the briefing/QA side so the two can never drift again.
"""

from __future__ import annotations

from video_agent.orchestrator.briefing import _script_length_floor, build_task_prompt

# duration_sec_min=660, pace_wpm=120 -> floor = 660/60 * 120 = 1320 words.
CFG = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "content_format": {"duration_sec_min": 660, "target_duration_sec": 840},
    "tts": {"pace_wpm": 120},
}
FLOOR_WORDS = round(660 / 60 * 120)  # 1320

# The stale hardcoded contract that must never reappear anywhere in the briefing.
STALE_TOKENS = ("2900", "4350", "20-30 min", "145 wpm", "145 palabras")


def test_floor_helper_derives_from_config_no_maximum():
    f = _script_length_floor(CFG)
    assert f["script_word_floor"] == FLOOR_WORDS
    assert f["floor_sec"] == 660
    assert f["pace_wpm"] == 120


def test_script_briefing_uses_derived_floor_not_stale_range():
    prompt = build_task_prompt("script", "", CFG)
    assert str(FLOOR_WORDS) in prompt
    for stale in STALE_TOKENS:
        assert stale not in prompt, f"stale length token {stale!r} leaked into script briefing"


def test_script_qa_briefing_has_no_phantom_upper_bound():
    # The QA gate must not carry the obsolete 2900-4350 ceiling that rejected
    # on-contract long scripts. Assembled QA-side text (role + schema + task).
    from video_agent.orchestrator.briefing import _ROLES_ES, _SCHEMA_ES

    qa_text = (
        _ROLES_ES.get("script_qa", "")
        + _SCHEMA_ES.get("script_qa", "")
        + build_task_prompt("script_qa", "", CFG)
    )
    for stale in STALE_TOKENS:
        assert stale not in qa_text, f"stale length token {stale!r} leaked into script_qa briefing"


def test_floor_scales_with_channel_duration_floor():
    # A channel with a higher floor gets a proportionally higher word floor —
    # proves the number is derived, not a constant.
    cfg = {"content_format": {"duration_sec_min": 900}, "tts": {"pace_wpm": 130}}
    assert _script_length_floor(cfg)["script_word_floor"] == round(900 / 60 * 130)
