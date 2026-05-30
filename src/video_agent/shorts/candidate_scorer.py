"""Score Short candidates for standalone retention/funnel potential.

Heuristic, deterministic scoring (no LLM) so planning is fast and testable.
Weights and penalties follow spec §10.
"""
from __future__ import annotations

import re
from typing import Any

_PAIN_WORDS = [
    "dolor", "duele", "cuesta", "miedo", "cansancio", "cansado", "fatiga",
    "no puedes", "no logras", "insomnio", "estrés", "estres", "ansiedad",
    "bajón", "bajon", "te levantas cansado", "no descansas", "rígido", "rigido",
]
_ACTION_WORDS = [
    "haz", "prueba", "evita", "empieza", "marca", "reduce", "elige", "camina",
    "respira", "ajusta", "mueve", "muévete", "muevete", "apaga", "cambia", "usa",
]
_MISTAKE_WORDS = ["error", "errores", "no hagas", "deja de", "mito", "en realidad", "no es"]
_PREV_CONTEXT = [
    "como vimos", "como dijimos", "punto anterior", "antes hablamos", "paso anterior",
    "en el punto", "anteriormente", "como mencioné", "como mencione", "siguiente paso",
    "paso siguiente",
]
_DISCLAIMER = [
    "no sustituye", "consulta a tu médico", "consulta a tu medico", "informativo",
    "profesional de salud", "antes de cualquier cambio", "no es un consejo médico",
]
_MEDICAL_RISK = ["cura", "curar", "diagnóstico", "diagnostico", "tratamiento", "medicamento", "dosis"]
_GENERIC = ["bienestar en general", "en este vídeo", "en este video", "hoy vamos a", "hablar de"]

_NUM_RE = re.compile(r"\b\d+\b")

WEIGHTS = {
    "pain_clarity": 0.25,
    "standalone_value": 0.20,
    "hook_strength": 0.20,
    "practical_action": 0.15,
    "funnel_fit": 0.10,
    "visual_potential": 0.10,
}


def _has_any(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def _count_any(text: str, words: list[str]) -> int:
    return sum(1 for w in words if w in text)


def _first_sentence(narration: str) -> str:
    parts = re.split(r"[.\n!?]", narration.strip(), maxsplit=1)
    return parts[0].strip() if parts else narration.strip()


def score_candidate(candidate: dict, channel_config: dict) -> dict[str, Any]:
    narration = str(candidate.get("narration") or "")
    low = narration.lower()
    first = _first_sentence(low)
    visual = str(candidate.get("visual_prompt") or "").lower()

    pain_clarity = min(100, 40 + 30 * _count_any(low, _PAIN_WORDS))
    standalone_value = 80
    if _has_any(low, _PREV_CONTEXT):
        standalone_value = 30
    hook_strength = 40
    if "?" in first or _NUM_RE.search(first):
        hook_strength += 35
    if _has_any(first, _PAIN_WORDS) or _has_any(first, _MISTAKE_WORDS):
        hook_strength += 20
    hook_strength = min(100, hook_strength)
    practical_action = min(100, 35 + 25 * _count_any(low, _ACTION_WORDS))
    funnel_fit = 70 if _has_any(low, _ACTION_WORDS) else 50
    visual_potential = 40
    if visual and "generic" not in visual:
        visual_potential += 30
    if "vertical" in visual:
        visual_potential += 15
    visual_potential = min(100, visual_potential)

    components = {
        "pain_clarity": pain_clarity,
        "standalone_value": standalone_value,
        "hook_strength": hook_strength,
        "practical_action": practical_action,
        "funnel_fit": funnel_fit,
        "visual_potential": visual_potential,
    }
    score = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)

    penalties: list[str] = []
    if _has_any(low, _PREV_CONTEXT):
        score -= 20
        penalties.append("requires_previous_context")
    if _count_any(low, _DISCLAIMER) >= 2 or "no sustituye" in low:
        score -= 20
        penalties.append("disclaimer_heavy")
    if not _has_any(low, _ACTION_WORDS):
        score -= 15
        penalties.append("no_practical_payoff")
    if _has_any(low, _MEDICAL_RISK):
        score -= 15
        penalties.append("medical_risk")
    if not visual or "generic" in visual:
        score -= 10
        penalties.append("visually_generic")

    final = round(max(0.0, min(100.0, score)), 1)
    return {
        **candidate,
        "components": components,
        "penalties": penalties,
        "final_score": final,
        "tier": classify(final),
    }


def classify(score: float) -> str:
    if score >= 75:
        return "strong"
    if score >= 65:
        return "acceptable"
    return "reject"
