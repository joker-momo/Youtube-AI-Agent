"""Normalize raw QA issues into structured NormalizedIssue records."""

from __future__ import annotations

import re
from typing import Any

from video_agent.shorts.qa_common import *  # noqa: F401,F403


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
        m = re.search(r"\b(s\d+)\b", issue)
        if m:
            scene_id = m.group(1)
    elif isinstance(issue, dict):
        issue_type = issue.get("type") or issue.get("issue_type") or ""
        scene_id = issue.get("scene_id")
        detail = (
            issue.get("detail") or issue.get("required_change") or issue.get("description") or ""
        )
        repair_hint = issue.get("repair_hint") or issue.get("hint")
        if "source" in issue:
            inferred_source = issue["source"]
    else:
        # e.g. SceneValidationIssue object
        issue_type = getattr(issue, "type", "") or ""
        scene_id = getattr(issue, "scene_id", None)
        detail = getattr(issue, "detail", "") or getattr(issue, "description", "") or ""
        repair_hint = getattr(issue, "repair_hint", None)
        if hasattr(issue, "source") and issue.source:
            inferred_source = issue.source
        elif issue.__class__.__name__ == "SceneValidationIssue":
            inferred_source = "scene_validation"

    issue_type_lower = issue_type.lower()
    detail_lower = detail.lower()

    if (
        "total_duration_normalized" in issue_type_lower
        or "total_duration_normalized" in detail_lower
        or "duration_normalized" in issue_type_lower
    ):
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

    if (
        "duration_pacing" in issue_type_lower
        or "pacing remains strong" in detail_lower
        or "pacing remains strong" in (repair_hint or "").lower()
        or "rushed pacing" in detail_lower
        or "crammed" in detail_lower
    ):
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

    if (
        issue_type_lower == "duration"
        and "target_duration" in detail_lower
        and any(
            term in detail_lower
            for term in ("underperforms", "total sequence duration", "target duration")
        )
    ):
        return NormalizedIssue(
            issue_class=IssueClass.SOFT_WARNING,
            reason="duration_compression_after_deterministic_pass",
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
    if any(
        k in issue_type_lower
        for k in [
            "safety",
            "disclaimer",
            "overclaim",
            "fidelity",
            "support",
            "contract",
            "source_map",
            "empty_narration",
            "music_not_selected",
            "greeting",
        ]
    ):
        is_hard = True
    elif any(
        k in detail_lower
        for k in [
            "safety",
            "disclaimer",
            "overclaim",
            "fidelity",
            "support",
            "contract",
            "source_map",
            "empty_narration",
            "music_not_selected",
            "greeting",
        ]
    ):
        is_hard = True

    # Missing required checklist point, unreadable required item, malformed graphic payload, duration -> REPAIRABLE_BLOCKER
    is_repairable = False
    if any(
        k in issue_type_lower
        for k in [
            "checklist_point",
            "unreadable",
            "duration_cap",
            "graphic_payload",
            "malformed_graphic",
        ]
    ):
        is_repairable = True
    elif any(
        k in detail_lower
        for k in [
            "checklist point",
            "unreadable",
            "duration cap",
            "graphic payload",
            "malformed graphic",
        ]
    ):
        is_repairable = True
    elif issue_type_lower == "visual_only_unreadable":
        is_repairable = True

    # Aesthetic suggestion, weak hook motion (if first scene renderable), product scores 7-8 -> SOFT_WARNING
    is_suppressed = False
    script_ignored_stale_hook = "stale_hook_text_repaired" in (script.get("planner_warnings") or [])
    if (
        issue_type_lower == "idea_fidelity"
        and "unrelated" in detail_lower
        and script_ignored_stale_hook
    ):
        is_suppressed = True
        is_hard = False
        reason = "wrong_context_suppressed"

    if issue_type_lower == "idea_fidelity" and (
        "agrupan" in detail_lower or "agrupa" in detail_lower or "un solo bloque" in detail_lower
    ):
        is_repairable = True
        is_hard = False
        reason = "repairable_point_grouping"

    if issue_type_lower in ("style", "structure") and any(
        k in detail_lower
        for k in ("word count", "words", "speaking time", "audio-fit", "rushed", "pacing")
    ):
        is_repairable = True
        is_hard = False
        reason = "repairable_audio_fit"

    is_soft = False
    if issue_type_lower == "source_fidelity" and any(
        k in detail_lower
        for k in (
            "target duration",
            "target_duration",
            "heavily truncated",
            "truncates",
            "truncated",
            "compresses",
            "restore the full text",
            "exact narration",
        )
    ):
        is_hard = False
        is_repairable = False
        is_soft = True
        reason = "scene_compression_preference"

    if severity_lower in ("warning", "minor", "suggestion", "info"):
        is_soft = True
    elif issue_type_lower in [
        "weak_hook_motion",
        "hook_motion",
        "aesthetic",
        "visual_rhythm",
        "rhythm",
        "product_quality_average_low",
        "product_quality_score_low",
        "hook_polish",
        "polish",
        "visual",
        "visual_polish",
        "pacing_polish",
    ]:
        is_soft = True
        # Escalate product_quality_score_low back to hard if any score <= 6 or
        # the detail contains a concrete safety/source/schema/render-blocking problem.
        if issue_type_lower in ("product_quality_score_low", "product_quality_average_low"):
            _blocking_keywords = [
                "source",
                "schema",
                "render",
                "crash",
                "malformed",
                "json",
                "fidelity",
                "contract",
            ]
            # 'safety' is a tricky one because the hint itself might say 'preserving safety'
            # A concrete hard quality floor (any reported dimension below 6.0)
            # must block, not just keyword matches — otherwise a clarity=5 scene
            # renders because the FAIL is downgraded to a soft warning. A score of
            # 6 stays a soft terminal warning. The Required thresholds in the
            # detail are all >= 8.5, so only an actual failing dimension trips this.
            _hard_floor_breached = any(
                float(s) < 6.0 for s in re.findall(r"\d+(?:\.\d+)?", detail_lower)
            )
            if (
                _hard_floor_breached
                or any(bk in detail_lower for bk in _blocking_keywords)
                or ("safety" in detail_lower and "preserving safety" not in detail_lower)
            ):
                is_soft = False
                is_hard = True
            else:
                is_hard = False
    elif any(
        k in detail_lower
        for k in [
            "weak_hook_motion",
            "hook motion",
            "aesthetic",
            "visual rhythm",
            "polish",
            "pacing",
            "pacing preference",
            "could consolidate",
            "near limit",
            "verify",
            "ensure",
        ]
    ):
        is_soft = True
    elif "product quality scores are below" in detail_lower:
        is_soft = True
        scores = re.findall(r"(\d+(?:\.\d+)?)", detail_lower)
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

    if is_soft:
        # Don't let default hard overrides override the explicit minor
        is_hard = False
        is_repairable = False

    # Required changes (strings) shouldn't default to hard blocker unless they match hard markers
    if not isinstance(issue, dict) and not getattr(issue, "severity", None):
        if not is_hard and not is_repairable:
            is_soft = True

    if is_hard:
        issue_class = IssueClass.HARD_BLOCKER
    elif is_repairable:
        issue_class = IssueClass.REPAIRABLE_BLOCKER
    elif is_soft:
        issue_class = IssueClass.SOFT_WARNING
        trigger_regeneration = False

    if is_suppressed:
        issue_class = IssueClass.STALE_OR_SUPPRESSED
        trigger_regeneration = False
        include_in_retry_feedback = False

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
            "guárdalo",
            "guardalo",
            "próxima compra",
            "proxima compra",
            "para tu próxima",
            "para tu proxima",
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
                    "5-step",
                    "5 step",
                    "5 steps",
                    "five-step",
                    "five step",
                    "five steps",
                    "cinco",
                    "quinto",
                    "5 errores",
                    "5-errores",
                    "5 pasos",
                    "5-pasos",
                    "5 habitos",
                    "5 hábitos",
                    "5 items",
                    "5-item",
                    "5 points",
                    "5-point",
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
