"""Spec v6 §2.3 — Generate + persist a Short script via ChatGPT."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths, prompts
from video_agent.shorts.hook_lab import build_hook_lab
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
    retention_plan: dict | None = None,
    feedback: str = "",
    attempt: int = 1,
    write_to_disk: bool = True,
) -> dict[str, Any]:
    sa = source_artifacts or {}
    source_idea_hook = sa.get("idea", {}).get("hook_text")
    if source_idea_hook and short_plan.get("hook_text") and short_plan["hook_text"] != source_idea_hook:
        short_plan["hook_text"] = source_idea_hook
        short_plan.setdefault("planner_warnings", []).append("stale_hook_text_repaired")

    hook_lab_result = build_hook_lab(
        short_plan,
        sa,
        retention_plan or {},
        channel_config,
    )
    short_plan["hook_lab"] = hook_lab_result
    short_plan["hook_text"] = hook_lab_result.get("selected_hook") or short_plan.get("hook_text") or ""
    
    # Normalize stale hook text right before prompt rendering
    if source_idea_hook and short_plan.get("hook_text") != source_idea_hook:
        short_plan["hook_text"] = source_idea_hook
        if "stale_hook_text_repaired" not in short_plan.get("planner_warnings", []):
            short_plan.setdefault("planner_warnings", []).append("stale_hook_text_repaired")

    prompt = prompts.short_script_prompt(
        channel_config,
        short_plan,
        source_artifacts or {},
        retention_plan=retention_plan,
    )
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
    script.setdefault("source_mapped_flow", [])
    script = ensure_script_idea_fields(script, short_plan)

    if not script.get("source_mapped_flow") and script.get("idea_items"):
        script["source_mapped_flow"] = [
            {
                "item_id": item.get("item_id"),
                "source_support": item.get("source_support", []),
                "spoken_summary": f"Fallback derived for {item.get('label', '')}",
                "visual_role": item.get("spoken_or_visual_role", "narration")
            }
            for item in script["idea_items"]
        ]
        script.setdefault("planner_warnings", []).append("ChatGPT omitted source_mapped_flow; fallback generated.")

    if write_to_disk:
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
