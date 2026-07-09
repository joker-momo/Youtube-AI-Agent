"""Poster-plan schema + validation for infographic shorts."""
from __future__ import annotations

from typing import Any

# poster_format -> (min_items, max_items)
POSTER_FORMATS: dict[str, tuple[int, int]] = {
    "category_grid": (5, 7),
    "numbered_tips": (5, 7),
    "warning_list": (5, 6),
    "comparison": (4, 6),
}
_MAX_TITLE_WORDS = 6
_MAX_ITEM_WORDS = 3


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
    lo, hi = POSTER_FORMATS[fmt]
    items = _items(plan)
    if not (lo <= len(items) <= hi):
        issues.append(f"items count {len(items)} outside [{lo}, {hi}] for {fmt}")
    for idx, it in enumerate(items):
        label = str(it.get("label") or "").strip()
        if not label:
            issues.append(f"item {idx} has an empty label")
        elif len(label.split()) > _MAX_ITEM_WORDS:
            issues.append(f"item {idx} label exceeds {_MAX_ITEM_WORDS} words")
    if fmt == "comparison":
        groups = {str(it.get("group") or "").strip() for it in items}
        groups.discard("")
        if len(groups) != 2:
            issues.append("comparison plan must split items into exactly 2 groups")
    return issues
