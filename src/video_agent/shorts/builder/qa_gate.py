"""QA gate helpers extracted from short_builder."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


HARD_SCENE_VALIDATION_TYPES = {
    "missing_item_coverage",
    "unknown_item_coverage",
    "layout",
    "payload",
    "audio_fit",
    "source_support",
    "safety",
    "duration_range",
    "duration_cap",
    "scene_narration_fit",
    "empty_scenes",
    "first_scene_layout",
    "last_scene_cta",
}


_HARD_QA_ISSUE_MARKERS = (
    "safety",
    "source_fidelity",
    "source_support",
    "idea",
    "schema",
    "layout",
    "contract",
    "audio_fit",
    "duration_cap",
    "missing_item",
    "first_scene",
    "last_scene",
    "empty_scenes",
)


def _is_soft_scene_qa_issue(item: dict[str, Any]) -> bool:
    itype = str(item.get("type") or "").lower()
    detail = str(item.get("detail") or "").lower()
    if "product_quality" in itype:
        return True
    if "source_fidelity" in itype and (
        "target duration" in detail
        or "heavily truncated" in detail
        or "exact narration" in detail
        or "restore the full text" in detail
    ):
        return True
    return False


def _scene_qa_has_hard_fail(result: dict[str, Any]) -> bool:
    """True when a scene-QA result carries a blocking/repairable issue that must
    be regenerated. Soft suggestions (severity warning, no required changes) are
    not hard fails."""
    for item in result.get("issues") or []:
        if not isinstance(item, dict):
            # Bare string issue: treat as soft suggestion.
            continue
        if _is_soft_scene_qa_issue(item):
            continue
        severity = str(item.get("severity") or "").lower()
        if severity in {"minor", "warning", "suggestion", "info"}:
            continue
        if severity in {"blocking_error", "repairable_error", "major"}:
            return True
        itype = str(item.get("type") or "").lower()
        if any(marker in itype for marker in _HARD_QA_ISSUE_MARKERS):
            return True
    return False


def has_hard_fail(result: dict[str, Any]) -> bool:
    if result.get("provider") == "rule_based" and result.get("verdict") == "FAIL":
        return True
    issues = result.get("issues") or []
    for item in issues:
        if isinstance(item, str):
            item_lower = item.lower()
            if any(
                m in item_lower
                for m in [
                    "safety",
                    "source_fidelity",
                    "source_support",
                    "health_claim",
                    "disclaimer",
                    "medical",
                    "contract",
                ]
            ):
                return True
            continue
        itype = str(item.get("type") or "").lower()
        severity = str(item.get("severity") or "").lower()
        detail = str(item.get("detail") or "").lower()
        if severity in {"minor", "warning", "suggestion", "info"}:
            continue
        if severity == "blocking_error":
            return True
        hard_markers = {
            "safety",
            "source_fidelity",
            "source_support",
            "idea",
            "schema",
            "layout",
            "contract",
            "first_scene",
            "empty_scenes",
            "greeting",
            "disclaimer",
            "medical",
            "overclaim",
            "narration",
            "source_map",
        }
        if any(m in itype for m in hard_markers):
            if "product_quality" in itype:
                continue
            return True
        if any(
            m in detail
            for m in [
                "safety",
                "source_fidelity",
                "source_support",
                "health claim",
                "medical overclaim",
            ]
        ):
            return True
    return False


def _qa_blocker_details(result: dict[str, Any]) -> list[str]:
    """Human-readable details for the hard blockers in a QA result.

    Used to build an explicit ``failure_reason`` for terminal hard failures so
    the UI can show the actual blocker instead of a generic
    "QA failed after max regeneration attempts" message. ``slideshow_risk`` and
    plain ``warning`` severities are quality preferences, not blockers, so they
    are excluded here.
    """
    details: list[str] = []
    has_structured_issues = False
    structured_issues_are_soft_only = True
    for item in result.get("issues") or []:
        if isinstance(item, str):
            details.append(item)
            continue
        if not isinstance(item, dict):
            continue
        has_structured_issues = True
        severity = str(item.get("severity") or "").lower()
        itype = str(item.get("type") or "").lower()
        if itype == "slideshow_risk" or severity == "warning":
            continue
        structured_issues_are_soft_only = False
        if severity in {"blocking_error", "repairable_error", "major", "critical"} or (
            severity not in {"", "minor", "info"}
        ):
            details.append(str(item.get("detail") or item.get("type") or "issue"))
    # Required changes explain how to repair the structured issues; they are
    # not independent blockers. Appending them unconditionally reintroduced a
    # slideshow-only REPAIR PLAN after the slideshow issue itself was filtered,
    # causing the terminal gate to report a false hard blocker. Keep them only
    # when a real blocker was found, or when the provider returned no structured
    # issues at all.
    if details or not has_structured_issues or not structured_issues_are_soft_only:
        for rc in result.get("required_changes") or []:
            rc_str = rc if isinstance(rc, str) else str(rc)
            if rc_str and rc_str not in details:
                details.append(rc_str)
    return details


def check_and_apply_auto_pass(qa_result: dict[str, Any]) -> bool:
    verdict = qa_result.get("verdict", "FAIL")
    if verdict in {"PASS", "WARN"}:
        return True
    if has_hard_fail(qa_result):
        return False
    from video_agent.shorts.qa import parse_defensive_score

    p_scores = qa_result.get("product_scores") or {}
    avg_product = 0.0
    if p_scores:
        vals = [parse_defensive_score(v) for v in p_scores.values()]
        avg_product = sum(vals) / len(vals) if vals else 0.0
    q_scores = qa_result.get("scores") or {}
    avg_quality = 0.0
    if q_scores:
        vals = [parse_defensive_score(v) for v in q_scores.values()]
        avg_quality = sum(vals) / len(vals) if vals else 0.0
    if avg_product >= 8.5 or avg_quality >= 85:
        qa_result["verdict"] = "WARN"
        qa_result["forced_pass_reason"] = "high_score_no_hard_fail"
        return True
    return False


def should_fallback_to_gemini_scene_qa(issues: list) -> bool:
    """Allow scene QA fallback only when deterministic issues are genuinely soft."""
    if not issues:
        return True
    for issue in issues:
        if issue.severity in {"blocking_error", "repairable_error"}:
            if issue.type in {"slideshow_risk", "visual_only_unreadable"}:
                return False
            return False
        if issue.type in HARD_SCENE_VALIDATION_TYPES:
            return False
    return True


def build_script_compression_feedback(short_script: dict[str, Any] | None) -> str:
    from video_agent.shorts.idea_preservation import allowed_spoken_points_from_contract

    script = short_script or {}
    contract = script.get("idea_contract") or {}
    allowed_points = allowed_spoken_points_from_contract(contract)
    count_label = str(contract.get("count_label") or "items").strip() or "items"
    point_line = (
        f"- Keep all {allowed_points} promised {count_label}."
        if allowed_points
        else "- Keep 3-4 spoken points if it improves retention and no locked count exists."
    )
    return "\n".join(
        [
            "SCRIPT COMPRESSION REQUIRED:",
            "- Scene-level narration fit failed after 2 attempts.",
            point_line,
            "- Make each item shorter and more natural.",
            "- Move supporting detail to on-screen text, captions, visual action, or layout_payload.",
            "- Use only source-supported language from the current idea.",
            "- If it still cannot fit without rushed narration or poor readability, return split_recommended.",
            "- Do not reduce the promised count unless adaptation_allowed=true.",
        ]
    )
