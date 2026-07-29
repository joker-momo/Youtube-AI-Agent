from __future__ import annotations

from typing import Any


def disclaimer_duration_sec(branding: dict[str, Any]) -> float:
    """Return the auto-probed duration of the replaceable disclaimer clip."""
    return max(0.0, float(branding.get("disclaimer_sec") or 0.0))


def without_long_form_branding(branding: dict[str, Any]) -> dict[str, Any]:
    """Return branding safe for scene-only Short compositions."""
    result = {
        **branding,
        "intro_sec": 0.0,
        "outro_sec": 0.0,
        "intro_video_path": None,
        "outro_video_path": None,
        "disclaimer_video_path": None,
        "disclaimer_sec": 0.0,
    }
    # Strip the retired card contract from cached/legacy branding too.
    result.pop("medical_disclaimer", None)
    return result
