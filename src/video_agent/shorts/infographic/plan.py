"""LLM-driven infographic poster plan (pick format + fill), validated."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from video_agent.audience_age import resolve_target_min_age
from video_agent.shorts.infographic.schema import POSTER_FORMATS, validate_poster_plan

_MAX_RETRIES = 2


def _prompt(channel_config: dict, source: dict, min_age: int, feedback: str) -> str:
    topic = str(source.get("topic") or source.get("title") or "").strip()
    fmts = ", ".join(sorted(POSTER_FORMATS))
    fb = f"\nFIX THESE ISSUES FROM THE LAST ATTEMPT:\n{feedback}\n" if feedback else ""
    return (
        "Return ONE raw JSON object for a Spanish infographic-poster Short.\n"
        f"Audience: adultos {min_age}+ (Spain-first es-ES). Topic: {topic}.\n"
        f"Pick the best poster_format for this topic from: {fmts}.\n"
        "Schema: {\"poster_format\": one of the above, \"title\": <=6 words, "
        "\"subtitle\": optional short, \"hook_line\": <=40 chars scroll-stopper that names a "
        "topic word, \"items\": array of {\"label\": 1-3 words, \"note\": short (warning_list only), "
        "\"group\": 'bien'|'mal' (comparison only)}, \"cta\": short}. Item counts: category_grid 5-7, "
        "numbered_tips 5-7, warning_list 5-6, comparison 4-6 split into 2 groups. No invented stats/"
        "claims/credentials. Output ONLY the JSON." + fb
    )


def build_poster_plan(channel_config: dict, source: dict, llm_fn: Callable[..., str]) -> dict[str, Any]:
    min_age = resolve_target_min_age(
        channel_config,
        str(source.get("topic") or ""),
        str(source.get("title") or ""),
        str(source.get("narration") or "")[:400],
    )
    feedback = ""
    last_issues: list[str] = []
    for _attempt in range(_MAX_RETRIES + 1):
        raw = llm_fn(_prompt(channel_config, source, min_age, feedback))
        try:
            plan = _parse_json(raw)
        except ValueError:
            plan = {}
        plan["short_type"] = "infographic"
        plan.setdefault("audience_min_age", min_age)
        last_issues = validate_poster_plan(plan)
        if not last_issues:
            return plan
        feedback = "\n".join(f"- {i}" for i in last_issues)
    raise ValueError(f"poster_plan invalid after {_MAX_RETRIES} retries: {last_issues}")


def _parse_json(raw: str) -> dict[str, Any]:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    if not objs:
        raise ValueError("no JSON object in LLM output")
    return objs[0]
