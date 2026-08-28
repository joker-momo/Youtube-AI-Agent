import pytest
from video_agent.seo.title_scorer import score_variant, score_variants, _title_score, _thumbnail_score


def test_perfect_variant_scores_high():
    variant = {
        "title": "5 secretos para dormir mejor después de los 45",
        "thumbnail_text": "DUERME MEJOR HOY",
    }
    result = score_variant(variant)
    assert result["score"] >= 70
    assert "title_score" in result["breakdown"]
    assert "thumbnail_score" in result["breakdown"]


def test_weak_variant_scores_low():
    variant = {
        "title": "Tips",
        "thumbnail_text": "some text here",
    }
    result = score_variant(variant)
    assert result["score"] <= 30


def test_score_returns_required_keys():
    variant = {"title": "Cómo mejorar el sueño en 7 días", "thumbnail_text": "MEJORA TU SUEÑO"}
    result = score_variant(variant)
    assert "score" in result
    assert "breakdown" in result
    assert isinstance(result["score"], int)


def test_score_variants_returns_sorted_list():
    variants = [
        {"title": "Tips", "thumbnail_text": "tips"},
        {"title": "5 secretos para dormir mejor después de los 45", "thumbnail_text": "DUERME MEJOR HOY"},
        {"title": "Cómo mejorar el sueño en 7 días con estos pasos", "thumbnail_text": "SECRETO DEL SUEÑO"},
    ]
    scored = score_variants(variants)
    assert len(scored) == 3
    assert scored[0]["score"] >= scored[1]["score"] >= scored[2]["score"]


def test_all_caps_check():
    assert _thumbnail_score({"thumbnail_text": "DUERME MEJOR HOY"})["all_caps"] is True
    assert _thumbnail_score({"thumbnail_text": "Duerme mejor hoy"})["all_caps"] is False


def test_word_count_boundaries():
    r3 = _thumbnail_score({"thumbnail_text": "WORD WORD WORD"})
    r7 = _thumbnail_score({"thumbnail_text": "WORD WORD WORD WORD WORD WORD WORD"})
    assert r3["word_count_score"] > r7["word_count_score"]


def test_handles_none_fields():
    result = score_variant({"title": None, "thumbnail_text": None})
    assert result["score"] == 0
    assert isinstance(result["breakdown"], dict)


def test_handles_missing_fields():
    result = score_variant({})
    assert result["score"] == 0


def test_title_word_count_boundaries():
    # 3 words → below optimal, gets fallback tier only if 4-12 → no, 3 < 4 → 0
    r3 = _title_score({"title": "uno dos tres"})
    # 5 words → 4-12 tier → +8
    r5 = _title_score({"title": "uno dos tres cuatro cinco"})
    # 8 words → 6-10 tier → +15
    r8 = _title_score({"title": "uno dos tres cuatro cinco seis siete ocho"})
    # 13 words → outside all tiers → 0
    r13 = _title_score({"title": "uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece"})
    assert r3["word_count_score"] == 0
    assert r5["word_count_score"] == 8
    assert r8["word_count_score"] == 15
    assert r13["word_count_score"] == 0


def test_score_empty_variant():
    result = score_variant({})
    assert result["score"] == 0
    assert "breakdown" in result


# ── semantic title scoring: topic/stake/payoff/specificity over punctuation ──

@pytest.mark.parametrize(
    ("descriptive", "stronger"),
    [
        (
            "Meriendas con proteína después de los 60: qué elegir",
            "¿Pierdes músculo después de los 60? Estas meriendas sí aportan proteína",
        ),
        (
            "Pan, arroz y patata: qué elegir",
            "¿Pan, arroz o patata? La elección que evita la pesadez después de comer",
        ),
    ],
)
def test_title_score_prefers_concrete_stake_and_payoff(descriptive, stronger):
    weaker_total = _title_score({"title": descriptive})["total"]
    stronger_total = _title_score({"title": stronger})["total"]
    assert stronger_total > weaker_total


def test_title_score_question_mark_alone_does_not_beat_concrete_payoff():
    question_only = _title_score({"title": "¿Qué tal si cambias esto hoy mismo?"})["total"]
    concrete_payoff = _title_score({"title": "Duerme mejor con esta rutina de tres minutos"})["total"]
    assert concrete_payoff > question_only


