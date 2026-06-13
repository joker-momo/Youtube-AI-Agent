from __future__ import annotations

import pytest
from video_agent.shorts.validation.checks import validate_full_short_script_candidate

def test_rejects_partial_script_too_few_blocks():
    script = {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook"},
            {"time_sec": "3-6", "purpose": "setup"},
            {"time_sec": "6-10", "purpose": "cta"}
        ],
        "cta": "Suscríbete."
    }
    errors = validate_full_short_script_candidate(script, {})
    assert "partial_script_too_few_blocks" in errors

def test_rejects_script_not_starting_at_zero():
    script = {
        "beats": [
            {"time_sec": "15-20", "purpose": "hook"},
            {"time_sec": "20-25", "purpose": "setup"},
            {"time_sec": "25-30", "purpose": "payoff"},
            {"time_sec": "30-35", "purpose": "payoff"},
            {"time_sec": "35-40", "purpose": "cta"}
        ],
        "cta": "Suscríbete."
    }
    errors = validate_full_short_script_candidate(script, {})
    assert "script_does_not_start_at_zero" in errors

def test_rejects_missing_cta():
    script = {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook"},
            {"time_sec": "3-8", "purpose": "setup"},
            {"time_sec": "8-15", "purpose": "payoff"},
            {"time_sec": "15-20", "purpose": "payoff"},
            {"time_sec": "20-25", "purpose": "payoff"}
        ]
        # No 'cta' field and no 'cta' purpose beat
    }
    errors = validate_full_short_script_candidate(script, {})
    assert "missing_cta" in errors

def test_rejects_duplicate_rewrite_text_across_scenes():
    # Simulate the bug where the same rewrite phrase is pasted across 4+ items
    script = {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook"},
            {"time_sec": "3-8", "purpose": "setup"},
            {"time_sec": "8-15", "purpose": "payoff"},
            {"time_sec": "15-20", "purpose": "payoff"},
            {"time_sec": "20-25", "purpose": "cta"}
        ],
        "cta": "Suscríbete al canal.",
        "source_mapped_flow": [
            {"spoken_summary": "relaja la mandíbula y siéntate cómodo"},
            {"spoken_summary": "relaja la mandíbula y siéntate cómodo"},
            {"spoken_summary": "relaja la mandíbula y siéntate cómodo"},
            {"spoken_summary": "relaja la mandíbula y siéntate cómodo"}
        ]
    }
    errors = validate_full_short_script_candidate(script, {})
    assert "same_rewrite_repeated_across_source_scenes" in errors

def test_passes_valid_full_script():
    script = {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook", "narration": "¿Te pasa esto?"},
            {"time_sec": "3-8", "purpose": "setup"},
            {"time_sec": "8-15", "purpose": "payoff"},
            {"time_sec": "15-20", "purpose": "payoff"},
            {"time_sec": "20-25", "purpose": "cta"}
        ],
        "cta": "Vídeo completo en el canal.",
        "source_mapped_flow": [
            {"spoken_summary": "Este es el punto uno."},
            {"spoken_summary": "Este es el punto dos."},
            {"spoken_summary": "Este es el punto tres."}
        ]
    }
    errors = validate_full_short_script_candidate(script, {"target_duration_sec": 35})
    assert len(errors) == 0

def test_dynamic_cta_failure_on_comment_trigger():
    script = {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook", "narration": "¿Te pasa esto?"},
            {"time_sec": "3-8", "purpose": "setup"},
            {"time_sec": "8-15", "purpose": "payoff"},
            {"time_sec": "15-20", "purpose": "payoff"},
            {"time_sec": "20-25", "purpose": "cta"}
        ],
        "cta": "¿También te pasa?"
    }
    source_map = {
        "funnel": {"cta": "Guárdalo para esta noche."}
    }
    errors = validate_full_short_script_candidate(script, {"target_duration_sec": 35}, source_map)
    assert "missing_expected_funnel_cta" in errors

def test_dynamic_cta_fallback_success():
    script = {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook", "narration": "¿Te pasa esto?"},
            {"time_sec": "3-8", "purpose": "setup"},
            {"time_sec": "8-15", "purpose": "payoff"},
            {"time_sec": "15-20", "purpose": "payoff"},
            {"time_sec": "20-25", "purpose": "cta"}
        ],
        "cta": "Vídeo completo en el canal."
    }
    # No source_map or short_plan provided -> fallbacks to "Vídeo completo en el canal."
    errors = validate_full_short_script_candidate(script, {"target_duration_sec": 35})
    assert "missing_expected_funnel_cta" not in errors
