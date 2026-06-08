"""Spec v4 §4.3 — tighter CTA/tip scene-duration targets."""
from __future__ import annotations


def _issues(scene):
    from video_agent.shorts import validate_scenes
    return validate_scenes.validate_scene_structure([scene])


def test_short_tip_over_hard_max_45_triggers_cap():
    scene = {"id": "s01", "layout": "short_tip", "duration_sec": 4.6,
             "narration": "Mira.", "on_screen_text": "ETIQUETA"}
    types = {i.type for i in _issues(scene)}
    assert "duration_cap" in types, types


def test_short_tip_at_45_is_allowed():
    scene = {"id": "s01", "layout": "short_tip", "duration_sec": 4.5,
             "narration": "Mira.", "on_screen_text": "ETIQUETA"}
    types = {i.type for i in _issues(scene)}
    assert "duration_cap" not in types, types


def test_short_cta_roomy_27_gives_pacing_warning_not_cap():
    scene = {"id": "s01", "layout": "short_cta", "duration_sec": 2.7,
             "narration": "Comenta.", "on_screen_text": "TU TOSTADA"}
    issues = _issues(scene)
    types = {i.type for i in issues}
    assert "duration_pacing" in types, types  # outside tightened 2.0-2.6 ideal
    assert "duration_cap" not in types        # still under 2.8 hard max


def test_targets_match_spec_v4():
    from video_agent.shorts.validate_scenes import LAYOUT_DURATION_TARGETS
    assert LAYOUT_DURATION_TARGETS["short_cta"] == (2.0, 2.6, 2.8)
    assert LAYOUT_DURATION_TARGETS["short_tip"] == (3.2, 4.0, 4.5)
