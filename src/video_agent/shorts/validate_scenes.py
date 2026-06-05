"""Pre-render validation for Shorts graphic scenes (spec v7 §18).

Runs after ``build_short_scenes`` + ``run_short_scenes_qa`` and before render
props are written / Remotion is invoked. Catches unsupported graphic layouts and
malformed payloads early, with clear errors, so bad scenes never reach the
renderer. Mirrors the Zod checks in ``remotion/src/graphics/graphic-payloads.ts``.
"""
from __future__ import annotations

from typing import Any

SUPPORTED_GRAPHIC_LAYOUTS = {
    "graphic_plate_ratio",
    "graphic_checklist",
    "graphic_step_list",
}

PLATE_RATIO_TOTAL = 100.0
PLATE_RATIO_EPSILON = 0.01
MAX_GRAPHIC_SCENES_PER_SHORT = 2
GRAPHIC_MIN_DURATION_SEC = 2.5
GRAPHIC_MAX_DURATION_SEC = 4.0

# Text-density limits (keep in sync with the TypeScript Zod schemas).
_PLATE_LABEL_MAX = 48
_CHECKLIST_ITEM_MAX = 48
_STEP_TEXT_MAX = 56
_FOOTER_MAX = 72


def validate_short_graphic_scenes(scenes: list[dict[str, Any]]) -> list[str]:
    """Validate graphic scenes in place. Raises ``ValueError`` on hard errors.

    Returns a list of non-fatal warnings (e.g. duration / count advisories).
    Also inserts safe compatibility stubs for the rich ``Scene`` fields graphic
    scenes do not use directly, so render props stay schema-compatible.
    """
    warnings: list[str] = []
    graphic_count = 0

    for index, scene in enumerate(scenes):
        sid = scene.get("id", index)
        layout = scene.get("layout")

        if "scene_id" in scene and "id" not in scene:
            raise ValueError(
                f"Scene at index {index} uses scene_id but is missing id. "
                "Normalize scene_id -> id before render props."
            )

        if not isinstance(layout, str) or not layout.startswith("graphic_"):
            continue

        graphic_count += 1

        if layout not in SUPPORTED_GRAPHIC_LAYOUTS:
            raise ValueError(
                f"Scene {sid} uses unsupported graphic layout {layout}. "
                f"Supported MVP layouts: {', '.join(sorted(SUPPORTED_GRAPHIC_LAYOUTS))}."
            )

        # Compatibility stubs for the existing rich Scene type.
        scene.setdefault("visual_type", "graphic")
        scene.setdefault("on_screen_text", "")
        scene.setdefault("caption", "")
        scene.setdefault("motion", "none")

        payload = scene.get("layout_payload")
        if not isinstance(payload, dict):
            raise ValueError(f"Graphic scene {sid} ({layout}) is missing layout_payload.")

        _validate_title(payload, sid, layout)
        _validate_footer(payload, sid, layout, warnings)

        if layout == "graphic_plate_ratio":
            _validate_plate_ratio(payload, sid, warnings)
        elif layout == "graphic_checklist":
            _validate_checklist(payload, sid, warnings)
        elif layout == "graphic_step_list":
            _validate_step_list(payload, sid, warnings)

        dur = float(scene.get("duration_sec") or 0)
        if not (GRAPHIC_MIN_DURATION_SEC <= dur <= GRAPHIC_MAX_DURATION_SEC):
            warnings.append(
                f"Scene {sid} graphic duration {dur}s is outside the recommended "
                f"{GRAPHIC_MIN_DURATION_SEC}-{GRAPHIC_MAX_DURATION_SEC}s range."
            )

    if graphic_count > MAX_GRAPHIC_SCENES_PER_SHORT:
        warnings.append(
            f"Short has {graphic_count} graphic scenes; "
            f"max recommended is {MAX_GRAPHIC_SCENES_PER_SHORT} for MVP."
        )

    return warnings


def _validate_title(payload: dict, sid: Any, layout: str) -> None:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Graphic scene {sid} ({layout}) requires a non-empty title.")
    if len(title) > 48:
        raise ValueError(f"Graphic scene {sid} title exceeds 48 chars: {len(title)}.")


def _validate_footer(payload: dict, sid: Any, layout: str, warnings: list[str]) -> None:
    footer = payload.get("footer")
    if footer is not None and isinstance(footer, str) and len(footer) > _FOOTER_MAX:
        warnings.append(f"Scene {sid} ({layout}) footer exceeds {_FOOTER_MAX} chars: {len(footer)}.")


def _validate_plate_ratio(payload: dict, sid: Any, warnings: list[str]) -> None:
    segments = payload.get("segments")
    if not isinstance(segments, list) or not (2 <= len(segments) <= 4):
        raise ValueError(f"graphic_plate_ratio scene {sid} requires 2-4 segments.")
    total = sum(float(s.get("value", 0)) for s in segments if isinstance(s, dict))
    if abs(total - PLATE_RATIO_TOTAL) > PLATE_RATIO_EPSILON:
        raise ValueError(
            f"graphic_plate_ratio scene {sid} segments must sum to {int(PLATE_RATIO_TOTAL)} "
            f"+/- {PLATE_RATIO_EPSILON}; got {total}."
        )
    for s in segments:
        label = s.get("label") if isinstance(s, dict) else None
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"graphic_plate_ratio scene {sid} has a segment with an empty label.")
        if len(label) > _PLATE_LABEL_MAX:
            warnings.append(f"Scene {sid} plate label exceeds {_PLATE_LABEL_MAX} chars: '{label}'.")


def _validate_checklist(payload: dict, sid: Any, warnings: list[str]) -> None:
    items = payload.get("items")
    if not isinstance(items, list) or not (2 <= len(items) <= 5):
        raise ValueError(f"graphic_checklist scene {sid} requires 2-5 items.")
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"graphic_checklist scene {sid} has an empty item.")
        if len(item) > _CHECKLIST_ITEM_MAX:
            warnings.append(f"Scene {sid} checklist item exceeds {_CHECKLIST_ITEM_MAX} chars: '{item}'.")


def _validate_step_list(payload: dict, sid: Any, warnings: list[str]) -> None:
    steps = payload.get("steps")
    if not isinstance(steps, list) or not (2 <= len(steps) <= 4):
        raise ValueError(f"graphic_step_list scene {sid} requires 2-4 steps.")
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError(f"graphic_step_list scene {sid} has a non-object step.")
        text = step.get("text")
        label = step.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"graphic_step_list scene {sid} has a step with an empty label.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"graphic_step_list scene {sid} has a step with empty text.")
        if len(text) > _STEP_TEXT_MAX:
            warnings.append(f"Scene {sid} step text exceeds {_STEP_TEXT_MAX} chars: '{text}'.")
