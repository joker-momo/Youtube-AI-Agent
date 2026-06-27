"""Whole-short scene structure validation, repair plan & pacing."""

from __future__ import annotations

import re

from video_agent.shorts.validation._constants import *  # noqa: F401,F403
from video_agent.shorts.validation._helpers import _duration, _scene_id
from video_agent.shorts.validation.audio_fit import validate_audio_fit
from video_agent.shorts.validation.graphic_checks import (
    _looks_like_checklist_or_explainer,
    _missing_graphic_candidate,
    graphic_repair_targets,
    is_explicit_graphic_led,
)
from video_agent.shorts.validation.issues import *  # noqa: F401,F403


def _scene_tokens(scene: dict[str, Any]) -> set[str]:
    text = f"{scene.get('narration') or ''} {scene.get('on_screen_text') or ''}"
    return {w.lower() for w in _words(text)}


def _redundancy_score(scene: dict[str, Any], others: list[dict[str, Any]]) -> float:
    """Jaccard-style overlap of a scene's tokens against the union of the others.
    Higher means the scene adds less new information."""
    tokens = _scene_tokens(scene)
    if not tokens:
        return 1.0
    union: set[str] = set()
    for other in others:
        union |= _scene_tokens(other)
    if not union:
        return 0.0
    return len(tokens & union) / len(tokens)


