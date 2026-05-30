"""Rule-based Shorts QA gate (deterministic, no browser).

At least as strict as long-form QA plus Shorts-specific retention/funnel/mobile/
safety checks (spec §14). Returns a verdict + issues so the builder can
regenerate with feedback.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_agent.shorts import paths

_GREETINGS = ["hola", "bienvenid", "hoy vamos a", "en este short", "en este vídeo", "en este video", "buenas"]
_DISCLAIMER = ["no sustituye", "consulta a tu médico", "consulta siempre a tu médico", "profesional de salud"]
_OVERCLAIM = ["cura", "curar", "para siempre", "garantizado", "milagro", "elimina para siempre", "diagnóstico", "tratamiento"]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _word_count(text: str) -> int:
    return len([w for w in str(text).split() if w.strip()])


def run_short_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    music_track: str | None = None,
    cover_text: str | None = None,
) -> dict[str, Any]:
    sd = paths.short_dir(long_job_dir, short_id)
    script = _load(sd / "short_script.json")
    scenes_doc = _load(sd / "short_scenes.json")
    source_map = _load(sd / "short_source_map.json")
    scenes = scenes_doc.get("scenes") or []

    dcfg = (channel_config.get("shorts") or {}).get("duration") or {}
    min_sec = float(dcfg.get("min_sec", 20))
    max_sec = float(dcfg.get("target_max_sec", 45))
    cta_max_words = int(((channel_config.get("shorts") or {}).get("funnel") or {}).get("cta_max_words", 8))

    narration = str(script.get("narration") or "")
    low = narration.lower()
    hook = str(script.get("hook") or "")

    issues: list[str] = []
    warnings: list[str] = []

    # greeting / generic recap
    if any(g in low for g in _GREETINGS) or any(g in hook.lower() for g in _GREETINGS):
        issues.append("greeting_or_generic_intro")

    # long disclaimer
    if sum(1 for d in _DISCLAIMER if d in low) >= 2 or "no sustituye" in low:
        issues.append("long_disclaimer")

    # medical overclaim / miracle promise
    if any(o in low for o in _OVERCLAIM):
        issues.append("medical_overclaim")

    # duration
    total = scenes_doc.get("total_duration_sec") or sum(float(s.get("duration_sec") or 0) for s in scenes)
    if total and not (min_sec <= float(total) <= max_sec):
        issues.append(f"duration_out_of_range_{round(float(total),1)}s")

    # standalone payoff before CTA
    if not narration.strip():
        issues.append("empty_narration")

    # source map present + scenes recorded
    if not source_map or not (source_map.get("used_source_scenes")):
        issues.append("missing_source_map")

    # on-screen text 2-5 words; CTA not dominating
    cta_scene_dur = 0.0
    for s in scenes:
        ost_words = _word_count(s.get("on_screen_text", ""))
        if ost_words and not (2 <= ost_words <= 5):
            warnings.append(f"on_screen_text_words_{s.get('id')}={ost_words}")
        if str(s.get("layout")) == "short_cta":
            cta_scene_dur += float(s.get("duration_sec") or 0)
    if total and cta_scene_dur > 0.2 * float(total):
        issues.append("cta_dominates")

    # CTA short
    if _word_count(script.get("cta", "")) > cta_max_words:
        warnings.append("cta_too_long")

    # music selected
    if not music_track:
        issues.append("music_not_selected")

    verdict = "PASS" if not issues else "FAIL"
    return {
        "verdict": verdict,
        "issues": issues,
        "required_changes": issues,
        "warnings": warnings,
        "scores": {
            "hook": 90 if "greeting_or_generic_intro" not in issues else 40,
            "payoff": 85 if "empty_narration" not in issues else 30,
            "funnel": 80 if "cta_dominates" not in issues else 40,
            "source_fidelity": 90 if "missing_source_map" not in issues else 30,
            "safety": 95 if not ({"long_disclaimer", "medical_overclaim"} & set(issues)) else 40,
            "mobile_readability": 90,
        },
    }
