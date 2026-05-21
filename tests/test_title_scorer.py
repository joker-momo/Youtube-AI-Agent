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
