from __future__ import annotations

import re
from typing import Any

ALLOWED_LAYOUTS = {"hook", "subtitle", "checklist", "warning", "quote", "cta"}
PATTERN_BREAK_LAYOUTS = {"hook", "checklist", "warning", "quote"}
WARNING_MARKERS = {
    "error", "errores", "evita", "evitar", "no hagas", "cuidado", "riesgo",
    "problema", "peligro", "demasiado", "extremo", "saltarte", "culpa",
    "ansiedad", "mistake", "avoid", "risk", "warning", "danger",
}


def normalize_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    bullets = value.get("bullets") if isinstance(value.get("bullets"), list) else []
    return {
        "title": str(value.get("title") or "").strip(),
        "body": str(value.get("body") or "").strip(),
        "bullets": [str(b).strip() for b in bullets if str(b).strip()],
        "cta": str(value.get("cta") or "").strip(),
    }


def add_warning(scene: dict[str, Any], warning: str) -> None:
    warnings = scene.get("planner_warnings")
    if not isinstance(warnings, list):
        warnings = []
    warnings.append(warning)
    scene["planner_warnings"] = warnings


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\wáéíóúñüÁÉÍÓÚÑÜ]+\b", str(text or "")))


def _supported_text(scene: dict[str, Any]) -> str:
    return " ".join(
        str(part or "")
        for part in [
            scene.get("narration"),
            scene.get("caption"),
            scene.get("on_screen_text"),
        ]
    ).lower()


def _is_supported(scene: dict[str, Any], text: str) -> bool:
    if not text:
        return False
    return str(text).lower() in _supported_text(scene)


def has_warning_intent(scene: dict[str, Any]) -> bool:
    text = _supported_text(scene)
    return any(marker in text for marker in WARNING_MARKERS)


def has_valid_bullets(scene: dict[str, Any]) -> bool:
    payload = normalize_payload(scene.get("layout_payload"))
    bullets = payload["bullets"]
    if not 2 <= len(bullets) <= 4:
        return False
    return all(_is_supported(scene, bullet) for bullet in bullets)


def has_valid_hook_text(scene: dict[str, Any]) -> bool:
    payload = normalize_payload(scene.get("layout_payload"))
    text = payload.get("title") or str(scene.get("on_screen_text") or "").strip()
    return 2 <= word_count(text) <= 8 and _is_supported(scene, text)


def has_valid_quote_text(scene: dict[str, Any]) -> bool:
    payload = normalize_payload(scene.get("layout_payload"))
    text = payload.get("body") or payload.get("title")
    return 1 <= word_count(text) <= 16 and _is_supported(scene, text)


def has_valid_cta(scene: dict[str, Any], *, is_last: bool, script: dict[str, Any] | None) -> bool:
    payload = normalize_payload(scene.get("layout_payload"))
    cta = payload.get("cta") or str((script or {}).get("cta") or "").strip()
    return is_last and bool(cta)


def downgrade(scene: dict[str, Any], reason: str) -> None:
    scene["layout"] = "subtitle"
    add_warning(scene, reason)


def _safe_short_text(scene: dict[str, Any], key: str, *, max_words: int = 16) -> str:
    text = str(scene.get(key) or "").strip()
    return text if text and word_count(text) <= max_words and _is_supported(scene, text) else ""


def _derive_checklist_payload(scene: dict[str, Any]) -> dict[str, Any] | None:
    title = _safe_short_text(scene, "on_screen_text", max_words=8)
    caption = _safe_short_text(scene, "caption", max_words=10)
    bullets = []
    for value in [title, caption]:
        if value and value.lower() not in {item.lower() for item in bullets}:
            bullets.append(value)
    if len(bullets) < 2:
        return None
    return {"title": title or caption, "body": "", "bullets": bullets[:4], "cta": ""}


def _derive_quote_payload(scene: dict[str, Any]) -> dict[str, Any] | None:
    quote = _safe_short_text(scene, "caption", max_words=16)
    if not quote:
        quote = _safe_short_text(scene, "on_screen_text", max_words=8)
    if not quote:
        return None
    return {"title": "", "body": quote, "bullets": [], "cta": ""}


