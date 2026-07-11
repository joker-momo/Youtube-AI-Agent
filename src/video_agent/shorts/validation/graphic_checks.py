"""Graphic-scene detection and graphic payload field validation."""

from __future__ import annotations

from video_agent.shorts.validation._constants import *  # noqa: F401,F403
from video_agent.shorts.validation._helpers import _joined_scene_text, _scene_id
from video_agent.shorts.validation.issues import *  # noqa: F401,F403


def _looks_like_checklist_or_explainer(
    script: dict[str, Any] | None, scenes: list[dict[str, Any]]
) -> bool:
    text = " ".join(
        [
            str((script or {}).get("short_format") or ""),
            str((script or {}).get("format") or ""),
            str((script or {}).get("narration") or ""),
            " ".join(_joined_scene_text(scene) for scene in scenes),
        ]
    ).lower()
    return any(
        term in text
        for term in ("checklist", "lista", "paso", "revisa", "etiqueta", "por 100 g", "ingrediente")
    )


def _missing_graphic_candidate(scene: dict[str, Any]) -> bool:
    if str(scene.get("layout") or "").startswith("graphic_"):
        return False
    text = _joined_scene_text(scene).lower()
    label_terms = (
        "etiqueta",
        "fibra",
        "azúcar",
        "azucar",
        "azúcares",
        "azucares",
        "sal",
        "proteína",
        "proteina",
        "por 100 g",
    )
    comparison_terms = ("mejor", "cuidado", "opción a", "opcion a", "opción b", "opcion b")
    structured_terms = ("1/2", "1/4", "50%", "25%", "paso 1", "paso 2")
    portion_terms = (
        "porción",
        "porcion",
        "palma",
        "tamaño de tu palma",
        "plato",
        "hidratos",
        "rebanada",
    )
    enumerated_terms = ("uno:", "dos:", "tres:", "cuatro:", "cinco:")
    return (
        sum(1 for term in label_terms if term in text) >= 2
        or any(term in text for term in comparison_terms)
        and (" vs " in text or "opción" in text or "opcion" in text)
        or any(term in text for term in structured_terms)
        or sum(1 for term in portion_terms if term in text) >= 2
        or any(term in text for term in enumerated_terms)
    )


def count_graphic_scenes(scenes: list[dict[str, Any]]) -> int:
    """Deterministic count of graphic_* layout scenes."""
    return sum(1 for s in (scenes or []) if str(s.get("layout") or "").startswith("graphic_"))


def is_graphic_led(scenes: list[dict[str, Any]], *, script: dict[str, Any] | None = None) -> bool:
    """A Short is intentionally graphic-led when graphics make up at least half of
    its scenes — those legitimately want 3 graphics. Below that, 3 graphics is an
    accident to be flagged."""
    scenes = list(scenes or [])
    if not scenes:
        return False
    graphic_count = count_graphic_scenes(scenes)
    return graphic_count * 2 >= len(scenes)


def is_explicit_graphic_led(script: dict[str, Any] | None) -> bool:
    """A Short is graphic-led ONLY when the input says so explicitly. Being a
    checklist/explainer is NOT enough — those are normal Shorts capped at 2
    graphics. We look for an explicit flag/marker in the plan or script."""
    if not script:
        return False
    if script.get("graphic_led") is True or script.get("is_graphic_led") is True:
        return True
    markers = " ".join(
        str(script.get(k) or "")
        for k in ("style", "mode", "short_format", "format", "visual_style")
    ).lower()
    return "graphic_led" in markers or "graphic-led" in markers


