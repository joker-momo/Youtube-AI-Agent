"""Product-score classification and Gemini scenes-QA normalization."""

from __future__ import annotations

import re
from typing import Any

from video_agent.shorts.qa_common import *  # noqa: F401,F403


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
        and pacing is not None
        and pacing == SOFT_PACING_SCORE
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
        "graphic_label_callout",
        "graphic_comparison",
        "graphic_checklist",
        "graphic_plate_ratio",
        "graphic_step_list",
        "graphic_routine_split",
        "graphic_stat",
        "graphic_myth",
        "graphic_do_dont",
        "graphic_recipe_snapshot",
        "graphic_quote_portrait",
        "graphic_evidence_nugget",
        "graphic_warning",
    ]

    def is_missing_graphic_requirement(text: str, issue_type: str = "") -> bool:
        t = f"{issue_type} {text}".lower()
        return "missing_graphic_required" in t or "missing graphic" in t

    def is_graphic_pref(text: str) -> bool:
        t = text.lower()
        return any(p in t for p in graphic_pref_patterns)

    graphic_count_is_real = _graphic_count_is_real_error(graphic_count, graphic_led)

    # Filter out graphic preference issues (and false-positive graphic-count
    # complaints) and move them to warnings.
    new_issues = []
    for issue in issues:
        detail = str(issue.get("detail") or "")
        issue_type = str(issue.get("type") or "")
        if is_missing_graphic_requirement(detail, issue_type):
            new_issues.append(issue)
            continue
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
        if is_missing_graphic_requirement(str(rc)):
            new_required_changes.append(rc)
        elif is_graphic_count_complaint(rc) and not graphic_count_is_real:
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
        score_issues.append(
            {
                "type": "product_quality_scores_missing",
                "scene_id": None,
                "severity": "major",
                "detail": "Gemini scene QA did not return all required product_scores. Hint: Return all seven product_scores from 0 to 10.",
            }
        )
        score_required_changes.append(
            "Gemini scene QA did not return all required product_scores. Hint: Return all seven product_scores from 0 to 10."
        )
    else:
        low_scores = {
            key: val
            for key, val in score_dict.items()
            if key in REQUIRED_PRODUCT_SCORE_THRESHOLDS
            and val < REQUIRED_PRODUCT_SCORE_THRESHOLDS[key]
        }
        average = sum(values) / len(values) if values else 0.0

        if low_scores:
            score_issues.append(
                {
                    "type": "product_quality_score_low",
                    "scene_id": None,
                    "severity": "major",
                    "detail": f"Some product quality scores are below their required thresholds: {low_scores}. Required: {REQUIRED_PRODUCT_SCORE_THRESHOLDS}. Hint: Improve the weak product-quality dimensions while preserving safety, audio-fit, and scene caps.",
                }
            )
            score_required_changes.append(
                f"Some product quality scores are below their required thresholds: {low_scores}. Required: {REQUIRED_PRODUCT_SCORE_THRESHOLDS}. Hint: Improve the weak product-quality dimensions while preserving safety, audio-fit, and scene caps."
            )

        if average < MIN_AVERAGE_PRODUCT_SCORE:
            score_issues.append(
                {
                    "type": "product_quality_average_low",
                    "scene_id": None,
                    "severity": "major",
                    "detail": f"Average product quality score is {average:.1f}, below {MIN_AVERAGE_PRODUCT_SCORE:.2f}. Hint: Improve hook, visual specificity, clarity, pacing, natural Spanish, and saveability.",
                }
            )
            score_required_changes.append(
                f"Average product quality score is {average:.1f}, below {MIN_AVERAGE_PRODUCT_SCORE:.2f}. Hint: Improve hook, visual specificity, clarity, pacing, natural Spanish, and saveability."
            )

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
