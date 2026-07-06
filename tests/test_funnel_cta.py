"""Topic-aware Short funnel CTA (names the companion long video's theme)."""

from __future__ import annotations

from video_agent.shorts import prompts
from video_agent.shorts.source_map import funnel_topic_es, is_generic_cta, resolve_funnel_cta

FUNNEL = {
    "default_cta_without_url": "Vídeo completo en el canal.",
    "default_cta_with_url": "Mira la guía completa aquí.",
    "cta_topic_template_without_url": "Más sobre {tema} en el canal.",
    "cta_topic_template_with_url": "Guía de {tema}, míralo en el canal.",
    "cta_max_words": 8,
}


def test_topic_resolves_from_pillar():
    assert funnel_topic_es({"pillar": "sleep"}) == "el sueño"
    assert funnel_topic_es({"pillar": "nutrition"}) == "la alimentación"


def test_topic_falls_back_to_keywords_when_pillar_missing():
    """bug-484: many Short plans carry pillar=None; derive the topic from the
    plan's text (title/hook/viewer_pain/narration) so the CTA still specializes."""
    # olive-oil nutrition Short with NO pillar (the real failing case)
    plan = {
        "pillar": None,
        "title": "Si te sienta mal, cambia",
        "viewer_pain": "el aceite de oliva en ayunas le sienta mal",
        "narration_seed": "Toma el aceite de oliva con comida.",
    }
    assert funnel_topic_es(plan) == "la alimentación"
    # sleep Short, no pillar
    assert funnel_topic_es({"hook_text": "No puedes dormir de noche"}) == "el sueño"
    # a medical topic beats the generic 'la alimentación' when both hint
    assert funnel_topic_es({"viewer_pain": "glucosa alta y diabetes"}) == "el azúcar"
    # truly unknown -> empty (generic CTA)
    assert funnel_topic_es({"title": "algo sin pistas claras"}) == ""


def test_resolve_cta_specializes_without_pillar_via_keywords():
    cta = resolve_funnel_cta(FUNNEL, {"pillar": None, "viewer_pain": "aceite de oliva"}, has_url=False)
    assert cta == "Más sobre la alimentación en el canal."
    assert len(cta.split()) <= 8


def test_resolve_cta_substitutes_topic_within_word_budget():
    cta = resolve_funnel_cta(FUNNEL, {"pillar": "sleep"}, has_url=False)
    assert cta == "Más sobre el sueño en el canal."
    assert len(cta.split()) <= 8


def test_resolve_cta_with_url_uses_url_template():
    cta = resolve_funnel_cta(FUNNEL, {"pillar": "nutrition"}, has_url=True)
    assert "la alimentación" in cta
    assert len(cta.split()) <= 8


def test_resolve_cta_falls_back_to_plain_default_when_no_topic():
    # No topic template configured -> plain default (backward compatible).
    plain = {"default_cta_without_url": "Vídeo completo en el canal."}
    assert resolve_funnel_cta(plain, {"pillar": "sleep"}, has_url=False) == "Vídeo completo en el canal."
    # Topic template present but pillar unknown -> plain default, never literal '{tema}'.
    cta = resolve_funnel_cta(FUNNEL, {"pillar": "unknown-x"}, has_url=False)
    assert "{tema}" not in cta
    assert cta == "Vídeo completo en el canal."


def test_topic_from_extra_text_when_plan_has_no_fields():
    # The real live case: plan carries no topic fields, but the parent long video
    # title does ("...aceite de oliva...").
    assert funnel_topic_es({}, extra_text="Toma 1 cucharada de aceite de oliva cada mañana") == "la alimentación"


def test_is_generic_cta():
    assert is_generic_cta(FUNNEL, "Vídeo completo en el canal.") is True
    assert is_generic_cta(FUNNEL, "Mira la guía completa aquí.") is True
    assert is_generic_cta(FUNNEL, "Más sobre el sueño en el canal.") is False


def test_script_prompt_topic_cta_beats_generic_source_map_cta():
    """The exact live failure: source_map pre-set a generic funnel.cta and the plan
    has no topic fields, but source_video_title carries the topic → topic CTA wins."""
    cfg = {"shorts": {"funnel": FUNNEL}}
    plan = {"format": "pain_to_tip"}  # no pillar, no title/viewer_pain
    source_artifacts = {
        "source_video_title": "Toma 1 cucharada de aceite de oliva cada mañana",
        "funnel": {"cta": "Vídeo completo en el canal.", "long_video_url": ""},
    }
    p = prompts.short_script_prompt(cfg, plan, source_artifacts)
    assert "Más sobre la alimentación en el canal." in p
    assert 'include this exact phrase in the CTA: "Vídeo completo en el canal."' not in p


def test_build_source_map_writes_topic_cta_from_long_title(tmp_path):
    from video_agent.shorts.source_map import build_source_map

    job = tmp_path / "toma-1-cucharada-de-aceite-de-oliva"
    (job / "json").mkdir(parents=True)
    (job / "scenes.json").write_text("{}")
    # _long_title reads the long job's seo/script title; provide a seo.json.
    (job / "seo.json").write_text('{"title": "Toma 1 cucharada de aceite de oliva cada mañana"}')
    sm = build_source_map(
        job, {"short_id": "short-01", "scene_ids": []}, {"narration": "x"},
        {"shorts": {"funnel": FUNNEL}}, long_video_url="",
    )
    assert sm["funnel"]["cta"] == "Más sobre la alimentación en el canal."


def test_script_prompt_embeds_topic_specific_cta():
    cfg = {"shorts": {"funnel": FUNNEL}}
    p = prompts.short_script_prompt(cfg, {"pillar": "sleep", "format": "pain_to_tip"})
    assert "Más sobre el sueño en el canal." in p
    # explicit funnel.cta still wins over the derived default
    p2 = prompts.short_script_prompt(
        cfg, {"pillar": "sleep", "funnel": {"cta": "Guárdalo para esta noche."}}
    )
    assert "Guárdalo para esta noche." in p2
