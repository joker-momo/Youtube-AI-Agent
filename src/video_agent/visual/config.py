"""Resolve long-form visual-span planning config from a channel config.

Config namespace is ``visual.span_planning`` on the channel config — independent
from the shorts ``shorts.visual_timeline`` namespace. Defaults are tuned for
long-form, where a single editorial scene is typically 9-22s (so the shorts
default ``max_span_sec`` of 10 would never allow a span to form).
"""

from __future__ import annotations

from typing import Any

# Long-form defaults (Gate 0 confirmed 2026-06-24):
# - only contiguous ``subtitle`` scenes may be grouped (hook/cta/graphic isolated);
# - a span holds at most 3 scenes or 40s, whichever comes first;
# - mode defaults to report_only (no effect on render until enforced).
DEFAULT_SPAN_CONFIG: dict[str, Any] = {
    "enabled": True,
    "mode": "report_only",  # disabled | report_only | enforced
    "isolate_hook": True,
    "isolate_cta": True,
    "isolate_graphic_scenes": True,
    # Only these layouts may be merged into a multi-scene span. Everything else
    # (hook, cta, checklist, warning, quote, ...) stays a one-scene span.
    "groupable_layouts": ("subtitle",),
    "max_scenes_per_span": 3,
    "max_span_sec": 40.0,
}

_VALID_MODES = {"disabled", "report_only", "enforced"}


def resolve_visual_span_config(channel_config: dict[str, Any]) -> dict[str, Any]:
    """Merge ``visual.span_planning`` over :data:`DEFAULT_SPAN_CONFIG`.

    Unknown / missing keys fall back to defaults. ``mode`` is clamped to
    ``{disabled, report_only, enforced}`` (default ``report_only``). The returned
    dict is a fresh copy and safe to mutate.
    """
    cfg: dict[str, Any] = dict(DEFAULT_SPAN_CONFIG)
    visual = (channel_config or {}).get("visual") or {}
    planning = visual.get("span_planning") or {}

    if "enabled" in planning:
        cfg["enabled"] = bool(planning["enabled"])

    mode = str(planning.get("mode") or cfg["mode"]).strip().lower()
    cfg["mode"] = mode if mode in _VALID_MODES else "report_only"

    for key in ("isolate_hook", "isolate_cta", "isolate_graphic_scenes"):
        if key in planning:
            cfg[key] = bool(planning[key])

    if "groupable_layouts" in planning:
        layouts = planning["groupable_layouts"]
        if isinstance(layouts, (list, tuple, set)):
            normalized = tuple(
                str(x).strip().lower() for x in layouts if str(x).strip()
            )
            if normalized:
                cfg["groupable_layouts"] = normalized

    if "max_scenes_per_span" in planning:
        try:
            cfg["max_scenes_per_span"] = max(1, int(planning["max_scenes_per_span"]))
        except (TypeError, ValueError):
            pass

    if "max_span_sec" in planning:
        try:
            cfg["max_span_sec"] = float(planning["max_span_sec"])
        except (TypeError, ValueError):
            pass

    return cfg
