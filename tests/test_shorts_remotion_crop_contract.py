from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_render_props_and_short_background_support_crop_plan():
    render_props = (REPO / "remotion/src/render-props.ts").read_text(encoding="utf-8")
    short_bg = (REPO / "remotion/src/shorts/ShortBackground.tsx").read_text(encoding="utf-8")
    short_video = (REPO / "remotion/src/ShortVideo.tsx").read_text(encoding="utf-8")

    assert "crop_plan?" in render_props
    assert "first_frame_plan?" in render_props
    assert "cropPlan" in short_bg
    assert "cropTransform" in short_bg
    assert "motionTransform" in short_bg
    assert "motion_profile" not in short_bg
    assert "cropPlan={scene.crop_plan}" in short_video
