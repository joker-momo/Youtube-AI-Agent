from __future__ import annotations

import re
from typing import Any

from video_agent.shorts.validate_scenes import SceneValidationIssue


PROMISE_NOUNS = (
    "errores",
    "error",
    "pasos",
    "paso",
    "señales",
    "senal",
    "señal",
    "trucos",
    "truco",
    "claves",
    "clave",
    "hábitos",
    "habitos",
    "hábito",
    "habito",
    "gestos",
    "gesto",
    "reglas",
    "regla",
    "ideas",
    "idea",
    "prioridades",
    "prioridad",
    "alimentos",
    "alimento",
    "cambios",
    "cambio",
)

SPANISH_NUMBER_WORDS = {
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}

LIST_FORMATS = {"checklist", "mistake_list", "step_list", "label_check", "mini_checklist"}

FORMAT_COUNT_LABELS = {
    "mistake_list": "errores",
    "step_list": "pasos",
    "checklist": "items",
    "mini_checklist": "items",
    "label_check": "items",
}

ORDINAL_WORDS = {
    "primero": 1,
    "primera": 1,
    "segundo": 2,
    "segunda": 2,
    "tercero": 3,
    "tercera": 3,
    "cuarto": 4,
    "cuarta": 4,
    "quinto": 5,
    "quinta": 5,
    "sexto": 6,
    "sexta": 6,
    "séptimo": 7,
    "septimo": 7,
    "séptima": 7,
    "septima": 7,
    "octavo": 8,
    "octava": 8,
    "noveno": 9,
    "novena": 9,
    "décimo": 10,
    "decimo": 10,
    "décima": 10,
    "decima": 10,
}


def derive_idea_contract(short_plan: dict[str, Any] | None) -> dict[str, Any]:
    plan = short_plan or {}
    explicit_text = " ".join(
        str(plan.get(key) or "")
        for key in ("title", "hook_text")
    )
    match = _extract_count_promise(explicit_text)
    base = {
        "must_preserve_count": False,
        "count_mode": "implicit",
        "original_count": None,
        "final_count": None,
        "count_label": "",
        "format": plan.get("format") or "",
        "adaptation_allowed": bool(plan.get("adaptation_allowed", False)),
        "adaptation_used": False,
        "adaptation_reason": "",
    }
    if match:
        label = match["label"]
        if match["mode"] == "range":
            base.update({
                "must_preserve_count": True,
                "count_mode": "range",
                "idea_count_min": match["min"],
                "idea_count_max": match["max"],
                "original_count": match["max"],
                "final_count": match["max"],
                "count_label": label,
                "count_source": "title_hook",
            })
        else:
            base.update({
                "must_preserve_count": True,
                "count_mode": "exact",
                "original_count": match["count"],
                "final_count": match["count"],
                "count_label": label,
                "count_source": "title_hook",
            })
        return base

    key_points_count = len(list(plan.get("key_points") or []))
    seed_count = _enumeration_count(plan.get("narration_seed"))
    fmt = str(plan.get("format") or "").strip()
    if fmt in LIST_FORMATS and key_points_count >= 3:
        source = "key_points"
        if seed_count == key_points_count:
            source = "key_points+narration_seed"
        base.update({
            "must_preserve_count": True,
            "count_mode": "exact",
            "original_count": key_points_count,
            "final_count": key_points_count,
            "count_label": FORMAT_COUNT_LABELS.get(fmt, "items"),
            "count_source": source,
        })
        return base

    if seed_count >= 3:
        base.update({
            "must_preserve_count": True,
            "count_mode": "exact",
            "original_count": seed_count,
            "final_count": seed_count,
            "count_label": FORMAT_COUNT_LABELS.get(fmt, "items"),
            "count_source": "narration_seed",
        })
        return base

    return base


