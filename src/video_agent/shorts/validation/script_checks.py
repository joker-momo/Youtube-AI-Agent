"""Script-level validation: word budget, candidate validation, checklist cap."""

from __future__ import annotations

import re
from collections import Counter

from video_agent.shorts.validation._constants import *  # noqa: F401,F403
from video_agent.shorts.validation.issues import *  # noqa: F401,F403


def validate_script_word_budget(
    script: dict[str, Any], *, wps: float = DEFAULT_SPANISH_WPS
) -> SceneValidationIssue | None:
    narration = str((script or {}).get("narration") or "")
    target = float((script or {}).get("target_duration_sec") or 35.0)
    words = count_spoken_words(narration)
    estimated = estimate_spanish_narration_sec(narration, wps=wps)
    max_words = max_spoken_words_for_duration(target, wps=wps)
    if estimated > target * 1.05 or estimated > 38.0 or words > max_words:
        if estimated <= MAX_SHORT_DURATION_SEC:
            return SceneValidationIssue(
                type="script_word_budget",
                scene_id=None,
                severity="warning",
                detail=(
                    f"Script narration has {words} spoken words; estimated_spoken_duration "
                    f"is {estimated:.1f}s at {wps:.2f} wps for target {target:.1f}s "
                    f"(old preference max about {max_words} words)."
                ),
                repair_hint=(
                    "This is a content-led duration warning, not a count-reduction failure. "
                    "Keep promised idea items, compact wording, and verify audio-fit."
                ),
            )
        return SceneValidationIssue(
            type="script_word_budget",
            scene_id=None,
            severity="repairable_error",
            detail=(
                f"Script narration has {words} spoken words; estimated_spoken_duration "
                f"is {estimated:.1f}s at {wps:.2f} wps for target {target:.1f}s "
                f"(recommended max about {max_words} words)."
            ),
            repair_hint=(
                "Condense narration before scene generation without silently reducing a locked idea count. "
                "If the promised items cannot fit within the Short ceiling, recommend split_recommended."
            ),
        )
    return None


def validate_full_short_script_candidate(
    script: dict[str, Any],
    short_plan: dict[str, Any],
    source_map: dict[str, Any] | None = None,
) -> list[str]:
    """Validates that a generated script is complete and not a partial rewrite fragment."""
    errors = []

    beats = list(script.get("beats") or [])
    if len(beats) < 5:
        errors.append("partial_script_too_few_blocks")

    target_duration_sec = short_plan.get("target_duration_sec") or 35
    total_words = sum(
        len(re.findall(r"\w+", str(b.get("narration") or ""))) for b in beats if isinstance(b, dict)
    )
    global_words = len(re.findall(r"\w+", str(script.get("narration") or "")))
    if target_duration_sec == 35 and (total_words > 72 or global_words > 72):
        errors.append("audio_fit_over_soft_budget")
    elif target_duration_sec == 45 and (total_words > 95 or global_words > 95):
        errors.append("audio_fit_over_soft_budget")

    if beats and isinstance(beats[0], dict):
        t_sec = beats[0].get("time_sec")
        first_time = str(t_sec).strip() if t_sec is not None else ""
        # Ensure the first beat starts at 0 or 1
        match = re.match(r"^(\d+)", first_time)
        if match:
            start_sec = int(match.group(1))
            if start_sec > 1:
                errors.append("script_does_not_start_at_zero")
        elif not first_time.startswith("0") and not first_time.startswith("1"):
            errors.append("script_does_not_start_at_zero")

        first_text = str(beats[0].get("narration") or "").lower()
        if not first_text:
            first_text = str(beats[0].get("visual") or "").lower()

        plan_hook = str(short_plan.get("hook_text") or "").lower().strip()
        has_hook = (
            "?" in first_text
            or "si " in first_text
            or "no " in first_text
            or "te pasa" in first_text
            or "después de los 45" in first_text
            or "45" in first_text
            or (bool(plan_hook) and plan_hook in first_text)
        )
        if not has_hook:
            errors.append("missing_strong_hook_first_two_seconds")

    has_cta_beat = False
    for b in beats:
        if isinstance(b, dict) and str(b.get("purpose") or "").lower() == "cta":
            has_cta_beat = True
            break

    def normalize_str(text: str) -> str:
        return re.sub(r"\W+", " ", text.lower()).strip()

    expected_cta = "Vídeo completo en el canal."
    if source_map and source_map.get("funnel", {}).get("cta"):
        expected_cta = source_map["funnel"]["cta"]
    elif short_plan.get("funnel", {}).get("cta"):
        expected_cta = short_plan["funnel"]["cta"]

    cta_text = str(script.get("cta") or "").strip()
    if not has_cta_beat and not cta_text:
        errors.append("missing_cta")
    elif cta_text:
        word_count = len(re.findall(r"\w+", cta_text))
        if word_count > 8:
            errors.append("cta_too_long_exceeds_8_words")
        if normalize_str(expected_cta) not in normalize_str(cta_text):
            errors.append("missing_expected_funnel_cta")

    flow = list(script.get("source_mapped_flow") or [])
    if flow:

        def normalize(text: str) -> str:
            return re.sub(r"\W+", " ", text.lower()).strip()

        summaries = [
            normalize(str(item.get("spoken_summary") or ""))
            for item in flow
            if str(item.get("spoken_summary") or "").strip()
        ]
        counts = Counter(summaries)

        for text, count in counts.items():
            if count >= 3 and len(text.split()) >= 4:
                errors.append("same_rewrite_repeated_across_source_scenes")
                break

    return errors