def test_title_score_digit_alone_does_not_beat_audience_relevant_consequence():
    digit_only = _title_score({"title": "5 cosas sobre el café que quizá no sabías"})["total"]
    consequence = _title_score({"title": "Pierdes fuerza en las piernas si evitas caminar"})["total"]
    assert consequence >= digit_only


def test_title_score_generic_power_words_do_not_earn_automatic_points():
    generic = _title_score({"title": "El secreto mejor guardado que nadie te cuenta"})
    assert generic["total"] < 20


@pytest.mark.parametrize(
    "title",
    [
        "La verdad científica que nadie te cuenta sobre el sueño",
        "Este remedio está garantizado para curar el insomnio",
        "El diagnóstico que los médicos ocultan sobre tu dolor",
    ],
)
def test_title_score_penalizes_unsupported_claims_with_stable_reason_code(title):
    detail = _title_score({"title": title})
    assert "unsupported_claim" in detail["reason_codes"]


def test_title_score_natural_keyword_first_title_stays_competitive():
    keyword_first = _title_score(
        {"title": "Aceite de oliva después de los 60: cuándo tomarlo para cuidar el corazón"}
    )["total"]
    punctuation_trick = _title_score({"title": "¿¿¿Esto??? ¡¡¡Nadie lo sabe!!!"})["total"]
    assert keyword_first > punctuation_trick


# ── package alignment: title/thumbnail must share the same pain angle ───────

def test_score_variant_penalizes_different_pain_between_title_and_thumbnail():
    aligned = {"title": "Cómo evitar el bajón después de comer", "thumbnail_text": "ENERGÍA SIN BAJÓN"}
    mismatched = {"title": "Cómo evitar el bajón después de comer", "thumbnail_text": "DUERME TODA LA NOCHE"}
    aligned_result = score_variant(aligned)
    mismatch_result = score_variant(mismatched)
    assert aligned_result["score"] > mismatch_result["score"]
    assert "pain_mismatch" in mismatch_result["breakdown"]["alignment"]["reason_codes"]


def test_score_variant_penalizes_unsupported_thumbnail_outcome():
    result = score_variant({
        "title": "Meriendas con proteína después de los 60",
        "thumbnail_text": "CURA LA SARCOPENIA",
    })
    assert "unsupported_outcome" in result["breakdown"]["alignment"]["reason_codes"]


def test_complementary_thumbnail_with_added_payoff_outranks_title_repeat():
    """A thumbnail that adds a short decision/payoff must outrank one that
    simply repeats the title's own words back."""
    title = "Cómo evitar el bajón después de comer"
    repeat = score_variant({"title": title, "thumbnail_text": "EL BAJÓN DESPUÉS DE COMER"})
    added_payoff = score_variant({"title": title, "thumbnail_text": "ENERGÍA SIN BAJÓN TRAS COMER"})
    assert added_payoff["score"] > repeat["score"]


# ── set-level device diversity ───────────────────────────────────────────────

def test_score_variants_applies_duplicate_device_penalty():
    variants = [
        {"title": "¿Qué merienda tiene más proteína?", "thumbnail_text": "MÁS PROTEÍNA"},
        {"title": "¿Qué snack evita el hambre?", "thumbnail_text": "SIN HAMBRE"},
        {"title": "Meriendas con proteína después de los 60", "thumbnail_text": "ELIGE MEJOR"},
    ]
    scored = score_variants(variants)
    assert sum(
        "duplicate_title_device" in item["score_breakdown"]["set_reason_codes"]
        for item in scored
    ) >= 1


def test_score_variants_is_deterministic_for_identical_input():
    variants = [
        {"title": "¿Qué merienda tiene más proteína?", "thumbnail_text": "MÁS PROTEÍNA"},
        {"title": "¿Qué snack evita el hambre?", "thumbnail_text": "SIN HAMBRE"},
        {"title": "Meriendas con proteína después de los 60", "thumbnail_text": "ELIGE MEJOR"},
    ]
    first = score_variants([dict(v) for v in variants])
    second = score_variants([dict(v) for v in variants])
    assert first == second
