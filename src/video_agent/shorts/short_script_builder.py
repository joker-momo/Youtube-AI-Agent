"""Generate + persist a Short script (short_script.json) from a plan."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths, prompts
from video_agent.storage.atomic import atomic_write_json


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def build_short_script(
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    llm_fn: Callable[[str, str], str],
    *,
    source_artifacts: dict | None = None,
    feedback: str = "",
) -> dict[str, Any]:
    prompt = prompts.short_script_prompt(channel_config, short_plan, source_artifacts or {})
    if feedback:
        prompt += f"\nFIX THESE QA ISSUES FROM THE PREVIOUS ATTEMPT:\n{feedback}\n"
    script = _parse(llm_fn("script", prompt))
    atomic_write_json(paths.short_dir(long_job_dir, short_plan["short_id"]) / paths.SHORT_SCRIPT_FILE, script)
    return script
