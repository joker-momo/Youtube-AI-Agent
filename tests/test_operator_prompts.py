"""Tests for Spain-first locale-aware operator prompts."""

from __future__ import annotations

from video_agent.operator import (
    _chatgpt_scenes_batch_prompt,
    _chatgpt_scenes_plan_prompt,
    _chatgpt_scenes_prompt,
    _chatgpt_script_prompt,
    _chatgpt_seo_prompt,
    _gemini_qa_prompt,
    _locale_guidance,
)
from video_agent.orchestrator.briefing import build_task_prompt


SPAIN_CONFIG = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+", "description": "Test"},
    "audience": {"language": "es-ES", "age_range": [45, 75], "primary_markets": ["ES"]},
    "seo": {"language": "es-ES", "min_tags": 5, "max_tags": 8},
    "content_format": {
        "target_duration_sec": 840,
        "scenes_count_min": 40,
        "scenes_count_max": 55,
    },
    "positioning": {
        "forbidden_phrases": ["adultos mayores", "tercera edad"],
        "preferred_phrases": ["personas de más de 45 años", "adultos 45+"],
    },
    "locale_style": {
        "target_locale": "Spain",
        "language_code": "es-ES",
        "lexical_preferences": {
            "prefer": ["móvil", "ordenador", "por la tarde", "de madrugada"],
            "avoid": ["celular", "computadora", "adultos mayores"],
        },
    },
    "tts": {"pace_wpm": 145},
}


VALID_SCRIPT = {
    "channel_id": "vida-plena-45",
    "job_id": "j-1",
    "hook": "hook",
    "sections": [],
    "narration": "n",
    "cta": "cta",
}

VALID_SCENES = {
    "total_duration_sec": 540,
    "scenes": [{"visual_prompt": "evening home table"}],
}


def test_locale_guidance_resolves_spain_first_config():
    locale = _locale_guidance(SPAIN_CONFIG)
    assert locale["language"] == "es-ES"
    assert locale["target_locale"] == "Spain"
    assert "móvil" in locale["prefer"]
    assert "celular" in locale["avoid"]


def test_locale_guidance_falls_back_to_default_when_config_empty():
    locale = _locale_guidance({})
    assert locale["language"] == "es-ES"
    assert locale["target_locale"] == "Spain"


def test_locale_guidance_keeps_non_spain_spanish_language_dynamic():
    locale = _locale_guidance({"audience": {"language": "es-MX"}})
    assert locale["language"] == "es-MX"
    assert locale["target_locale"] == "Latin America"


def test_seo_prompt_uses_dynamic_language():
    prompt = _chatgpt_seo_prompt(SPAIN_CONFIG, VALID_SCRIPT, VALID_SCENES)
    assert "language: must be es-ES" in prompt
    assert "Spain-first Spanish" in prompt
    assert "móvil" in prompt
    assert "ordenador" in prompt
    # Old hard-coded text must be gone
    assert "language: must be es-MX" not in prompt
    assert "Spanish/LatAm wellness search terms" not in prompt
    assert "and other social links" not in prompt


def test_seo_prompt_forbids_placeholder_social_text():
    prompt = _chatgpt_seo_prompt(SPAIN_CONFIG, VALID_SCRIPT, VALID_SCENES)
    assert "Redes adicionales: no proporcionadas" in prompt
    assert "Never mention missing resources" in prompt


def test_seo_prompt_timestamp_format_is_one_per_line():
    prompt = _chatgpt_seo_prompt(SPAIN_CONFIG, VALID_SCRIPT, VALID_SCENES)
    assert "one timestamp per line" in prompt
    assert "MM:SS - Section title" in prompt


def test_seo_prompt_chapters_enforce_youtube_auto_chapter_rules():
    """Chapters must follow YouTube's auto-chapter contract so they actually
    render: first at 00:00, >=3, ascending, and specific (not vague) labels."""
    prompt = _chatgpt_seo_prompt(SPAIN_CONFIG, VALID_SCRIPT, VALID_SCENES)
    assert "00:00" in prompt
    assert "AT LEAST 3 chapters" in prompt
    assert "ascending time order" in prompt
    assert "SHORT and SPECIFIC" in prompt


def test_script_prompt_forbids_content_repetition_and_enforces_pacing():
    """Coach feedback: videos read as too long / repetitive / slow. The script
    prompt must ban re-explaining ideas and demand forward pacing."""
    prompt = _chatgpt_script_prompt(SPAIN_CONFIG, {"topic": "dormir mejor"})
    assert "NO IDEA TWICE" in prompt
    assert "EXPAND WITH DEPTH, NOT REPETITION" in prompt
    assert "FORWARD MOMENTUM" in prompt


