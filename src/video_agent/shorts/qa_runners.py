"""QA runners: rule + Gemini QA for script, scenes and short."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_agent.shorts import paths, prompts, validate_scenes
from video_agent.shorts.idea_preservation import validate_script_idea_contract
from video_agent.shorts.llm import LLMCallLog, log_llm_call
from video_agent.shorts.qa_common import *  # noqa: F401,F403
from video_agent.shorts.qa_product_scores import normalize_gemini_scenes_qa


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
    max_sec = float(dcfg.get("target_max_sec", 60))
    cta_max_words = int(
        ((channel_config.get("shorts") or {}).get("funnel") or {}).get("cta_max_words", 8)
    )

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
        issues.append(f"duration_out_of_range_{round(float(total), 1)}s")
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
    log_llm_call(
        LLMCallLog(
            task="short_qa",
            provider=LLM_PROVIDER,
            short_id=short_id,
            attempt=attempt,
            input_artifacts=["short_script.json", "short_scenes.json", "short_source_map.json"],
            output_artifact="short_qa.json",
        )
    )
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
    gemini = _run_gemini_qa(
        long_job_dir, short_id, channel_config, gemini_fn=gemini_fn, attempt=attempt
    )
    # Merge rule warnings (mostly soft) into Gemini's output for diagnostics.
    merged_warnings = sorted(set(gemini["warnings"] + rule["warnings"]))
    out = dict(gemini)
    out["warnings"] = merged_warnings
    return out


def _route_validation_issue(
    issue: validate_scenes.SceneValidationIssue,
    issues: list[str],
    warnings: list[str],
) -> None:
    severity = getattr(issue, "severity", "") or "repairable_error"
    if severity == "warning":
        warnings.append(issue.detail)
        return
    issues.append(issue.type)
    warnings.append(issue.detail)


def _get_source_idea_hook_text(long_job_dir: Path, idea_id: str) -> str | None:
    selected_p = long_job_dir / paths.SHORTS_DIRNAME / paths.SELECTED_SHORT_IDEAS_FILE
    if selected_p.exists():
        try:
            import json

            ideas = json.loads(selected_p.read_text(encoding="utf-8"))
            for idea in ideas:
                if str(idea.get("idea_id")) == str(idea_id):
                    return idea.get("hook_text")
        except Exception:
            pass
    ideas_p = long_job_dir / paths.SHORTS_DIRNAME / paths.SHORT_IDEAS_FILE
    if ideas_p.exists():
        try:
            import json

            ideas_doc = json.loads(ideas_p.read_text(encoding="utf-8"))
            ideas = ideas_doc.get("ideas") or []
            for idea in ideas:
                if str(idea.get("idea_id")) == str(idea_id):
                    return idea.get("hook_text")
        except Exception:
            pass
    return None


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

    # Normalize original_idea.hook_text if conflict detected
    original_idea = script.get("original_idea") or {}
    idea_id = original_idea.get("idea_id") or script.get("idea_id") or source_map.get("idea_id")
    if idea_id:
        source_hook = _get_source_idea_hook_text(long_job_dir, idea_id)
        if source_hook and original_idea.get("hook_text") != source_hook:
            original_idea["hook_text"] = source_hook
            script["original_idea"] = original_idea
            from video_agent.storage.atomic import atomic_write_json

            atomic_write_json(paths.resolve_short_json(sd, paths.SHORT_SCRIPT_FILE), script)

    cta_max_words = int(
        ((channel_config.get("shorts") or {}).get("funnel") or {}).get("cta_max_words", 8)
    )

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
        _route_validation_issue(word_budget_issue, issues, warnings)
    checklist_issue = validate_scenes.validate_script_checklist_point_cap(script)
    if checklist_issue:
        _route_validation_issue(checklist_issue, issues, warnings)
    if script.get("original_idea"):
        for idea_issue in validate_script_idea_contract(
            script,
            original_idea=script.get("original_idea") or {},
        ):
            _route_validation_issue(idea_issue, issues, warnings)
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

    # Normalize original_idea.hook_text if conflict detected
    original_idea = script.get("original_idea") or {}
    idea_id = original_idea.get("idea_id") or script.get("idea_id") or source_map.get("idea_id")
    if idea_id:
        source_hook = _get_source_idea_hook_text(long_job_dir, idea_id)
        if source_hook and original_idea.get("hook_text") != source_hook:
            original_idea["hook_text"] = source_hook
            script["original_idea"] = original_idea
            from video_agent.storage.atomic import atomic_write_json

            atomic_write_json(paths.resolve_short_json(sd, paths.SHORT_SCRIPT_FILE), script)

    prompt = prompts.gemini_script_qa_prompt(
        channel_config,
        script,
        source_map,
        original_idea=script.get("original_idea") or {},
    )
    log_llm_call(
        LLMCallLog(
            task="short_script_qa",
            provider=LLM_PROVIDER,
            short_id=short_id,
            attempt=attempt,
            input_artifacts=["short_script.json", "short_source_map.json"],
            output_artifact="short_script_qa.json",
        )
    )
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
    gemini = _run_gemini_script_qa(
        long_job_dir, short_id, channel_config, gemini_fn=gemini_fn, attempt=attempt
    )
    merged_warnings = sorted(set(gemini["warnings"] + rule["warnings"]))
    out = dict(gemini)
    out["warnings"] = merged_warnings
    return out


def _run_rule_scenes_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    attempt: int = 1,
) -> dict[str, Any]:
    sd = paths.short_dir(long_job_dir, short_id)
    scenes_doc = _load(paths.resolve_short_json(sd, paths.SHORT_SCENES_FILE))
    script = _load(paths.resolve_short_json(sd, paths.SHORT_SCRIPT_FILE))
    scenes = scenes_doc.get("scenes") or []

    dcfg = (channel_config.get("shorts") or {}).get("duration") or {}
    min_sec = float(dcfg.get("min_sec", 20))
    max_sec = float(dcfg.get("target_max_sec", 60))

    issues: list[str] = []
    warnings: list[str] = []

    structure_issues = validate_scenes.validate_scene_structure(
        scenes,
        scenes_doc=scenes_doc,
        script=script,
        attempt=attempt,
    )
    hard_structure = [
        issue
        for issue in structure_issues
        if issue.severity in {"blocking_error", "repairable_error"}
    ]
    warnings.extend(issue.detail for issue in structure_issues if issue.severity == "warning")

    total = scenes_doc.get("total_duration_sec") or sum(
        float(s.get("duration_sec") or 0) for s in scenes
    )
    if total and not (min_sec <= float(total) <= max_sec):
        issues.append(f"duration_out_of_range_{round(float(total), 1)}s")

    cta_scene_dur = 0.0
    for s in scenes:
        ost_words = _word_count(s.get("on_screen_text", ""))
        if ost_words and not (2 <= ost_words <= 5):
            warnings.append(f"on_screen_text_words_{s.get('id')}={ost_words}")
        if str(s.get("layout")) == "short_cta":
            cta_scene_dur += float(s.get("duration_sec") or 0)
    if total and cta_scene_dur > 0.2 * float(total):
        issues.append("cta_dominates")
    if scenes:
        first = scenes[0]
        first_prompt = str(first.get("visual_prompt") or "").lower()
        first_plan = first.get("first_frame_plan") or {}
        if not first_plan:
            warnings.append("first_scene_missing_first_frame_plan")
        if first_plan.get("strategy") in {"evidence_closeup", "object_contrast"} and not first.get(
            "crop_plan"
        ):
            warnings.append("first_scene_missing_crop_plan")
        elif not first.get("crop_plan") and any(
            term in first_prompt
            for term in ("label", "ingredient", "package", "bread", "pan", "etiqueta")
        ):
            warnings.append("first_scene_missing_crop_plan")
        if any(
            term in first_prompt
            for term in ("wide", "smiling", "stock pose", "generic", "centered")
        ):
            warnings.append("first_scene_generic_stock_risk")
    if hard_structure:
        issues.extend(issue.type for issue in hard_structure)
        repair_plan = validate_scenes.build_scene_repair_plan(
            scenes, structure_issues, script=script
        )
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
    log_llm_call(
        LLMCallLog(
            task="short_scenes_qa",
            provider=LLM_PROVIDER,
            short_id=short_id,
            attempt=attempt,
            input_artifacts=["short_script.json", "short_scenes.json"],
            output_artifact="short_scenes_qa.json",
        )
    )
    raw = gemini_fn(prompt)
    parsed = _parse_gemini(raw) or {}
    scenes = scenes_doc.get("scenes") or []
    graphic_count = validate_scenes.count_graphic_scenes(scenes)
    graphic_led = validate_scenes.is_graphic_led(scenes, script=script)
    normalized = normalize_gemini_scenes_qa(
        parsed, graphic_count=graphic_count, graphic_led=graphic_led
    )
    normalized["provider_call_ok"] = True
    normalized["qa_pass"] = normalized.get("verdict") == "PASS"
    return normalized


def run_short_scenes_qa(
    long_job_dir: Path,
    short_id: str,
    channel_config: dict,
    *,
    gemini_fn: Callable[[str], str] | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    rule = _run_rule_scenes_qa(long_job_dir, short_id, channel_config, attempt=attempt)
    if rule["verdict"] == "FAIL":
        return rule
    if gemini_fn is None:
        return rule
    gemini = _run_gemini_scenes_qa(
        long_job_dir, short_id, channel_config, gemini_fn=gemini_fn, attempt=attempt
    )
    merged_warnings = sorted(set(gemini["warnings"] + rule["warnings"]))
    out = dict(gemini)
    out["warnings"] = merged_warnings
    return out