def estimate_spoken_checklist_points(script: dict[str, Any]) -> int:
    text = str((script or {}).get("narration") or "")
    lower = text.lower()
    numbered_words = re.findall(r"\b(uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\s*:", lower)
    numeric_markers = re.findall(r"(?:^|[\s\n])(?:\d+)[\).:]", text)
    if numbered_words or numeric_markers:
        return len(numbered_words) + len(numeric_markers)
    if "cinco cosas" in lower or "cinco puntos" in lower or "cinco pasos" in lower:
        return 5
    if "cuatro cosas" in lower or "cuatro puntos" in lower or "cuatro pasos" in lower:
        return 4
    return 0


def validate_script_checklist_point_cap(script: dict[str, Any]) -> SceneValidationIssue | None:
    from video_agent.shorts.idea_preservation import allowed_spoken_points_from_contract

    text = " ".join(
        str((script or {}).get(key) or "")
        for key in ("short_format", "format", "narration", "hook")
    ).lower()
    if not any(term in text for term in ("checklist", "lista", "revisa", "paso", "punto")):
        return None
    points = estimate_spoken_checklist_points(script)
    contract = (script or {}).get("idea_contract") or {}

    # Extract contract fields directly or fallback to original_idea's contract
    must_preserve = bool(contract.get("must_preserve_count"))
    count_mode = str(contract.get("count_mode") or "")
    original_count = contract.get("original_count")

    if not must_preserve:
        orig_contract = (script or {}).get("original_idea", {}).get("idea_contract") or {}
        if orig_contract.get("must_preserve_count"):
            contract = orig_contract
            must_preserve = True
            count_mode = str(orig_contract.get("count_mode") or "")
            original_count = orig_contract.get("original_count")

    if must_preserve and count_mode == "exact" and original_count is not None:
        try:
            allowed_spoken_points = int(original_count)
            if points <= allowed_spoken_points:
                return None
        except (ValueError, TypeError):
            pass

    allowed = allowed_spoken_points_from_contract(contract)
    if allowed is not None:
        if points <= allowed:
            return None
        return SceneValidationIssue(
            type="script_checklist_point_cap",
            scene_id=None,
            severity="repairable_error",
            detail=(
                f"Checklist narration appears to speak {points} points, above the locked idea count/range "
                f"upper bound of {allowed}."
            ),
            repair_hint=(
                f"Keep all {allowed} promised items, but do not add extra numbered points. "
                "Compact each item and move supporting detail to visuals; use split_recommended if quality still fails."
            ),
        )
    if points > 4:
        return SceneValidationIssue(
            type="script_checklist_point_cap",
            scene_id=None,
            severity="repairable_error",
            detail=f"Checklist/explainer narration appears to speak {points} points; implicit-list Shorts should usually speak 3-4 compact points.",
            repair_hint="For implicit lists, speak the top 3-4 points and move remaining details to on-screen text or a graphic payload.",
        )
    return None


def classify_script_validation(errors: list[str]) -> str:
    if not errors:
        return "PASSED"
    if len(errors) == 1 and errors[0] == "audio_fit_over_soft_budget":
        return "REJECTED_AUDIO_FIT"
    return "REJECTED_PARTIAL"
