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

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable

from video_agent.shorts import paths, prompts, validate_scenes
from video_agent.shorts.idea_preservation import validate_script_idea_contract
from video_agent.shorts.llm import LLMCallLog, log_llm_call

LLM_PROVIDER = "gemini"


class IssueClass:
    HARD_BLOCKER = "hard_blocker"
    REPAIRABLE_BLOCKER = "repairable_blocker"
    SOFT_WARNING = "soft_warning"
    STALE_OR_SUPPRESSED = "stale_or_suppressed"


@dataclass
class NormalizedIssue:
    issue_class: str
    reason: str
    source: str  # scene_validation | gemini_scene_qa | script_qa | renderer | audio
    scene_id: str | None
    issue_type: str
    detail: str
    repair_hint: str | None = None
    include_in_retry_feedback: bool = True
    trigger_regeneration: bool = True

    def to_dict(self) -> dict:
        d = {
            "issue_class": self.issue_class,
            "reason": self.reason,
            "source": self.source,
            "scene_id": self.scene_id,
            "issue_type": self.issue_type,
            "detail": self.detail,
            "repair_hint": self.repair_hint,
            "include_in_retry_feedback": self.include_in_retry_feedback,
            "trigger_regeneration": self.trigger_regeneration,
        }
        if self.issue_class == IssueClass.STALE_OR_SUPPRESSED:
            d["original_detail"] = self.detail
        return d


def get_short_rule_context(idea: dict, script: dict) -> dict:
    title = str(idea.get("title") or "")
    fmt = str(idea.get("format") or "")
    hook = str(script.get("hook") or "")
    viewer_pain = str(idea.get("viewer_pain") or "")
    original_count = idea.get("original_count")

    is_five_errors_bread_short = (
        "5 errores" in title.lower()
        or "cinco errores" in title.lower()
        or (fmt in {"mistakes", "errors"} and original_count == 5)
    )

    is_bread_shopping_checklist = (
        "compra" in title.lower()
        or "paquete" in hook.lower()
        or "etiqueta" in viewer_pain.lower()
    ) and fmt == "checklist"

    is_bread_topic = (
        "pan" in title.lower()
        or "pan" in hook.lower()
        or "pan" in viewer_pain.lower()
    )

    is_toast_assembly = (
        "tostada" in title.lower()
        or "toast" in title.lower()
    )

    return {
        "is_bread_topic": is_bread_topic,
        "is_five_errors_bread_short": is_five_errors_bread_short,
        "is_bread_shopping_checklist": is_bread_shopping_checklist,
        "is_toast_assembly": is_toast_assembly,
        "format": fmt,
        "hook_text": hook,
    }


