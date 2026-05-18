from __future__ import annotations

from typing import Any

from video_agent.qa.common import pass_result, revise_result

REQUIRED_SCENE_FIELDS = ("id", "duration_sec", "narration", "visual_prompt", "on_screen_text", "caption", "motion")


def check_scenes(scene_doc: dict[str, Any], channel_config: dict[str, Any]) -> dict[str, Any]:
    scenes = scene_doc.get("scenes", [])
    if not scenes:
        return revise_result("NO_SCENES", "At least one scene is required.", "regenerate_scenes")
    for scene in scenes:
        missing = [field for field in REQUIRED_SCENE_FIELDS if not scene.get(field)]
        if missing:
            return revise_result("SCENE_FIELD_MISSING", f"{scene.get('id', 'scene')} is missing {missing}.", "repair_scene_fields")
        if not 7 <= int(scene["duration_sec"]) <= 14:
            return revise_result("SCENE_DURATION_RANGE", f"{scene['id']} duration must be 7-14 seconds.", "rebalance_scene_duration")
    total = sum(int(scene["duration_sec"]) for scene in scenes)
    if not 45 <= total <= 60:
        return revise_result("TOTAL_DURATION_RANGE", f"Total duration {total}s must be 45-60s.", "rebalance_timeline")
    return pass_result({"scene_count": len(scenes), "total_duration_sec": total})