def simplify_scenes_for_pacing(
    scenes_doc: dict[str, Any],
    *,
    script: dict[str, Any] | None = None,
    target_max: int = 8,
    target_min: int = 7,
) -> dict[str, Any]:
    """Deterministic pacing repair for over-long Shorts (spec: retention_pacing low
    with many scenes). Drops the most redundant late summary scenes and merges a
    trailing tip/quote into the CTA, targeting 7-8 scenes. Never adds graphics.

    Returns ``{"scenes_doc", "changed", "notes", "removed_ids", "merged"}``.
    The returned doc is a fresh copy; the input is not mutated.
    """
    doc = json.loads(json.dumps(scenes_doc or {}))
    scenes = list(doc.get("scenes") or [])
    notes: list[str] = []
    removed_ids: list[str] = []
    merged = False

    if len(scenes) <= target_max:
        return {
            "scenes_doc": scenes_doc,
            "changed": False,
            "notes": [],
            "removed_ids": [],
            "merged": False,
        }

    # 1. Drop the most redundant late summary scenes (never the hook or the CTA),
    #    preferring scenes in the back half, until we reach the target count.
    while len(scenes) > target_max:
        # Candidate body scenes: exclude first (hook) and last (CTA).
        body_indices = list(range(1, len(scenes) - 1))
        # Restrict to non-graphic scenes in the back half so we strip late recaps,
        # not core graphics or the opening payoff.
        late_start = max(1, len(scenes) // 2)
        candidates = [
            i
            for i in body_indices
            if i >= late_start and not str(scenes[i].get("layout") or "").startswith("graphic_")
        ]
        if not candidates:
            candidates = [
                i
                for i in body_indices
                if not str(scenes[i].get("layout") or "").startswith("graphic_")
            ]
        if not candidates:
            break
        # Pick the most redundant candidate (ties -> latest index).
        best_i = max(
            candidates,
            key=lambda i: (
                _redundancy_score(scenes[i], [s for j, s in enumerate(scenes) if j != i]),
                i,
            ),
        )
        removed = scenes.pop(best_i)
        removed_ids.append(_scene_id(removed, best_i))
        notes.append(f"Removed redundant late summary scene {_scene_id(removed, best_i)}.")

    # 2. Merge a trailing tip/quote into the CTA when it fits the layout cap.
    if len(scenes) > target_min and len(scenes) >= 2:
        cta = scenes[-1]
        penult = scenes[-2]
        cta_is_cta = str(cta.get("layout") or "") == "short_cta"
        penult_layout = str(penult.get("layout") or "")
        if cta_is_cta and penult_layout in {
            "short_tip",
            "short_quote",
            "short_pain",
            "short_checklist",
        }:
            combined_narration = (
                f"{penult.get('narration') or ''} {cta.get('narration') or ''}".strip()
            )
            combined_dur = round(_duration(penult) + _duration(cta), 1)
            # Respect the CTA layout's own hard cap (short_cta is much tighter
            # than the global cap), so the merge never produces an over-long CTA.
            cta_hard_max = LAYOUT_DURATION_TARGETS.get(
                str(cta.get("layout") or ""), (0.0, 0.0, GLOBAL_SCENE_MAX_SEC)
            )[2]
            fits = (
                estimate_spanish_narration_sec(combined_narration) <= cta_hard_max
                and combined_dur <= cta_hard_max
            )
            if fits:
                cta["narration"] = combined_narration
                cta["duration_sec"] = combined_dur
                scenes.pop(-2)
                removed_ids.append(_scene_id(penult, len(scenes) - 1))
                merged = True
                notes.append("Merged final tip/quote into the CTA scene.")

    changed = bool(removed_ids) or merged
    if not changed:
        return {
            "scenes_doc": scenes_doc,
            "changed": False,
            "notes": [],
            "removed_ids": [],
            "merged": False,
        }

    doc["scenes"] = scenes
    doc["total_duration_sec"] = round(sum(_duration(s) for s in scenes), 1)
    return {
        "scenes_doc": doc,
        "changed": True,
        "notes": notes,
        "removed_ids": removed_ids,
        "merged": merged,
    }


def validate_scene_structure(
    scenes: list[dict[str, Any]],
    *,
    scenes_doc: dict[str, Any] | None = None,
    script: dict[str, Any] | None = None,
    audio_duration_sec: float | None = None,
    attempt: int = 1,
) -> list[SceneValidationIssue]:
    """Deterministic pre-QA validation for Shorts scene structure.

    This is the numeric/layout authority for spec v1.3. LLM QA can comment on
    product quality, but duration caps and arithmetic are decided here.
    """
    issues: list[SceneValidationIssue] = []
    scenes_doc = scenes_doc or {}
    scenes = list(scenes or [])
    scene_count = len(scenes)
    is_checklist = _looks_like_checklist_or_explainer(script, scenes)

    min_count = 6 if is_checklist else 4
    max_count = 12 if is_checklist else 12
    if scene_count == 0:
        # Genuine empty scenes array from valid JSON (spec §7.2). A provider error
        # is caught earlier in build_short_scenes and never reaches here, so a
        # zero count means the model returned {"scenes": []}.
        issues.append(
            SceneValidationIssue(
                type="empty_scenes",
                scene_id=None,
                severity="repairable_error",
                detail="Scenes array is empty.",
                repair_hint="Your JSON contains an empty scenes array. Return 5-8 actual scenes. This is invalid.",
            )
        )
    elif scene_count < min_count or scene_count > max_count:
        issues.append(
            SceneValidationIssue(
                type="scene_count",
                scene_id=None,
                severity="repairable_error",
                detail=f"Scene count {scene_count} is outside recommended range {min_count}-{max_count}.",
                repair_hint="Use 5-8 scenes by default, 6-9 for checklist/explainer, 4-6 for simple hook-tip-CTA.",
            )
        )

    if scenes:
        first_layout = str(scenes[0].get("layout") or "")
        if first_layout != "short_hook":
            issues.append(
                SceneValidationIssue(
                    type="first_scene_layout",
                    scene_id=_scene_id(scenes[0], 0),
                    severity="blocking_error",
                    detail=f"First scene layout is {first_layout!r}; expected short_hook.",
                    repair_hint="Regenerate with the first scene as short_hook.",
                )
            )
        first_motion = str(scenes[0].get("motion") or "").strip()
        if first_motion not in {"push_in", "object_reveal", "face_cut", "text_pop", "crop_shift"}:
            issues.append(
                SceneValidationIssue(
                    type="weak_hook_motion",
                    scene_id=_scene_id(scenes[0], 0),
                    severity="warning",
                    detail="First scene is missing a strong hook motion cue.",
                    repair_hint="Use push_in, object_reveal, face_cut, or text_pop for the hook scene.",
                )
            )
        if not str(scenes[0].get("retention_function") or "").strip():
            issues.append(
                SceneValidationIssue(
                    type="missing_retention_function",
                    scene_id=_scene_id(scenes[0], 0),
                    severity="warning",
                    detail="First scene is missing retention_function metadata.",
                    repair_hint="Set first scene retention_function to hook.",
                )
            )
        cta_text = str((script or {}).get("cta") or "").strip()
        has_cta = bool(cta_text) or any(
            str(scene.get("layout") or "") == "short_cta" for scene in scenes
        )
        if has_cta and str(scenes[-1].get("layout") or "") != "short_cta":
            issues.append(
                SceneValidationIssue(
                    type="last_scene_cta",
                    scene_id=_scene_id(scenes[-1], scene_count - 1),
                    severity="blocking_error",
                    detail="CTA exists but the last scene is not short_cta.",
                    repair_hint="Append or regenerate a final short_cta scene.",
                )
            )

    scene_sum = round(sum(_duration(scene) for scene in scenes), 3)
    computed_total = round(sum(_duration(scene) for scene in scenes), 1)
    original_declared = scenes_doc.get("total_duration_sec") if scenes_doc is not None else None
    if original_declared is not None:
        try:
            declared_float = float(original_declared)
            if abs(declared_float - computed_total) > 0.11:
                issues.append(
                    SceneValidationIssue(
                        type="total_duration_normalized",
                        scene_id=None,
                        severity="warning",
                        detail=f"total_duration_sec normalized from {original_declared} to {computed_total}.",
                        repair_hint=None,
                    )
                )
        except (TypeError, ValueError):
            issues.append(
                SceneValidationIssue(
                    type="total_duration_normalized",
                    scene_id=None,
                    severity="warning",
                    detail=f"total_duration_sec normalized from {original_declared!r} to {computed_total}.",
                    repair_hint=None,
                )
            )
    if scenes_doc is not None:
        scenes_doc["total_duration_sec"] = computed_total
    declared = computed_total

    total_for_range = float(declared or scene_sum or 0.0)
    if total_for_range and not (
        MIN_SHORT_DURATION_SEC <= total_for_range <= MAX_SHORT_DURATION_SEC
    ):
        issues.append(
            SceneValidationIssue(
                type="duration_range",
                scene_id=None,
                severity="repairable_error",
                detail=f"Total duration {total_for_range:.1f}s is outside hard range 20-60s.",
                repair_hint="Keep final duration within 20-60s; do not stretch individual scenes.",
            )
        )
    elif total_for_range and not (
        IDEAL_MIN_SHORT_DURATION_SEC <= total_for_range <= IDEAL_MAX_SHORT_DURATION_SEC
    ):
        issues.append(
            SceneValidationIssue(
                type="duration_ideal",
                scene_id=None,
                severity="warning",
                detail=f"Total duration {total_for_range:.1f}s is outside ideal 28-38s but within hard range.",
                repair_hint="Render is allowed if pacing and audio-fit are strong.",
            )
        )

    graphic_count = 0
    missing_graphic_candidates = 0
    static_run = 0
    text_heavy_run = 0
    previous_text = None
    for index, scene in enumerate(scenes):
        sid = _scene_id(scene, index)
        layout = str(scene.get("layout") or "")
        dur = _duration(scene)

        if layout not in SUPPORTED_SCENE_LAYOUTS:
            issues.append(
                SceneValidationIssue(
                    type="layout",
                    scene_id=sid,
                    severity="blocking_error",
                    detail=f"Unsupported scene layout {layout!r}.",
                    repair_hint="Use only supported short_* or graphic_* layouts.",
                )
            )
            continue

        if layout.startswith("graphic_"):
            graphic_count += 1
            if index == 0 or index == len(scenes) - 1:
                issues.append(
                    SceneValidationIssue(
                        type="graphic_setup_or_cta",
                        scene_id=sid,
                        severity="repairable_error",
                        detail=f"Graphic scene {sid} is used as setup or CTA.",
                        repair_hint="Use realistic short_* footage for hook/setup/CTA; reserve graphics for proof/payoff moments.",
                    )
                )

        motion = str(scene.get("motion") or "").strip()
        if motion in {"", "none", "static"}:
            static_run += 1
        else:
            static_run = 0
        if static_run > 3:
            issues.append(
                SceneValidationIssue(
                    type="repeated_static_scenes",
                    scene_id=sid,
                    severity="warning",
                    detail="More than 3 consecutive scenes are static or missing motion.",
                    repair_hint="Vary motion with crop_shift, push_in, object_reveal, text_pop, or pan_left.",
                )
            )

        on_screen_raw = str(scene.get("on_screen_text") or "").strip().lower()
        if previous_text and on_screen_raw and on_screen_raw == previous_text:
            issues.append(
                SceneValidationIssue(
                    type="repeated_on_screen_text",
                    scene_id=sid,
                    severity="warning",
                    detail=f"Scene {sid} repeats the previous on_screen_text structure.",
                    repair_hint="Change the overlay text or visual beat so the Short does not feel like a slideshow.",
                )
            )
        previous_text = on_screen_raw or previous_text

        if count_spoken_words(str(scene.get("on_screen_text") or "")) > 6:
            text_heavy_run += 1
        else:
            text_heavy_run = 0
        if text_heavy_run > 2:
            issues.append(
                SceneValidationIssue(
                    type="text_heavy_run",
                    scene_id=sid,
                    severity="warning",
                    detail="Too many consecutive text-heavy scenes.",
                    repair_hint="Shorten overlays and move detail to narration, caption, or a single graphic payoff.",
                )
            )

        if dur > GLOBAL_SCENE_MAX_SEC:
            issues.append(
                SceneValidationIssue(
                    type="duration_cap",
                    scene_id=sid,
                    severity="repairable_error",
                    detail=f"Scene {sid} duration {dur:.1f}s exceeds global hard max {GLOBAL_SCENE_MAX_SEC:.1f}s.",
                    repair_hint=f"No scene may exceed 5.0 sec in a normal Short. Split or regenerate {sid}.",
                )
            )
        target = LAYOUT_DURATION_TARGETS.get(layout)
        if target:
            target_min, target_max, hard_max = target
            if dur > hard_max:
                issues.append(
                    SceneValidationIssue(
                        type="duration_cap",
                        scene_id=sid,
                        severity="repairable_error",
                        detail=f"Scene {sid} ({layout}) duration {dur:.1f}s exceeds hard max {hard_max:.1f}s.",
                        repair_hint=f"No scene may exceed {hard_max:.1f} sec for layout {layout}. Split or regenerate {sid}.",
                    )
                )
            elif dur and not (target_min <= dur <= target_max):
                issues.append(
                    SceneValidationIssue(
                        type="duration_pacing",
                        scene_id=sid,
                        severity="warning",
                        detail=f"Scene {sid} ({layout}) duration {dur:.1f}s is outside target {target_min:.1f}-{target_max:.1f}s.",
                        repair_hint="Allowed if pacing remains strong and hard caps are respected.",
                    )
                )

        narration = str(scene.get("narration") or "")
        estimated_scene_audio = estimate_spanish_narration_sec(narration)
        if narration.strip() and estimated_scene_audio > dur + 0.3:
            issues.append(
                SceneValidationIssue(
                    type="scene_narration_fit",
                    scene_id=sid,
                    severity="repairable_error",
                    detail=f"Scene {sid} narration estimates {estimated_scene_audio:.1f}s for {dur:.1f}s scene (exceeds 0.3s tolerance).",
                    repair_hint="Condense narration or increase scene duration within layout cap. Do not exceed hard cap.",
                )
            )
        elif narration.strip() and estimated_scene_audio > dur:
            issues.append(
                SceneValidationIssue(
                    type="scene_narration_fit",
                    scene_id=sid,
                    severity="warning",
                    detail=f"Scene {sid} narration estimates {estimated_scene_audio:.1f}s for {dur:.1f}s scene.",
                    repair_hint="Consider condensing narration slightly or adjusting duration.",
                )
            )

        on_screen_text = str(scene.get("on_screen_text") or "").strip().upper()
        if on_screen_text in PASSIVE_CTA_TEXTS:
            issues.append(
                SceneValidationIssue(
                    type="passive_cta",
                    scene_id=sid,
                    severity="repairable_error",
                    detail=f"Scene {sid} CTA text '{on_screen_text}' is passive/status-like.",
                    repair_hint="Use GUARDA ESTA LISTA, GUÁRDALO PARA LA COMPRA, MÍRALO ANTES DE COMPRAR PAN, or ÚSALO EN EL SÚPER.",
                )
            )

        # validate source_scene_ids vs covers_items
        covers = scene.get("covers_items") or []
        source_ids = scene.get("source_scene_ids") or []
        if covers and not source_ids and layout not in ("short_hook", "short_cta", "short_quote"):
            issues.append(
                SceneValidationIssue(
                    type="missing_source_scene_ids",
                    scene_id=sid,
                    severity="repairable_error",
                    detail=f"Scene {sid} covers items but has empty source_scene_ids.",
                    repair_hint="If a scene covers an idea item, it must reference the supporting source_scene_ids.",
                )
            )
        if source_ids and script:
            valid_ids = {
                s.get("source_scene_id")
                for s in (script.get("source_mapped_flow") or [])
                if s.get("source_scene_id")
            }
            invalid_ids = [sid for sid in source_ids if valid_ids and sid not in valid_ids]
            if invalid_ids:
                issues.append(
                    SceneValidationIssue(
                        type="invalid_source_scene_ids",
                        scene_id=sid,
                        severity="repairable_error",
                        detail=f"Scene {sid} references invalid source_scene_ids: {invalid_ids}",
                        repair_hint="Use only valid source_scene_ids provided in the SCRIPT context.",
                    )
                )
        if _missing_graphic_candidate(scene):
            missing_graphic_candidates += 1

    if graphic_count > MAX_GRAPHIC_SCENES_PER_SHORT:
        explicit_graphic_led = is_explicit_graphic_led(script)
        keep_ids, convert_ids = graphic_repair_targets(scenes)
        convert_txt = ", ".join(convert_ids) or "the lowest-value graphic(s)"
        keep_txt = ", ".join(keep_ids) or "the highest-value graphics"
        if explicit_graphic_led and graphic_count == 3:
            # Input explicitly opted into a graphic-led Short: 3 is allowed but flagged.
            issues.append(
                SceneValidationIssue(
                    type="graphic_count",
                    scene_id=None,
                    severity="warning",
                    detail="Short has 3 graphic scenes (graphic-led requested). Confirm pacing stays strong.",
                    repair_hint="Keep 3 only if intentionally graphic-led; otherwise reduce to 1-2.",
                )
            )
        else:
            # Normal Short over the 2-graphic cap -> repairable error.
            issues.append(
                SceneValidationIssue(
                    type="graphic_count",
                    scene_id=None,
                    severity="repairable_error",
                    detail=(
                        f"Short has {graphic_count} graphic scenes; a normal Short allows at most "
                        f"{MAX_GRAPHIC_SCENES_PER_SHORT}. Being a checklist/explainer does not make it graphic-led."
                    ),
                    repair_hint=(
                        f"Keep only the 1-2 highest-value graphics ({keep_txt}) for the current idea. Convert setup/recap graphics "
                        f"({convert_txt}) into realistic short_tip or short_myth scenes with supermarket/kitchen visuals."
                    ),
                )
            )

    needs_checklist_graphic = (
        script is not None
        and _looks_like_checklist_or_explainer(script, scenes)
        and graphic_count == 0
    )
    if needs_checklist_graphic:
        issues.append(
            SceneValidationIssue(
                type="missing_graphic_required",
                scene_id=None,
                severity="repairable_error",
                detail=(
                    "Checklist/explainer Short has no graphic scene. Structured list, "
                    "portion, plate, or component beats need at least one ChatGPT-generated infographic."
                ),
                repair_hint=(
                    "Convert the highest-value list/proof/payoff beat to a graphic_* layout: "
                    "use graphic_checklist for action lists, graphic_plate_ratio for plate/portion/"
                    "component ratios, graphic_label_callout for label facts, or graphic_comparison "
                    "for two-choice comparisons. Keep hook and CTA as realistic short_* scenes."
                ),
            )
        )

    if missing_graphic_candidates and graphic_count < MAX_GRAPHIC_SCENES_PER_SHORT:
        issues.append(
            SceneValidationIssue(
                type="missing_graphic_required",
                scene_id=None,
                severity="repairable_error",
                detail="A short_* scene contains compact visual structure that should be a ChatGPT-generated graphic.",
                repair_hint=(
                    "Convert the best structured scene to graphic_checklist, graphic_plate_ratio, "
                    "graphic_label_callout, or graphic_comparison. Do not render this structure as "
                    "generic lifestyle footage only."
                ),
            )
        )

    if missing_graphic_candidates and graphic_count >= MAX_GRAPHIC_SCENES_PER_SHORT:
        issues.append(
            SceneValidationIssue(
                type="missing_graphic_warning",
                scene_id=None,
                severity="warning",
                detail="A stock scene contains visualizable label/checklist structure, but the Short already has 2 graphics.",
                repair_hint="Do not add a third graphic; improve the stock visual_prompt instead.",
            )
        )

    if script:
        contract = (script or {}).get("idea_contract") or {}
        # Detect if this is a 5-error bread Short
        is_5_error_bread = (
            contract.get("original_count") == 5 or contract.get("final_count") == 5
        ) and any(
            term in str(script.get("narration") or "").lower()
            for term in ("pan", "bread", "hogaza")
        )
        if is_5_error_bread:
            # 1. Enforce total duration >= 25.5s
            if total_for_range and total_for_range < 25.5:
                issues.append(
                    SceneValidationIssue(
                        type="duration_range",
                        scene_id=None,
                        severity="repairable_error",
                        detail=f"Total duration {total_for_range:.1f}s is too short for a 5-error Short (minimum 25.5s required).",
                        repair_hint="Increase individual scene durations to 3.2-4.0s for errors, 4.2-5.0s for payoff, 2.4-2.8s for CTA to reach 26-30s.",
                    )
                )
            # 2. Enforce graphic_checklist payoff scene layout (scene right before CTA)
            if len(scenes) >= 2:
                payoff_idx = len(scenes) - 2
                payoff_scene = scenes[payoff_idx]
                payoff_id = _scene_id(payoff_scene, payoff_idx)
                if payoff_scene.get("layout") != "graphic_checklist":
                    issues.append(
                        SceneValidationIssue(
                            type="payoff_layout",
                            scene_id=payoff_id,
                            severity="repairable_error",
                            detail=f"Payoff scene {payoff_id} layout is {payoff_scene.get('layout')!r}; expected graphic_checklist for 5-error bread Short.",
                            repair_hint="Use layout 'graphic_checklist' for the payoff scene to render a readable saveable checklist card.",
                        )
                    )

    if script and (
        script.get("idea_items") or (script.get("idea_contract") or {}).get("must_preserve_count")
    ):
        from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

        issues.extend(validate_scene_idea_coverage(scenes_doc, script, attempt=attempt))

    if audio_duration_sec is not None:
        issue = validate_audio_fit(total_for_range or scene_sum, audio_duration_sec)
        if issue:
            issues.append(issue)

    return issues


def build_scene_repair_plan(
    scenes: list[dict[str, Any]],
    issues: list[SceneValidationIssue],
    script: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_issues = [issue for issue in issues if issue.severity != "warning"]
    if (
        len(active_issues) == 1
        and active_issues[0].type == "duration_cap"
        and active_issues[0].scene_id
    ):
        only_issue = active_issues[0]
        original = next(
            (scene for scene in scenes if _scene_id(scene, -1) == only_issue.scene_id), {}
        )
        if str(original.get("layout") or "") == "short_cta":
            only_issue.instructions = [f"- Set {only_issue.scene_id} duration_sec to 2.6-2.8."]
            return {
                "repair_mode": "shorten_cta_duration",
                "instructions": [
                    "REPAIR PLAN:",
                    f"- Set {only_issue.scene_id} duration_sec to 2.6-2.8.",
                ],
                "suggested_scene_plan": [],
            }

    repair_modes: list[str] = []
    instructions: list[str] = [
        "REPAIR PLAN:",
        "- You must fix the listed scene IDs and not reintroduce the same violation.",
        "- target_duration_sec is a soft planning target; do not stretch scenes to reach 35 sec.",
        "- Final total may be 28-34 sec, or any 20-60 sec duration, if pacing and audio-fit are strong.",
        "- Keep s02-s06 as realistic short_tip/short_pain scenes, not short_checklist.",
    ]
    suggested_scene_plan: list[dict[str, Any]] = []

    for issue in active_issues:
        issue_instrs = []
        if issue.type in {"duration_cap", "scene_narration_fit"} and issue.scene_id:
            repair_modes.append("split_long_scene")
            original = next(
                (scene for scene in scenes if _scene_id(scene, -1) == issue.scene_id), {}
            )
            layout = original.get("layout") or ""
            if issue.type == "scene_narration_fit":
                if layout == "short_hook":
                    issue_instrs.extend(
                        [
                            f"- Fix {issue.scene_id}:",
                            "  - Hook narration is too long for 3.0 sec.",
                            "  - Replace with a 4-6 word hook that preserves the current idea.",
                            "  - Keep the longer idea in on_screen_text or next scene.",
                        ]
                    )
                elif layout == "graphic_label_callout":
                    issue_instrs.extend(
                        [
                            f"- Fix {issue.scene_id}:",
                            "  - Current narration is too long for a single graphic_label_callout scene.",
                            "  - Do not exceed 5.0 sec.",
                            "  - Shorten narration while preserving the current source-supported point.",
                            "  - Move examples/details into layout_payload callouts.",
                            "  - Or split into:",
                            "    s06a short_tip 3.2s: compact setup line.",
                            "    s06b graphic_label_callout 4.2s: compact source-supported label.",
                        ]
                    )
                elif layout == "short_quote":
                    issue_instrs.extend(
                        [
                            f"- Fix {issue.scene_id}:",
                            "  - Quote narration is too long.",
                            "  - Shorten to one source-supported sentence.",
                            "  - Keep nuance in on_screen_text or caption only if readable.",
                        ]
                    )
                elif layout == "short_cta":
                    issue_instrs.extend(
                        [
                            f"- Fix {issue.scene_id}:",
                            "  - CTA narration is too long.",
                            '  - Shorten to: "Guárdalo para la compra." or "Úsalo en el súper."',
                        ]
                    )
                else:
                    issue_instrs.append(f"- Fix {issue.scene_id}: {issue.detail}")
                    issue_instrs.append(
                        "- Cut this scene's narration to one short sentence (about 6-8 spoken words), or move the extra sentence into the next scene. Do not exceed the layout cap."
                    )
                # An over-long scene on a checklist Short usually means the model
                # crammed several items into one scene (which also drops other
                # items' coverage). Force a 1-item-per-scene layout.
                if _looks_like_checklist_or_explainer(script, scenes):
                    issue_instrs.append(
                        "- Do not cram multiple checklist items into one scene; give each promised item its own scene and keep the setup/myth scene short (4-8 spoken words)."
                    )
            else:
                if layout == "short_cta":
                    issue_instrs.append(f"- Set {issue.scene_id} duration_sec to 2.6-2.8.")
                else:
                    issue_instrs.append(f"- Fix {issue.scene_id}: {issue.detail}")
                    issue_instrs.append(
                        "- No scene may exceed 5.0 sec in a normal Short; split, shorten, or regenerate the scene."
                    )
            if layout != "short_cta":
                suggested_scene_plan.append(
                    {
                        "id": f"{issue.scene_id}a",
                        "duration_sec": 3.4,
                        "layout": "short_tip",
                        "on_screen_text": str(original.get("on_screen_text") or "COMPARA CON OTRO")[
                            :32
                        ],
                    }
                )
                suggested_scene_plan.append(
                    {
                        "id": f"{issue.scene_id}b",
                        "duration_sec": 3.2,
                        "layout": "short_tip",
                        "on_screen_text": "ETIQUETA CLARA",
                    }
                )
        elif issue.type == "graphic_count":
            repair_modes.append("reduce_graphics")
            keep_ids, convert_ids = graphic_repair_targets(scenes)
            issue_instrs.append(
                f"- Keep at most {MAX_GRAPHIC_SCENES_PER_SHORT} graphic scenes: "
                f"{', '.join(keep_ids) or 'the highest-value graphics'} for the current idea."
            )
            for cid in convert_ids:
                original = next((s for s in scenes if _scene_id(s, -1) == cid), {})
                ost = str(original.get("on_screen_text") or "MIRA LA ETIQUETA")[:32]
                issue_instrs.append(
                    f"- Convert {cid} (graphic setup/recap) into a realistic short_myth or short_tip scene "
                    f'with supermarket/kitchen visuals; keep on_screen_text like "{ost}". Do NOT keep it as a graphic.'
                )
                suggested_scene_plan.append(
                    {
                        "id": cid,
                        "duration_sec": 3.0,
                        "layout": "short_myth",
                        "on_screen_text": ost,
                    }
                )
            if not convert_ids:
                issue_instrs.append(
                    "- Convert setup/recap graphics into stock short_tip or short_myth scenes with realistic visuals."
                )
        elif issue.type == "missing_item_coverage":
            repair_modes.append("restore_item_coverage")
            m = re.search(r"item\s+(\w+)", str(issue.detail or ""), re.IGNORECASE)
            item_ref = m.group(1) if m else "the missing item"
            issue_instrs.extend(
                [
                    f"- Required idea item {item_ref} is not covered by any scene.",
                    f"- Give item {item_ref} its OWN dedicated scene; do not merge or cram it into another scene's narration.",
                    "- Every promised idea item must map 1:1 to its own scene so nothing is dropped.",
                ]
            )
        elif issue.type == "passive_cta":
            repair_modes.append("cta_rewrite")
            issue_instrs.append(
                "- Rewrite passive CTA text to an action CTA such as GUARDA ESTA LISTA or GUÁRDALO PARA LA COMPRA."
            )
        elif issue.type == "audio_fit":
            repair_modes.append("audio_fit")
            contract = (script or {}).get("idea_contract") or {}
            from video_agent.shorts.idea_preservation import allowed_spoken_points_from_contract

            allowed_points = allowed_spoken_points_from_contract(contract)
            issue_instrs.extend(
                [
                    "AUDIO-FIT REPAIR PLAN:",
                    "- Actual narration audio exceeds video duration.",
                    "- Condense narration; do not stretch scenes above caps.",
                    (
                        f"- Keep all {allowed_points} promised {contract.get('count_label') or 'items'}."
                        if allowed_points
                        else "- For implicit lists, keep 3-4 spoken checklist points if it improves retention."
                    ),
                    "- Move supporting detail to on_screen_text or graphic payload.",
                    "- Regenerate scenes after script compression.",
                ]
            )
        elif issue.type == "script_word_budget":
            repair_modes.append("script_condense")
            issue_instrs.append(
                "- Compress narration while preserving source-supported promised items."
            )
            issue_instrs.append(
                "- Treat 35s as a soft target; use split_recommended if quality cannot fit the Short ceiling."
            )
        elif issue.type == "slideshow_risk":
            repair_modes.append("reduce_slideshow_density")
            if issue.scene_id:
                issue_instrs.extend(
                    [
                        f"- Fix {issue.scene_id}:",
                        f"  - Reduce {issue.scene_id}, the exact dense checklist/graphic scene identified by the validator.",
                        "  - Keep only 2-3 visible text chunks total: one short title plus 1-2 short labels/items.",
                        "  - If the idea still needs more context, move it to realistic footage-led narration/caption in adjacent short_tip scenes.",
                    ]
                )
            else:
                issue_instrs.append(
                    "- Reduce only the exact dense checklist/graphic scene identified by the validator."
                )
            issue_instrs.append(
                "- Do not convert good footage-led item scenes into short_checklist scenes."
            )
            if issue.repair_hint and not (
                issue.scene_id and str(issue.repair_hint).startswith(f"Reduce {issue.scene_id}")
            ):
                issue_instrs.append(f"- {issue.repair_hint}")
        elif issue.type == "payoff_layout":
            repair_modes.append("payoff_checklist")
            issue_instrs.extend(
                [
                    f"- Fix {issue.scene_id}:",
                    "  - Convert the payoff scene to layout 'graphic_checklist'.",
                    "  - Use title: 'MEJOR ASÍ'.",
                    "  - Set items to: ['Porción visible', 'Plato pequeño', 'Comida completa'].",
                    "  - Set duration_sec to 4.2-5.0 seconds.",
                ]
            )
        else:
            issue_instrs.append(f"- Fix {issue.type}: {issue.detail}")
            if issue.repair_hint:
                issue_instrs.append(f"- {issue.repair_hint}")

        instructions.extend(issue_instrs)
        issue.instructions = issue_instrs

    mode = " | ".join(sorted(set(repair_modes))) if repair_modes else "warnings_only"
    return {
        "repair_mode": mode,
        "instructions": instructions,
        "suggested_scene_plan": suggested_scene_plan,
    }