def normalize_qa_issue(
    issue: Any,
    *,
    idea: dict,
    script: dict,
    scenes: dict,
    deterministic_validation: dict | None = None,
    source: str | None = None,
) -> NormalizedIssue:
    if isinstance(issue, NormalizedIssue):
        return issue

    issue_type = ""
    scene_id = None
    detail = ""
    repair_hint = None
    inferred_source = source or "unknown"

    if isinstance(issue, str):
        issue_type = issue
        detail = issue
        m = re.search(r'\b(s\d+)\b', issue)
        if m:
            scene_id = m.group(1)
    elif isinstance(issue, dict):
        issue_type = issue.get("type") or issue.get("issue_type") or ""
        scene_id = issue.get("scene_id")
        detail = issue.get("detail") or issue.get("required_change") or issue.get("description") or ""
        repair_hint = issue.get("repair_hint") or issue.get("hint")
        if "source" in issue:
            inferred_source = issue["source"]
    else:
        # e.g. SceneValidationIssue object
        issue_type = getattr(issue, "type", "") or ""
        scene_id = getattr(issue, "scene_id", None)
        detail = getattr(issue, "detail", "") or getattr(issue, "description", "") or ""
        repair_hint = getattr(issue, "repair_hint", None)
        if hasattr(issue, "source") and getattr(issue, "source"):
            inferred_source = getattr(issue, "source")
        elif issue.__class__.__name__ == "SceneValidationIssue":
            inferred_source = "scene_validation"

    issue_type_lower = issue_type.lower()
    detail_lower = detail.lower()

    if "total_duration_normalized" in issue_type_lower or "total_duration_normalized" in detail_lower or "duration_normalized" in issue_type_lower:
        return NormalizedIssue(
            issue_class=IssueClass.SOFT_WARNING,
            reason="duration_normalized",
            source=inferred_source,
            scene_id=scene_id,
            issue_type=issue_type,
            detail=detail,
            repair_hint=repair_hint,
            include_in_retry_feedback=False,
            trigger_regeneration=False,
        )

    if "duration_pacing" in issue_type_lower or "pacing remains strong" in detail_lower or "pacing remains strong" in (repair_hint or "").lower():
        return NormalizedIssue(
            issue_class=IssueClass.SOFT_WARNING,
            reason="duration_pacing",
            source=inferred_source,
            scene_id=scene_id,
            issue_type=issue_type,
            detail=detail,
            repair_hint=repair_hint,
            include_in_retry_feedback=True,
            trigger_regeneration=False,
        )

    severity_lower = ""
    if isinstance(issue, dict):
        severity_lower = str(issue.get("severity") or "").lower()
    elif issue is not None:
        severity_lower = str(getattr(issue, "severity", "") or "").lower()

    # Determine Issue Class and Reason
    issue_class = IssueClass.HARD_BLOCKER
    reason = issue_type or "unknown_issue"
    trigger_regeneration = True
    include_in_retry_feedback = True

    # Safety, source fidelity/support, health claim, contract -> HARD_BLOCKER
    is_hard = False
    if any(k in issue_type_lower for k in ["safety", "disclaimer", "overclaim", "fidelity", "support", "contract", "source_map", "empty_narration", "music_not_selected", "greeting"]):
        is_hard = True
    elif any(k in detail_lower for k in ["safety", "disclaimer", "overclaim", "fidelity", "support", "contract", "source_map", "empty_narration", "music_not_selected", "greeting"]):
        is_hard = True

    # Missing required checklist point, unreadable required item, malformed graphic payload, duration -> REPAIRABLE_BLOCKER
    is_repairable = False
    if any(k in issue_type_lower for k in ["checklist_point", "unreadable", "duration_cap", "graphic_payload", "malformed_graphic"]):
        is_repairable = True
    elif any(k in detail_lower for k in ["checklist point", "unreadable", "duration cap", "graphic payload", "malformed graphic"]):
        is_repairable = True
    elif issue_type_lower == "visual_only_unreadable":
        is_repairable = True

    # Aesthetic suggestion, weak hook motion (if first scene renderable), product scores 7-8 -> SOFT_WARNING
    is_soft = False
    if severity_lower in ("warning", "minor", "suggestion", "info"):
        is_soft = True
    elif issue_type_lower in ["weak_hook_motion", "hook_motion", "aesthetic", "visual_rhythm", "rhythm", "product_quality_average_low", "product_quality_score_low", "hook_polish", "polish", "visual", "visual_polish", "pacing_polish"]:
        is_soft = True
        # Escalate product_quality_score_low back to hard if any score <= 6 or
        # the detail contains a concrete safety/source/schema/render-blocking problem.
        if issue_type_lower in ("product_quality_score_low", "product_quality_average_low"):
            _blocking_keywords = ["source", "schema", "render", "crash", "malformed", "json", "fidelity", "contract"]
            # 'safety' is a tricky one because the hint itself might say 'preserving safety'
            if any(bk in detail_lower for bk in _blocking_keywords) or ("safety" in detail_lower and "preserving safety" not in detail_lower):
                is_soft = False
                is_hard = True
            else:
                # Parse score values from dict-like detail string
                score_vals = re.findall(r'(\d+(?:\.\d+)?)', detail_lower)
                for s_str in score_vals:
                    try:
                        s_val = float(s_str)
                        if s_val <= 6.0:
                            is_soft = False
                            is_hard = True
                            break
                    except ValueError:
                        pass
    elif any(k in detail_lower for k in ["weak_hook_motion", "hook motion", "aesthetic", "visual rhythm", "polish", "pacing", "pacing preference", "could consolidate", "near limit", "verify", "ensure"]):
        is_soft = True
    elif "product quality scores are below" in detail_lower:
        is_soft = True
        scores = re.findall(r'(\d+(?:\.\d+)?)', detail_lower)
        for s_str in scores:
            try:
                s_val = float(s_str)
                if s_val <= 6.0:
                    is_soft = False
                    is_hard = True
                    break
            except ValueError:
                pass
    elif "average product quality" in detail_lower:
        is_soft = True

    if is_hard:
        issue_class = IssueClass.HARD_BLOCKER
    elif is_repairable:
        issue_class = IssueClass.REPAIRABLE_BLOCKER
    elif is_soft:
        issue_class = IssueClass.SOFT_WARNING
        trigger_regeneration = False

    # Apply wrong context check
    real_idea = idea or script.get("original_idea") or {}
    context = get_short_rule_context(real_idea, script)
    if not context["is_five_errors_bread_short"]:
        suppress_patterns = [
            "no es el pan",
            "mira cómo lo usas",
            "son 5 hábitos",
            "son cinco hábitos",
            "error 1",
            "cena improvisada",
            "guárdalo",
            "generic error label",
            "five-errors-rule",
        ]
        is_duration_rule = False
        if "3.2" in detail_lower and "4" in detail_lower:
            is_duration_rule = True

        if any(p in detail_lower for p in suppress_patterns) or is_duration_rule:
            issue_class = IssueClass.STALE_OR_SUPPRESSED
            reason = "wrong_context_five_errors_rule"
            trigger_regeneration = False
            include_in_retry_feedback = False

    # Suppress wrong-context CTA requirement for bread-shopping checklist
    if context.get("is_bread_shopping_checklist"):
        cta_suppress_patterns = [
            "guárdalo", "guardalo",
            "próxima compra", "proxima compra",
            "para tu próxima", "para tu proxima",
            "cta requirement",
            "matches cta requirement perfectly",
        ]
        if any(p in detail_lower for p in cta_suppress_patterns):
            # Check if script CTA is context-valid and <= 8 words
            script_cta = str(script.get("cta") or "").strip()
            cta_word_count = len([w for w in script_cta.split() if w.strip()])
            if script_cta and cta_word_count <= 8:
                issue_class = IssueClass.STALE_OR_SUPPRESSED
                reason = "wrong_context_suppressed"
                trigger_regeneration = False
                include_in_retry_feedback = False

    # Apply noncanonical count authority check
    contract_data = script.get("idea_contract") or {}
    if not isinstance(contract_data, dict):
        contract_data = {}
    orig_count = contract_data.get("original_count") or real_idea.get("original_count")
    if orig_count is not None:
        try:
            orig_count_val = int(orig_count)
        except (ValueError, TypeError):
            orig_count_val = None

        if orig_count_val is not None:
            has_mismatch = False
            if orig_count_val != 5:
                # If the canonical count is not 5 (e.g. 4 or 3), and the QA issue demands 5 steps/errors/items,
                # it is a noncanonical count inference from the narration seed.
                five_patterns = [
                    "5-step", "5 step", "5 steps", "five-step", "five step", "five steps",
                    "cinco", "quinto", "5 errores", "5-errores", "5 pasos", "5-pasos",
                    "5 habitos", "5 hábitos", "5 items", "5-item", "5 points", "5-point"
                ]
                if any(p in detail_lower for p in five_patterns):
                    has_mismatch = True

            if has_mismatch:
                issue_class = IssueClass.STALE_OR_SUPPRESSED
                reason = "noncanonical_count_inference"
                trigger_regeneration = False
                include_in_retry_feedback = False

    return NormalizedIssue(
        issue_class=issue_class,
        reason=reason,
        source=inferred_source,
        scene_id=scene_id,
        issue_type=issue_type,
        detail=detail,
        repair_hint=repair_hint,
        include_in_retry_feedback=include_in_retry_feedback,
        trigger_regeneration=trigger_regeneration,
    )



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
    max_sec = float(dcfg.get("target_max_sec", 60))
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
    prompt = prompts.gemini_script_qa_prompt(
        channel_config,
        script,
        source_map,
        original_idea=script.get("original_idea") or {},
    )
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
NEW_PRODUCT_SCORE_KEYS = [
    "hook_specificity",
    "micro_tension",
    "human_naturalness",
    "visual_rhythm",
    "identity_resonance",
    "commentability",
]
NEW_PRODUCT_SCORE_THRESHOLDS = {
    "hook_specificity": {"pass": 75, "warn": 55, "fail": 54},
    "micro_tension": {"pass": 70, "warn": 50, "fail": 49},
    "human_naturalness": {"pass": 75, "warn": 55, "fail": 54},
    "visual_rhythm": {"pass": 70, "warn": 50, "fail": 49},
    "identity_resonance": {"pass": 70, "warn": 50, "fail": 49},
    "commentability": {"pass": 65, "warn": 45, "fail": 44},
}
REQUIRED_PRODUCT_SCORE_THRESHOLDS = {
    "hook_strength": 9.0,
    "clarity": 9.0,
    "retention_pacing": 9.0,
    "visual_specificity": 9.0,
    "audience_fit_45_plus": 9.0,
    "natural_spanish": 9.0,
    "saveability": 8.5,
}
MIN_PRODUCT_SCORE = 9.0
MIN_AVERAGE_PRODUCT_SCORE = 8.9

