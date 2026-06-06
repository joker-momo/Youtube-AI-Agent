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
    "graphic_label_callout",
    "graphic_comparison",
    "graphic_routine_split",
}

ALLOWED_GRAPHIC_VARIANTS = {
    "brand_default",
    "warm_olive",
    "soft_clay",
    "cream_focus",
    "evening_calm",
}

ALLOWED_GRAPHIC_VISUAL_TONES = {
    "calm",
    "focus",
    "warning_soft",
    "positive",
    "evening",
}

ALLOWED_GRAPHIC_BACKGROUND_MODES = {
    "clean",
    "radial",
    "paper",
    "video_blur",
}

ALLOWED_GRAPHIC_SURFACE_STYLES = {
    "none",
    "soft_card",
    "editorial",
    "plate_focus",
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
_TITLE_MAX_PHASE15 = 60
_LABEL_CALLOUT_PRODUCT_MAX = 36
_LABEL_CALLOUT_LABEL_MAX = 22
_LABEL_CALLOUT_VALUE_MAX = 26
_LABEL_CALLOUT_NOTE_MAX = 48
_COMPARISON_HEADING_MAX = 24
_COMPARISON_TEXT_MAX = 68
_COMPARISON_BADGE_MAX = 28
_ROUTINE_TOTAL_MAX = 16
_ROUTINE_TIME_MAX = 16
_ROUTINE_TEXT_MAX = 52

FORBIDDEN_HEALTH_MARKETING_WORDS = (
    "veneno",
    "prohibido",
    "nunca",
    "milagro",
    "cura",
    "doctores no quieren",
)


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
                f"Supported graphic layouts: {', '.join(sorted(SUPPORTED_GRAPHIC_LAYOUTS))}."
            )

        # Compatibility stubs for the existing rich Scene type.
        scene.setdefault("visual_type", "graphic")
        if not str(scene.get("on_screen_text") or "").strip():
            scene["on_screen_text"] = _title_from_payload(scene.get("layout_payload", {}))
        scene.setdefault("caption", "")
        scene.setdefault("motion", "none")
        scene.setdefault("asset_refs", {})
        if isinstance(scene.get("asset_refs"), dict):
            scene["asset_refs"].setdefault("background", "")

        payload = scene.get("layout_payload")
        if not isinstance(payload, dict):
            raise ValueError(f"Graphic scene {sid} ({layout}) is missing layout_payload.")

        _validate_visual_style_fields(payload, sid, layout)
        _validate_title(payload, sid, layout)
        _validate_footer(payload, sid, layout, warnings)

        if layout == "graphic_plate_ratio":
            _validate_plate_ratio(payload, sid, warnings)
        elif layout == "graphic_checklist":
            _validate_checklist(payload, sid, warnings)
        elif layout == "graphic_step_list":
            _validate_step_list(payload, sid, warnings)
        elif layout == "graphic_label_callout":
            _validate_label_callout(payload, sid, warnings)
        elif layout == "graphic_comparison":
            _validate_comparison(payload, sid)
        elif layout == "graphic_routine_split":
            _validate_routine_split(payload, sid, warnings)

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


def _validate_optional_choice(
    payload: dict,
    field: str,
    allowed: set[str],
    sid: Any,
    layout: str,
) -> None:
    value = payload.get(field)
    if value is None:
        return
    if not isinstance(value, str) or value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(
            f"Graphic scene {sid} ({layout}) has invalid {field}: {value!r}. "
            f"Allowed values: {allowed_values}."
        )


def _validate_visual_style_fields(payload: dict, sid: Any, layout: str) -> None:
    _validate_optional_choice(payload, "variant", ALLOWED_GRAPHIC_VARIANTS, sid, layout)
    _validate_optional_choice(payload, "visual_tone", ALLOWED_GRAPHIC_VISUAL_TONES, sid, layout)
    _validate_optional_choice(payload, "background_mode", ALLOWED_GRAPHIC_BACKGROUND_MODES, sid, layout)
    _validate_optional_choice(payload, "surface_style", ALLOWED_GRAPHIC_SURFACE_STYLES, sid, layout)


