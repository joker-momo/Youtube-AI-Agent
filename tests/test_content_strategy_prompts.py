from __future__ import annotations

from video_agent.operator import _chatgpt_script_prompt, _chatgpt_seo_prompt
from video_agent.orchestrator.stages import _build_thumbnail_prompt


def _channel_config() -> dict:
    return {
        "channel": {
            "id": "vida-plena-45",
            "name": "Vida Plena 45+",
            "description": "Bienestar para adultos 45+",
        },
        "content_format": {"target_duration_sec": 840},
        "tts": {"pace_wpm": 145},
    }


def test_script_prompt_demands_specific_pain_hook_after_45():
    prompt = _chatgpt_script_prompt(
        _channel_config(),
        {"topic": "alimentación saludable después de los 45", "job_id": "job-1"},
    )

    assert "Do NOT open with generic teaching phrases" in prompt
    assert "En este video aprenderás" in prompt
    assert "specific pain after 45" in prompt
    assert "fuerza de voluntad" in prompt


def test_script_prompt_requires_actionable_specific_advice():
    prompt = _chatgpt_script_prompt(
        _channel_config(),
        {"topic": "plato saludable después de los 45", "job_id": "job-1"},
    )

    assert "1/2 plato verduras" in prompt
    assert "1/4 proteína" in prompt
    assert "1/4 carbohidrato" in prompt
    assert "evita picar por ansiedad" in prompt
    assert "Do not leave advice as generic slogans" in prompt


def test_script_prompt_uses_narrative_format_without_limiting_topics():
    prompt = _chatgpt_script_prompt(
        _channel_config(),
        {"topic": "rutina simple después de los 45", "job_id": "job-1"},
    )

    assert "core narrative format" in prompt
    assert "pain after 45 -> common misunderstanding -> simple explanation -> 3-5 practical steps -> relief close" in prompt
    assert "This is a story framework, not a topic restriction" in prompt
    for pillar in [
        "sleep",
        "nutrition",
        "movement",
        "menopause",
        "stress",
        "energy",
        "weight",
        "digestion",
        "daily habits",
    ]:
        assert pillar in prompt


def test_script_prompt_demands_distinct_angle_per_video():
    prompt = _chatgpt_script_prompt(
        _channel_config(),
        {"topic": "cena después de los 45", "job_id": "job-1"},
    )

    assert "Choose ONE distinct angle for this video" in prompt
    assert "do not reuse the same pain, misunderstanding, and steps" in prompt
    assert "cena ligera" in prompt
    assert "despertar cansada" in prompt
    assert "hambre aunque ya comiste" in prompt
    assert "rodillas" in prompt
    assert "metabolismo cambió" in prompt


def test_seo_prompt_aligns_title_and_thumbnail_to_same_pain():
    prompt = _chatgpt_seo_prompt(
        _channel_config(),
        {"hook": "Si tu plato saludable te deja sin energía...", "narration": "texto"},
        {"scenes": []},
    )

    assert "same specific pain angle" in prompt
    assert "TU PLATO TE HABLA" in prompt
    assert "quitando energía después de los 45" in prompt
    assert "If thumbnail_text points to one pain" in prompt


def test_thumbnail_prompt_requires_visuals_to_match_title_pain_angle():
    prompt = _build_thumbnail_prompt(
        "Cómo saber si tu plato te está quitando energía después de los 45",
        "TU PLATO TE HABLA",
        "#F2C94C",
        "Bienestar 45+",
    )

    # v1.3 planner uses topic-category classification; pain-angle binding is
    # enforced via Visual category + Main prop derived from title/topic.
    assert "food choice" in prompt  # topic classified as food category
    assert "Mediterranean plate" in prompt or "plato" in prompt.lower() or "food" in prompt
    assert "lifestyle-oriented" in prompt  # safety tone replaces old genre warning
    assert "TU PLATO TE HABLA" in prompt  # hook text rendered verbatim