# Tiered product-score gate (QA storm fix v2.2). The old 9.0-on-every-dimension
# wall hard-failed near-good Shorts and drove regeneration storms. Tiers below
# separate true blockers (<7, weak pacing/visual) from product polish that
# should pass-with-warning or trigger a bounded repair instead of an infinite
# hard block. Precedence is strict: first matching tier wins.
KEY_PRODUCT_DIMENSIONS = (
    "hook_strength",
    "clarity",
    "retention_pacing",
    "visual_specificity",
    "audience_fit_45_plus",
    "saveability",
)
# Tier thresholds.
TIER_HARD_FLOOR = 7.0            # any dimension below this hard-blocks
TIER_RETENTION_FLOOR = 7.5      # retention_pacing below this hard-blocks
TIER_VISUAL_FIRST_FLOOR = 7.5   # visual_specificity floor for visual-first Shorts
TIER_REPAIR_AVERAGE = 8.2       # average below this triggers repair/retry
TIER_REPAIR_KEY = 8.0           # any key dimension below this triggers repair
TIER_NATURAL_SPANISH_MIN = 8.0  # natural_spanish must reach this to pass normal QA
# Publish target (aspirational, pass-with-warning below it — NOT a hard block).
TIER_PUBLISH_AVERAGE = 8.6
TIER_PUBLISH_KEY = 8.5
TIER_PUBLISH_NATURAL_SPANISH = 9.0


