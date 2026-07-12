"""Poster-plan schema + validation for infographic shorts."""
from __future__ import annotations

import re
from typing import Any

# poster_format -> (min_items, max_items)
POSTER_FORMATS: dict[str, tuple[int, int]] = {
    "category_grid": (5, 7),
    "numbered_tips": (5, 7),
    "warning_list": (5, 6),
    "comparison": (4, 6),
    "myth_vs_truth": (3, 5),
    "timeline_routine": (3, 6),
    "checklist_score": (5, 7),
}

# Legacy narrated-era idea formats -> poster formats. Stored ideas (and the
# idea prompt's older outputs) keep working; unmapped values return "" so the
# poster-plan LLM falls back to picking a format itself.
_FORMAT_ALIASES: dict[str, str] = {
    "checklist": "numbered_tips",
    "top_tips": "numbered_tips",
    "pain_to_tip": "numbered_tips",
    "mistake_list": "warning_list",
    "warning_signs": "warning_list",
    "myth_truth": "myth_vs_truth",
    "problem_solution": "comparison",
    "recap": "category_grid",
}

_MAX_TITLE_WORDS = 6
_MAX_ITEM_WORDS = 3
# Formats whose labels carry a short sentence rather than a 1-3 word tag.
_ITEM_WORD_CAPS: dict[str, int] = {
    "myth_vs_truth": 6,     # the myth statement
    "checklist_score": 4,   # a self-check criterion
}
_TIME_RE = re.compile(r"^\d{1,2}(?:[:h]\d{0,2})?$")

# Standalone micro-promise (2026-07-12, mirrors long-form bug-530): a poster
# header hiding its content behind "esto/eso" tells the 45+ viewer nothing —
# title and hook_line must name the concrete topic.
_DEICTIC_RE = re.compile(r"\b(esto|eso|aquello)\b", re.IGNORECASE)


def resolve_poster_format(fmt: str) -> str:
    """Normalize an idea/plan format to a valid poster format ('' if unknown)."""
    cleaned = str(fmt or "").strip().lower()
    if cleaned in POSTER_FORMATS:
        return cleaned
    return _FORMAT_ALIASES.get(cleaned, "")


def _items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw = plan.get("items")
    return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []


def validate_poster_plan(plan: dict[str, Any]) -> list[str]:
    """Return a list of human-readable issues; empty means valid."""
    issues: list[str] = []
    fmt = str(plan.get("poster_format") or "")
    if fmt not in POSTER_FORMATS:
        issues.append(f"poster_format {fmt!r} is not one of {sorted(POSTER_FORMATS)}")
        return issues
    title = str(plan.get("title") or "").strip()
    if not title:
        issues.append("title is empty")
    elif len(title.split()) > _MAX_TITLE_WORDS:
        issues.append(f"title has more than {_MAX_TITLE_WORDS} words")
    for field in ("title", "hook_line"):
        value = str(plan.get(field) or "").strip()
        if value and _DEICTIC_RE.search(value):
            issues.append(
                f"{field} hides the content behind 'esto/eso' — name the concrete "
                "topic object instead (e.g. 'Sal: compárala en 5 pasos')"
            )
    lo, hi = POSTER_FORMATS[fmt]
    items = _items(plan)
    if not (lo <= len(items) <= hi):
        issues.append(f"items count {len(items)} outside [{lo}, {hi}] for {fmt}")
    max_words = _ITEM_WORD_CAPS.get(fmt, _MAX_ITEM_WORDS)
    for idx, it in enumerate(items):
        label = str(it.get("label") or "").strip()
        if not label:
            issues.append(f"item {idx} has an empty label")
        elif len(label.split()) > max_words:
            issues.append(f"item {idx} label exceeds {max_words} words")
        if fmt == "myth_vs_truth" and not str(it.get("note") or "").strip():
            issues.append(f"item {idx} needs a non-empty note (la verdad) for myth_vs_truth")
        if fmt == "timeline_routine":
            time = str(it.get("time") or "").strip()
            if not time or not _TIME_RE.match(time):
                issues.append(f"item {idx} needs a time like '7:00' or '14h30' for timeline_routine")
    if fmt == "comparison":
        groups = {str(it.get("group") or "").strip() for it in items}
        groups.discard("")
        if len(groups) != 2:
            issues.append("comparison plan must split items into exactly 2 groups")
    return issues