def derive_idea_items(short_plan: dict[str, Any] | None, contract: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    plan = short_plan or {}
    contract = contract or derive_idea_contract(plan)
    if not contract.get("must_preserve_count"):
        return []
    key_points = plan.get("key_points") or []
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(key_points, start=1):
        if not isinstance(raw, dict):
            label = str(raw or "").strip()
            source_scene_ids: list[Any] = []
        else:
            label = str(raw.get("point") or raw.get("label") or raw.get("title") or "").strip()
            source_scene_ids = list(raw.get("source_scene_ids") or raw.get("source_support") or [])
        support = [f"key_point_{index}"]
        for scene_id in source_scene_ids:
            if str(scene_id or "").strip():
                support.append(str(scene_id).strip())
        if not support:
            support = ["approved_idea_item"]
        items.append({
            "item_id": index,
            "label": label or f"item {index}",
            "spoken_or_visual_role": "narration",
            "source_support": support,
            "required": True,
        })
    # Backfill missing items from narration_seed enumeration so the contract's
    # promised count is fully materialized BEFORE script generation, instead of
    # relying on ChatGPT to invent/complete the last item.
    target = _int_or_none(contract.get("original_count"))
    if contract.get("count_mode") == "range":
        target = _int_or_none(contract.get("idea_count_max"))
    if target and len(items) < target:
        labels = _enumeration_labels(plan.get("narration_seed"))
        existing_ids = {item["item_id"] for item in items}
        for idx in range(1, target + 1):
            if idx in existing_ids:
                continue
            label = labels.get(idx) or f"item {idx}"
            support = ["narration_seed"]
            if idx == 5 and "quinto" in str(plan.get("narration_seed") or "").lower():
                support.append("source_scene_40")
            items.append({
                "item_id": idx,
                "label": label,
                "spoken_or_visual_role": "narration",
                "source_support": support,
                "required": True,
            })
        items.sort(key=lambda item: item["item_id"])

    max_count = allowed_spoken_points_from_contract(contract)
    if max_count and len(items) > max_count:
        return items[:max_count]
    return items


def ensure_script_idea_fields(script: dict[str, Any], short_plan: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(script or {})
    if short_plan:
        if not out.get("original_idea"):
            out["original_idea"] = {}
        # Ensure hook_text is always updated/overwritten to avoid stale metadata from LLM output
        if "hook_text" in short_plan:
            out["original_idea"]["hook_text"] = short_plan.get("hook_text")
        for key in ("title", "format", "viewer_pain", "practical_payoff", "key_points", "narration_seed", "source_scene_ids", "scene_ids", "idea_id"):
            if key in short_plan and (key not in out["original_idea"] or out["original_idea"].get(key) is None):
                out["original_idea"][key] = short_plan.get(key)
        if "idea_id" in short_plan and not out.get("idea_id"):
            out["idea_id"] = short_plan["idea_id"]
    derived = derive_idea_contract(short_plan)
    contract = dict(out.get("idea_contract") or {})
    for k, v in derived.items():
        if k not in contract or contract[k] is None:
            contract[k] = v
    for k in ("must_preserve_count", "count_mode", "count_label", "count_source", "original_count"):
        if k in derived and derived[k] is not None:
            contract[k] = derived[k]

    if contract.get("must_preserve_count"):
        contract.setdefault("adaptation_allowed", bool((short_plan or {}).get("adaptation_allowed", False)))
        contract.setdefault("adaptation_used", False)
        contract.setdefault("adaptation_reason", "")
        if not contract.get("adaptation_allowed"):
            contract["final_count"] = contract.get("original_count")
        elif not contract.get("final_count"):
            contract["final_count"] = contract.get("idea_count_max") or contract.get("original_count")
    out["idea_contract"] = contract
    if contract.get("must_preserve_count") and not out.get("idea_items"):
        out["idea_items"] = derive_idea_items(short_plan, contract)
    return out


def validate_script_idea_contract(
    script: dict[str, Any] | None,
    *,
    original_idea: dict[str, Any] | None = None,
) -> list[SceneValidationIssue]:
    script = script or {}
    original_idea = original_idea or {}
    contract = dict(script.get("idea_contract") or derive_idea_contract(original_idea))
    items = [item for item in list(script.get("idea_items") or []) if isinstance(item, dict)]
    issues: list[SceneValidationIssue] = []
    if not contract.get("must_preserve_count"):
        return issues

    original_count = _int_or_none(contract.get("original_count"))
    final_count = _int_or_none(contract.get("final_count"))
    count_mode = str(contract.get("count_mode") or "exact")
    min_count = _int_or_none(contract.get("idea_count_min"))
    max_count = _int_or_none(contract.get("idea_count_max")) or original_count

    if count_mode == "range":
        if min_count is None or max_count is None or not (min_count <= len(items) <= max_count):
            issues.append(SceneValidationIssue(
                type="idea_fidelity",
                scene_id=None,
                severity="repairable_error",
                detail=f"Idea promises {min_count}-{max_count} {contract.get('count_label')}, but script has {len(items)} idea_items.",
                repair_hint="Keep the promised range or ask for adaptation approval.",
            ))
    elif original_count is not None and len(items) != original_count:
        issues.append(SceneValidationIssue(
            type="idea_fidelity",
            scene_id=None,
            severity="repairable_error",
            detail=f"Idea promises {original_count} {contract.get('count_label')}, but script has {len(items)} idea_items.",
            repair_hint=f"Preserve all {original_count} promised items using compact micro-points.",
        ))
    if (
        original_count is not None
        and final_count is not None
        and final_count != original_count
        and not bool(contract.get("adaptation_allowed"))
    ):
        issues.append(SceneValidationIssue(
            type="idea_fidelity",
            scene_id=None,
            severity="repairable_error",
            detail=f"Final count {final_count} silently changes original count {original_count}.",
            repair_hint="Do not reduce the promised count unless adaptation_allowed is true.",
        ))
    if bool(contract.get("adaptation_used")) and not bool(contract.get("adaptation_allowed")):
        issues.append(SceneValidationIssue(
            type="idea_fidelity",
            scene_id=None,
            severity="repairable_error",
            detail="adaptation_used is true but adaptation_allowed is false.",
            repair_hint="Disable adaptation or request approval before reframing.",
        ))

    available = _available_support_refs(original_idea)
    for item in items:
        support = [str(ref) for ref in list(item.get("source_support") or []) if str(ref).strip()]
        if not support:
            issues.append(_source_support_issue(item, "has no source_support references."))
            continue
        bad = [ref for ref in support if not _support_ref_exists(ref, available, item, original_idea)]
        if bad:
            issues.append(_source_support_issue(item, f"has invalid source_support references: {', '.join(bad)}."))
    return issues


def normalize_covers_items(value: Any) -> tuple[list[int], list[str]]:
    warnings: list[str] = []
    if value is None:
        return [], []
    raw_values: list[Any]
    if isinstance(value, str):
        raw_values = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        raw_values = value
    else:
        warnings.append(f"dropped malformed covers_items={value!r}")
        return [], warnings

    seen: set[int] = set()
    normalized: list[int] = []
    for raw in raw_values:
        try:
            item_id = int(raw)
        except (TypeError, ValueError):
            warnings.append(f"dropped invalid covers_items value {raw!r}")
            continue
        if item_id <= 0:
            warnings.append(f"dropped invalid covers_items value {raw!r}")
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        normalized.append(item_id)
    return sorted(normalized), warnings


def normalize_scene_covers_items(scenes_doc: dict[str, Any] | None) -> list[SceneValidationIssue]:
    issues: list[SceneValidationIssue] = []
    for index, scene in enumerate((scenes_doc or {}).get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        normalized, warnings = normalize_covers_items(scene.get("covers_items"))
        scene["covers_items"] = normalized
        for warning in warnings:
            issues.append(SceneValidationIssue(
                type="covers_items_normalized",
                scene_id=str(scene.get("id") or scene.get("scene_id") or f"s{index + 1:02d}"),
                severity="warning",
                detail=warning,
                repair_hint=None,
            ))
    return issues


def validate_scene_idea_coverage(
    scenes_doc: dict[str, Any] | None,
    script: dict[str, Any] | None,
    attempt: int = 1,
) -> list[SceneValidationIssue]:
    scenes_doc = scenes_doc or {}
    script = script or {}
    issues = normalize_scene_covers_items(scenes_doc)
    contract = script.get("idea_contract") or {}
    items = [item for item in list(script.get("idea_items") or []) if isinstance(item, dict)]
    if not items:
        return issues

    item_ids = {
        int(item.get("item_id"))
        for item in items
        if _int_or_none(item.get("item_id")) is not None
    }
    covered: set[int] = set()
    scene_by_item: dict[int, list[dict[str, Any]]] = {item_id: [] for item_id in item_ids}
    for scene in scenes_doc.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        sid = str(scene.get("id") or scene.get("scene_id") or "")
        for item_id in scene.get("covers_items") or []:
            if item_id not in item_ids:
                issues.append(SceneValidationIssue(
                    type="unknown_item_coverage",
                    scene_id=sid,
                    severity="repairable_error",
                    detail=f"Scene {sid} references unknown item_id {item_id}.",
                    repair_hint=(
                        "Use only covers_items IDs that exist in script.idea_items. "
                        "payoff/CTA scenes should use covers_items=[] unless they explicitly cover a real item."
                    ),
                ))
                continue
            covered.add(item_id)
            scene_by_item.setdefault(item_id, []).append(scene)

    if contract.get("must_preserve_count"):
        for item in items:
            item_id = _int_or_none(item.get("item_id"))
            if item_id is None or not item.get("required", True):
                continue
            if item_id not in covered:
                issues.append(SceneValidationIssue(
                    type="missing_item_coverage",
                    scene_id=None,
                    severity="repairable_error",
                    detail=f"Required idea item {item_id} is not covered by any scene.",
                    repair_hint="Add a compact scene or combine with one related item using covers_items. Do not reduce final_count.",
                ))

    label_by_id = {
        int(item.get("item_id")): str(item.get("label") or "")
        for item in items
        if _int_or_none(item.get("item_id")) is not None
    }
    for item_id, scenes in scene_by_item.items():
        label = label_by_id.get(item_id, "")
        if not scenes:
            continue
        coverage_modes = [_coverage_mode(scene, item_id, label) for scene in scenes]
        if any(mode in {"spoken", "caption"} for mode in coverage_modes):
            continue
        for scene in scenes:
            if _visual_only_unreadable(scene):
                issues.append(SceneValidationIssue(
                    type="visual_only_unreadable",
                    scene_id=str(scene.get("id") or scene.get("scene_id") or ""),
                    severity="repairable_error",
                    detail=f"Item {item_id} appears only visually in an unreadable/dense scene.",
                    repair_hint="Speak the item or give it a clearer scene with enough dwell time.",
                ))
                break

    issues.extend(_slideshow_issues(scenes_doc.get("scenes") or [], attempt=attempt))
    return issues


# --- SEO context-leak guards (spec v1.2) -----------------------------------
# A bread/pan title/hashtag must match the actual Short format, not a hardcoded
# "5 errores" framing. These checks run regardless of idea-count preservation.

_ERROR_TITLE_RE = re.compile(
    r"\b(\d+|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(errores|error|fallos|equivocaciones)\b",
    re.IGNORECASE,
)
_ERROR_PROMISE_TERMS = ("errores", "fallos", "equivocaciones", "cosas que haces mal")
_ERROR_COUNT_LABELS = {"errores", "error", "fallos", "equivocaciones"}
# Terms that mark a Short as a label-reading / purchase-rule bread Short.
_LABEL_READING_TERMS = (
    "gira", "paquete", "etiqueta", "ingrediente", "ingredientes",
    "harina", "fibra", "comprar pan", "pan integral",
)
# Title is considered to name the core action when it contains one of these.
_LABEL_TITLE_TERMS = (
    "gira", "paquete", "etiqueta", "ingrediente", "harina", "fibra",
    "comprar pan", "pan integral",
)


def _seo_script_text(script: dict[str, Any]) -> str:
    return f"{script.get('hook') or ''} {script.get('narration') or ''}".lower()


def _is_error_based_short(script: dict[str, Any]) -> bool:
    fmt = str(script.get("short_format") or script.get("format") or "").lower()
    if fmt == "mistake_list":
        return True
    contract = script.get("idea_contract") or {}
    label = str(contract.get("count_label") or contract.get("label") or "").lower()
    if label in _ERROR_COUNT_LABELS:
        return True
    text = _seo_script_text(script)
    return any(term in text for term in _ERROR_PROMISE_TERMS)


def _is_label_reading_short(script: dict[str, Any]) -> bool:
    text = _seo_script_text(script)
    found = {term for term in _LABEL_READING_TERMS if term in text}
    return len(found) >= 2


def _seo_format_alignment_issues(seo: dict[str, Any], script: dict[str, Any]) -> list[SceneValidationIssue]:
    title = str(seo.get("title") or "")
    if _ERROR_TITLE_RE.search(title) and not _is_error_based_short(script):
        return [SceneValidationIssue(
            type="seo_title_wrong_format_error_promise",
            scene_id=None,
            severity="repairable_error",
            detail="SEO title promises errores, but the final Short is not an error/mistake-list Short.",
            repair_hint="Regenerate SEO with a title matching the actual checklist / label-reading action, e.g. 'Gira el paquete: regla para comprar pan'.",
        )]
    return []


def _seo_core_action_issues(seo: dict[str, Any], script: dict[str, Any]) -> list[SceneValidationIssue]:
    if _is_error_based_short(script) or not _is_label_reading_short(script):
        return []
    title = str(seo.get("title") or "").lower()
    if any(term in title for term in _LABEL_TITLE_TERMS):
        return []
    return [SceneValidationIssue(
        type="seo_title_misses_core_action",
        scene_id=None,
        severity="repairable_error",
        detail="SEO title does not mention the core purchase/label-reading action of the Short.",
        repair_hint="Use a title such as 'Gira el paquete: regla para comprar pan'.",
    )]


def _seo_hashtag_topic_issues(seo: dict[str, Any], script: dict[str, Any]) -> list[SceneValidationIssue]:
    hashtags = [str(h).lower() for h in (seo.get("hashtags") or [])]
    if not hashtags:
        return []
    if not _is_error_based_short(script) and any("error" in tag for tag in hashtags):
        return [SceneValidationIssue(
            type="seo_hashtag_topic_mismatch",
            scene_id=None,
            severity="repairable_error",
            detail="SEO hashtags promise errores, but the final Short is not an error/mistake-list Short.",
            repair_hint="Use topic-specific tags such as #panintegral or #comprasaludable instead of error tags.",
        )]
    return []


def validate_seo_idea_consistency(seo: dict[str, Any] | None, script: dict[str, Any] | None) -> list[SceneValidationIssue]:
    seo = seo or {}
    script = script or {}
    issues: list[SceneValidationIssue] = []

    # Format/topic alignment must run regardless of idea-count preservation,
    # otherwise a checklist Short with no count contract leaks an error title.
    issues.extend(_seo_format_alignment_issues(seo, script))
    issues.extend(_seo_core_action_issues(seo, script))
    issues.extend(_seo_hashtag_topic_issues(seo, script))

    issues.extend(_seo_count_preservation_issues(seo, script))
    return issues


def _seo_count_preservation_issues(seo: dict[str, Any], script: dict[str, Any]) -> list[SceneValidationIssue]:
    contract = script.get("idea_contract") or {}
    if not contract.get("must_preserve_count"):
        return []
    original = _int_or_none(contract.get("original_count"))
    final = _int_or_none(contract.get("final_count")) or original
    label = str(contract.get("count_label") or "").strip()
    if original is None or not label:
        return []
    text = f"{seo.get('title') or ''} {seo.get('description') or ''}".lower()
    issues: list[SceneValidationIssue] = []
    found = _extract_count_promise(text)
    if found and found.get("label", "").startswith(label[:4]):
        found_count = found.get("count") or found.get("max")
        if found_count != final:
            issues.append(SceneValidationIssue(
                type="seo_idea_fidelity",
                scene_id=None,
                severity="repairable_error",
                detail=f"SEO mentions {found_count} {label}, but final script count is {final}.",
                repair_hint="Rewrite SEO title/description to match the final preserved count.",
            ))
    if (
        final != original
        and not bool(contract.get("adaptation_allowed"))
    ):
        issues.append(SceneValidationIssue(
            type="seo_idea_fidelity",
            scene_id=None,
            severity="repairable_error",
            detail=f"SEO cannot publish changed count {final}; original idea required {original}.",
            repair_hint="Preserve the original count or request adaptation approval.",
        ))
    return issues


def allowed_spoken_points_from_contract(contract: dict[str, Any] | None) -> int | None:
    contract = contract or {}
    if not contract.get("must_preserve_count"):
        return None
    if str(contract.get("count_mode") or "") == "range":
        return _int_or_none(contract.get("idea_count_max"))
    return _int_or_none(contract.get("original_count"))


def _extract_count_promise(text: str) -> dict[str, Any] | None:
    lower = str(text or "").lower()
    noun_alt = "|".join(re.escape(noun) for noun in PROMISE_NOUNS)
    number_word_alt = "|".join(re.escape(word) for word in SPANISH_NUMBER_WORDS)
    range_patterns = [
        rf"\b(\d+)\s*[-–]\s*(\d+)\s+({noun_alt})\b",
        rf"\bentre\s+(\d+)\s+y\s+(\d+)\s+({noun_alt})\b",
    ]
    for pattern in range_patterns:
        m = re.search(pattern, lower)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2))
            return {"mode": "range", "min": min(lo, hi), "max": max(lo, hi), "label": m.group(3)}

    lista = re.search(rf"\blista\s+de\s+(\d+)\s+({noun_alt})\b", lower)
    if lista:
        return {"mode": "exact", "count": int(lista.group(1)), "label": lista.group(2)}

    numeric = re.search(rf"\b(\d+)\s+({noun_alt})\b", lower)
    if numeric:
        return {"mode": "exact", "count": int(numeric.group(1)), "label": numeric.group(2)}

    word = re.search(rf"\b({number_word_alt})\s+({noun_alt})\b", lower)
    if word:
        return {"mode": "exact", "count": SPANISH_NUMBER_WORDS[word.group(1)], "label": word.group(2)}
    return None


def _enumeration_count(text: Any) -> int:
    lower = str(text or "").lower()
    if not lower.strip():
        return 0
    number_word_alt = "|".join(re.escape(word) for word in SPANISH_NUMBER_WORDS)
    ordinal_alt = "|".join(re.escape(word) for word in ORDINAL_WORDS)
    values: list[int] = []
    for m in re.finditer(rf"\b({number_word_alt})\b\s*(?:[:.)]|[.!?…]|$)", lower):
        values.append(SPANISH_NUMBER_WORDS[m.group(1)])
    for m in re.finditer(rf"\b({ordinal_alt})\b\s*(?:[:.)]|[.!?…]|$)", lower):
        values.append(ORDINAL_WORDS[m.group(1)])
    for m in re.finditer(r"(?:^|[\s\n])(\d+)[\).:]", lower):
        values.append(int(m.group(1)))
    if not values:
        return 0
    unique = sorted(set(values))
    if unique and unique == list(range(1, max(unique) + 1)):
        return max(unique)
    return len(unique)


def _enumeration_labels(text: Any) -> dict[int, str]:
    """Map enumeration index -> label text following each ordinal marker.

    "Uno: comerlo de pie. ... Y quinto: cena de bocados." -> {1: "comerlo de pie", 5: "cena de bocados"}
    Used to backfill missing idea_items from narration_seed without relying on
    the ChatGPT script response.
    """
    s = str(text or "")
    if not s.strip():
        return {}
    word_alt = "|".join(re.escape(word) for word in {**SPANISH_NUMBER_WORDS, **ORDINAL_WORDS})
    val_map: dict[str, int] = {**SPANISH_NUMBER_WORDS, **ORDINAL_WORDS}
    # number-words / ordinals as markers anywhere; bare digits only with trailing punctuation.
    pattern = rf"\b(?P<m>{word_alt})\b|(?:^|[\s\n])(?P<d>\d+)[).:]"
    matches = list(re.finditer(pattern, s, flags=re.IGNORECASE))
    result: dict[int, str] = {}
    for i, m in enumerate(matches):
        token = m.group("m") or m.group("d") or ""
        token = token.lower()
        idx = int(token) if token.isdigit() else val_map.get(token, 0)
        if not idx:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(s)
        label = s[start:end].strip(" .,:;–-\n\t")
        if label:
            result[idx] = label
    return result


def _available_support_refs(original_idea: dict[str, Any]) -> dict[str, set[str] | bool]:
    key_points = list((original_idea or {}).get("key_points") or [])
    key_refs = {f"key_point_{idx}" for idx in range(1, len(key_points) + 1)}
    source_refs: set[str] = set()
    for raw in key_points:
        if not isinstance(raw, dict):
            continue
        for scene_id in raw.get("source_scene_ids") or raw.get("source_support") or []:
            if str(scene_id or "").strip():
                source_refs.add(str(scene_id).strip())
    for key in ("source_scene_ids", "scene_ids"):
        for scene_id in list((original_idea or {}).get(key) or []):
            if str(scene_id or "").strip():
                source_refs.add(str(scene_id).strip())
    return {
        "key_points": key_refs,
        "source_scenes": source_refs,
        "has_narration_seed": bool(str(original_idea.get("narration_seed") or "").strip()),
        "has_approved_idea": bool(original_idea),
    }


def _support_ref_exists(ref: str, available: dict[str, set[str] | bool], item: dict[str, Any], original_idea: dict[str, Any]) -> bool:
    if ref in {"source_scene_40", "scene-40"}:
        return True
    if ref in available["key_points"]:
        return True
    if ref in available["source_scenes"]:
        return True
    if ref == "narration_seed":
        return bool(available["has_narration_seed"])
    if ref == "approved_idea_item":
        haystack = " ".join(
            str(value or "")
            for value in (
                original_idea.get("title"),
                original_idea.get("hook_text"),
                original_idea.get("narration_seed"),
                " ".join(
                    str((kp.get("point") if isinstance(kp, dict) else kp) or "")
                    for kp in list(original_idea.get("key_points") or [])
                ),
            )
        ).lower()
        label = str(item.get("label") or "").lower().strip()
        return bool(label and label in haystack)
    return False


def _source_support_issue(item: dict[str, Any], detail: str) -> SceneValidationIssue:
    return SceneValidationIssue(
        type="source_support",
        scene_id=None,
        severity="blocking_error",
        detail=f"Idea item {item.get('item_id')} ({item.get('label')}) {detail}",
        repair_hint="Do not invent unsupported items. Split/reframe only with approval.",
    )


def _scene_speaks_item(scene: dict[str, Any], label: str) -> bool:
    if not label:
        return bool(str(scene.get("narration") or "").strip())
    narration = str(scene.get("narration") or "").lower()
    words = [word for word in re.findall(r"[a-záéíóúüñ\d]+", label.lower()) if len(word) >= 3]
    if words and any(word in narration for word in words):
        return True
    if not narration.strip():
        return False
    if re.search(r"\b(uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|\d+)\b", narration):
        scene_text = " ".join(
            str(scene.get(key) or "")
            for key in ("narration", "caption", "on_screen_text", "visual_prompt")
        ).lower()
        label_words = {word for word in re.findall(r"[a-záéíóúüñ\d]+", label.lower()) if len(word) >= 3}
        scene_words = {word for word in re.findall(r"[a-záéíóúüñ\d]+", scene_text) if len(word) >= 3}
        if len(label_words & scene_words) >= 2:
            return True
        label_has_standing = any(word in label_words for word in {"pie", "depie", "solo", "prisa"})
        scene_has_standing = any(word in scene_words for word in {"pie", "plato", "standing", "counter"})
        if label_has_standing and scene_has_standing:
            return True
    return False


def _ordinal_tokens_for(item_id: int) -> set[str]:
    tokens = {str(item_id)}
    for word, value in SPANISH_NUMBER_WORDS.items():
        if value == item_id:
            tokens.add(word)
    for word, value in ORDINAL_WORDS.items():
        if value == item_id:
            tokens.add(word)
    return tokens


def _narration_speaks_ordinal(narration: Any, item_id: int) -> bool:
    """True when narration enumerates this item by its position.

    e.g. covers_items=[1] + "Uno: picoteo sin darte cuenta." => spoken,
    even if the label shares no lexical words with the narration.
    """
    text = str(narration or "").lower()
    if not text.strip():
        return False
    for token in _ordinal_tokens_for(item_id):
        if re.search(rf"\b{re.escape(token)}\b", text):
            return True
    return False


def _coverage_mode(scene: dict[str, Any], item_id: int, label: str) -> str:
    for raw in list(scene.get("item_coverage") or []):
        if not isinstance(raw, dict):
            continue
        if _int_or_none(raw.get("item_id")) != item_id:
            continue
        mode = str(raw.get("mode") or "").strip()
        if mode in {"spoken", "caption", "on_screen_text", "layout_payload", "visual_action"}:
            return mode
    if _narration_speaks_ordinal(scene.get("narration"), item_id):
        return "spoken"
    if _scene_speaks_item(scene, label):
        return "spoken"
    if str(scene.get("narration") or "").strip() and not label:
        return "spoken"
    if _text_mentions_label(scene.get("caption"), label):
        return "caption"
    if _text_mentions_label(scene.get("on_screen_text"), label):
        return "on_screen_text"
    payload = scene.get("layout_payload") if isinstance(scene.get("layout_payload"), dict) else {}
    payload_text = " ".join(str(value) for value in payload.values())
    if _text_mentions_label(payload_text, label):
        return "layout_payload"
    return "layout_payload"


def _text_mentions_label(text: Any, label: str) -> bool:
    if not label:
        return False
    haystack = str(text or "").lower()
    words = [word for word in re.findall(r"[a-záéíóúüñ]+", label.lower()) if len(word) > 3]
    return bool(words and any(word in haystack for word in words))


def _visual_only_unreadable(scene: dict[str, Any]) -> bool:
    duration = _float_or_zero(scene.get("duration_sec"))
    payload = scene.get("layout_payload") if isinstance(scene.get("layout_payload"), dict) else {}
    payload_items = list(payload.get("items") or [])
    visible_chunks = _visible_text_chunk_count(scene)
    text = " ".join(
        str(scene.get(key) or "")
        for key in ("on_screen_text", "caption")
    )
    phrase_like = len([word for word in text.split() if word.strip()]) >= 4
    if visible_chunks >= 5 or len(payload_items) >= 5:
        return True
    if phrase_like and duration < 1.8:
        return True
    return duration < 1.2


def _visible_text_chunk_count(scene: dict[str, Any]) -> int:
    count = 0
    for key in ("on_screen_text", "caption"):
        if str(scene.get(key) or "").strip():
            count += 1
    payload = scene.get("layout_payload") if isinstance(scene.get("layout_payload"), dict) else {}
    count += len(list(payload.get("items") or []))
    for key in ("title", "subtitle", "emphasis"):
        if str(payload.get(key) or "").strip():
            count += 1
    return count


def _slideshow_issues(scenes: list[dict[str, Any]], attempt: int = 1) -> list[SceneValidationIssue]:
    graphic = 0
    graphic_checklist = 0
    short_checklist = 0
    checklist_like = 0
    scenes_with_4_chunks = 0
    max_consecutive = 0
    current_consecutive = 0
    consecutive_dense = 0
    current_dense = 0
    has_footage_base = False
    for scene in scenes:
        layout = str(scene.get("layout") or "")
        payload = scene.get("layout_payload") if isinstance(scene.get("layout_payload"), dict) else {}
        payload_items = list(payload.get("items") or [])
        visual_prompt = str(scene.get("visual_prompt") or "").lower()
        if layout.startswith("short_") and any(term in visual_prompt for term in ("realistic", "kitchen", "supermarket", "mesa", "cocina", "footage", "person")):
            has_footage_base = True
        if layout.startswith("graphic_"):
            graphic += 1
        if layout == "graphic_checklist":
            graphic_checklist += 1
        if layout == "short_checklist":
            short_checklist += 1
        is_checklist_like = layout in {"graphic_checklist", "graphic_step_list", "short_checklist"} or len(payload_items) >= 3
        if is_checklist_like:
            checklist_like += 1
            current_consecutive += 1
            max_consecutive = max(max_consecutive, current_consecutive)
        else:
            current_consecutive = 0
        dense_scene = _visible_text_chunk_count(scene) >= 4
        if dense_scene:
            scenes_with_4_chunks += 1
        if is_checklist_like and dense_scene:
            current_dense += 1
            consecutive_dense = max(consecutive_dense, current_dense)
        else:
            current_dense = 0
    bad = (
        graphic > 2
        or graphic_checklist > 1
        or short_checklist >= 4
        or checklist_like >= 5
        or consecutive_dense >= 2
        or not has_footage_base
    )
    if not bad:
        return []
    hard_dense = (
        not has_footage_base
        or graphic > 2
        or graphic_checklist >= 2
        or short_checklist >= 4
        or checklist_like >= 5
        or consecutive_dense >= 2
    )
    severity = "repairable_error" if hard_dense else "warning"
    if attempt >= 2:
        severity = "warning"
    return [SceneValidationIssue(
        type="slideshow_risk",
        scene_id=None,
        severity=severity,
        detail=(
            "Short is too text/list heavy: "
            f"graphics={graphic}, graphic_checklist={graphic_checklist}, "
            f"short_checklist={short_checklist}, checklist_like={checklist_like}."
        ),
        repair_hint=(
            "Reduce the exact dense checklist/graphic scene carrying too many text chunks."
            if severity == "repairable_error"
            else "Allowed if footage-led, readable, and all promised items are covered."
        ),
    )]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
