from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths
from video_agent.shorts.quality_config import quality_layers_config
from video_agent.shorts.quality_hash import stable_hash
from video_agent.storage.atomic import atomic_write_json

ALLOWED_HOOK_PATTERNS = {
    "contradiction",
    "hidden_truth",
    "common_mistake",
    "number_promise",
    "identity_relief",
    "visual_reveal",
}

_BEAT_FUNCTIONS = ["hook", "tension", "proof", "payoff", "identity", "cta"]
_INTERRUPTS = ["zoom", "crop_shift", "object_reveal", "text_change", "face_cut", "graphic_burst"]

# Known, grammatically-tagged topic objects. Only these produce article+noun
# lines; anything else falls back to neutral templates so the planner never
# emits broken Spanish like "el acompañamientos".
_TOPIC_OBJECTS: dict[str, dict[str, str]] = {
    "tostada": {"singular": "tostada", "plural": "tostadas", "article_singular": "la", "article_plural": "las", "kind": "food_object"},
    "pan": {"singular": "pan", "plural": "panes", "article_singular": "el", "article_plural": "los", "kind": "food_object"},
    "desayuno": {"singular": "desayuno", "plural": "desayunos", "article_singular": "el", "article_plural": "los", "kind": "meal"},
    "plato": {"singular": "plato", "plural": "platos", "article_singular": "el", "article_plural": "los", "kind": "food_object"},
    "cena": {"singular": "cena", "plural": "cenas", "article_singular": "la", "article_plural": "las", "kind": "meal"},
    "compra": {"singular": "compra", "plural": "compras", "article_singular": "la", "article_plural": "las", "kind": "activity"},
    "etiqueta": {"singular": "etiqueta", "plural": "etiquetas", "article_singular": "la", "article_plural": "las", "kind": "object"},
    "sueño": {"singular": "sueño", "plural": "sueños", "article_singular": "el", "article_plural": "los", "kind": "state"},
    "rutina": {"singular": "rutina", "plural": "rutinas", "article_singular": "la", "article_plural": "las", "kind": "activity"},
    "proteína": {"singular": "proteína", "plural": "proteínas", "article_singular": "la", "article_plural": "las", "kind": "food_object"},
}

_NEUTRAL_TOPIC = {
    "singular": None, "plural": None, "article_singular": None, "article_plural": None,
    "kind": "neutral", "safe_comment_phrase": None, "safe_action_phrase": None,
}


def _tokens(text: str) -> list[str]:
    return [w.strip(".,;:¿?¡!()\"'").lower() for w in str(text or "").split()]


def safe_topic(short_plan: dict[str, Any]) -> dict[str, Any]:
    """Structured, grammar-safe topic object.

    Scans the title/hook first, then viewer pain, for a KNOWN object. Never
    guesses an article for an unknown long noun (that produced "el
    acompañamientos"); unknown topics return a neutral object instead.
    """
    for field in ("title", "hook_angle"):
        for tok in _tokens(short_plan.get(field)):
            if tok in _TOPIC_OBJECTS:
                obj = _TOPIC_OBJECTS[tok]
                return {
                    **obj,
                    "safe_action_phrase": f"{obj['article_singular']} {obj['singular']}",
                    "safe_comment_phrase": f"{obj['article_plural']} {obj['plural']}",
                }
    for tok in _tokens(short_plan.get("viewer_pain")):
        if tok in _TOPIC_OBJECTS:
            obj = _TOPIC_OBJECTS[tok]
            return {
                **obj,
                "safe_action_phrase": f"{obj['article_singular']} {obj['singular']}",
                "safe_comment_phrase": f"{obj['article_plural']} {obj['plural']}",
            }
    return dict(_NEUTRAL_TOPIC)


def _pain_short(short_plan: dict[str, Any]) -> str:
    pain = str(short_plan.get("viewer_pain") or short_plan.get("hook_angle") or "te falla la pista").strip().rstrip(".")
    words = pain.split()
    if len(words) > 8:
        pain = " ".join(words[:8])
    return pain[0].upper() + pain[1:] if pain else "Te falla la pista"


def _piece_count(short_plan: dict[str, Any]) -> str:
    import re
    text = f"{short_plan.get('title') or ''} {short_plan.get('narration_seed') or ''}"
    m = re.search(r"\d+", text)
    return m.group(0) if m else "3"