def graphic_repair_targets(
    scenes: list[dict[str, Any]],
    *,
    max_keep: int = None,
) -> tuple[list[str], list[str]]:
    """Decide which graphics to keep vs. convert when over the cap.

    Returns ``(keep_ids, convert_ids)``. Keeps the highest-value graphics
    (label_callout for the first ingredient, comparison for fibra/azúcar);
    convert ids are the lower-value setup/recap graphics (e.g. graphic_checklist)
    that should become realistic short_tip/short_myth scenes."""
    if max_keep is None:
        max_keep = MAX_GRAPHIC_SCENES_PER_SHORT
    graphics = [
        (i, s)
        for i, s in enumerate(scenes or [])
        if str(s.get("layout") or "").startswith("graphic_")
    ]

    def rank(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        idx, scene = item
        layout = str(scene.get("layout") or "")
        pri = _GRAPHIC_KEEP_PRIORITY.index(layout) if layout in _GRAPHIC_KEEP_PRIORITY else 99
        return (pri, idx)

    ranked = sorted(graphics, key=rank)
    keep = ranked[:max_keep]
    convert = ranked[max_keep:]
    keep_ids = [_scene_id(s, i) for i, s in keep]
    convert_ids = [_scene_id(s, i) for i, s in convert]
    return keep_ids, convert_ids


def _repair_degenerate_graphic_payloads(
    scenes: list[dict[str, Any]], warnings: list[str]
) -> None:
    """Deterministically repair layout/payload mismatches instead of killing the job.

    The model occasionally picks a list layout for a single idea (bug-486: a
    graphic_checklist with one item — valid content, wrong container — used to
    raise and fail the whole Short after scenes QA had already passed). A single
    valid entry downgrades the scene to ``graphic_quote_portrait`` (one featured
    sentence). Oversized CHECKLISTS are NOT trimmed: silently dropping the fifth
    idea violates idea preservation — the hard validator rejects them so the
    pipeline requests a simpler/split scene instead. Genuinely empty/invalid
    payloads still fall through to the hard validators.
    """
    def _downgrade_to_quote(scene: dict, payload: dict, sid: Any, layout: str, text: str) -> None:
        payload.pop("items", None)
        payload.pop("steps", None)
        payload["title"] = text.strip()[:_TITLE_MAX_PHASE15]
        scene["layout"] = "graphic_quote_portrait"
        warnings.append(
            f"Scene {sid}: {layout} had a single entry — downgraded to "
            "graphic_quote_portrait (one featured sentence)."
        )

    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        sid = scene.get("id", index)
        layout = scene.get("layout")
        payload = scene.get("layout_payload")
        if not isinstance(payload, dict):
            continue

        if layout == "graphic_checklist":
            items = payload.get("items")
            if isinstance(items, list):
                valid = [i for i in items if isinstance(i, str) and i.strip()]
                if len(valid) == 1:
                    _downgrade_to_quote(scene, payload, sid, str(layout), valid[0])
        elif layout == "graphic_step_list":
            steps = payload.get("steps")
            if isinstance(steps, list):
                valid_steps = [
                    s for s in steps
                    if isinstance(s, dict) and str(s.get("text") or "").strip()
                ]
                if len(valid_steps) == 1:
                    _downgrade_to_quote(scene, payload, sid, str(layout), str(valid_steps[0].get("text")))
                elif len(valid_steps) > 4:
                    payload["steps"] = valid_steps[:4]
                    warnings.append(
                        f"Scene {sid}: step list had {len(valid_steps)} steps — trimmed to 4."
                    )


def validate_short_graphic_scenes(scenes: list[dict[str, Any]]) -> list[str]:
    """Validate graphic scenes in place. Raises ``ValueError`` on hard errors.

    Returns a list of non-fatal warnings (e.g. duration / count advisories).
    Also inserts safe compatibility stubs for the rich ``Scene`` fields graphic
    scenes do not use directly, so render props stay schema-compatible.
    """
    warnings: list[str] = []
    graphic_count = 0
    is_bread_label_topic = _looks_like_bread_label_topic(scenes)
    _repair_degenerate_graphic_payloads(scenes, warnings)

    for index, scene in enumerate(scenes):
        sid = scene.get("id", index)
        layout = scene.get("layout")

        _validate_non_graphic_scene_tuning(
            scene, sid, layout, index, is_bread_label_topic, warnings
        )

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
        elif layout in {"graphic_stat", "graphic_evidence_nugget"}:
            _validate_title_body_card(payload, sid, layout, warnings, body_required=True)
        elif layout == "graphic_myth":
            _validate_title_body_card(payload, sid, layout, warnings, body_required=True)
        elif layout == "graphic_do_dont":
            _validate_do_dont(payload, sid)
        elif layout == "graphic_recipe_snapshot":
            _validate_recipe_snapshot(payload, sid, warnings)
        elif layout == "graphic_quote_portrait":
            pass  # title (the quotation) already validated by _validate_title
        elif layout == "graphic_warning":
            _validate_warning(payload, sid, warnings)

        _validate_graphic_duration(scene, sid, layout, warnings)

    if graphic_count > MAX_GRAPHIC_SCENES_PER_SHORT:
        warnings.append(
            f"Short has {graphic_count} graphic scenes; "
            f"max recommended is {MAX_GRAPHIC_SCENES_PER_SHORT} for MVP."
        )

    return warnings


def _looks_like_bread_label_topic(scenes: list[dict[str, Any]]) -> bool:
    text = " ".join(_joined_scene_text(scene).lower() for scene in scenes)
    return any(term in text for term in BREAD_LABEL_TOPIC_TERMS)


def _validate_non_graphic_scene_tuning(
    scene: dict[str, Any],
    sid: Any,
    layout: Any,
    index: int,
    is_bread_label_topic: bool,
    warnings: list[str],
) -> None:
    on_screen_text = str(scene.get("on_screen_text") or "").strip().upper()
    if on_screen_text in PASSIVE_CTA_TEXTS:
        warnings.append(
            f"Scene {sid} CTA text '{on_screen_text}' is passive/status-like; prefer "
            "GUARDA ESTA LISTA or GUÁRDALO PARA LA COMPRA."
        )

    if layout == "short_myth" and float(scene.get("duration_sec") or 0) > 3.0:
        warnings.append(f"Scene {sid} myth/setup duration exceeds 3.0s; keep myth beats short.")

    if on_screen_text == "MITO RÁPIDO" and float(scene.get("duration_sec") or 0) > 3.0:
        warnings.append(
            f"Scene {sid} keeps generic MITO RÁPIDO too long; use a specific myth statement."
        )

    if index == 0 and layout == "short_hook" and is_bread_label_topic:
        visual_prompt = str(scene.get("visual_prompt") or "").lower()
        if not any(term in visual_prompt for term in BREAD_LABEL_HOOK_VISUAL_TERMS):
            warnings.append(
                f"Scene {sid} bread/label hook visual is too generic; include bread, package, label, "
                "supermarket shelf, or shopping basket imagery."
            )


def _validate_graphic_duration(
    scene: dict[str, Any], sid: Any, layout: str, warnings: list[str]
) -> None:
    dur = float(scene.get("duration_sec") or 0)
    target_min, target_max, hard_max = GRAPHIC_LAYOUT_DURATION_TARGETS.get(
        layout,
        (GRAPHIC_MIN_DURATION_SEC, GRAPHIC_MAX_DURATION_SEC, GRAPHIC_MAX_DURATION_SEC),
    )

    if dur > hard_max:
        raise ValueError(
            f"Graphic scene {sid} ({layout}) duration {dur}s exceeds hard max {hard_max}s; "
            "graphics must be fast explanatory bursts, not slides."
        )

    if not (target_min <= dur <= target_max):
        warnings.append(
            f"Scene {sid} ({layout}) graphic duration {dur}s is outside the target "
            f"{target_min}-{target_max}s range."
        )


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
    _validate_optional_choice(
        payload, "background_mode", ALLOWED_GRAPHIC_BACKGROUND_MODES, sid, layout
    )
    _validate_optional_choice(payload, "surface_style", ALLOWED_GRAPHIC_SURFACE_STYLES, sid, layout)
    if (
        payload.get("surface_style") == "numbered_photo_bands"
        and layout not in NUMBERED_BANDS_COMPATIBLE_LAYOUTS
    ):
        compatible = ", ".join(sorted(NUMBERED_BANDS_COMPATIBLE_LAYOUTS))
        raise ValueError(
            f"Graphic scene {sid} ({layout}) cannot use surface_style numbered_photo_bands: "
            f"each band renders one list entry, so it is only valid for {compatible}."
        )


def _validate_title(payload: dict, sid: Any, layout: str) -> None:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Graphic scene {sid} ({layout}) requires a non-empty title.")
    max_len = (
        _TITLE_MAX_PHASE15
        if layout
        in {
            "graphic_label_callout",
            "graphic_comparison",
            "graphic_routine_split",
            # Long-form ports whose title carries a full sentence (mito / quotation).
            "graphic_myth",
            "graphic_quote_portrait",
        }
        else 48
    )
    if len(title) > max_len:
        raise ValueError(f"Graphic scene {sid} title exceeds {max_len} chars: {len(title)}.")


def _validate_footer(payload: dict, sid: Any, layout: str, warnings: list[str]) -> None:
    footer = payload.get("footer")
    if footer is not None and isinstance(footer, str) and len(footer) > _FOOTER_MAX:
        warnings.append(
            f"Scene {sid} ({layout}) footer exceeds {_FOOTER_MAX} chars: {len(footer)}."
        )


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
    # 2-4 hard limit (2026-07 density contract): a fifth item is unreadable in a
    # 2.5-5s scene. Never truncate here — rejecting forces a simpler/split scene
    # so no idea is silently dropped.
    if not isinstance(items, list) or not (2 <= len(items) <= 4):
        raise ValueError(f"graphic_checklist scene {sid} requires 2-4 items.")
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"graphic_checklist scene {sid} has an empty item.")
        if len(item) > _CHECKLIST_ITEM_MAX:
            warnings.append(
                f"Scene {sid} checklist item exceeds {_CHECKLIST_ITEM_MAX} chars: '{item}'."
            )


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
        # Optional time prefix (absorbs the legacy graphic_routine_split time
        # blocks): when present it must be a short non-empty string.
        if "time" in step:
            _require_short_string(step.get("time"), _ROUTINE_TIME_MAX, "step.time", sid)


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
        raise ValueError(
            f"graphic_label_callout scene {sid} callouts must contain 2-4 items, got {got}."
        )
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
                f"graphic scene {sid} contains forbidden health-marketing word "
                f"'{word}' in {field}."
            )


