"""Regression guard for long-form composition duration (B1).

The compiled asset schedule's ``total_duration_in_frames`` is the SCENE-LAYER
frame count only (0-based). In ``ChannelVideo.tsx`` the scene layer is shifted by
``introFrames`` and an outro is appended, so the real composition length is
``introFrames + totalSceneFrames + outroFrames``. When span planning is enforced,
Root.tsx must NOT size the composition to the scenes-only schedule total (that cuts
the intro shift + the entire outro). The long render_props therefore carries an
explicit ``render.duration_in_frames`` computed here; Root prefers it.

Frame rounding matches the renderer (JS ``Math.round`` == ``floor(x + 0.5)``).
"""

from __future__ import annotations

from video_agent.pipeline import _comp_duration_in_frames


def test_includes_intro_and_outro():
    scenes = [{"duration_sec": 2.0}, {"duration_sec": 3.0}]  # 60 + 90 = 150 @30fps
    # intro 2.0s -> 60, outro 2.0s -> 60
    assert _comp_duration_in_frames(scenes, intro_sec=2.0, outro_sec=2.0, fps=30) == 270


def test_includes_medical_disclaimer_between_intro_and_scenes():
    scenes = [{"duration_sec": 2.0}, {"duration_sec": 3.0}]
    assert _comp_duration_in_frames(
        scenes,
        intro_sec=2.0,
        disclaimer_sec=8.0,
        outro_sec=2.0,
        fps=30,
    ) == 510


def test_no_branding_is_scene_frames_only():
    scenes = [{"duration_sec": 2.0}, {"duration_sec": 3.0}]
    assert _comp_duration_in_frames(scenes, intro_sec=0.0, outro_sec=0.0, fps=30) == 150


def test_per_scene_rounding_matches_js_round():
    # 1.017s @30 -> round(30.51) = 31 frames per scene (floor(x+0.5)), x2 = 62.
    scenes = [{"duration_sec": 1.017}, {"duration_sec": 1.017}]
    assert _comp_duration_in_frames(scenes, intro_sec=0.0, outro_sec=0.0, fps=30) == 62