def _promote_candidate_with_safe_payload(candidate: dict[str, Any], layout: str) -> bool:
    if layout == "warning" and has_warning_intent(candidate):
        candidate["layout"] = "warning"
        add_warning(candidate, "Planner promoted warning using existing risk/avoidance narration.")
        return True
    if layout == "checklist":
        payload = _derive_checklist_payload(candidate)
        if payload:
            candidate["layout_payload"] = payload
            candidate["layout"] = "checklist"
            add_warning(candidate, "Planner promoted checklist using existing on-screen/caption text.")
            return True
    if layout == "quote":
        payload = _derive_quote_payload(candidate)
        if payload:
            candidate["layout_payload"] = payload
            candidate["layout"] = "quote"
            add_warning(candidate, "Planner promoted quote using existing caption text.")
            return True
    return False


def apply_pattern_break_rhythm(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    run_start = None
    for idx, scene in enumerate(scenes + [{"layout": "__end__"}]):
        if scene.get("layout") == "subtitle":
            if run_start is None:
                run_start = idx
            continue
        if run_start is not None and idx - run_start > 5:
            window = scenes[run_start:idx]
            promoted = False
            for candidate in window:
                proposed = str(candidate.get("_proposed_layout") or "").lower()
                if proposed == "checklist" and has_valid_bullets(candidate):
                    candidate["layout"] = "checklist"
                    promoted = True
                    break
                if proposed == "warning" and has_warning_intent(candidate):
                    candidate["layout"] = "warning"
                    promoted = True
                    break
                if proposed == "quote" and has_valid_quote_text(candidate):
                    candidate["layout"] = "quote"
                    promoted = True
                    break
            if not promoted:
                existing_layouts = {str(item.get("layout") or "") for item in scenes}
                layout_priority = [
                    layout for layout in ("warning", "checklist", "quote")
                    if layout not in existing_layouts
                ] + [
                    layout for layout in ("warning", "checklist", "quote")
                    if layout in existing_layouts
                ]
                for layout in layout_priority:
                    for candidate in window:
                        if candidate.get("layout") != "subtitle":
                            continue
                        if _promote_candidate_with_safe_payload(candidate, layout):
                            promoted = True
                            break
                    if promoted:
                        break
            if not promoted:
                add_warning(
                    scenes[run_start],
                    "Could not insert safe pattern break: no eligible scene with valid layout payload.",
                )
        run_start = None
    return scenes


def apply_retention_layouts(
    scenes: list[dict[str, Any]],
    *,
    script: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not scenes:
        return scenes

    for idx, scene in enumerate(scenes):
        scene["planner_warnings"] = list(scene.get("planner_warnings") or [])
        scene["layout_payload"] = normalize_payload(scene.get("layout_payload"))
        layout = str(scene.get("layout") or "subtitle").strip().lower()
        scene["_proposed_layout"] = layout
        if layout not in ALLOWED_LAYOUTS:
            layout = "subtitle"
            add_warning(scene, "Invalid layout downgraded to subtitle.")
        scene["layout"] = layout
        is_last = idx == len(scenes) - 1

        if layout == "hook" and not has_valid_hook_text(scene):
            downgrade(scene, "Hook downgraded to subtitle: missing 2-8 word title.")
        elif layout == "checklist" and not has_valid_bullets(scene):
            downgrade(scene, "Checklist downgraded to subtitle: missing 2-4 valid bullets.")
        elif layout == "warning" and not has_warning_intent(scene):
            downgrade(scene, "Warning downgraded to subtitle: narration does not express a mistake/risk/avoidance.")
        elif layout == "quote" and not has_valid_quote_text(scene):
            downgrade(scene, "Quote downgraded to subtitle: missing short supported quote text.")
        elif layout == "cta" and not has_valid_cta(scene, is_last=is_last, script=script):
            downgrade(scene, "CTA downgraded to subtitle: CTA is allowed only on final scene and requires CTA text.")

    first = scenes[0]
    if (
        first.get("layout") == "subtitle"
        and str(first.get("_proposed_layout") or "subtitle").lower() == "subtitle"
        and has_valid_hook_text(first)
    ):
        first["layout"] = "hook"
        add_warning(first, "Planner promoted first scene to hook using existing safe text.")

    last = scenes[-1]
    if last.get("layout") != "cta":
        payload = normalize_payload(last.get("layout_payload"))
        if not payload.get("cta") and script and script.get("cta"):
            payload["cta"] = str(script["cta"]).strip()
            last["layout_payload"] = payload
        if has_valid_cta(last, is_last=True, script=script):
            last["layout"] = "cta"
            add_warning(last, "Planner promoted final scene to CTA using existing safe CTA text.")

    for _ in range(3):
        before = [scene.get("layout") for scene in scenes]
        apply_pattern_break_rhythm(scenes)
        if [scene.get("layout") for scene in scenes] == before:
            break
    for scene in scenes:
        scene.pop("_proposed_layout", None)
    return scenes
