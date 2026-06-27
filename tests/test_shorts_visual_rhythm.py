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


def test_visual_rhythm_pool_keys_are_renderer_valid():
    """Every rotation-pool motion must be a key Remotion understands."""
    from video_agent.shorts.asset_schedule import VALID_MOTIONS
    from video_agent.shorts.visual_rhythm import _MOTIONS

    assert set(_MOTIONS) <= VALID_MOTIONS


def test_visual_rhythm_replaces_non_enum_sentence(tmp_path: Path):
    """A long descriptive motion string must be snapped to a real enum key,
    not leaked through to the renderer (which would weak-zoom)."""
    from video_agent.shorts.asset_schedule import VALID_MOTIONS
    from video_agent.shorts.visual_rhythm import build_visual_rhythm_plan

    scenes = _scenes()
    scenes["scenes"][1]["motion"] = "Camera settles into a top-down view of two plates"

    plan = build_visual_rhythm_plan(
        _job(tmp_path),
        "short-01",
        scenes,
        {"retention_beats": [{"function": "hook"}, {"function": "proof"}]},
        {"shorts": {}},
    )

    for item in plan["scene_rhythm"]:
        assert item["motion"] in VALID_MOTIONS


def test_motion_plan_clamps_descriptive_string():
    """asset_schedule._motion_plan defends the renderer contract directly."""
    from video_agent.shorts import asset_schedule
    from video_agent.shorts.asset_schedule import VALID_MOTIONS, clamp_motion

    # Long sentence -> safe fallback, never the raw sentence.
    assert clamp_motion("Quick three-beat action sequence") == "none"
    # Case / whitespace normalized; valid key preserved.
    assert clamp_motion("  Push_In  ") == "push_in"

    sentence_scene = {"motion": "Two plates slide into frame from the left"}
    plan = asset_schedule._motion_plan(asset_schedule.NATIVE_IMAGE, sentence_scene)
    assert plan["name"] in VALID_MOTIONS
    assert plan["name"] == "none"

    valid_scene = {"motion": "crop_shift"}
    assert asset_schedule._motion_plan(asset_schedule.NATIVE_IMAGE, valid_scene)["name"] == "crop_shift"

