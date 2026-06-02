"""Build ``short_seo.json`` for a Short (LLM-generated, parsed + normalized)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths, prompts
from video_agent.storage.atomic import atomic_write_json


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _invoke(llm_fn: Callable[..., str], kind: str, prompt: str) -> str:
    try:
        return llm_fn(prompt)
    except TypeError:
        return llm_fn(kind, prompt)


def build_short_seo(
    long_job_dir: Path,
    short_id: str,
    short_plan: dict,
    short_script: dict,
    channel_config: dict,
    llm_fn: Callable[..., str],
    long_video_url: str = "",
) -> dict[str, Any]:
    prompt = prompts.short_seo_prompt(channel_config, short_plan, short_script, long_video_url)
    parsed = _parse(_invoke(llm_fn, "seo", prompt))
    funnel = (channel_config.get("shorts") or {}).get("funnel") or {}
    pinned_template = funnel.get("pinned_comment_template", "")
    pinned = parsed.get("pinned_comment") or (
        pinned_template.replace("{long_video_url}", long_video_url) if pinned_template else ""
    )
    seo = {
        "short_id": short_id,
        "title": parsed.get("title") or short_script.get("hook", "")[:60],
        "description": parsed.get("description", ""),
        "hashtags": parsed.get("hashtags") or ["#shorts", "#bienestar45"],
        "pinned_comment": pinned,
        "long_video_url": long_video_url,
        "language": "es-ES",
        "ai_disclosure": True,
    }
    atomic_write_json(paths.short_dir(long_job_dir, short_id) / paths.SHORT_SEO_FILE, seo)
    return seo