def _retention_beats(short_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic, beat-distinct, grammar-safe retention curve."""
    topic = safe_topic(short_plan)
    pain = _pain_short(short_plan)
    count = _piece_count(short_plan)
    known = topic["kind"] != "neutral"
    action = topic.get("safe_action_phrase")  # e.g. "la tostada"

    reframe = f"No siempre falla {action}." if known else "No siempre falla esto."
    comment = f"¿Cómo montas tú {action}?" if known else "¿Cómo lo haces tú?"

    # (function, tension_line, expected_viewer_question) — short lines only.
    shape = [
        ("hook", f"¿{pain}?", "¿Por qué me pasa?"),
        ("tension", reframe, "¿Entonces qué falla?"),
        ("proof", f"Son {count} piezas.", "¿Cuáles son?"),
        ("payoff", "No se comportan igual.", "¿Qué comparación importa?"),
        ("identity", "Déjalo listo antes.", "¿Cómo lo hago fácil?"),
        ("cta", comment, "¿Qué opción uso yo?"),
    ]

    beats: list[dict[str, Any]] = []
    for idx, (fn, line, question) in enumerate(shape):
        start = round(idx * 5.5, 1)
        end = 2.0 if idx == 0 else round(start + 4.0, 1)
        beats.append({
            "start_sec": 0.0 if idx == 0 else start,
            "end_sec": end,
            "function": fn,
            "tension_line": line[:60],
            "visual_interrupt": _INTERRUPTS[idx % len(_INTERRUPTS)],
            "expected_viewer_question": question,
        })
    return beats


def _comment_trigger(short_plan: dict[str, Any]) -> dict[str, Any]:
    explicit = str(short_plan.get("comment_trigger") or "").strip()
    if explicit:
        question = explicit
    else:
        topic = safe_topic(short_plan)
        if topic["kind"] != "neutral":
            question = f"¿Cómo montas tú {topic['safe_action_phrase']}?"
        else:
            question = "¿También te pasa?"
    return {
        "question": question,
        "type": str(short_plan.get("comment_trigger_type") or "personal_experience"),
    }


def _parse(raw: str) -> dict[str, Any]:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _invoke(llm_fn: Callable[..., str], kind: str, prompt: str) -> str:
    try:
        return llm_fn(prompt)
    except TypeError:
        return llm_fn(kind, prompt)


def _hook_pattern(short_plan: dict[str, Any]) -> str:
    text = " ".join(str(short_plan.get(k) or "") for k in ("hook_angle", "title", "format", "narration_seed")).lower()
    if any(ch.isdigit() for ch in text) or any(word in text for word in ("cinco", "tres", "pasos", "errores")):
        return "number_promise"
    if any(word in text for word in ("error", "mistake", "equivoc", "fallo")):
        return "common_mistake"
    if any(word in text for word in ("no ", "pero", "aunque", "contrario")):
        return "contradiction"
    if "45" in text:
        return "identity_relief"
    return "hidden_truth"


def _fallback(long_job_dir: Path, short_plan: dict[str, Any], channel_config: dict[str, Any]) -> dict[str, Any]:
    short_id = str(short_plan.get("short_id") or "short-01")
    viewer_pain = str(short_plan.get("viewer_pain") or short_plan.get("hook_angle") or "perder una pista importante").strip()
    payoff = str(short_plan.get("practical_payoff") or short_plan.get("payoff") or "mirar el detalle correcto antes de decidir").strip()
    hook_pattern = _hook_pattern(short_plan)
    beats = _retention_beats(short_plan)
    interrupts = [
        {"at_sec": float(sec), "type": _INTERRUPTS[i % len(_INTERRUPTS)], "purpose": "reset visual para sostener curiosidad"}
        for i, sec in enumerate((2.5, 5.0, 8.0, 11.0, 14.0, 18.0))
    ]
    return {
        "short_id": short_id,
        "source_long_job_id": str(short_plan.get("source_long_job_id") or long_job_dir.name),
        "hook_pattern": hook_pattern,
        "viewer_pain": viewer_pain,
        "curiosity_gap": str(short_plan.get("curiosity_gap") or f"por qué {viewer_pain} no se resuelve con la pista obvia").strip(),
        "payoff_promise": payoff,
        "retention_beats": beats,
        "pattern_interrupts": interrupts,
        "identity_resonance": {
            "avoid_shame": True,
            "affirmation": "sin culpa y con una pista práctica",
            "audience_phrase": "después de los 45",
        },
        "comment_trigger": _comment_trigger(short_plan),
        "qa": {"verdict": "PENDING"},
    }


def _valid(plan: dict[str, Any]) -> bool:
    return (
        isinstance(plan, dict)
        and str(plan.get("hook_pattern") or "") in ALLOWED_HOOK_PATTERNS
        and isinstance(plan.get("retention_beats"), list)
        and isinstance(plan.get("pattern_interrupts"), list)
        and isinstance(plan.get("identity_resonance"), dict)
        and isinstance(plan.get("comment_trigger"), dict)
    )


def build_retention_plan(
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    llm_fn: Callable[..., str] | None = None,
    *,
    source_artifacts: dict | None = None,
) -> dict[str, Any]:
    cfg = quality_layers_config(channel_config)
    short_id = str(short_plan.get("short_id") or "short-01")
    artifact = paths.short_json_dir(long_job_dir, short_id) / paths.SHORT_RETENTION_PLAN_FILE
    input_hash = stable_hash(short_plan, source_artifacts or {}, channel_config=channel_config)
    if cfg.get("reuse_existing_artifacts") and artifact.exists():
        cached = json.loads(artifact.read_text(encoding="utf-8"))
        if cached.get("input_hash") == input_hash:
            cached["generation_mode"] = "cached"
            return cached

    mode = "deterministic"
    plan = _fallback(long_job_dir, short_plan, channel_config)
    if cfg.get("enable_llm_retention_plan") and llm_fn and int(cfg.get("max_new_quality_llm_calls_per_short") or 0) > 0:
        prompt = "Return raw JSON retention_plan for this Short:\n" + json.dumps({"short_plan": short_plan, "fallback": plan}, ensure_ascii=False)
        parsed = _parse(_invoke(llm_fn, "retention_plan", prompt))
        if _valid(parsed):
            plan.update(parsed)
            mode = "llm"
    plan["input_hash"] = input_hash
    plan["generation_mode"] = mode
    artifact.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(artifact, plan)
    return plan
