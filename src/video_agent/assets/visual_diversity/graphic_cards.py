"""Graphic card gating + renderer capability detection (spec §22).

The current Remotion compositions do not yet implement checklist/timeline/
habit_matrix cards, so `detect_renderer_caps()` returns `graphic_cards: False`
by default. When the compositions land, flip the detector accordingly.
"""

from __future__ import annotations

from typing import Any


SUPPORTED_FIRST_CUT = {"checklist", "timeline", "habit_matrix"}


def detect_renderer_caps(visual_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return capability flags consumed by the planner and gating logic.

    Detection is intentionally conservative: until the Remotion side declares
    explicit support (e.g. via an env var or a compositions registry), we treat
    graphic cards as unavailable so `auto_if_supported` mode silently skips
    them rather than producing broken render input.
    """
    # Future hook: read RENDERER_GRAPHIC_CARDS env or a manifest file shipped
    # with the Remotion bundle. For now we keep the safe default.
    return {
        "graphic_cards": False,
        "supported_card_types": sorted(SUPPORTED_FIRST_CUT),
    }


def should_fail_for_missing_graphic_renderer(
    visual_config: dict[str, Any],
    renderer_caps: dict[str, Any],
) -> bool:
    card_cfg = (visual_config or {}).get("graphic_cards", {}) or {}
    return (
        card_cfg.get("enabled") is True
        and str(card_cfg.get("rollout_mode")) == "enforce"
        and not renderer_caps.get("graphic_cards", False)
    )


def graphic_card_action(visual_config: dict[str, Any], renderer_caps: dict[str, Any]) -> str:
    """Return one of: 'do_nothing' | 'plan_only' | 'render' | 'skip_with_warning' | 'fail'.

    Implements the §22 behavior matrix.
    """
    card_cfg = (visual_config or {}).get("graphic_cards", {}) or {}
    if not card_cfg.get("enabled"):
        return "do_nothing"

    mode = str(card_cfg.get("rollout_mode", "auto_if_supported"))
    supported = bool(renderer_caps.get("graphic_cards", False))

    if mode == "disabled":
        return "do_nothing"
    if mode == "report_only":
        return "plan_only"
    if mode == "auto_if_supported":
        return "render" if supported else "skip_with_warning"
    if mode == "enforce":
        return "render" if supported else "fail"
    return "plan_only"