def test_seo_prompt_thumbnail_text_uses_competitor_devices():
    """Competitor thumbnail study (2026-07-06): imperative+age, negation command,
    and count devices beat pure emotional hooks in the ES 45+/60+ niche."""
    prompt = _chatgpt_seo_prompt(SPAIN_CONFIG, VALID_SCRIPT, VALID_SCENES)
    assert "3-7 words" in prompt
    assert "IMPERATIVE + AGE" in prompt
    assert "NEGATION COMMAND" in prompt
    assert "COUNT:" in prompt
    assert "HAZ ESTO DESPUÉS DE LOS 50" in prompt
    assert "use DIFFERENT devices" in prompt
    assert "3-5 words ALL-CAPS Spanish emotional hook" not in prompt


def test_script_prompt_varies_mechanism_opener():
    """The mechanism rule must not funnel every section into 'Esto ocurre porque'."""
    prompt = _chatgpt_script_prompt(SPAIN_CONFIG, {"topic": "dormir mejor"})
    assert "VARY THE OPENER" in prompt
    assert "more than ONE section's explanation with 'Esto ocurre porque" in prompt


def test_script_prompt_contains_spain_locale_guidance():
    prompt = _chatgpt_script_prompt(SPAIN_CONFIG, {"topic": "dormir mejor"})
    assert "Spanish for Spain" in prompt
    assert "es-ES" in prompt
    assert "móvil" in prompt
    assert "ordenador" in prompt


def test_script_prompt_uses_filtered_script_context_not_full_channel_config():
    cfg = {
        **SPAIN_CONFIG,
        "style_dna": {"path": "configs/vida-plena-45/style-dna.json"},
        "visuals": {"visual_dna_path": "configs/vida-plena-45/visual-dna.yaml"},
        "thumbnail": {"persona_reference": "configs/vida-plena-45/persona/thumbnail_face.jpeg"},
        "personas": [{"id": "maria", "profile_path": "configs/vida-plena-45/personas/maria.md"}],
        "render": {"concurrency": "auto", "fps": 30},
        "branding": {"intro_video_path": "asset_library/source/intro.mp4"},
        "niche": {
            "category": "health_wellness",
            "sub_niches": ["nutrition_45plus", "sleep_quality"],
            "avoid_topics": ["miracle_cures"],
        },
    }

    prompt = _chatgpt_script_prompt(cfg, {"topic": "dormir mejor"})

    assert "Script context (filtered from channel config" in prompt
    assert "Channel config:" not in prompt
    assert "style_dna" not in prompt
    assert "style-dna" not in prompt
    assert "visual_dna" not in prompt
    assert "visual-dna" not in prompt
    assert "thumbnail_face" not in prompt
    assert "persona_reference" not in prompt
    assert "profile_path" not in prompt
    assert '"render"' not in prompt
    assert '"concurrency"' not in prompt
    assert "intro_video_path" not in prompt
    assert "Vida Plena 45+" in prompt
    assert "nutrition_45plus" in prompt
    assert "duration_sec_min" in prompt
    assert "pace_wpm" in prompt


def test_scenes_prompt_visual_context_not_hardcoded_to_sleep():
    # The visual-context guidance must not force a sleep-only setting on every
    # topic (it used to hardcode "sleep-wellness context: bedroom...").
    prompt = _chatgpt_scenes_prompt(SPAIN_CONFIG, VALID_SCRIPT)
    assert "sleep-wellness context" not in prompt
    assert "¿Por qué no puedes dormir?" not in prompt
    assert "derive" in prompt.lower()
    assert "choose a calm bedroom only when the narration is about sleep" in prompt


def test_scenes_prompt_visual_prompt_avoids_reusable_sofa_tea_template():
    prompt = _chatgpt_scenes_prompt(SPAIN_CONFIG, VALID_SCRIPT)
    assert "Mature woman in her 50s [action from narration]" not in prompt
    assert "Mature adult woman drinking herbal tea on a sofa" not in prompt
    assert "Do NOT reuse generic wellness filler" in prompt
    assert "visual signature" in prompt


