"""bug-547: the deterministic thumbnail copy scorer must recognise the
body-posture / relaxation / sleep-disruption vocabulary this wellness channel
actually uses, and reduction-framed benefits ("menos tensión").

Regression: job 20260721-164455 stalled at seo_promote for 5 retries because
title_variants[1]/[2] thumbnail_text were rejected as "context-free fragments"
even though each names a concrete object AND a concrete benefit/symptom. The
generator was producing good copy; the scorer's food-biased lexicon could not
see it.
"""
from video_agent.seo.title_scorer import score_variant


def _detail(thumbnail_text: str, title: str = "") -> dict:
    return score_variant({"thumbnail_text": thumbnail_text, "title": title})["breakdown"]["thumbnail_detail"]


def _passes_standalone_contract(detail: dict) -> bool:
    # Mirrors the gate in orchestrator/stages/seo.py::_enforce_seo_language_qa.
    return (
        detail["standalone_value_score"] >= 12
        and detail["vagueness_penalty"] == 0
        and detail["trust_penalty"] == 0
    )


# The two EXACT production thumbnail texts that stalled the job. Both are valid
# standalone micro-promises for a 45-75 sleep-wellness audience.
def test_reduction_framed_benefit_is_a_valid_outcome():
    d = _detail(
        "SILLA Y PIERNAS: MENOS TENSIÓN NOCTURNA",
        title="Lo que cambia al apoyar las piernas: despertares nocturnos",
    )
    assert _passes_standalone_contract(d), d


def test_relaxation_method_for_nighttime_awakenings_is_valid():
    d = _detail(
        "SILLA Y RESPIRACIÓN PARA DESPERTARES NOCTURNOS",
        title="No es forzar el sueño: postura para despertares nocturnos",
    )
    assert _passes_standalone_contract(d), d


def test_the_already_good_variant_still_passes():
    d = _detail(
        "PIERNAS APOYADAS 3 MINUTOS AL DORMIR",
        title="Despertares nocturnos: postura suave de tres minutos antes de dormir",
    )
    assert _passes_standalone_contract(d), d


# ── guardrails: the gate must NOT be weakened. Genuinely context-free copy,
# fear-bait, and topic-less fragments must still be rejected. ──────────────────
def test_deictic_fragment_without_object_still_fails():
    for bad in ("ESTO CAMBIA TODO", "LA CLAVE PARA DORMIR", "ESO NADIE TE LO DICE"):
        assert not _passes_standalone_contract(_detail(bad, title="Cómo dormir mejor")), bad


def test_reducing_a_plain_object_is_not_an_outcome():
    # "menos sal" reduces an INGREDIENT, not a discomfort — it must not be
    # miscounted as a benefit that lets a topic-only fragment through.
    d = _detail("MENOS SAL", title="")
    assert not _passes_standalone_contract(d), d


def test_fear_bait_still_fails_even_with_a_topic():
    d = _detail("ESTA POSTURA ARRUINA TU DESCANSO", title="Postura para dormir")
    assert d["trust_penalty"] > 0
    assert not _passes_standalone_contract(d), d
