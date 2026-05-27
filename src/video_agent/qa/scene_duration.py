"""Scene-duration validator: warns when scenes exceed retention-safe limits."""
from __future__ import annotations

from typing import Any, Iterable

LONG_SCENE_WARNING_SEC = 18.0
VERY_LONG_SCENE_WARNING_SEC = 22.0
EARLY_SCENE_STRICT_LIMIT_SEC = 16.0  # scenes 1-8


def validate_scene_duration(
    scene: dict[str, Any], scene_index: int
) -> list[str]:
    """Return warnings (strings) for a single scene. Empty list = OK."""
    warnings: list[str] = []
    duration = float(scene.get("duration_sec") or 0.0)
    scene_id = str(scene.get("id") or f"scene-{scene_index:02d}")

    if duration > VERY_LONG_SCENE_WARNING_SEC:
        warnings.append(
            f"Scene {scene_id} duration {duration:.2f}s is very long (>{VERY_LONG_SCENE_WARNING_SEC}s); split it."
        )
    elif duration > LONG_SCENE_WARNING_SEC:
        warnings.append(
            f"Scene {scene_id} duration {duration:.2f}s is long; split or shorten."
        )

    if scene_index <= 8 and duration > EARLY_SCENE_STRICT_LIMIT_SEC:
        warnings.append(
            f"Scene {scene_id} (early scene, position {scene_index}) duration {duration:.2f}s exceeds {EARLY_SCENE_STRICT_LIMIT_SEC}s; trim for opening retention."
        )

    return warnings


def validate_scenes_durations(scenes: Iterable[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for idx, scene in enumerate(scenes, start=1):
        out.extend(validate_scene_duration(scene, idx))
    return out
