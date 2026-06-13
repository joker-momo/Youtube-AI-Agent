"""Deterministic hook candidate lab for Shorts.

Generates local template candidates, scores them through candidate_scorer, and
selects a safe hook without LLM/browser access.
"""
from __future__ import annotations

import re
from typing import Any

from video_agent.shorts.candidate_scorer import score_hook_candidate

HOOK_ARCHETYPES = [
    "proof_first",
    "common_mistake",
    "contradiction",
    "practical_warning",
    "myth_break",
    "identity_tension",
]

_FORBIDDEN = (
    "esto te está matando",
    "esto te esta matando",
    "la industria no quiere",
    "destruye tu salud",
    "milagro",
    "secreto que nadie",
)


def _text(plan: dict[str, Any], key: str) -> str:
    return str(plan.get(key) or "").strip()


def _topic_text(short_plan: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "format", "hook_angle", "viewer_pain", "curiosity_gap", "topic_family", "narration_seed"):
        parts.append(_text(short_plan, key))
    for item in short_plan.get("key_points") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("point") or ""))
        else:
            parts.append(str(item))
    return " ".join(parts).lower()


def _is_nutrition_label_topic(short_plan: dict[str, Any]) -> bool:
    text = _topic_text(short_plan)
    return any(term in text for term in ("pan", "integral", "ingrediente", "etiqueta", "yogur", "azúcar", "azucar", "supermercado", "nutrition", "label"))


def _vars(short_plan: dict[str, Any]) -> dict[str, str]:
    if _is_nutrition_label_topic(short_plan):
        return {
            "surface_cue": "el color",
            "proof_object": "el primer ingrediente",
            "common_belief": "El pan marrón",
            "desired_outcome": "que sea integral",
            "object": "pan integral",
            "audience_action": "compras pan integral",
            "identity": "compras con prisa",
        }
    title = _text(short_plan, "title").lower()
    obj = "este hábito"
    if "sueño" in title or "dormir" in title:
        obj = "tu rutina de noche"
    elif "carga mental" in title or "estrés" in title or "estres" in title:
        obj = "tu lista mental"
    return {
        "surface_cue": "lo más visible",
        "proof_object": "el detalle práctico",
        "common_belief": "Lo saludable",
        "desired_outcome": "que te ayude",
        "object": obj,
        "audience_action": "quieres hacerlo mejor",
        "identity": "vas con poco tiempo",
    }


def _dedupe(items: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(r"\s+", " ", item["hook"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _raw_candidates(short_plan: dict[str, Any]) -> list[dict[str, str]]:
    v = _vars(short_plan)
    candidates = [
        ("proof_first", f"No mires {v['surface_cue']}. Mira {v['proof_object']}."),
        ("contradiction", f"{v['common_belief']} no siempre significa {v['desired_outcome']}."),
        ("practical_warning", f"Antes de comprar {v['object']}, revisa {v['proof_object']}."),
        ("proof_first", f"Este detalle cambia cómo eliges {v['object']}."),
        ("common_mistake", f"{v['object']}: el error está en {v['proof_object']}."),
        ("practical_warning", f"Si {v['audience_action']}, revisa {v['proof_object']} primero."),
        ("myth_break", f"La palabra saludable no basta. Mira {v['proof_object']}."),
        ("identity_tension", f"Si {v['identity']}, este detalle te ahorra un error."),
        ("contradiction", f"Lo que parece mejor puede fallar en {v['proof_object']}."),
        ("proof_first", f"El detalle clave está en {v['proof_object']}."),
        ("common_mistake", f"No elijas por el envase. Elige por {v['proof_object']}."),
        ("myth_break", f"El truco no es el color: es {v['proof_object']}."),
    ]
    return _dedupe([{"hook_type": hook_type, "hook": hook} for hook_type, hook in candidates])


def _metrics(candidate: dict[str, str], short_plan: dict[str, Any]) -> dict[str, int]:
    hook = candidate["hook"].lower()
    hook_type = candidate["hook_type"]
    proof_terms = ("ingrediente", "etiqueta", "detalle", "envase", "color")
    has_proof = any(term in hook for term in proof_terms)
    clickbait = 2 if not any(term in hook for term in _FORBIDDEN) else 9
    clarity = 9 if len(hook.split()) <= 9 else 8
    curiosity = 8 if hook_type in {"proof_first", "contradiction", "myth_break"} else 7
    tension = 8 if hook_type in {"common_mistake", "contradiction", "identity_tension"} else 7
    trust = 9 if clickbait < 8 else 4
    fidelity = 9 if has_proof or _is_nutrition_label_topic(short_plan) else 8
    return {
        "clarity_2s": clarity,
        "curiosity_gap": curiosity,
        "emotional_tension": tension,
        "trust_fit_45plus": trust,
        "clickbait_risk": clickbait,
        "source_fidelity": fidelity,
    }


def build_hook_lab(
    short_plan: dict[str, Any],
    source_artifacts: dict | None = None,
    retention_plan: dict | None = None,
    channel_config: dict | None = None,
) -> dict[str, Any]:
    scored = []
    warnings: list[str] = []
    for raw in _raw_candidates(short_plan):
        scored.append(score_hook_candidate({**raw, **_metrics(raw, short_plan)}, short_plan))

    if len(scored) < 8:
        warnings.append("hook_candidate_count_below_target")

    eligible = [item for item in scored if not item.get("reject")]
    if not eligible:
        warnings.append("all_hook_candidates_rejected")
        eligible = scored

    selected = sorted(
        eligible,
        key=lambda item: (-float(item.get("score") or 0), HOOK_ARCHETYPES.index(item["hook_type"]) if item.get("hook_type") in HOOK_ARCHETYPES else 99, item["hook"]),
    )[0]

    return {
        "selected_hook": selected["hook"],
        "selected_hook_type": selected["hook_type"],
        "candidates": scored,
        "warnings": warnings,
    }
