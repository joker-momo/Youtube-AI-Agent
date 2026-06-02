"""Placeholder baseline resolution from previous reports (spec §23)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def resolve_placeholder_baseline(
    channel_id: str,
    current_job_id: str,
    outputs_root: Path,
) -> float | None:
    """Average placeholder_ratio across prior visual-diversity reports for the channel.

    Returns None when no prior report is found. Callers should fall back to
    `max_placeholder_ratio_enforce` in enforce mode.
    """
    outputs_root = Path(outputs_root)
    if not outputs_root.exists():
        return None

    ratios: list[float] = []
    for report_path in outputs_root.glob("*/visual-diversity-report.json"):
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("channel_id") != channel_id:
            continue
        if data.get("job_id") == current_job_id:
            continue
        ratio = data.get("placeholder_ratio")
        if isinstance(ratio, (int, float)):
            ratios.append(float(ratio))

    if not ratios:
        return None
    return round(sum(ratios) / len(ratios), 4)


def placeholder_fail_threshold(
    visual_config: dict[str, Any],
    baseline: float | None,
) -> float:
    """Resolve the ratio above which the report should flag placeholder regression."""
    if baseline is not None:
        return baseline
    diversity = (visual_config or {}).get("diversity", {}) or {}
    return float(diversity.get("max_placeholder_ratio_enforce", 0.05) or 0.05)
