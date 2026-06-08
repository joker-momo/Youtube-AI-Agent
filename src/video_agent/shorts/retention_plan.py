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


def deterministic_repair_retention_plan(plan: dict[str, Any], short_plan: dict[str, Any]) -> dict[str, Any]:
    # 1. Grammar repairs across all text fields in plan
    def repair_text(text: str) -> str:
        if not isinstance(text, str):
            return text
        # Spanish grammar fixes
        import re
        replacements = [
            (r"\b[eE]l acompañamientos\b", "los acompañamientos"),
            (r"\b[lL]a acompañamientos\b", "los acompañamientos"),
            (r"\b[lL]a pan\b", "el pan"),
            (r"\b[eE]l tostada\b", "la tostada"),
        ]
        for pattern, repl in replacements:
            text = re.sub(pattern, repl, text)
        
        # Suffix completion for truncated hook lines/sentences
        completions = {
            "bu": "bueno",
            "sa": "sano",
            "integ": "integral",
            "acompaña": "acompañamiento",
            "nutri": "nutritivo",
            "saluda": "saludable",
            "diabe": "diabetes",
            "sac": "sacia",
            "tost": "tostada",
        }
        words = text.split()
        if words:
            last_word = words[-1].lower().strip(".,;:¿?¡!()\"'")
            for prefix, full in completions.items():
                if last_word == prefix:
                    punctuation = words[-1][len(last_word):]
                    leading_punct = words[-1][:-len(last_word)] if words[-1].startswith(("¿", "¡")) else ""
                    words[-1] = leading_punct + full + punctuation
                    text = " ".join(words)
                    break
        return text

    # Recursively repair all strings in the plan dict
    def repair_dict(d: Any) -> Any:
        if isinstance(d, dict):
            return {k: repair_dict(v) for k, v in d.items()}
        elif isinstance(d, list):
            return [repair_dict(item) for item in d]
        elif isinstance(d, str):
            return repair_text(d)
        return d

    plan = repair_dict(plan)

    # 2. Awkward comment trigger repair
    comment_trigger = plan.get("comment_trigger") or {}
    q = comment_trigger.get("question") or ""
    
    # Determine topic keywords
    text_context = " ".join([
        str(short_plan.get("title") or ""),
        str(short_plan.get("hook_angle") or ""),
        str(short_plan.get("viewer_pain") or ""),
        str(short_plan.get("practical_payoff") or ""),
        str(short_plan.get("narration_seed") or ""),
    ]).lower()
    
    is_bread_shopping = "pan" in text_context and ("compra" in text_context or "etiqueta" in text_context or "frontal" in text_context or "ingredientes" in text_context or "paquete" in text_context)
    is_toast_assembly = "tostada" in text_context or ("pan" in text_context and ("desayuno" in text_context or "monta" in text_context or "sacia" in text_context))

    is_awkward = (
        "montas tú la compra" in q.lower()
        or "montas tú la acompañamientos" in q.lower()
        or "acompañamientos" in q.lower()
        or q == ""
    )

    if is_awkward or is_bread_shopping or is_toast_assembly:
        # Preferred options
        bread_shopping_options = [
            "¿Tantos panes? Mira esto.",
            "Gira el paquete.",
            "No mires solo el frontal.",
            "¿Tú qué miras primero?",
            "¿También giras el paquete?",
        ]
        toast_assembly_options = [
            "¿Pan y hambre otra vez?",
            "No siempre falla el pan.",
            "¿Cómo montas tú la tostada?",
        ]
        neutral_fallbacks = [
            "No siempre falla esto.",
            "¿Tú qué miras primero?",
            "¿También te pasa?",
        ]

        if is_bread_shopping:
            chosen = "¿También giras el paquete?" if "paquete" in text_context or "gira" in text_context else "No mires solo el frontal."
            if chosen not in bread_shopping_options:
                chosen = bread_shopping_options[0]
            comment_trigger["question"] = chosen
        elif is_toast_assembly:
            chosen = "¿Cómo montas tú la tostada?" if "tostada" in text_context or "montas" in text_context else "No siempre falla el pan."
            if chosen not in toast_assembly_options:
                chosen = toast_assembly_options[0]
            comment_trigger["question"] = chosen
        elif is_awkward:
            comment_trigger["question"] = neutral_fallbacks[0]

    return plan


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

    plan = deterministic_repair_retention_plan(plan, short_plan)

    plan["input_hash"] = input_hash
    plan["generation_mode"] = mode
    artifact.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(artifact, plan)
    return plan