def test_scenes_prompt_does_not_default_nutrition_to_food_cards():
    prompt = _chatgpt_scenes_prompt(SPAIN_CONFIG, VALID_SCRIPT)

    assert "Use layout=\"plate_map\" ONLY when" in prompt
    assert "Use layout=\"recipe_snapshot\" ONLY when" in prompt
    assert "not a generic nutrition checklist" in prompt
    assert "Prefer stat, steps, comparison, myth, or do_dont" in prompt


def test_scenes_batch_prompt_does_not_default_nutrition_to_food_cards():
    plan = {
        "data": {
            "batches": [
                {"batch_index": 1, "scene_start": "scene-01", "scene_end": "scene-06"}
            ]
        }
    }
    prompt = _chatgpt_scenes_batch_prompt(
        SPAIN_CONFIG,
        VALID_SCRIPT,
        plan,
        {"batch_index": 1, "scene_start": "scene-01", "scene_end": "scene-06"},
    )

    assert "Use layout=\"plate_map\" ONLY when" in prompt
    assert "Use layout=\"recipe_snapshot\" ONLY when" in prompt
    assert "not a generic nutrition checklist" in prompt
    assert "Prefer stat, steps, comparison, myth, or do_dont" in prompt


def test_scenes_prompt_visual_context_reflects_niche_category():
    cfg = {**SPAIN_CONFIG, "niche": {"category": "nutrition_45plus"}}
    prompt = _chatgpt_scenes_prompt(cfg, VALID_SCRIPT)
    assert "nutrition 45plus" in prompt  # underscores normalized to spaces


def test_scenes_prompt_visual_context_override_wins():
    cfg = {**SPAIN_CONFIG, "niche": {"visual_context": "bright kitchens with real Spanish food"}}
    prompt = _chatgpt_scenes_prompt(cfg, VALID_SCRIPT)
    assert "bright kitchens with real Spanish food" in prompt


def test_scenes_prompt_contains_locale_and_keeps_visual_prompt_english():
    prompt = _chatgpt_scenes_prompt(SPAIN_CONFIG, VALID_SCRIPT)
    assert "es-ES" in prompt
    assert "móvil" in prompt
    assert "ordenador" in prompt
    assert "visual_prompt: English" in prompt or "visual_prompt must remain English" in prompt


def test_scenes_plan_prompt_contains_locale_rules():
    prompt = _chatgpt_scenes_plan_prompt(SPAIN_CONFIG, VALID_SCRIPT)
    assert "Locale rules" in prompt
    assert "es-ES" in prompt


def test_qa_prompt_with_channel_config_contains_locale_qa():
    prompt = _gemini_qa_prompt("seo", {"language": "es-ES"}, SPAIN_CONFIG)
    assert "expected language is es-ES" in prompt
    assert "not EXACTLY the expected language, verdict MUST be NEEDS_REWORK" in prompt
    assert "forbidden age-positioning" in prompt
    assert "no proporcionadas" in prompt


def test_seo_task_briefing_uses_channel_language_contract():
    prompt = build_task_prompt(
        "seo",
        "Write SEO JSON.",
        channel_config=SPAIN_CONFIG,
    )

    assert '"language": "es-ES"' in prompt
    assert "Confirma language=es-ES" in prompt
    assert "Idioma es-ES con acentos correctos" in prompt
    assert '"language": "es-MX"' not in prompt
    assert "Confirma language=es-MX" not in prompt


def test_qa_prompt_backward_compatible_without_channel_config():
    # Default channel_config=None must still build a prompt with safe defaults.
    prompt = _gemini_qa_prompt("seo", {"language": "es-ES"})
    assert "expected language is es-ES" in prompt


def test_seo_prompt_for_non_spain_spanish_channel_keeps_language_dynamic():
    cfg = dict(SPAIN_CONFIG)
    cfg["seo"] = {"language": "es-MX", "min_tags": 5, "max_tags": 8}
    cfg["audience"] = {"language": "es-MX", "age_range": [45, 75], "primary_markets": ["MX"]}
    cfg.pop("locale_style", None)
    prompt = _chatgpt_seo_prompt(cfg, VALID_SCRIPT, VALID_SCENES)
    assert "language: must be es-MX" in prompt
    # Must NOT label the output as Spain-first when channel is LatAm.
    assert "Spain-first Spanish wellness" not in prompt


def test_seo_prompt_forces_age_device_once_per_set():
    prompt = _chatgpt_seo_prompt(SPAIN_CONFIG, VALID_SCRIPT, VALID_SCENES)
    assert "MANDATORY: at least ONE of the 3 variants uses device 1" in prompt
    assert "never two negations" in prompt
