from __future__ import annotations

from .conftest import *  # noqa: F401,F403

def test_short_seo_prompt_prefers_broad_nutrition_tags_over_nutricion45():
    from video_agent.shorts import prompts

    p = prompts.short_seo_prompt(
        _cfg(),
        {"short_id": "short-01", "format": "pain_to_tip"},
        {"hook": "¿El pan engorda?", "narration": "Usa la regla del plato saludable."},
    )
    low = p.lower()

    assert "nutricion45" not in low
    assert "nutrición45" not in low
    assert "#alimentacionsaludable" in low
    assert "#platosaludable" in low


def test_short_seo_prompt_uses_high_volume_keywords_with_spain_45_intent():
    from video_agent.shorts import prompts

    p = prompts.short_seo_prompt(
        _cfg(),
        {"short_id": "short-01", "format": "pain_to_tip", "pillar": "nutrition"},
        {
            "hook": "El error no es comer pan. Es no darle sitio.",
            "narration": "Usa la regla del plato: medio verduras, un cuarto proteína y un cuarto pan o hidrato.",
        },
    )
    low = p.lower()

    assert "high-volume" in low
    assert "alimentación saludable" in low
    assert "plato saludable" in low
    assert "el pan engorda" in low
    assert "combine one broad search keyword" in low
    assert "spain" in low
    assert "45+" in low
    assert "description must reuse" in low


def test_short_seo_normalizes_concatenated_hashtags_and_removes_nutricion45():
    from video_agent.shorts.short_seo_builder import _normalize_hashtags

    assert _normalize_hashtags(["#nutricion45#pan", "#Plato Saludable", "#shorts"]) == [
        "#nutricion",
        "#pan",
        "#platosaludable",
        "#shorts",
    ]


def test_build_short_seo_rewrites_description_with_spaced_normalized_hashtags(tmp_path: Path):
    from video_agent.shorts import short_seo_builder

    job = _long_job(tmp_path)

    def llm_fn(kind, prompt):
        return json.dumps({
            "title": "¿El pan engorda?",
            "description": "Dale sitio al pan.#nutricion45#pan#platosaludable",
            "hashtags": ["#nutricion45#pan", "#platosaludable"],
            "pinned_comment": "¿Cómo lo haces tú?",
        })

    seo = short_seo_builder.build_short_seo(
        job,
        "short-01",
        {"short_id": "short-01"},
        {"hook": "¿El pan engorda?", "narration": "Dale sitio al pan."},
        _cfg(),
        llm_fn,
    )

    assert seo["hashtags"] == ["#nutricion", "#pan", "#platosaludable"]
    assert seo["description"].endswith("#nutricion #pan #platosaludable")
    assert "#nutricion45#pan" not in seo["description"]


