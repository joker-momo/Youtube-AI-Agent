from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from video_agent.shorts import paths, validate_scenes
from video_agent.storage.atomic import atomic_write_json


def write_performance_memory(
    long_job_dir: Path,
    short_id: str,
    short_plan: dict,
    short_script: dict,
    short_scenes: dict,
    retention_plan: dict,
    *,
    status: str = "scenes_ready",
    failure_stage: str | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    scenes = list((short_scenes or {}).get("scenes") or [])
    narration = str((short_script or {}).get("narration") or "")
    duration = float((short_scenes or {}).get("total_duration_sec") or sum(float(s.get("duration_sec") or 0) for s in scenes) or 0.0)
    record = {
        "short_id": short_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "topic": str(short_plan.get("title") or short_plan.get("hook_angle") or short_plan.get("format") or ""),
        "hook_pattern": str(retention_plan.get("hook_pattern") or short_plan.get("hook_pattern") or ""),
        "format": str(short_plan.get("format") or ""),
        "duration_sec": round(duration, 1),
        "script_word_count": validate_scenes.count_spoken_words(narration),
        "scene_count": len(scenes),
        "graphic_count": validate_scenes.count_graphic_scenes(scenes),
        "comment_trigger_type": str((retention_plan.get("comment_trigger") or {}).get("type") or ""),
        "analytics": {
            "ctr": None,
            "average_view_duration": None,
            "retention_0_3s": None,
            "retention_3_10s": None,
            "comments_per_1000_views": None,
            "saves_per_1000_views": None,
        },
        "status": status,
        "failure_stage": failure_stage,
        "failure_reason": failure_reason,
        "learning_notes": [],
    }
    artifact = paths.short_json_dir(long_job_dir, short_id) / paths.SHORT_PERFORMANCE_MEMORY_FILE
    artifact.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(artifact, record)
    return record
