"""Per-video audience age is derived from the idea, not hardcoded to 45.

The channel brand is "Vida Plena 45+", but an individual idea can target a
different age ("si tienes MÁS DE 60 AÑOS ..."). Every content builder must then
depict that age. When the idea carries no explicit age, fall back to the
channel's configured ``audience.age_range`` floor (45 here) so existing
generic-idea behaviour is unchanged.
"""

from __future__ import annotations

from video_agent.audience_age import audience_context, resolve_target_min_age

CHANNEL = {"audience": {"age_range": [45, 75]}}


def test_explicit_spanish_age_more_than_60():
    signals = ["🚨SI TIENES MAS DE 60 AÑOS ESTOS ALIMENTOS SON IMPRESCINDIBLES"]
    assert resolve_target_min_age(CHANNEL, *signals) == 60


def test_explicit_spanish_despues_de_los_70():
    assert resolve_target_min_age(CHANNEL, "carencias nutricionales después de los 70") == 70


def test_explicit_english_over_50():
    assert resolve_target_min_age(CHANNEL, "the best habits for adults over 50") == 50


def test_no_age_signal_falls_back_to_channel_floor():
    signals = ["cómo se pueden disfrutar los partidos nocturnos del mundial"]
    assert resolve_target_min_age(CHANNEL, *signals) == 45


def test_no_channel_and_no_signal_defaults_to_45():
    assert resolve_target_min_age({}, "algo sin edad") == 45


def test_ignores_implausible_numbers():
    # A "2026" year or "7 días" must not be read as an age.
    assert resolve_target_min_age(CHANNEL, "reto de 7 días en 2026, sin edad") == 45


def test_context_descriptors_track_the_age():
    ctx = audience_context(60)
    assert ctx["min_age"] == 60
    assert ctx["es_plus"] == "adultos 60+"
    assert ctx["en_plus"] == "adults 60+"
    # subject band spans a decade from the floor for image styling.
    assert ctx["subject_band"] == "60-70"


def test_context_for_default_45():
    ctx = audience_context(45)
    assert ctx["es_plus"] == "adultos 45+"
    assert ctx["en_plus"] == "adults 45+"
    assert ctx["subject_band"] == "45-55"


def test_resolve_and_context_end_to_end():
    ctx = audience_context(resolve_target_min_age(CHANNEL, "alimentos después de los 60"))
    assert ctx["en_plus"] == "adults 60+"
