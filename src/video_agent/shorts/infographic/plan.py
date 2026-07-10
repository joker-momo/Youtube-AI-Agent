"""LLM-driven infographic poster plan (pick format + fill), validated."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from video_agent.audience_age import resolve_target_min_age
from video_agent.shorts.infographic.schema import (
    POSTER_FORMATS,
    resolve_poster_format,
    validate_poster_plan,
)

_MAX_RETRIES = 2


def _prompt(channel_config: dict, source: dict, min_age: int, feedback: str) -> str:
    topic = str(source.get("topic") or source.get("title") or "").strip()
    fmts = ", ".join(sorted(POSTER_FORMATS))
    fb = f"\nFIX THESE ISSUES FROM THE LAST ATTEMPT:\n{feedback}\n" if feedback else ""
    pinned = resolve_poster_format(str(source.get("poster_format") or ""))
    format_line = (
        f'Use poster_format "{pinned}" (the idea was conceived for this layout).\n'
        if pinned
        else f"Pick the best poster_format for this topic from: {fmts}.\n"
    )
    return (
        "Return ONE raw JSON object for a Spanish infographic-poster Short.\n"
        f"Audience: adultos {min_age}+ (Spain-first es-ES). Topic: {topic}.\n"
        + format_line +
        "Schema: {\"poster_format\": one of " + fmts + ", \"title\": <=6 words, "
        "\"subtitle\": optional short, \"hook_line\": <=40 chars scroll-stopper that names a "
        "topic word, \"items\": array of {\"label\": short, \"note\": short (warning_list: caution; "
        "myth_vs_truth: la verdad, REQUIRED), \"group\": 'bien'|'mal' (comparison only), "
        "\"time\": '7:00' style clock time (timeline_routine only, REQUIRED)}, "
        "\"score_line\": short self-score band like '4+ = vas bien' (checklist_score only), "
        "\"cta\": short}.\n"
        "Item counts: category_grid 5-7, numbered_tips 5-7, warning_list 5-6, comparison 4-6 "
        "split into 2 groups, myth_vs_truth 3-5 (label = the myth <=6 words, note = the truth), "
        "timeline_routine 3-6 in chronological order, checklist_score 5-7 (label = a self-check "
        "criterion <=4 words). No invented stats/claims/credentials. Output ONLY the JSON." + fb
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
