"""Shorts QA — dual gate (spec v6 §2.5 + §13).

Pipeline:

1. ``_run_rule_qa`` (deterministic, no LLM) catches hard violations cheaply:
   greeting, long disclaimer, medical overclaim, duration, missing source map,
   etc. If the rule gate FAILS, we short-circuit and skip the LLM call.
2. ``_run_gemini_qa`` (LLM) is the final verdict for everything else:
   layout choices, source fidelity, mobile readability, funnel quality.

This matches spec v6 §2.5 (Gemini is the QA gate) while keeping cheap rule
checks first per §13 (which doesn't forbid pre-filtering).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths, prompts, validate_scenes
from video_agent.shorts.llm import LLMCallLog, log_llm_call

LLM_PROVIDER = "gemini"

_GREETINGS = ["hola", "bienvenid", "hoy vamos a", "en este short",
              "en este vídeo", "en este video", "buenas"]
_DISCLAIMER = ["no sustituye", "consulta a tu médico",
               "consulta siempre a tu médico", "profesional de salud"]
_OVERCLAIM = ["cura", "curar", "para siempre", "garantizado", "milagro",
              "elimina para siempre", "diagnóstico", "tratamiento"]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _word_count(text: str) -> int:
    return len([w for w in str(text).split() if w.strip()])


def _run_rule_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    music_track: str | None,
) -> dict[str, Any]:
    sd = paths.short_dir(long_job_dir, short_id)
    script = _load(paths.resolve_short_json(sd, paths.SHORT_SCRIPT_FILE))
    scenes_doc = _load(paths.resolve_short_json(sd, paths.SHORT_SCENES_FILE))
    source_map = _load(paths.resolve_short_json(sd, paths.SHORT_SOURCE_MAP_FILE))
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

    if any(g in low for g in _GREETINGS) or any(g in hook.lower() for g in _GREETINGS):
        issues.append("greeting_or_generic_intro")
    if sum(1 for d in _DISCLAIMER if d in low) >= 2 or "no sustituye" in low:
        issues.append("long_disclaimer")
    if any(o in low for o in _OVERCLAIM):
        issues.append("medical_overclaim")

    total = scenes_doc.get("total_duration_sec") or sum(
        float(s.get("duration_sec") or 0) for s in scenes
    )
    if total and not (min_sec <= float(total) <= max_sec):
        issues.append(f"duration_out_of_range_{round(float(total),1)}s")
    if not narration.strip():
        issues.append("empty_narration")
    if not source_map or not (source_map.get("used_source_scenes")):
        issues.append("missing_source_map")

    cta_scene_dur = 0.0
    for s in scenes:
        ost_words = _word_count(s.get("on_screen_text", ""))
        if ost_words and not (2 <= ost_words <= 5):
            warnings.append(f"on_screen_text_words_{s.get('id')}={ost_words}")
        if str(s.get("layout")) == "short_cta":
            cta_scene_dur += float(s.get("duration_sec") or 0)
    if total and cta_scene_dur > 0.2 * float(total):
        issues.append("cta_dominates")

    if _word_count(script.get("cta", "")) > cta_max_words:
        warnings.append("cta_too_long")
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
        "provider": "rule_based",
    }


def _parse_gemini(raw: str) -> dict:
    from video_agent.operator import extract_json_objects
    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _run_gemini_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    gemini_fn: Callable[[str], str],
    attempt: int,
) -> dict[str, Any]:
    sd = paths.short_dir(long_job_dir, short_id)
    script = _load(paths.resolve_short_json(sd, paths.SHORT_SCRIPT_FILE))
    scenes_doc = _load(paths.resolve_short_json(sd, paths.SHORT_SCENES_FILE))
    source_map = _load(paths.resolve_short_json(sd, paths.SHORT_SOURCE_MAP_FILE))
    prompt = prompts.gemini_qa_prompt(channel_config, script, scenes_doc, source_map)
    log_llm_call(LLMCallLog(
        task="short_qa", provider=LLM_PROVIDER, short_id=short_id,
        attempt=attempt,
        input_artifacts=["short_script.json", "short_scenes.json", "short_source_map.json"],
        output_artifact="short_qa.json",
    ))
    raw = gemini_fn(prompt)
    parsed = _parse_gemini(raw) or {}
    verdict = str(parsed.get("verdict", "")).upper() or "FAIL"
    if verdict not in ("PASS", "FAIL"):
        verdict = "FAIL"
    return {
        "verdict": verdict,
        "issues": list(parsed.get("issues") or []),
        "required_changes": list(parsed.get("required_changes") or []),
        "warnings": list(parsed.get("warnings") or []),
        "scores": dict(parsed.get("scores") or {}),
        "provider": LLM_PROVIDER,
    }


def run_short_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    music_track: str | None = None,
    cover_text: str | None = None,
    gemini_fn: Callable[[str], str] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Dual-gate Shorts QA.

    1. Run cheap rule checks. If FAIL, return without calling Gemini.
    2. Otherwise, call Gemini for the final verdict (spec v6 §2.5).
    3. If no ``gemini_fn`` is provided, return the rule verdict as-is
       (test/no-browser path).
    """
    rule = _run_rule_qa(long_job_dir, short_id, channel_config, music_track=music_track)
    if rule["verdict"] == "FAIL":
        return rule
    if gemini_fn is None:
        return rule
    gemini = _run_gemini_qa(long_job_dir, short_id, channel_config,
                            gemini_fn=gemini_fn, attempt=attempt)
    # Merge rule warnings (mostly soft) into Gemini's output for diagnostics.
    merged_warnings = sorted(set(gemini["warnings"] + rule["warnings"]))
    out = dict(gemini)
    out["warnings"] = merged_warnings
    return out


