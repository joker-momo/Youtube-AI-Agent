from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_QUALITY_LAYERS = {
    "enable_llm_retention_plan": False,
    "enable_llm_humanization": False,
    "enable_llm_anti_ai_review": False,
    "max_new_quality_llm_calls_per_short": 1,
    "quality_llm_timeout_sec": 20,
    "reuse_existing_artifacts": True,
}

GENERIC_PHRASE_VERSION = "generic_phrases_es_v1"
THRESHOLD_VERSION = "quality_thresholds_v1"
PROMPT_VERSION = "quality_prompts_v1"


def quality_layers_config(channel_config: dict[str, Any] | None) -> dict[str, Any]:
    shorts = ((channel_config or {}).get("shorts") or {})
    supplied = shorts.get("quality_layers") or {}
    out = dict(DEFAULT_QUALITY_LAYERS)
    out.update(supplied)
    return out


def load_generic_phrases(channel_config: dict[str, Any] | None) -> list[str]:
    shorts = ((channel_config or {}).get("shorts") or {})
    configured = shorts.get("generic_phrases_es")
    if isinstance(configured, list):
        return [str(item).strip().lower() for item in configured if str(item).strip()]
    data_path = Path(__file__).with_name("data") / "generic_phrases_es.json"
    try:
        return [str(item).strip().lower() for item in json.loads(data_path.read_text(encoding="utf-8")) if str(item).strip()]
    except Exception:
        return []


def quality_fingerprint(channel_config: dict[str, Any] | None) -> dict[str, Any]:
    phrases = load_generic_phrases(channel_config)
    return {
        "quality_layers": quality_layers_config(channel_config),
        "generic_phrase_version": GENERIC_PHRASE_VERSION,
        "generic_phrases": phrases,
        "threshold_version": THRESHOLD_VERSION,
        "prompt_version": PROMPT_VERSION,
    }
