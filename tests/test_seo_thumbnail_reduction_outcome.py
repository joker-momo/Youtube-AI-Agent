"""bug-547: the deterministic thumbnail copy scorer must recognise the
body-posture / relaxation / sleep-disruption vocabulary this wellness channel
actually uses, reduction-framed benefits ("menos tensión"), and Spanish
appetite-outcome word families.

Regression: job 20260721-164455 stalled at seo_promote for 5 retries because
title_variants[1]/[2] thumbnail_text were rejected as "context-free fragments"
even though each names a concrete object AND a concrete benefit/symptom. The
generator was producing good copy; the scorer's food-biased lexicon could not
see it.
"""
import pytest

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


def test_food_plus_hunger_is_a_valid_standalone_micro_promise():
    """Exact production regression from job 20260819-121133.

    The copy names concrete foods plus the viewer pain it addresses.  The
    scorer must not reject it merely because its finite wellness vocabulary
    forgot the Spanish hunger word family.
    """
    d = _detail(
        "YOGUR Y FRUTA CONTRA EL HAMBRE",
        title="El error de la tarde: merienda proteica y hambre tras los 60",
    )
    assert _passes_standalone_contract(d), d
    assert d["standalone_value_score"] == 14


def test_hunger_word_family_counts_as_pain_when_anchored_to_food():
    d = _detail(
        "PAN PARA NO LLEGAR HAMBRIENTO",
        title="Cómo evitar llegar hambriento a la cena",
    )
    assert _passes_standalone_contract(d), d


@pytest.mark.parametrize(
    ("thumbnail_text", "title"),
    [
        (
            "YOGUR GRIEGO: PROTEÍNA PARA SACIAR",
            "Pérdida muscular después de los 60: meriendas ricas en proteína",
        ),
        (
            "AVENA SACIANTE",
            "Avena saciante como merienda sencilla",
        ),
        (
            "YOGUR PARA MAYOR SACIEDAD",
            "Yogur con proteína para mejorar la saciedad",
        ),
        (
            "MERIENDA QUE TE DEJA SATISFECHO",
            "Merienda proteica para quedar satisfecho hasta la cena",
        ),
    ],
)
def test_appetite_outcome_word_families_form_valid_micro_promises(
    thumbnail_text: str,
    title: str,
):
    """One semantic family covers useful inflections, not one literal phrase."""
    d = _detail(thumbnail_text, title=title)
    assert _passes_standalone_contract(d), d
    assert d["standalone_value_score"] == 14


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


def test_hunger_without_topic_action_or_specificity_still_fails():
    d = _detail("CONTRA EL HAMBRE", title="Meriendas fáciles después de los 60")
    assert not _passes_standalone_contract(d), d


def test_appetite_outcome_verb_cannot_double_count_as_title_topic():
    d = _detail("SACIAR", title="Cómo saciar el hambre después de los 60")
    assert not _passes_standalone_contract(d), d


def test_fear_bait_still_fails_even_with_a_topic():
    d = _detail("ESTA POSTURA ARRUINA TU DESCANSO", title="Postura para dormir")
    assert d["trust_penalty"] > 0
    assert not _passes_standalone_contract(d), d
