from __future__ import annotations

import re
import unicodedata
from typing import Any

ALLOWED_LAYOUTS = {
    "hook", "subtitle", "checklist", "warning", "quote", "cta", "stat", "steps", "comparison", "myth",
    "plate_map", "recipe_snapshot", "quote_portrait", "evidence_nugget", "do_dont",
}
PATTERN_BREAK_LAYOUTS = {
    "hook", "checklist", "warning", "quote", "stat", "steps", "comparison", "myth",
    "plate_map", "recipe_snapshot", "quote_portrait", "evidence_nugget", "do_dont",
}
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


def _normalize(text: str) -> str:
    """Lower-case, strip accents, and flatten punctuation/whitespace — so a bullet the
    model paraphrased only in accents/case/punctuation ("Proteína." vs "proteina") still
    matches its narration. Does NOT loosen grounding: the words must still be present."""
    t = unicodedata.normalize("NFKD", str(text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _is_supported(scene: dict[str, Any], text: str) -> bool:
    if not text:
        return False
    return _normalize(text) in _normalize(_supported_text(scene))


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


def has_valid_stat(scene: dict[str, Any]) -> bool:
    payload = normalize_payload(scene.get("layout_payload"))
    text = payload.get("title")
    return bool(text) and word_count(text) <= 8 and _is_supported(scene, text)


def has_valid_steps(scene: dict[str, Any]) -> bool:
    # Same shape as a checklist (2-4 supported items) — rendered as an ordered flow.
    return has_valid_bullets(scene)


def has_valid_comparison(scene: dict[str, Any]) -> bool:
    payload = normalize_payload(scene.get("layout_payload"))
    bullets = payload["bullets"]
    if len(bullets) >= 2 and all(_is_supported(scene, b) for b in bullets[:2]):
        return True
    title, body = payload.get("title"), payload.get("body")
    return bool(title) and bool(body) and _is_supported(scene, title) and _is_supported(scene, body)


def has_valid_myth(scene: dict[str, Any]) -> bool:
    payload = normalize_payload(scene.get("layout_payload"))
    title, body = payload.get("title"), payload.get("body")
    return bool(title) and bool(body) and _is_supported(scene, title) and _is_supported(scene, body)


def downgrade(scene: dict[str, Any], reason: str) -> None:
    scene["layout"] = "subtitle"
    # Clear the card payload: a downgraded scene renders as a plain subtitle (no card),
    # so a retained rich payload is dead data that QA flags as "rich payload despite
    # downgraded". Emptying it keeps the artifact consistent with the layout.
    scene["layout_payload"] = {"title": "", "body": "", "bullets": [], "cta": ""}
    add_warning(scene, reason)


def apply_pattern_break_rhythm(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Spec-strict pattern break.

    Only promote a scene if it ALREADY shipped valid checklist/warning/quote
    payload from ChatGPT (tracked in ``_proposed_layout``). Never fabricate
    overlay content from on_screen_text or caption — that produces duplicate
    or non-actionable bullets (see retention layout spec §"Python planner
    must not", "Safe Data Source Rule").
    """
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
        elif layout == "stat" and not has_valid_stat(scene):
            downgrade(scene, "Stat downgraded to subtitle: missing a supported number/short phrase in title.")
        elif layout == "steps" and not has_valid_steps(scene):
            downgrade(scene, "Steps downgraded to subtitle: missing 2-4 supported ordered steps.")
        elif layout == "comparison" and not has_valid_comparison(scene):
            downgrade(scene, "Comparison downgraded to subtitle: missing two supported sides.")
        elif layout == "myth" and not has_valid_myth(scene):
            downgrade(scene, "Myth downgraded to subtitle: missing supported myth + reality text.")
        elif layout == "plate_map" and not has_valid_bullets(scene):
            downgrade(scene, "Plate map downgraded to subtitle: missing 2-4 supported plate components.")
        elif layout == "recipe_snapshot" and not has_valid_bullets(scene):
            downgrade(scene, "Recipe snapshot downgraded to subtitle: missing 2-4 supported items.")
        elif layout == "quote_portrait" and not has_valid_quote_text(scene):
            downgrade(scene, "Quote portrait downgraded to subtitle: missing short supported quote text.")
        elif layout == "evidence_nugget" and not has_valid_stat(scene):
            downgrade(scene, "Evidence nugget downgraded to subtitle: missing a supported number/fact in title.")
        elif layout == "do_dont" and not has_valid_comparison(scene):
            downgrade(scene, "Do/Don't downgraded to subtitle: missing two supported sides (worse vs better).")

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

    # Dedup: two graphic cards with the same headline read as repetitive (e.g. two
    # scenes titled "Evita los dos extremos") — downgrade the later duplicate to subtitle.
    seen_titles: set[str] = set()
    for scene in scenes:
        if scene.get("layout") in ("subtitle", "cta"):
            continue
        title = normalize_payload(scene.get("layout_payload")).get("title", "").strip().lower()
        if not title:
            continue
        if title in seen_titles:
            downgrade(scene, f"Duplicate graphic headline downgraded to subtitle: {title!r}.")
        else:
            seen_titles.add(title)

    for scene in scenes:
        scene.pop("_proposed_layout", None)
    return scenes
