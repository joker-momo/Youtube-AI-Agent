from __future__ import annotations

from pathlib import Path


def _job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    return job


def _scenes() -> dict:
    return {
        "short_id": "short-01",
        "total_duration_sec": 21.0,
        "scenes": [
            {"id": "s01", "layout": "short_hook", "duration_sec": 2.0, "motion": "none", "narration": "Mira el pan.", "on_screen_text": "PAN OSCURO"},
            {"id": "s02", "layout": "short_tip", "duration_sec": 4.0, "motion": "none", "narration": "Lee ingredientes.", "on_screen_text": "MIRA ETIQUETA"},
            {"id": "s03", "layout": "short_tip", "duration_sec": 4.0, "motion": "none", "narration": "Busca fibra.", "on_screen_text": "MIRA ETIQUETA"},
            {"id": "s04", "layout": "short_cta", "duration_sec": 2.4, "motion": "none", "narration": "Guárdalo.", "on_screen_text": "GUÁRDALO"},
        ],
    }


def test_visual_rhythm_detects_static_risk_and_varies_motion(tmp_path: Path):
    from video_agent.shorts.visual_rhythm import build_visual_rhythm_plan

    plan = build_visual_rhythm_plan(
        _job(tmp_path),
        "short-01",
        _scenes(),
        {"retention_beats": [{"function": "hook"}, {"function": "proof"}]},
        {"shorts": {}},
    )

    assert plan["scene_rhythm"][0]["motion"] in {"push_in", "object_reveal", "face_cut", "text_pop"}
    assert len({item["motion"] for item in plan["scene_rhythm"]}) > 1
    assert any(item["risk"] in {"static", "repeated_visual"} for item in plan["scene_rhythm"])


def test_apply_visual_rhythm_preserves_core_scene_fields():
    from video_agent.shorts.visual_rhythm import apply_visual_rhythm_to_scenes

    before = _scenes()
    updated = apply_visual_rhythm_to_scenes(before, {
        "scene_rhythm": [
            {"scene_id": "s01", "motion": "push_in", "text_animation_hint": "pop", "visual_change_reason": "hook", "risk": "static"},
            {"scene_id": "s02", "motion": "crop_shift", "text_animation_hint": "slide", "visual_change_reason": "proof", "risk": "none"},
        ],
        "pattern_interrupts": [{"at_sec": 3.0, "type": "zoom", "purpose": "reset"}],
    })

    assert updated["scenes"][0]["id"] == "s01"
    assert updated["scenes"][0]["layout"] == "short_hook"
    assert updated["scenes"][0]["narration"] == "Mira el pan."
    assert updated["scenes"][0]["on_screen_text"] == "PAN OSCURO"
    assert updated["scenes"][0]["motion"] == "push_in"
    assert updated["scenes"][0]["rhythm_tag"]
    assert updated["scenes"][0]["pattern_interrupt"] == "zoom"