def _validate_comparison(payload: dict, sid: Any) -> None:
    _check_forbidden_language(payload.get("title"), sid, "title")
    _check_forbidden_language(payload.get("footer"), sid, "footer")
    for side_name in ("left", "right"):
        side = payload.get(side_name)
        if not isinstance(side, dict):
            raise ValueError(f"graphic_comparison scene {sid} requires object '{side_name}'.")
        _require_short_string(
            side.get("heading"), _COMPARISON_HEADING_MAX, f"{side_name}.heading", sid
        )
        _require_short_string(side.get("text"), _COMPARISON_TEXT_MAX, f"{side_name}.text", sid)
        badge = side.get("badge")
        if isinstance(badge, str) and not badge.strip():
            side.pop("badge", None)
            badge = None
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
        raise ValueError(
            f"graphic_routine_split scene {sid} blocks must contain 2-4 items, got {got}."
        )
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError(f"graphic_routine_split scene {sid} has a non-object block.")
        _require_short_string(block.get("time"), _ROUTINE_TIME_MAX, "block.time", sid)
        _require_short_string(block.get("text"), _ROUTINE_TEXT_MAX, "block.text", sid)


# --- Long-form graphic card ports (stat/myth/do_dont/recipe_snapshot/ -------
# --- quote_portrait/evidence_nugget/warning) --------------------------------


