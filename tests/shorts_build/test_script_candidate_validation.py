from __future__ import annotations

from video_agent.shorts.validation.checks import validate_full_short_script_candidate
from video_agent.shorts.validation.script_checks import (
    cta_beat_has_channel_direction,
    repair_cta_beat_channel_direction,
)


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


def _script_with_cta_beat(cta_narration: str) -> dict:
    return {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook", "narration": "¿Te pasa esto?"},
            {"time_sec": "3-8", "purpose": "setup", "narration": "Mira."},
            {"time_sec": "8-15", "purpose": "payoff", "narration": "Haz esto."},
            {"time_sec": "15-20", "purpose": "payoff", "narration": "Y esto."},
            {"time_sec": "20-25", "purpose": "cta", "narration": cta_narration},
        ],
        "cta": "¿También te pasa? Vídeo completo en el canal.",
        "narration": "¿Te pasa esto? Mira. Haz esto. Y esto. " + cta_narration,
    }


def test_rejects_cta_beat_missing_channel_direction():
    # Spoken CTA beat omits the channel direction -> funnel break, must be caught.
    script = _script_with_cta_beat("El objetivo es ganar control y confianza. ¿También te pasa?")
    errors = validate_full_short_script_candidate(script, {"target_duration_sec": 35})
    assert "cta_beat_missing_channel_direction" in errors


def test_passes_cta_beat_with_channel_direction():
    script = _script_with_cta_beat("¿También te pasa? Vídeo completo en el canal.")
    errors = validate_full_short_script_candidate(script, {"target_duration_sec": 35})
    assert "cta_beat_missing_channel_direction" not in errors
    assert cta_beat_has_channel_direction(script, {})


def test_repair_injects_channel_direction_into_cta_beat():
    script = _script_with_cta_beat("El objetivo es ganar control. ¿También te pasa?")
    assert cta_beat_has_channel_direction(script, {}) is False

    applied = repair_cta_beat_channel_direction(script, {})
    assert applied is True
    assert cta_beat_has_channel_direction(script, {}) is True
    # Repaired script now clears the deterministic gate.
    errors = validate_full_short_script_candidate(script, {"target_duration_sec": 35})
    assert "cta_beat_missing_channel_direction" not in errors
    # Idempotent: a second repair is a no-op.
    assert repair_cta_beat_channel_direction(script, {}) is False


def test_repair_keeps_global_narration_in_sync():
    old_cta = "El objetivo es ganar control. ¿También te pasa?"
    script = _script_with_cta_beat(old_cta)
    repair_cta_beat_channel_direction(script, {})
    assert old_cta not in script["narration"]
    assert "Vídeo completo en el canal" in script["narration"]


# --- bug-484: validator must accept the topic-aware funnel CTA -----------------

_TOPIC_CFG = {
    "shorts": {
        "funnel": {
            "default_cta_without_url": "Vídeo completo en el canal.",
            "cta_topic_template_without_url": "Más sobre {tema} en el canal.",
            "cta_max_words": 8,
        }
    }
}
_LONG_TITLE = "El error oculto: aceite de oliva en ayunas tras los 45"


def _topic_script(cta: str) -> dict:
    return {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook", "narration": "¿Te pasa esto?"},
            {"time_sec": "3-8", "purpose": "setup"},
            {"time_sec": "8-15", "purpose": "payoff"},
            {"time_sec": "15-20", "purpose": "payoff"},
            {"time_sec": "20-25", "purpose": "cta", "narration": cta},
        ],
        "cta": cta,
    }


def test_validator_accepts_topic_cta_matching_prompt():
    """The exact live rejection (bug-484): the prompt asked for the topic CTA,
    the model obeyed, and the validator (sm=None at script time) hardcoded the
    generic default and rejected every attempt."""
    script = _topic_script("Más sobre la alimentación en el canal.")
    errors = validate_full_short_script_candidate(
        script,
        {"target_duration_sec": 35},
        None,
        channel_config=_TOPIC_CFG,
        long_video_title=_LONG_TITLE,
    )
    assert "missing_expected_funnel_cta" not in errors
    assert "cta_beat_missing_channel_direction" not in errors


