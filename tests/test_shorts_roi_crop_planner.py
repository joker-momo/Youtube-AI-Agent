from __future__ import annotations


def test_plan_crop_for_evidence_closeup_uses_object_dominant_scale():
    from video_agent.shorts.roi_crop_planner import plan_crop

    scene = {
        "id": "s01",
        "first_frame_plan": {"strategy": "evidence_closeup", "roi_target": "ingredient label"},
    }

    crop = plan_crop(scene, scene_index=0)

    assert crop["mode"] == "object_dominant"
    assert crop["target"] == "ingredient label"
    assert crop["scale"] >= 1.12
    assert crop["safe_area"] == "mobile_9_16"


def test_plan_crop_fallback_does_not_require_vision():
    from video_agent.shorts.roi_crop_planner import plan_crop

    crop = plan_crop({"id": "s02", "first_frame_plan": {"roi_target": "face reaction"}}, scene_index=1)

    assert crop["mode"] == "center"
    assert 1.04 <= crop["scale"] <= 1.12
    assert "target_box" not in crop
