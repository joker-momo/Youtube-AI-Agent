from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths
from video_agent.shorts.quality_config import load_generic_phrases, quality_layers_config
from video_agent.shorts.quality_hash import stable_hash
from video_agent.storage.atomic import atomic_write_json

_DELIVERY_STYLES = ("warm_direct", "practical_urgent", "calm_reassuring", "curious_reveal")


def _parse(raw: str) -> dict[str, Any]:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _invoke(llm_fn: Callable[..., str], kind: str, prompt: str) -> str:
    try:
        return llm_fn(prompt)
    except TypeError:
        return llm_fn(kind, prompt)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", str(text or "").lower())


def detect_generic_phrases(text: str, channel_config: dict[str, Any]) -> list[str]:
    low = str(text or "").lower()
    return [phrase for phrase in load_generic_phrases(channel_config) if phrase and phrase in low]


def _count_markers(text: str) -> int:
    low = str(text or "").lower()
    markers = re.findall(r"\b(uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\s*:", low)
    numeric = re.findall(r"(?:^|[\s\n])\d+[\).:]", str(text or ""))
    return len(markers) + len(numeric)


def _rewrite_preserves_contract(original: str, rewritten: str, script: dict[str, Any]) -> bool:
    contract = (script or {}).get("idea_contract") or {}
    if contract.get("must_preserve_count"):
        expected = contract.get("final_count") or contract.get("original_count")
        try:
            expected_int = int(expected)
        except (TypeError, ValueError):
            expected_int = 0
        if expected_int:
            return _count_markers(rewritten) == expected_int or _count_markers(original) == _count_markers(rewritten)
    for item in (script or {}).get("idea_items") or []:
        if item.get("required") and str(item.get("label") or "").lower() not in rewritten.lower():
            return False
    unsafe = ("cura", "curar", "garantizado", "milagro", "diagnóstico", "tratamiento")
    return not any(term in rewritten.lower() for term in unsafe)


def _emphasis_map(narration: str, retention_plan: dict[str, Any]) -> list[dict[str, Any]]:
    phrases: list[str] = []
    for beat in retention_plan.get("retention_beats") or []:
        line = str(beat.get("tension_line") or "").strip()
        if line:
            phrases.append(line)
    first_sentence = re.split(r"[.!?¡¿]+", str(narration or "").strip())[0].strip()
    if first_sentence:
        phrases.insert(0, first_sentence)
    out = []
    for phrase in phrases[:5]:
        out.append({"phrase": phrase[:90], "emphasis": "strong" if len(out) == 0 else "medium", "pause_after_ms": 160 if len(out) == 0 else 120})
    return out


def build_spoken_humanization(
    long_job_dir: Path,
    short_id: str,
    short_script: dict,
    retention_plan: dict,
    channel_config: dict,
    llm_fn: Callable[..., str] | None = None,
) -> dict[str, Any]:
    cfg = quality_layers_config(channel_config)
    artifact = paths.short_json_dir(long_job_dir, short_id) / paths.SHORT_HUMANIZATION_FILE
    input_hash = stable_hash(short_script, retention_plan, channel_config=channel_config)
    if cfg.get("reuse_existing_artifacts") and artifact.exists():
        cached = json.loads(artifact.read_text(encoding="utf-8"))
        if cached.get("input_hash") == input_hash:
            cached["generation_mode"] = "cached"
            return cached

    narration = str(short_script.get("narration") or "")
    found = detect_generic_phrases(narration, channel_config)
    doc: dict[str, Any] = {
        "short_id": short_id,
        "delivery_style": "warm_direct",
        "emphasis_map": _emphasis_map(narration, retention_plan),
        "fragmentation_notes": ["Divide las frases largas con pausas naturales."],
        "forbidden_robotic_phrases_found": found,
        "tts_notes": {"preferred_speed_delta": 0.0, "avoid_flat_delivery": True},
        "input_hash": input_hash,
        "generation_mode": "deterministic",
    }
    if cfg.get("enable_llm_humanization") and llm_fn and int(cfg.get("max_new_quality_llm_calls_per_short") or 0) > 0:
        prompt = "Return raw JSON spoken_humanization. rewritten_narration is advisory only:\n" + json.dumps({"script": short_script, "retention_plan": retention_plan}, ensure_ascii=False)
        parsed = _parse(_invoke(llm_fn, "spoken_humanization", prompt))
        if parsed:
            style = str(parsed.get("delivery_style") or doc["delivery_style"])
            if style in _DELIVERY_STYLES:
                doc["delivery_style"] = style
            rewrite = str(parsed.get("rewritten_narration") or "").strip()
            if rewrite:
                if _rewrite_preserves_contract(narration, rewrite, short_script):
                    doc["rewritten_narration"] = rewrite
                else:
                    doc["rewrite_discarded"] = True
            doc["generation_mode"] = "llm"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(artifact, doc)
    return doc