def test_validator_still_accepts_generic_cta_with_topic_config():
    """Wording must never brick a render: the generic default stays acceptable."""
    script = _topic_script("Vídeo completo en el canal.")
    errors = validate_full_short_script_candidate(
        script,
        {"target_duration_sec": 35},
        None,
        channel_config=_TOPIC_CFG,
        long_video_title=_LONG_TITLE,
    )
    assert "missing_expected_funnel_cta" not in errors
    assert "cta_beat_missing_channel_direction" not in errors


def test_repair_does_not_overwrite_topic_cta():
    """Attempt-3 repair must not rewrite a topic CTA back to the generic."""
    script = _topic_script("¿También te pasa? Más sobre la alimentación en el canal.")
    applied = repair_cta_beat_channel_direction(
        script,
        {},
        channel_config=_TOPIC_CFG,
        long_video_title=_LONG_TITLE,
    )
    assert applied is False
    assert "Más sobre la alimentación en el canal." in script["cta"]


def test_qa_mirror_without_config_accepts_channel_direction():
    """The QA mirror (no channel_config plumbing) must not flag a topic CTA."""
    script = _topic_script("Más sobre la alimentación en el canal.")
    assert cta_beat_has_channel_direction(script, {}) is True


def test_validator_accepts_natural_cta_naming_long_video_content():
    """New contract: the model writes a natural CTA naming the long video's
    content; the gate only requires the channel direction ('canal')."""
    script = _topic_script("La guía completa del aceite está en el canal.")
    errors = validate_full_short_script_candidate(
        script,
        {"target_duration_sec": 35},
        None,
        channel_config=_TOPIC_CFG,
        long_video_title=_LONG_TITLE,
    )
    assert "missing_expected_funnel_cta" not in errors
    assert "cta_beat_missing_channel_direction" not in errors


# ── bug-504: model CTA laundered into a strict operator override ─────────────
# build_source_map copies the script's own natural CTA into source_map.funnel.cta.
# acceptable_funnel_ctas then treated any non-generic source_map CTA as an
# OPERATOR override (strict exact-phrase), so every script REGENERATION whose
# natural CTA varied slightly ('y sueño', 'Mira...') was rejected with
# missing_expected_funnel_cta + cta_beat_missing_channel_direction even though
# it carried 'canal' — a 5-attempt reject storm ending in failed_hard_blocker.
# Only short_plan.funnel.cta is a real operator override.

def _cafe_script(cta: str) -> dict:
    return {
        "beats": [
            {"time_sec": "0-3", "purpose": "hook", "narration": "PRUÉBALO 7 DÍAS."},
            {"time_sec": "3-8", "purpose": "setup", "narration": "Si no sabes si el café influye, sigue este método."},
            {"time_sec": "8-15", "purpose": "payoff", "narration": "Registra sin cambiar nada."},
            {"time_sec": "15-20", "purpose": "payoff", "narration": "Verás tendencias."},
            {"time_sec": "20-25", "purpose": "cta", "narration": cta},
        ],
        "cta": cta,
    }


def test_model_derived_source_map_cta_is_not_a_strict_override():
    """Regeneration with a slightly different natural CTA (still 'canal') must pass."""
    source_map = {"funnel": {"cta": "Descubre el error del café sin azúcar en el canal."}}
    script = _cafe_script("Descubre el error del café sin azúcar y sueño en el canal.")
    errors = validate_full_short_script_candidate(script, {"target_duration_sec": 35}, source_map)
    assert "missing_expected_funnel_cta" not in errors, errors
    assert "cta_beat_missing_channel_direction" not in errors, errors


def test_operator_plan_cta_stays_strict():
    """A real operator override in short_plan.funnel.cta is still exact-phrase."""
    plan = {"target_duration_sec": 35, "funnel": {"cta": "Guarda este truco del café."}}
    script = _cafe_script("Descubre el error del café en el canal.")
    errors = validate_full_short_script_candidate(script, plan)
    assert "missing_expected_funnel_cta" in errors


def test_cta_beat_channel_direction_accepts_natural_variation():
    source_map = {"funnel": {"cta": "Descubre el error del café sin azúcar en el canal."}}
    script = _cafe_script("Mira el error del café sin azúcar en el canal.")
    assert cta_beat_has_channel_direction(script, {}, source_map)
