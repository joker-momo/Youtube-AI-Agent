from __future__ import annotations

from video_agent.shorts.builder.qa_gate import _qa_blocker_details
from video_agent.shorts.idea_preservation import _slideshow_issues as detect_slideshow_issues
from video_agent.shorts.validation.repairs import repair_slideshow_density


def _live_repro_scenes() -> list[dict]:
    return [
        {
            "id": "s06",
            "layout": "short_checklist",
            "duration_sec": 4.1,
            "narration": "Tres: observa calma, digestión y qué lo acompaña.",
            "on_screen_text": "3 SEÑALES",
            "caption": "Mira el contexto.",
            "visual_prompt": "Vertical realistic kitchen footage with a notebook.",
            "layout_payload": {
                "title": "3 SEÑALES",
                "items": ["Calma", "Digestión", "Acompaña"],
                "emphasis": "observa",
            },
        },
        {
            "id": "s07a",
            "layout": "short_tip",
            "duration_sec": 3.6,
            "narration": "Cuatro: mantén cena, ejercicio y sueño parecidos.",
            "on_screen_text": "TENDENCIAS",
            "caption": "Busca tendencias.",
            "visual_prompt": "Vertical realistic Spanish home routine footage.",
            "layout_payload": {
                "title": "TENDENCIAS",
                "items": ["Cena", "Ejercicio", "Sueño"],
                "emphasis": "no sentencias",
            },
            "covers_items": [4],
            "source_scene_ids": ["scene-81", "scene-83"],
        },
        {
            "id": "s07b",
            "layout": "short_tip",
            "duration_sec": 2.7,
            "narration": "Así verás tendencias, no sentencias.",
            "on_screen_text": "",
            "caption": "",
            "visual_prompt": "Vertical realistic Spanish home routine footage.",
        },
    ]


def _slideshow_issues(scenes: list[dict]):
    return detect_slideshow_issues(scenes, attempt=1)


def test_dense_footage_tip_is_normalized_without_dropping_content():
    scenes = _live_repro_scenes()
    issues = _slideshow_issues(scenes)
    assert issues and issues[0].scene_id == "s07a"

    before_narration = scenes[1]["narration"]
    before_coverage = list(scenes[1]["covers_items"])
    assert repair_slideshow_density(scenes, issues) is True

    repaired = next(s for s in scenes if s["id"] == "s07a")
    assert repaired["narration"] == before_narration
    assert repaired["covers_items"] == before_coverage
    assert repaired["layout"] == "short_tip"
    assert repaired.get("layout_payload") in ({}, None)
    assert not [i for i in _slideshow_issues(scenes) if i.severity == "repairable_error"]


def test_slideshow_repair_plan_is_not_reintroduced_as_hard_blocker():
    result = {
        "verdict": "FAIL",
        "issues": [
            {
                "type": "slideshow_risk",
                "scene_id": "s07a",
                "severity": "repairable_error",
                "detail": "Short is too text/list heavy.",
            }
        ],
        "required_changes": [
            "REPAIR PLAN:",
            "- Reduce s07a, the exact dense checklist/graphic scene.",
        ],
    }

    assert _qa_blocker_details(result) == []