def classify_product_scores(
    scores: dict[str, float],
    *,
    visual_first: bool = False,
    saveable: bool = False,
) -> str:
    """Classify product scores into a gate tier.

    Returns one of ``"hard_block"``, ``"repair"``, ``"pass_with_warning"``,
    ``"pass"``. First matching tier wins (hard_block > repair > pass_with_warning
    > pass)."""
    values = [float(v) for v in scores.values()]
    if not values:
        return "repair"
    retention = float(scores.get("retention_pacing", 10.0))
    visual = float(scores.get("visual_specificity", 10.0))
    natural = float(scores.get("natural_spanish", 10.0))
    average = sum(values) / len(values)

    # 1. Hard block — true quality floors.
    if any(v < TIER_HARD_FLOOR for v in values):
        return "hard_block"
    if retention < TIER_RETENTION_FLOOR:
        return "hard_block"
    if visual_first and visual < TIER_VISUAL_FIRST_FLOOR:
        return "hard_block"

    # 2. Repair / retry — below product bar but recoverable.
    if average < TIER_REPAIR_AVERAGE:
        return "repair"
    if any(float(scores.get(dim, 10.0)) < TIER_REPAIR_KEY for dim in KEY_PRODUCT_DIMENSIONS):
        return "repair"
    if natural < TIER_NATURAL_SPANISH_MIN:
        return "repair"
    if saveable and float(scores.get("saveability", 10.0)) < TIER_REPAIR_KEY:
        return "repair"

    # 3/4. Publish target reached -> clean pass, else pass-with-warning.
    publish = (
        average >= TIER_PUBLISH_AVERAGE
        and all(float(scores.get(dim, 0.0)) >= TIER_PUBLISH_KEY for dim in KEY_PRODUCT_DIMENSIONS)
        and natural >= TIER_PUBLISH_NATURAL_SPANISH
    )
    return "pass" if publish else "pass_with_warning"