def _run_rule_script_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    music_track: str | None,
) -> dict[str, Any]:
    sd = paths.short_dir(long_job_dir, short_id)
    script = _load(paths.resolve_short_json(sd, paths.SHORT_SCRIPT_FILE))
    source_map = _load(paths.resolve_short_json(sd, paths.SHORT_SOURCE_MAP_FILE))

    cta_max_words = int(((channel_config.get("shorts") or {}).get("funnel") or {}).get("cta_max_words", 8))

    narration = str(script.get("narration") or "")
    low = narration.lower()
    hook = str(script.get("hook") or "")

    issues: list[str] = []
    warnings: list[str] = []

    if any(g in low for g in _GREETINGS) or any(g in hook.lower() for g in _GREETINGS):
        issues.append("greeting_or_generic_intro")
    if sum(1 for d in _DISCLAIMER if d in low) >= 2 or "no sustituye" in low:
        issues.append("long_disclaimer")
    if any(o in low for o in _OVERCLAIM):
        issues.append("medical_overclaim")
    if not narration.strip():
        issues.append("empty_narration")
    word_budget_issue = validate_scenes.validate_script_word_budget(script)
    if word_budget_issue:
        issues.append(word_budget_issue.type)
        warnings.append(word_budget_issue.detail)
    checklist_issue = validate_scenes.validate_script_checklist_point_cap(script)
    if checklist_issue:
        issues.append(checklist_issue.type)
        warnings.append(checklist_issue.detail)
    if not source_map or not (source_map.get("used_source_scenes")):
        issues.append("missing_source_map")

    if _word_count(script.get("cta", "")) > cta_max_words:
        warnings.append("cta_too_long")
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
            "source_fidelity": 90 if "missing_source_map" not in issues else 30,
            "safety": 95 if not ({"long_disclaimer", "medical_overclaim"} & set(issues)) else 40,
        },
        "provider": "rule_based",
    }


def _run_gemini_script_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    gemini_fn: Callable[[str], str],
    attempt: int,
) -> dict[str, Any]:
    sd = paths.short_dir(long_job_dir, short_id)
    script = _load(paths.resolve_short_json(sd, paths.SHORT_SCRIPT_FILE))
    source_map = _load(paths.resolve_short_json(sd, paths.SHORT_SOURCE_MAP_FILE))
    prompt = prompts.gemini_script_qa_prompt(channel_config, script, source_map)
    log_llm_call(LLMCallLog(
        task="short_script_qa", provider=LLM_PROVIDER, short_id=short_id,
        attempt=attempt,
        input_artifacts=["short_script.json", "short_source_map.json"],
        output_artifact="short_script_qa.json",
    ))
    raw = gemini_fn(prompt)
    parsed = _parse_gemini(raw) or {}
    verdict = str(parsed.get("verdict", "")).upper() or "FAIL"
    if verdict not in ("PASS", "FAIL"):
        verdict = "FAIL"
    return {
        "verdict": verdict,
        "issues": list(parsed.get("issues") or []),
        "required_changes": list(parsed.get("required_changes") or []),
        "warnings": list(parsed.get("warnings") or []),
        "scores": dict(parsed.get("scores") or {}),
        "provider": LLM_PROVIDER,
    }


