"""Unit coverage for the deterministic local pre-QA gate (stages/local_qa.py).

The gate must catch mechanical failures (language contract, YouTube limits,
Spanish visual_prompts) with zero false positives on clean artifacts, so a
Gemini round-trip is only spent on editorial review.
"""
from __future__ import annotations

from video_agent.orchestrator.stages.local_qa import (
    YOUTUBE_DESCRIPTION_MAX,
    YOUTUBE_TAGS_TOTAL_MAX,
    YOUTUBE_TITLE_MAX,
    local_artifact_issues,
    scenes_issues,
    seo_issues,
)

_CHANNEL = {"seo": {"language": "es-ES"}}


def _clean_seo() -> dict:
    return {
        "title": "5 hábitos nocturnos para dormir mejor después de los 45",
        "description": "Rutina suave y realista para descansar mejor.",
        "tags": ["dormir mejor", "rutina nocturna", "bienestar 45"],
        "language": "es-ES",
        "ai_disclosure": True,
    }


def _clean_scene(idx: int = 1) -> dict:
    return {
        "id": f"scene-{idx:02d}",
        "duration_sec": 8,
        "narration": "El desayuno con proteína ayuda a mantener el músculo.",
        "on_screen_text": "Proteína en el desayuno",
        "caption": "Desayuno con proteína",
        "visual_prompt": "senior woman eating eggs and yogurt at a bright kitchen table",
        "motion": "slow pan",
        "asset_refs": [],
    }


def test_clean_seo_produces_no_issues():
    assert seo_issues(_clean_seo(), _CHANNEL) == []


def test_seo_language_mismatch_flagged():
    payload = dict(_clean_seo(), language="es-MX")
    issues = seo_issues(payload, _CHANNEL)
    assert any("es-ES" in i and "es-MX" in i for i in issues)


def test_seo_title_over_youtube_limit_flagged():
    payload = dict(_clean_seo(), title="x" * (YOUTUBE_TITLE_MAX + 1))
    assert any(str(YOUTUBE_TITLE_MAX) in i for i in seo_issues(payload, _CHANNEL))


def test_seo_description_over_limit_flagged():
    payload = dict(_clean_seo(), description="y" * (YOUTUBE_DESCRIPTION_MAX + 1))
    assert any(str(YOUTUBE_DESCRIPTION_MAX) in i for i in seo_issues(payload, _CHANNEL))


def test_seo_tags_total_over_limit_and_duplicates_flagged():
    payload = dict(_clean_seo(), tags=["a" * 300, "b" * 300])
    assert any(str(YOUTUBE_TAGS_TOTAL_MAX) in i for i in seo_issues(payload, _CHANNEL))
    payload = dict(_clean_seo(), tags=["dormir", "Dormir"])
    assert any("duplicate" in i.lower() for i in seo_issues(payload, _CHANNEL))


def test_clean_scenes_produce_no_issues():
    payload = {"scenes": [_clean_scene(1), _clean_scene(2)]}
    assert scenes_issues(payload) == []


def test_spanish_visual_prompt_flagged():
    bad = _clean_scene(1)
    bad["visual_prompt"] = "una mujer mayor desayunando huevos en la cocina"
    issues = scenes_issues({"scenes": [bad]})
    assert any("Spanish" in i or "English" in i for i in issues)


def test_scene_missing_fields_and_bad_duration_flagged():
    broken = _clean_scene(1)
    del broken["caption"]
    broken["duration_sec"] = 0
    issues = scenes_issues({"scenes": [broken]})
    assert any("missing fields" in i for i in issues)
    assert any("duration_sec" in i for i in issues)


def test_dispatch_unknown_artifact_returns_empty():
    assert local_artifact_issues("thumbnail", {}, _CHANNEL) == []
