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


def test_short_seo_prompt_uses_four_scroll_stopper_formulas_not_old_device():
    """Shorts titles use the 4 scroll-stopper formulas, <=40 chars. The old
    'option B wins / QUESTION-CONTRARIAN / prefer over flat El error' device and
    the 60-char limit must be GONE (they contradicted the Warning formula)."""
    from video_agent.shorts import prompts

    p = prompts.short_seo_prompt(
        _cfg(),
        {"short_id": "short-01", "format": "pain_to_tip", "pillar": "sleep"},
        {"hook": "El café sin azúcar y el sueño", "narration": "Cuidado con la tarde."},
    )
    low = p.lower()
    # New rules present.
    assert "scroll" in low
    assert "error al" in low            # Warning formula
    assert "60 segundos" in low         # Quick Win
    assert "la verdad científica" in low  # Myth-Buster
    # Call Out marker changed 2026-07-12: "escucha esto" was a banned
    # context-free fragment; the formula now teaches a topic-named call out.
    assert "si tienes más de" in low    # Call Out
    assert "escucha esto" not in low
    assert "40" in p
    # Old contradictory rules GONE.
    assert "option B wins" not in p
    assert "CONTRARIAN:" not in p
    assert "over a flat 'El error…' statement" not in p
    assert "Maximum 60 characters" not in p


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


def test_build_short_seo_hard_enforces_40_char_title(tmp_path: Path):
    """A title over 40 chars must NOT survive (reviewer probe: 51-char title
    'Este título tiene claramente más de cuarenta carac…' was accepted at 50)."""
    from video_agent.shorts import short_seo_builder

    job = _long_job(tmp_path)
    long_title = "Este título tiene claramente más de cuarenta caracteres"  # 55 chars
    assert len(long_title) > 40

    def llm_fn(kind, prompt):
        return json.dumps({
            "title": long_title,
            "description": "Contenido real del pan.",
            "hashtags": ["#alimentacionsaludable", "#shorts"],
            "pinned_comment": "¿Cómo lo haces tú?",
        })

    seo = short_seo_builder.build_short_seo(
        job, "short-01", {"short_id": "short-01"},
        {"hook": "El pan y el título largo", "narration": "Dale sitio al pan."},
        _cfg(), llm_fn,
    )
    assert len(seo["title"]) <= 40, seo["title"]


def test_non_formula_title_cannot_survive_retries(tmp_path: Path):
    """A non-formula, hook-misaligned title must NOT be published — after
    retries the builder replaces it with a valid deterministic formula title."""
    from video_agent.shorts import short_seo_builder
    from video_agent.shorts.short_seo_builder import _title_issues

    job = _long_job(tmp_path)
    bad = "Consejos generales de nutrición"  # no formula, won't align with hook
    hook = "El insomnio tras la jubilación"

    def llm_fn(kind, prompt):  # stubbornly returns the same bad title every retry
        return json.dumps({
            "title": bad,
            "description": "Duerme mejor.",
            "hashtags": ["#bienestar", "#shorts"],
            "pinned_comment": "¿Te pasa?",
        })

    seo = short_seo_builder.build_short_seo(
        job, "short-01", {"short_id": "short-01"},
        {"hook": hook, "narration": "El descanso importa."},
        _cfg(), llm_fn,
    )
    assert seo["title"] != bad                    # the bad title did NOT survive
    assert _title_issues(seo["title"], hook) == []  # published title is valid
    assert len(seo["title"]) <= 40


