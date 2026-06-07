"""Spec v6 §2.3 — Generate + persist a Short script via ChatGPT."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths, prompts
from video_agent.shorts.idea_preservation import ensure_script_idea_fields
from video_agent.shorts.llm import LLMCallLog, log_llm_call
from video_agent.storage.atomic import atomic_write_json

PROVIDER = "chatgpt"


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def build_short_script(
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    llm_fn: Callable[..., str],
    *,
    source_artifacts: dict | None = None,
    feedback: str = "",
    attempt: int = 1,
) -> dict[str, Any]:
    prompt = prompts.short_script_prompt(channel_config, short_plan, source_artifacts or {})
    if feedback:
        prompt += f"\nFIX THESE QA ISSUES FROM THE PREVIOUS ATTEMPT:\n{feedback}\n"
    log_llm_call(LLMCallLog(
        task="short_script_builder", provider=PROVIDER,
        short_id=short_plan.get("short_id", "-"), attempt=attempt,
        input_artifacts=["shorts_plan.json"],
        output_artifact="short_script.json",
    ))
    raw = _invoke(llm_fn, "script", prompt)
    script = _parse(raw)
    script = ensure_script_idea_fields(script, short_plan)
    jd = paths.short_json_dir(long_job_dir, short_plan["short_id"])
    jd.mkdir(parents=True, exist_ok=True)
    atomic_write_json(jd / paths.SHORT_SCRIPT_FILE, script)
    return script


def _invoke(llm_fn: Callable[..., str], kind: str, prompt: str) -> str:
    """Accept both ``llm_fn(prompt)`` and the legacy ``llm_fn(kind, prompt)``."""
    try:
        return llm_fn(prompt)
    except TypeError:
        return llm_fn(kind, prompt)
