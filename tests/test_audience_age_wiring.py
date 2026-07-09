"""A 60+ idea must make the content builders speak to 60, not hardcoded 45.

Guards the wiring of ``audience_age`` into the long-form content prompt builders
so a video whose idea targets "más de 60 años" renders 60+ audience/subject
cues, while a generic idea keeps the channel's 45 floor.
"""

from __future__ import annotations

from video_agent.operator_prompts import (
    _chatgpt_scenes_prompt,
    _chatgpt_script_prompt,
    _chatgpt_seo_prompt,
)

CHANNEL = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "audience": {"language": "es-ES", "age_range": [45, 75]},
    "content_format": {"duration_sec_min": 660, "target_duration_sec": 840,
                       "scenes_count_min": 40, "scenes_count_max": 55},
    "tts": {"pace_wpm": 120},
    "niche": {"category": "health and wellness"},
    "locale_style": {"target_locale": "Spain", "language_code": "es-ES"},
    "seo": {"language": "es-ES"},
}

IDEA_60 = {
    "topic": "carencias nutricionales después de los 60",
    "title_seed": "SI TIENES MAS DE 60 AÑOS ESTOS ALIMENTOS SON IMPRESCINDIBLES",
    "target_keyword": "alimentos imprescindibles después de los 60",
}
IDEA_GENERIC = {
    "topic": "cómo disfrutar los partidos nocturnos del mundial",
    "title_seed": "Disfruta el mundial sin arruinar tu descanso",
}
SCRIPT_60 = {"channel_id": "vida-plena-45", "job_id": "j1", "title": "Alimentos después de los 60",
             "hook": "Si tienes más de 60 años...", "narration": "Después de los 60 el cuerpo cambia.",
             "sections": [], "cta": "cta"}
SCRIPT_GENERIC = {"channel_id": "vida-plena-45", "job_id": "j1", "title": "Disfruta el mundial",
                  "hook": "El mundial ya está aquí", "narration": "Los partidos nocturnos son tarde.",
                  "sections": [], "cta": "cta"}


def test_script_prompt_targets_idea_age():
    p60 = _chatgpt_script_prompt(CHANNEL, IDEA_60)
    assert "adults 60+" in p60
    assert "adults 45+" not in p60


def test_script_prompt_generic_idea_keeps_channel_floor():
    pg = _chatgpt_script_prompt(CHANNEL, IDEA_GENERIC)
    assert "adults 45+" in pg
    assert "adults 60+" not in pg


def test_scenes_prompt_targets_script_age():
    p60 = _chatgpt_scenes_prompt(CHANNEL, SCRIPT_60)
    assert "adults 60+" in p60
    assert "adults 45+" not in p60
    pg = _chatgpt_scenes_prompt(CHANNEL, SCRIPT_GENERIC)
    assert "adults 45+" in pg


def test_seo_prompt_targets_script_age():
    p60 = _chatgpt_seo_prompt(CHANNEL, SCRIPT_60, {"scenes": []})
    assert "adultos 60+" in p60
    assert "adultos 45+" not in p60


def test_graphic_prefix_tracks_age():
    from video_agent.orchestrator.stages.graphic_images import _prompt_prefix

    assert "adults 60+" in _prompt_prefix(60)
    assert "adults 45+" in _prompt_prefix(45)


def test_scene_asset_prefix_tracks_age():
    from video_agent.orchestrator.stages.assets_thumbnail import _asset_gen_prefix

    assert "adultos 60+" in _asset_gen_prefix(60)
    assert "adultos 45+" in _asset_gen_prefix(45)