# A single product dimension at or below this score blocks render outright — no
# best-candidate fallback may rescue it.
MIN_PRODUCT_SCORE_RENDER_BLOCK = 8.5
# A retention_pacing exactly at this value is treated as "soft": if it is the only
# weak dimension, we simplify deterministically or render the best candidate with
# a warning instead of failing after max regenerations.
SOFT_PACING_SCORE = 8.0
# Below this retention_pacing we attempt a simplification repair.
PRODUCT_REPAIR_PACING_THRESHOLD = 9.0
# Scene count at/above which simplification (drop redundant scenes, merge tip+CTA)
# is the preferred pacing repair.
SIMPLIFY_SCENE_COUNT_THRESHOLD = 9


# Gemini sometimes flags a Short for "having too many graphics" even when the
# deterministic graphic count is within the 1-2 cap. These patterns catch that
# class of complaint so it can be reconciled against the deterministic count.
_GRAPHIC_COUNT_COMPLAINT_PATTERNS = [
    "at most 2 graphic",
    "at most two graphic",
    "already has 2 graphic",
    "already has two graphic",
    "maximum 2 graphic",
    "maximum of 2 graphic",
    "max 2 graphic",
    "no more than 2 graphic",
    "more than 2 graphic",
    "exceeds 2 graphic",
    "exceeds the allowed 2 graphic",
    "exceed 2 graphic",
    "exceed the allowed 2 graphic",
    "only 2 graphic",
    "only 2 scenes use graphic",
    "only two scenes use graphic",
    "limit of 2 graphic",
    "allowed 2 graphic",
    "2 scenes use graphic",
    "two scenes use graphic",
    "2 graphics already",
    "two graphics already",
    "too many graphic",
]


def is_graphic_count_complaint(text: str) -> bool:
    t = str(text).lower()
    return any(p in t for p in _GRAPHIC_COUNT_COMPLAINT_PATTERNS)


def _graphic_count_is_real_error(graphic_count: int | None, graphic_led: bool) -> bool:
    """A graphic-count complaint is a repairable error only when the deterministic
    count actually exceeds the cap: >=4 always, or ==3 unless the Short is
    intentionally graphic-led. With <=2 graphics (or unknown count) the complaint
    is a Gemini false positive and is downgraded to a warning."""
    if graphic_count is None:
        return False
    if graphic_count >= 4:
        return True
    if graphic_count == 3 and not graphic_led:
        return True
    return False


def summarize_product_scores(scores: dict[str, Any]) -> dict[str, Any]:
    """Defensive summary of the seven product-quality scores, used by the build
    loop to decide between product repair, best-candidate fallback, and hard
    failure. Mirrors the gates in ``normalize_gemini_scenes_qa``."""
    values: list[float] = []
    parsed: dict[str, float] = {}
    for key in PRODUCT_SCORE_KEYS:
        if key in scores:
            v = parse_defensive_score(scores[key])
            values.append(v)
            parsed[key] = v

    missing = len(values) != len(PRODUCT_SCORE_KEYS)
    average = sum(values) / len(values) if values else 0.0
    min_score = min(values) if values else 0.0
    low_dims = {
        k: v
        for k, v in parsed.items()
        if k in REQUIRED_PRODUCT_SCORE_THRESHOLDS and v < REQUIRED_PRODUCT_SCORE_THRESHOLDS[k]
    }
    pacing = parsed.get("retention_pacing")

    blocks_render = missing or bool(low_dims)
    soft_pacing_only = (
        not missing
        and not blocks_render
        and set(low_dims.keys()) <= {"retention_pacing"}
        and pacing is not None and pacing == SOFT_PACING_SCORE
    )
    return {
        "values": values,
        "scores": parsed,
        "missing": missing,
        "average": average,
        "min_score": min_score,
        "low_dims": low_dims,
        "has_low": bool(low_dims),
        "avg_too_low": average < MIN_AVERAGE_PRODUCT_SCORE,
        "blocks_render": blocks_render,
        "soft_pacing_only": soft_pacing_only,
        "retention_pacing": pacing,
        "needs_pacing_simplify": pacing is not None and pacing < PRODUCT_REPAIR_PACING_THRESHOLD,
    }


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