def run_short_script_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    music_track: str | None = None,
    gemini_fn: Callable[[str], str] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    rule = _run_rule_script_qa(long_job_dir, short_id, channel_config, music_track=music_track)
    if rule["verdict"] == "FAIL":
        return rule
    if gemini_fn is None:
        return rule
    gemini = _run_gemini_script_qa(long_job_dir, short_id, channel_config,
                                   gemini_fn=gemini_fn, attempt=attempt)
    merged_warnings = sorted(set(gemini["warnings"] + rule["warnings"]))
    out = dict(gemini)
    out["warnings"] = merged_warnings
    return out


def _run_rule_scenes_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
) -> dict[str, Any]:
    sd = paths.short_dir(long_job_dir, short_id)
    scenes_doc = _load(paths.resolve_short_json(sd, paths.SHORT_SCENES_FILE))
    script = _load(paths.resolve_short_json(sd, paths.SHORT_SCRIPT_FILE))
    scenes = scenes_doc.get("scenes") or []

    dcfg = (channel_config.get("shorts") or {}).get("duration") or {}
    min_sec = float(dcfg.get("min_sec", 20))
    max_sec = float(dcfg.get("target_max_sec", 45))

    issues: list[str] = []
    warnings: list[str] = []

    structure_issues = validate_scenes.validate_scene_structure(
        scenes,
        scenes_doc=scenes_doc,
        script=script,
    )
    hard_structure = [
        issue for issue in structure_issues
        if issue.severity in {"blocking_error", "repairable_error"}
    ]
    warnings.extend(issue.detail for issue in structure_issues if issue.severity == "warning")

    total = scenes_doc.get("total_duration_sec") or sum(
        float(s.get("duration_sec") or 0) for s in scenes
    )
    if total and not (min_sec <= float(total) <= max_sec):
        issues.append(f"duration_out_of_range_{round(float(total),1)}s")

    cta_scene_dur = 0.0
    for s in scenes:
        ost_words = _word_count(s.get("on_screen_text", ""))
        if ost_words and not (2 <= ost_words <= 5):
            warnings.append(f"on_screen_text_words_{s.get('id')}={ost_words}")
        if str(s.get("layout")) == "short_cta":
            cta_scene_dur += float(s.get("duration_sec") or 0)
    if total and cta_scene_dur > 0.2 * float(total):
        issues.append("cta_dominates")
    if hard_structure:
        issues.extend(issue.type for issue in hard_structure)
        repair_plan = validate_scenes.build_scene_repair_plan(scenes, structure_issues, script=script)
        required_changes = repair_plan["instructions"]
    else:
        required_changes = issues

    verdict = "PASS" if not issues else "FAIL"
    return {
        "verdict": verdict,
        "issues": issues,
        "required_changes": required_changes,
        "warnings": warnings,
        "scores": {
            "funnel": 80 if "cta_dominates" not in issues else 40,
            "mobile_readability": 90,
        },
        "provider": "rule_based",
        "deterministic_issues": validate_scenes.issues_to_dicts(structure_issues),
    }


PRODUCT_SCORE_KEYS = [
    "audience_fit_45_plus",
    "hook_strength",
    "visual_specificity",
    "clarity",
    "retention_pacing",
    "natural_spanish",
    "saveability",
]
MIN_PRODUCT_SCORE = 7
MIN_AVERAGE_PRODUCT_SCORE = 8


