"""Decide whether Shorts autopilot should auto-run after a long-form review."""
from __future__ import annotations

from pathlib import Path

from video_agent.shorts.review_verdict import long_review_passed


def should_run_autopilot_after_review(job_dir: Path, channel_config: dict) -> bool:
    """True only when shorts autopilot is enabled, configured to run after a
    long review pass, AND the long review verdict is PASS."""
    shorts = channel_config.get("shorts") or {}
    if not shorts.get("enabled", False):
        return False
    ap = shorts.get("autopilot") or {}
    if not ap.get("enabled", False):
        return False
    if not ap.get("run_after_long_review_pass", True):
        return False
    return long_review_passed(job_dir)
