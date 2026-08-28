"""Deterministic benchmark matrix — Task 9 of the packaging-CTR plan.

Synthetic title/thumbnail_text packages standing in for eight recurring
real-world patterns. This asserts RELATIVE RANKINGS and REASON CODES the
scorer must produce, not a claim about actual CTR — real CTR uplift can only
be measured from YouTube Studio impressions data after release. No network
calls, no image generation, no downloaded thumbnails; everything here is a
plain Python string fixture.
"""

from __future__ import annotations

from video_agent.seo.title_scorer import score_variant, score_variants

# Each key names the pattern the plan asked the benchmark to represent.
PACKAGES: dict[str, dict[str, str]] = {
    "passive_repeated_face_led_food": {
        "title": "Meriendas después de los 60",
        "thumbnail_text": "MIRA ESTO",
    },
    "active_food_choice": {
        "title": "Qué elegir para merendar si pierdes fuerza después de los 60",
        "thumbnail_text": "ELIGE MÁS PROTEÍNA",
    },
    "object_only_label_inspection": {
        "title": "Cómo leer la etiqueta antes de comprar pan integral",
        "thumbnail_text": "ETIQUETA: QUÉ ELEGIR",
    },
    "honest_comparison": {
        "title": "Pan integral o pan blanco: qué elegir después de los 60",
        "thumbnail_text": "ELIGE EL PAN CORRECTO",
    },
    "mismatched_title_and_thumbnail": {
        "title": "Cómo evitar el bajón después de comer",
        "thumbnail_text": "DUERME TODA LA NOCHE",
    },
    "unsupported_scientific_authority": {
        "title": "La verdad científica que nadie te cuenta sobre el sueño",
        "thumbnail_text": "CURA EL INSOMNIO YA",
    },
    "descriptive_only_title": {
        "title": "Meriendas con proteína después de los 60: qué elegir",
        "thumbnail_text": "MERIENDAS SALUDABLES",
    },
    "concrete_curiosity_payoff_title": {
        "title": "¿Pierdes músculo después de los 60? Estas meriendas sí aportan proteína",
        "thumbnail_text": "MÁS PROTEÍNA, MENOS PÉRDIDA",
    },
}


def _score(name: str) -> int:
    return score_variant(PACKAGES[name])["score"]


def test_concrete_curiosity_payoff_outranks_descriptive_only_title():
    assert _score("concrete_curiosity_payoff_title") > _score("descriptive_only_title")


def test_active_food_choice_outranks_passive_repeated_face_led_copy():
    assert _score("active_food_choice") > _score("passive_repeated_face_led_food")


def test_honest_comparison_outranks_mismatched_title_and_thumbnail():
    assert _score("honest_comparison") > _score("mismatched_title_and_thumbnail")


def test_mismatched_package_is_flagged_pain_mismatch():
    result = score_variant(PACKAGES["mismatched_title_and_thumbnail"])
    assert "pain_mismatch" in result["breakdown"]["alignment"]["reason_codes"]


def test_unsupported_scientific_authority_is_flagged_and_ranks_low():
    result = score_variant(PACKAGES["unsupported_scientific_authority"])
    title_reason_codes = result["breakdown"]["title_detail"]["reason_codes"]
    alignment_reason_codes = result["breakdown"]["alignment"]["reason_codes"]
    assert "unsupported_claim" in title_reason_codes
    assert "unsupported_outcome" in alignment_reason_codes
    assert _score("unsupported_scientific_authority") < _score("concrete_curiosity_payoff_title")


def test_object_only_label_inspection_is_a_valid_standalone_package():
    """Object-only copy is a legitimate visual strategy (thumbnail_planner's
    object_driven concept) — it must not be penalized as context-free just
    for lacking a face."""
    result = score_variant(PACKAGES["object_only_label_inspection"])
    assert result["breakdown"]["thumbnail_detail"]["vagueness_penalty"] == 0


def test_benchmark_ranking_is_deterministic_across_repeated_runs():
    variants = list(PACKAGES.values())
    first = score_variants([dict(v) for v in variants])
    second = score_variants([dict(v) for v in variants])
    assert first == second


def test_full_benchmark_ranking_matches_expected_relative_order():
    """One holistic ranking check: the two strongest, honest, complete
    packages must outrank the two weakest/dishonest/incomplete ones."""
    strongest = {"active_food_choice", "concrete_curiosity_payoff_title", "honest_comparison"}
    weakest = {"passive_repeated_face_led_food", "unsupported_scientific_authority"}
    assert min(_score(name) for name in strongest) > max(_score(name) for name in weakest)
