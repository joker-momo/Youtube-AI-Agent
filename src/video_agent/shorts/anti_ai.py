from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths
from video_agent.shorts.quality_config import load_generic_phrases, quality_layers_config
from video_agent.shorts.quality_hash import stable_hash
from video_agent.storage.atomic import atomic_write_json

NEW_PRODUCT_SCORE_KEYS = [
    "hook_specificity",
    "micro_tension",
    "human_naturalness",
    "visual_rhythm",
    "identity_resonance",
    "commentability",
]

QUALITY_THRESHOLDS = {
    "hook_specificity": {"pass": 75, "warn": 55},
    "micro_tension": {"pass": 70, "warn": 50},
    "human_naturalness": {"pass": 75, "warn": 55},
    "visual_rhythm": {"pass": 70, "warn": 50},
    "identity_resonance": {"pass": 70, "warn": 50},
    "commentability": {"pass": 65, "warn": 45},
}

_GENERIC_INTROS = ("hola", "bienvenid", "hoy vamos a", "en este video", "en este vídeo", "en este short")
_SHAME = ("a tu edad", "ya no puedes", "viejo", "mayor frágil", "cuerpo arruinado")


def _parse(raw: str) -> dict[str, Any]:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _invoke(fn: Callable[[str], str], prompt: str) -> str:
    return fn(prompt)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", str(text or "").lower())


def generic_phrase_density(text: str, phrases: list[str]) -> float:
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    phrase_tokens = 0
    low = str(text or "").lower()
    for phrase in phrases:
        if phrase in low:
            phrase_tokens += len(_tokens(phrase))
    return phrase_tokens / len(tokens)


def _micro_tension_count(script: dict[str, Any], retention_plan: dict[str, Any]) -> int:
    lines = list(script.get("micro_tension_lines") or [])
    beat_lines = [str(b.get("tension_line") or "") for b in retention_plan.get("retention_beats") or []]
    lines.extend(beat_lines)
    text = " ".join([str(script.get("narration") or ""), *lines]).lower()
    cues = ("pero", "aunque", "no basta", "error", "cuidado", "antes de", "mira", "sin darte cuenta")
    cue_count = sum(1 for cue in cues if cue in text)
    beat_count = len([line for line in beat_lines if line.strip()])
    return max(cue_count, min(beat_count, 2))


def _score_review(script: dict[str, Any], scenes_doc: dict[str, Any], retention_plan: dict[str, Any], channel_config: dict[str, Any]) -> tuple[dict[str, int], list[str], list[str], list[str]]:
    narration = str(script.get("narration") or "")
    hook = str(script.get("hook") or narration[:100])
    low = f"{hook} {narration}".lower()
    phrases = load_generic_phrases(channel_config)
    generic_found = [phrase for phrase in phrases if phrase in low]
    robotic: list[str] = []
    severe: list[str] = []
    if any(intro in low[:140] for intro in _GENERIC_INTROS):
        robotic.append("greeting_or_generic_intro")
        severe.append("greeting_or_generic_intro")
    density = generic_phrase_density(narration, phrases)
    if density > 0.08 and len(generic_found) >= 2:
        robotic.append("generic_phrase_density_high")
        severe.append("generic_phrase_density_high")
    scenes = list((scenes_doc or {}).get("scenes") or [])
    static_run = 0
    max_static = 0
    for scene in scenes:
        motion = str(scene.get("motion") or "none")
        if motion in {"none", "", "static"} or str(scene.get("layout") or "") in {"short_checklist"}:
            static_run += 1
            max_static = max(max_static, static_run)
        else:
            static_run = 0
    if max_static > 3:
        severe.append("too_many_static_scenes")
    first_motion = str((scenes[0] if scenes else {}).get("motion") or "")
    if scenes and first_motion not in {"push_in", "object_reveal", "face_cut", "text_pop", "crop_shift"}:
        severe.append("weak_hook_motion")
    if any(term in low for term in _SHAME):
        severe.append("identity_shame")
    tension_count = _micro_tension_count(script, retention_plan)
    total_duration = float((scenes_doc or {}).get("total_duration_sec") or sum(float(s.get("duration_sec") or 0) for s in scenes) or script.get("target_duration_sec") or 0)
    comment = str(script.get("comment_trigger") or (retention_plan.get("comment_trigger") or {}).get("question") or script.get("cta") or "")
    generic_hook = any(phrase in hook.lower() for phrase in phrases) or any(term in hook.lower() for term in ("consejos", "saludables", "recordar"))
    scores = {
        "hook_specificity": 45 if "greeting_or_generic_intro" in severe else (55 if generic_hook else (82 if len(_tokens(hook)) >= 4 else 60)),
        "micro_tension": 45 if total_duration > 20 and tension_count < 2 else min(95, 65 + tension_count * 10),
        "human_naturalness": max(30, int(88 - density * 60 - len(generic_found) * 8)),
        "visual_rhythm": 45 if max_static > 3 or "weak_hook_motion" in severe else 78,
        "identity_resonance": 35 if "identity_shame" in severe else (78 if retention_plan.get("identity_resonance") else 60),
        "commentability": 40 if not comment or "suscr" in comment.lower() else (72 if "?" in comment or "guarda" in comment.lower() else 58),
    }
    return scores, generic_found, robotic, severe


