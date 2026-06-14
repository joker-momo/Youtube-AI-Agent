"""Shared leaf helpers for the validation checks."""

from __future__ import annotations

from video_agent.shorts.validation.issues import *  # noqa: F401,F403


def _scene_id(scene: dict[str, Any], index: int) -> str:
    return str(scene.get("id") or scene.get("scene_id") or f"s{index + 1:02d}")


def _duration(scene: dict[str, Any]) -> float:
    try:
        return float(scene.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _joined_scene_text(scene: dict[str, Any]) -> str:
    payload = scene.get("layout_payload")
    payload_text = ""
    if isinstance(payload, dict):
        payload_text = " ".join(
            str(v) for v in payload.values() if isinstance(v, (str, int, float))
        )
    return (
        " ".join(
            str(scene.get(key) or "")
            for key in ("narration", "on_screen_text", "caption", "visual_prompt")
        )
        + " "
        + payload_text
    )
