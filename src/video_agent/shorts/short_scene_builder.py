"""Generate + persist Short scenes (short_scenes.json) from a Short script."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths, prompts
from video_agent.storage.atomic import atomic_write_json


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def build_short_scenes(
    long_job_dir: Path,
    short_plan: dict,
    short_script: dict,
    channel_config: dict,
    llm_fn: Callable[[str, str], str],
) -> dict[str, Any]:
    prompt = prompts.short_scene_prompt(channel_config, short_plan, short_script)
    scenes = _parse(llm_fn("scenes", prompt))
    atomic_write_json(paths.short_dir(long_job_dir, short_plan["short_id"]) / paths.SHORT_SCENES_FILE, scenes)
    return scenes
