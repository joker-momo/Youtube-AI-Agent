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
    beats = []
    for idx, fn in enumerate(_BEAT_FUNCTIONS):
        start = round(idx * 5.5, 1)
        end = 2.0 if idx == 0 else round(start + 4.0, 1)
        beats.append({
            "start_sec": 0.0 if idx == 0 else start,
            "end_sec": end,
            "function": fn,
            "tension_line": str(short_plan.get("hook_angle") or short_plan.get("narration_seed") or payoff)[:140],
            "visual_interrupt": _INTERRUPTS[idx % len(_INTERRUPTS)],
            "expected_viewer_question": "¿Qué tendría que mirar ahora?",
        })
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
        "comment_trigger": {
            "question": str(short_plan.get("comment_trigger") or "¿También te pasaba esto al comprar?").strip(),
            "type": str(short_plan.get("comment_trigger_type") or "personal_experience"),
        },
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