def parse_defensive_score(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    import re
    match = re.match(r"^(\d+(?:\.\d+)?)\s*/\s*(\d+)$", val_str)
    if match:
        numerator = float(match.group(1))
        denominator = float(match.group(2))
        if denominator > 0:
            return (numerator / denominator) * 10.0
    try:
        return float(val_str)
    except ValueError:
        return 0.0


def normalize_gemini_scenes_qa(parsed: dict[str, Any]) -> dict[str, Any]:
    verdict = str(parsed.get("verdict", "")).upper() or "FAIL"
    issues = list(parsed.get("issues") or [])
    required_changes = list(parsed.get("required_changes") or [])
    warnings = list(parsed.get("warnings") or [])
    scores = dict(parsed.get("scores") or {})

    graphic_pref_patterns = [
        "should use graphic",
        "should be graphic",
        "could be graphic",
        "better as graphic",
        "candidate for graphic",
        "convert to graphic",
        "missing graphic",
        "graphic_label_callout",
        "graphic_comparison",
        "graphic_checklist",
    ]

    def is_graphic_pref(text: str) -> bool:
        t = text.lower()
        return any(p in t for p in graphic_pref_patterns)

    # Filter out graphic preference issues and move them to warnings
    new_issues = []
    for issue in issues:
        detail = str(issue.get("detail") or "")
        if is_graphic_pref(detail):
            warnings.append(f"Downgraded Gemini issue: {detail}")
        else:
            new_issues.append(issue)

    # Filter out graphic preference required changes
    new_required_changes = []
    for rc in required_changes:
        if is_graphic_pref(rc):
            warnings.append(f"Downgraded Gemini change: {rc}")
        else:
            new_required_changes.append(rc)

    # Extract and validate product scores
    prod_scores = parsed.get("product_scores") or {}
    has_scores_field = "product_scores" in parsed
    values = []
    score_dict = {}

    for key in PRODUCT_SCORE_KEYS:
        if key in prod_scores:
            val = parse_defensive_score(prod_scores[key])
            values.append(val)
            score_dict[key] = val

    score_issues = []
    score_required_changes = []

    if not has_scores_field or len(values) != len(PRODUCT_SCORE_KEYS):
        score_issues.append({
            "type": "product_quality_scores_missing",
            "scene_id": None,
            "severity": "major",
            "detail": "Gemini scene QA did not return all required product_scores. Hint: Return all seven product_scores from 0 to 10."
        })
        score_required_changes.append("Gemini scene QA did not return all required product_scores. Hint: Return all seven product_scores from 0 to 10.")
    else:
        low_scores = {
            key: val
            for key, val in score_dict.items()
            if val < MIN_PRODUCT_SCORE
        }
        average = sum(values) / len(values) if values else 0.0

        if low_scores:
            score_issues.append({
                "type": "product_quality_score_low",
                "scene_id": None,
                "severity": "major",
                "detail": f"Some product quality scores are below {MIN_PRODUCT_SCORE}: {low_scores}. Hint: Improve the weak product-quality dimensions while preserving safety, audio-fit, and scene caps."
            })
            score_required_changes.append(f"Some product quality scores are below {MIN_PRODUCT_SCORE}: {low_scores}. Hint: Improve the weak product-quality dimensions while preserving safety, audio-fit, and scene caps.")

        if average < MIN_AVERAGE_PRODUCT_SCORE:
            score_issues.append({
                "type": "product_quality_average_low",
                "scene_id": None,
                "severity": "major",
                "detail": f"Average product quality score is {average:.1f}, below {MIN_AVERAGE_PRODUCT_SCORE}. Hint: Improve hook, visual specificity, clarity, pacing, natural Spanish, and saveability."
            })
            score_required_changes.append(f"Average product quality score is {average:.1f}, below {MIN_AVERAGE_PRODUCT_SCORE}. Hint: Improve hook, visual specificity, clarity, pacing, natural Spanish, and saveability.")

    # Apply score issues to new_issues and required changes
    new_issues.extend(score_issues)
    new_required_changes.extend(score_required_changes)

    if score_issues:
        verdict = "FAIL"
    elif not new_issues and verdict == "FAIL":
        # If all issues/required changes (excluding scores) are downgraded, set verdict to PASS
        verdict = "PASS"
        warnings.append("layout_optimization_downgraded_to_warning")

    return {
        "verdict": verdict,
        "issues": new_issues,
        "required_changes": new_required_changes,
        "warnings": warnings,
        "scores": scores,
        "product_scores": score_dict,
        "provider": LLM_PROVIDER,
    }


def _run_gemini_scenes_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    gemini_fn: Callable[[str], str],
    attempt: int,
) -> dict[str, Any]:
    sd = paths.short_dir(long_job_dir, short_id)
    script = _load(paths.resolve_short_json(sd, paths.SHORT_SCRIPT_FILE))
    scenes_doc = _load(paths.resolve_short_json(sd, paths.SHORT_SCENES_FILE))
    prompt = prompts.gemini_scenes_qa_prompt(channel_config, script, scenes_doc)
    log_llm_call(LLMCallLog(
        task="short_scenes_qa", provider=LLM_PROVIDER, short_id=short_id,
        attempt=attempt,
        input_artifacts=["short_script.json", "short_scenes.json"],
        output_artifact="short_scenes_qa.json",
    ))
    raw = gemini_fn(prompt)
    parsed = _parse_gemini(raw) or {}
    return normalize_gemini_scenes_qa(parsed)


def run_short_scenes_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    gemini_fn: Callable[[str], str] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    rule = _run_rule_scenes_qa(long_job_dir, short_id, channel_config)
    if rule["verdict"] == "FAIL":
        return rule
    if gemini_fn is None:
        return rule
    gemini = _run_gemini_scenes_qa(long_job_dir, short_id, channel_config,
                                   gemini_fn=gemini_fn, attempt=attempt)
    merged_warnings = sorted(set(gemini["warnings"] + rule["warnings"]))
    out = dict(gemini)
    out["warnings"] = merged_warnings
    return out