def normalize_gemini_scenes_qa(
    parsed: dict[str, Any],
    *,
    graphic_count: int | None = None,
    graphic_led: bool = False,
) -> dict[str, Any]:
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

    graphic_count_is_real = _graphic_count_is_real_error(graphic_count, graphic_led)

    # Filter out graphic preference issues (and false-positive graphic-count
    # complaints) and move them to warnings.
    new_issues = []
    for issue in issues:
        detail = str(issue.get("detail") or "")
        if is_graphic_count_complaint(detail) and not graphic_count_is_real:
            warnings.append(
                f"Downgraded graphic-count issue (deterministic graphic_count={graphic_count}): {detail}"
            )
        elif is_graphic_pref(detail):
            warnings.append(f"Downgraded Gemini issue: {detail}")
        else:
            new_issues.append(issue)

    # Filter out graphic preference required changes
    new_required_changes = []
    for rc in required_changes:
        if is_graphic_count_complaint(rc) and not graphic_count_is_real:
            warnings.append(
                f"Downgraded graphic-count change (deterministic graphic_count={graphic_count}): {rc}"
            )
        elif is_graphic_pref(rc):
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
            if key in REQUIRED_PRODUCT_SCORE_THRESHOLDS and val < REQUIRED_PRODUCT_SCORE_THRESHOLDS[key]
        }
        average = sum(values) / len(values) if values else 0.0

        if low_scores:
            score_issues.append({
                "type": "product_quality_score_low",
                "scene_id": None,
                "severity": "major",
                "detail": f"Some product quality scores are below their required thresholds: {low_scores}. Required: {REQUIRED_PRODUCT_SCORE_THRESHOLDS}. Hint: Improve the weak product-quality dimensions while preserving safety, audio-fit, and scene caps."
            })
            score_required_changes.append(f"Some product quality scores are below their required thresholds: {low_scores}. Required: {REQUIRED_PRODUCT_SCORE_THRESHOLDS}. Hint: Improve the weak product-quality dimensions while preserving safety, audio-fit, and scene caps.")

        if average < MIN_AVERAGE_PRODUCT_SCORE:
            score_issues.append({
                "type": "product_quality_average_low",
                "scene_id": None,
                "severity": "major",
                "detail": f"Average product quality score is {average:.1f}, below {MIN_AVERAGE_PRODUCT_SCORE:.2f}. Hint: Improve hook, visual specificity, clarity, pacing, natural Spanish, and saveability."
            })
            score_required_changes.append(f"Average product quality score is {average:.1f}, below {MIN_AVERAGE_PRODUCT_SCORE:.2f}. Hint: Improve hook, visual specificity, clarity, pacing, natural Spanish, and saveability.")

    # Tiered product-score gate (QA storm fix v2.2). Classify the dimension
    # scores into a single tier and route by it instead of hard-failing every
    # dimension below 9.0. True quality floors (any dim < 7, weak pacing/visual)
    # hard-block; a near-good Short repairs within the attempt budget or passes
    # with a warning — it no longer drives an infinite regeneration storm.
    _hard_score_issues: list[dict[str, Any]] = []
    scores_complete = has_scores_field and len(values) == len(PRODUCT_SCORE_KEYS)
    if scores_complete:
        tier = classify_product_scores(score_dict, visual_first=bool(graphic_led))
    else:
        # Missing/incomplete scores keep their existing hard-fail behavior.
        tier = "hard_block"
    if tier == "hard_block":
        _hard_score_issues = list(score_issues)
        new_issues.extend(_hard_score_issues)
        new_required_changes.extend(score_required_changes)
    elif tier == "repair":
        # Recoverable polish gap: drive a bounded retry, but do not hard-FAIL.
        new_required_changes.extend(score_required_changes)
        for si in score_issues:
            warnings.append(f"Product score below target (repair tier): {si.get('detail', '')}")
    else:  # pass_with_warning / pass — surface as warnings only, no regen.
        for si in score_issues:
            warnings.append(f"Downgraded product score issue to warning: {si.get('detail', '')}")

    if _hard_score_issues:
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
    gemini = _run_gemini_scenes_qa(long_job_dir, short_id, channel_config,
                                   gemini_fn=gemini_fn, attempt=attempt)
    merged_warnings = sorted(set(gemini["warnings"] + rule["warnings"]))
    out = dict(gemini)
    out["warnings"] = merged_warnings
    return out