def _validate_title_body_card(
    payload: dict, sid: Any, layout: str, warnings: list[str], *, body_required: bool
) -> None:
    """title+body cards ported from long-form: graphic_stat (number + label),
    graphic_myth (mito + realidad), graphic_evidence_nugget (fact + context)."""
    body = payload.get("body")
    if body_required and (not isinstance(body, str) or not body.strip()):
        raise ValueError(f"{layout} scene {sid} requires a non-empty body.")
    _warn_if_long(body, _CARD_BODY_MAX, "body", sid, warnings)
    _check_forbidden_language(payload.get("title"), sid, "title")
    _check_forbidden_language(body, sid, "body")


def _validate_do_dont(payload: dict, sid: Any) -> None:
    """graphic_do_dont: a worse choice (bad) vs a clearly better one (good)."""
    _require_short_string(payload.get("bad"), _COMPARISON_TEXT_MAX, "bad", sid)
    _require_short_string(payload.get("good"), _COMPARISON_TEXT_MAX, "good", sid)
    for field in ("title", "bad", "good", "footer"):
        _check_forbidden_language(payload.get(field), sid, field)


def _validate_recipe_snapshot(payload: dict, sid: Any, warnings: list[str]) -> None:
    """graphic_recipe_snapshot: 2-3 real foods as labelled photo tiles."""
    items = payload.get("items")
    if not isinstance(items, list) or not (2 <= len(items) <= 3):
        got = len(items) if isinstance(items, list) else "missing"
        raise ValueError(
            f"graphic_recipe_snapshot scene {sid} items must contain 2-3 foods, got {got}."
        )
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"graphic_recipe_snapshot scene {sid} has an empty item.")
        if len(item) > _CHECKLIST_ITEM_MAX:
            warnings.append(
                f"Scene {sid} recipe item exceeds {_CHECKLIST_ITEM_MAX} chars: '{item}'."
            )


def _validate_warning(payload: dict, sid: Any, warnings: list[str]) -> None:
    """graphic_warning: 2-4 things to avoid, calm tone, no fear language."""
    items = payload.get("items")
    if not isinstance(items, list) or not (2 <= len(items) <= 4):
        got = len(items) if isinstance(items, list) else "missing"
        raise ValueError(f"graphic_warning scene {sid} items must contain 2-4 items, got {got}.")
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"graphic_warning scene {sid} has an empty item.")
        if len(item) > _CHECKLIST_ITEM_MAX:
            warnings.append(
                f"Scene {sid} warning item exceeds {_CHECKLIST_ITEM_MAX} chars: '{item}'."
            )
        _check_forbidden_language(item, sid, "items")
    _check_forbidden_language(payload.get("title"), sid, "title")
    _check_forbidden_language(payload.get("footer"), sid, "footer")