def verdict_from_scores(scores: dict[str, int], severe: list[str]) -> str:
    if severe:
        return "FAIL"
    warn_count = 0
    for key, score in scores.items():
        threshold = QUALITY_THRESHOLDS[key]
        if score < threshold["warn"]:
            return "FAIL"
        if score < threshold["pass"]:
            warn_count += 1
    return "WARN" if warn_count >= 1 else "PASS"


def run_anti_ai_review(
    long_job_dir: Path,
    short_id: str,
    short_script: dict,
    short_scenes: dict,
    retention_plan: dict,
    channel_config: dict,
    gemini_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    cfg = quality_layers_config(channel_config)
    artifact = paths.short_json_dir(long_job_dir, short_id) / paths.SHORT_ANTI_AI_REVIEW_FILE
    input_hash = stable_hash(short_script, short_scenes, retention_plan, channel_config=channel_config)
    if cfg.get("reuse_existing_artifacts") and artifact.exists():
        cached = json.loads(artifact.read_text(encoding="utf-8"))
        if cached.get("input_hash") == input_hash:
            cached["generation_mode"] = "cached"
            return cached
    scores, generic_found, robotic, severe = _score_review(short_script, short_scenes, retention_plan, channel_config)
    review = {
        "short_id": short_id,
        "verdict": verdict_from_scores(scores, severe),
        "generic_phrases": generic_found,
        "robotic_patterns": robotic,
        "over_explaining": len(_tokens(short_script.get("narration") or "")) > 125,
        "listicle_feel_risk": "high" if len((short_scenes or {}).get("scenes") or []) > 10 else "low",
        "recommended_changes": severe + robotic,
        "scores": scores,
        "input_hash": input_hash,
        "generation_mode": "deterministic",
    }
    if cfg.get("enable_llm_anti_ai_review") and gemini_fn and int(cfg.get("max_new_quality_llm_calls_per_short") or 0) > 0:
        parsed = _parse(_invoke(gemini_fn, "Review this Short for generic AI language:\n" + json.dumps(review, ensure_ascii=False)))
        if parsed.get("verdict") in {"PASS", "WARN", "FAIL"}:
            parsed.setdefault("scores", scores)
            parsed.setdefault("generic_phrases", generic_found)
            parsed.setdefault("robotic_patterns", robotic)
            parsed["input_hash"] = input_hash
            parsed["generation_mode"] = "llm"
            review = parsed
    artifact.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(artifact, review)
    return review