def _validate_title(payload: dict, sid: Any, layout: str) -> None:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Graphic scene {sid} ({layout}) requires a non-empty title.")
    max_len = _TITLE_MAX_PHASE15 if layout in {
        "graphic_label_callout",
        "graphic_comparison",
        "graphic_routine_split",
    } else 48
    if len(title) > max_len:
        raise ValueError(f"Graphic scene {sid} title exceeds {max_len} chars: {len(title)}.")


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


def _title_from_payload(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("title") or payload.get("productLabel") or "").strip()


def _warn_if_long(value: Any, max_len: int, label: str, sid: Any, warnings: list[str]) -> None:
    if isinstance(value, str) and len(value) > max_len:
        warnings.append(f"Scene {sid} {label} exceeds {max_len} chars: '{value}'.")


def _require_short_string(value: Any, max_len: int, label: str, sid: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Graphic scene {sid} requires non-empty {label}.")
    if len(value) > max_len:
        raise ValueError(f"Graphic scene {sid} {label} exceeds {max_len} chars: {len(value)}.")
    return value


def _validate_label_callout(payload: dict, sid: Any, warnings: list[str]) -> None:
    product_label = payload.get("productLabel")
    _warn_if_long(product_label, _LABEL_CALLOUT_PRODUCT_MAX, "productLabel", sid, warnings)
    callouts = payload.get("callouts")
    if not isinstance(callouts, list) or not (2 <= len(callouts) <= 4):
        got = len(callouts) if isinstance(callouts, list) else "missing"
        raise ValueError(f"graphic_label_callout scene {sid} callouts must contain 2-4 items, got {got}.")
    for callout in callouts:
        if not isinstance(callout, dict):
            raise ValueError(f"graphic_label_callout scene {sid} has a non-object callout.")
        _require_short_string(callout.get("label"), _LABEL_CALLOUT_LABEL_MAX, "callout.label", sid)
        _require_short_string(callout.get("value"), _LABEL_CALLOUT_VALUE_MAX, "callout.value", sid)
        note = callout.get("note")
        _warn_if_long(note, _LABEL_CALLOUT_NOTE_MAX, "callout.note", sid, warnings)


def _check_forbidden_language(value: Any, sid: Any, field: str) -> None:
    if not isinstance(value, str):
        return
    lower = value.lower()
    for word in FORBIDDEN_HEALTH_MARKETING_WORDS:
        if word in lower:
            raise ValueError(
                f"graphic_comparison scene {sid} contains forbidden health-marketing word "
                f"'{word}' in {field}."
            )


def _validate_comparison(payload: dict, sid: Any) -> None:
    _check_forbidden_language(payload.get("title"), sid, "title")
    _check_forbidden_language(payload.get("footer"), sid, "footer")
    for side_name in ("left", "right"):
        side = payload.get(side_name)
        if not isinstance(side, dict):
            raise ValueError(f"graphic_comparison scene {sid} requires object '{side_name}'.")
        _require_short_string(side.get("heading"), _COMPARISON_HEADING_MAX, f"{side_name}.heading", sid)
        _require_short_string(side.get("text"), _COMPARISON_TEXT_MAX, f"{side_name}.text", sid)
        badge = side.get("badge")
        if badge is not None:
            _require_short_string(badge, _COMPARISON_BADGE_MAX, f"{side_name}.badge", sid)
        for field in ("heading", "text", "badge"):
            _check_forbidden_language(side.get(field), sid, f"{side_name}.{field}")


def _validate_routine_split(payload: dict, sid: Any, warnings: list[str]) -> None:
    total_label = payload.get("totalLabel")
    _warn_if_long(total_label, _ROUTINE_TOTAL_MAX, "totalLabel", sid, warnings)
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not (2 <= len(blocks) <= 4):
        got = len(blocks) if isinstance(blocks, list) else "missing"
        raise ValueError(f"graphic_routine_split scene {sid} blocks must contain 2-4 items, got {got}.")
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError(f"graphic_routine_split scene {sid} has a non-object block.")
        _require_short_string(block.get("time"), _ROUTINE_TIME_MAX, "block.time", sid)
        _require_short_string(block.get("text"), _ROUTINE_TEXT_MAX, "block.text", sid)
