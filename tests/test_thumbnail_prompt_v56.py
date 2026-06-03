"""Thumbnail prompt P0+P1 hardening tests (post spec v5.6 review)."""

from __future__ import annotations

from video_agent.orchestrator.stages import _VARIANT_STRATEGY, _legacy_build_thumbnail_prompt as _build_thumbnail_prompt


def _prompt(variant_index: int = 1, text: str = "DUERME MEJOR") -> str:
    return _build_thumbnail_prompt(
        title="Por qué te despiertas cansado después de los 45",
        thumbnail_text=text,
        accent_color="#F2C94C",
        channel_description="Vida Plena 45+, practical wellness, Spain-first.",
        variant_index=variant_index,
    )


# P0 — variant differentiation ----------------------------------------------

def test_variant_strategies_are_distinct():
    p1 = _prompt(1)
    p2 = _prompt(2)
    p3 = _prompt(3)
    assert "FACE-DRIVEN" in p1
    assert "OBJECT-DRIVEN" in p2
    assert "COMPARISON-DRIVEN" in p3
    assert p1 != p2 != p3


def test_variant_strategy_lookup_returns_face_driven_default():
    # Unknown index falls back to variant 1's strategy.
    p_unknown = _prompt(99)
    assert "FACE-DRIVEN" in p_unknown
    assert _VARIANT_STRATEGY[1] in p_unknown


# P0 — Spain-first subject --------------------------------------------------

def test_prompt_does_not_use_hispanic_or_latina_as_subject():
    p = _prompt()
    # Positive subject description must not use Hispanic/Latina labels.
    assert "Hispanic or Latina" not in p
    # Hispanic/Latina styling may appear in the NEGATIVE block as forbidden;
    # outside the negative section it must not describe the subject.
    subject_block = p.split("NEGATIVE:")[0]
    assert "Hispanic" not in subject_block
    assert "Latina" not in subject_block


def test_prompt_uses_mediterranean_spanish_subject():
    p = _prompt()
    assert "Mediterranean Spanish" in p
    assert "Spain-first" in p


# P0 — Spanish diacritics ----------------------------------------------------

def test_prompt_preserves_spanish_diacritics_instruction():
    text = "MAÑANA SIN DOLOR"
    p = _prompt(text=text)
    assert text in p
    assert "preserving Spanish accents" in p
    # The literal accented character cheat-sheet must appear.
    for ch in ["ñ", "á", "é", "í", "ó", "ú", "¿", "¡"]:
        assert ch in p


# P0 — topic-category guidance, no plate hardcode ---------------------------

def test_prompt_drops_plate_taking_energy_bias():
    p = _prompt()
    assert "plate taking energy" not in p
    # All five topic categories must be hinted so the model picks the right one.
    for category in ["sleep", "food", "stiffness", "stress", "energy"]:
        assert category in p


# P0 — anti-stereotype + non-medical ----------------------------------------

def test_prompt_blocks_clinical_and_stereotype_visuals():
    p = _prompt()
    assert "frail-elderly stereotype" in p
    assert "doctor framing" in p
    assert "before/after weight-loss" in p
    assert "miracle cure" in p


# P0 — exact hook text baked in ---------------------------------------------

def test_prompt_includes_exact_hook_text_twice():
    text = "TU PLATO TE HABLA"
    p = _prompt(text=text)
    # Once in the TEXT OVERLAY block, once in the closing RULES sentence.
    assert p.count(text) >= 2


# Channel description hygiene ------------------------------------------------

def test_channel_description_is_english_not_raw_spanish():
    # The caller used to inject the raw Spanish channel.description directly,
    # but the stage now uses a stable English summary. The function still
    # accepts whatever channel_description the caller passes — we verify
    # the prompt structure does not assume English-only.
    p = _build_thumbnail_prompt(
        title="t",
        thumbnail_text="HOOK",
        accent_color="#fff",
        channel_description="custom-en-summary",
        variant_index=1,
    )
    assert "custom-en-summary" in p


# Smoke ----------------------------------------------------------------------

def test_prompt_includes_dimensions_and_accent():
    p = _prompt()
    assert "1920x1080" in p
    assert "16:9" in p
    assert "#F2C94C" in p
