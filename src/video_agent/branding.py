from __future__ import annotations

from typing import Any


def prepare_medical_disclaimer(branding_config: dict[str, Any]) -> dict[str, Any]:
    raw = branding_config.get("medical_disclaimer")
    config = raw if isinstance(raw, dict) else {}
    enabled = bool(config.get("enabled", False))
    raw_lines = config.get("lines")
    lines: list[str] = []
    if isinstance(raw_lines, list):
        for line in raw_lines:
            normalized = str(line).strip()
            if normalized:
                lines.append(normalized)
    return {
        "enabled": enabled,
        "duration_sec": max(0.0, float(config.get("duration_sec", 8.0))) if enabled else 0.0,
        "title": str(config.get("title") or "AVISO MÉDICO").strip(),
        "lines": lines,
    }


def medical_disclaimer_duration_sec(branding: dict[str, Any]) -> float:
    raw = branding.get("medical_disclaimer")
    disclaimer = raw if isinstance(raw, dict) else {}
    if not disclaimer.get("enabled"):
        return 0.0
    return max(0.0, float(disclaimer.get("duration_sec") or 0.0))


def without_long_form_branding(branding: dict[str, Any]) -> dict[str, Any]:
    """Return branding safe for scene-only Short compositions."""
    return {
        **branding,
        "intro_sec": 0.0,
        "outro_sec": 0.0,
        "intro_video_path": None,
        "outro_video_path": None,
        "medical_disclaimer": {
            **(branding.get("medical_disclaimer") or {}),
            "enabled": False,
            "duration_sec": 0.0,
        },
    }
