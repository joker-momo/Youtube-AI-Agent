from __future__ import annotations

from video_agent.shorts.validation.issues import *  # noqa: F401,F403
import re
from collections import Counter

def validate_script_word_budget(script: dict[str, Any], *, wps: float = DEFAULT_SPANISH_WPS) -> SceneValidationIssue | None:
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
    if target_duration_sec == 35:
        total_words = sum(len(re.findall(r'\w+', str(b.get("narration") or ""))) for b in beats if isinstance(b, dict))
        if total_words > 85:
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

        has_hook = (
            "?" in first_text or
            "si " in first_text or
            "no " in first_text or
            "te pasa" in first_text or
            "después de los 45" in first_text or
            "45" in first_text
        )
        if not has_hook:
            errors.append("missing_strong_hook_first_two_seconds")

    has_cta_beat = False
    for b in beats:
        if isinstance(b, dict) and str(b.get("purpose") or "").lower() == "cta":
            has_cta_beat = True
            break

    def normalize_str(text: str) -> str:
        return re.sub(r'\W+', ' ', text.lower()).strip()

    expected_cta = "Vídeo completo en el canal."
    if source_map and source_map.get("funnel", {}).get("cta"):
        expected_cta = source_map["funnel"]["cta"]
    elif short_plan.get("funnel", {}).get("cta"):
        expected_cta = short_plan["funnel"]["cta"]

    cta_text = str(script.get("cta") or "").strip()
    if not has_cta_beat and not cta_text:
        errors.append("missing_cta")
    elif cta_text:
        word_count = len(re.findall(r'\w+', cta_text))
        if word_count > 8:
            errors.append("cta_too_long_exceeds_8_words")
        if normalize_str(expected_cta) not in normalize_str(cta_text):
            errors.append("missing_expected_funnel_cta")

    flow = list(script.get("source_mapped_flow") or [])
    if flow:
        def normalize(text: str) -> str:
            return re.sub(r'\W+', ' ', text.lower()).strip()

        summaries = [normalize(str(item.get("spoken_summary") or "")) for item in flow if str(item.get("spoken_summary") or "").strip()]
        counts = Counter(summaries)

        for text, count in counts.items():
            if count >= 3 and len(text.split()) >= 4:
                errors.append("same_rewrite_repeated_across_source_scenes")
                break

    return errors


def classify_script_validation(errors: list[str]) -> str:
    if "audio_fit_over_soft_budget" in errors:
        return "REJECTED_AUDIO_FIT"
    return "REJECTED_PARTIAL"


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


def validate_audio_fit(
    render_duration_sec: float,
    narration_audio_sec: float,
    *,
    margin_sec: float = AUDIO_TAIL_MARGIN_SEC,
    epsilon_sec: float = AUDIO_TAIL_EPSILON_SEC,
) -> SceneValidationIssue | None:
    required_margin = float(margin_sec or 0.0)
    actual_margin = float(render_duration_sec or 0.0) - float(narration_audio_sec or 0.0)
    if actual_margin + float(epsilon_sec or 0.0) < required_margin:
        return SceneValidationIssue(
            type="audio_fit",
            scene_id=None,
            severity="blocking_error",
            detail=(
                f"Narration audio ({float(narration_audio_sec):.1f}s) exceeds video duration "
                f"({float(render_duration_sec):.1f}s) with margin {margin_sec:.1f}s."
            ),
            repair_hint="Condense narration or increase valid scene durations without exceeding scene caps.",
        )
    return None


def extend_scene_durations_for_audio_tail(
    scenes_doc: dict[str, Any],
    narration_audio_sec: float,
    *,
    margin_sec: float = AUDIO_TAIL_MARGIN_SEC,
    repair_buffer_sec: float = AUDIO_TAIL_REPAIR_BUFFER_SEC,
    max_auto_extension_sec: float = 1.5,
) -> dict[str, Any]:
    """Add a small deterministic tail pad when narration fits except margin.

    This handles the common exact-fit case (e.g. 22.4s audio over a 22.4s
    scene plan) without asking the LLM to regenerate an otherwise good Short.
    Large shortages still fail audio_fit and go through script compression.
    """
    scenes = list((scenes_doc or {}).get("scenes") or [])
    current_total = float(sum(_duration(scene) for scene in scenes))
    required_total = math.ceil(
        (
            float(narration_audio_sec or 0.0)
            + float(margin_sec or 0.0)
            + float(repair_buffer_sec or 0.0)
        )
        * 10.0
    ) / 10.0
    shortage = round(required_total - current_total, 3)
    if shortage <= 0:
        if scenes_doc is not None:
            scenes_doc["total_duration_sec"] = round(current_total, 1)
        return {"changed": False, "added_sec": 0.0, "reason": "already_fits"}
    if shortage > float(max_auto_extension_sec or 0.0):
        return {"changed": False, "added_sec": 0.0, "reason": "shortage_too_large", "shortage_sec": shortage}
    if current_total + shortage > MAX_SHORT_DURATION_SEC:
        return {"changed": False, "added_sec": 0.0, "reason": "would_exceed_short_cap", "shortage_sec": shortage}

    remaining = shortage
    notes: list[str] = []
    distribution: list[dict[str, Any]] = []
    # Prefer extending the final scene(s) first so the hook stays tight; only
    # walk backward into earlier scenes once the later ones hit their hard cap.
    # Respect per-layout hard caps and the global hard cap.
    for scene in reversed(scenes):
        if remaining <= 0:
            break
        layout = str(scene.get("layout") or "")
        hard_max = LAYOUT_DURATION_TARGETS.get(layout, (0.0, 0.0, GLOBAL_SCENE_MAX_SEC))[2]
        hard_max = min(float(hard_max), GLOBAL_SCENE_MAX_SEC)
        dur = _duration(scene)
        room = round(hard_max - dur, 3)
        if room <= 0:
            continue
        add = min(room, remaining)
        scene["duration_sec"] = round(dur + add, 1)
        remaining = round(remaining - add, 3)
        sid = scene.get("id") or scene.get("scene_id") or "?"
        added_here = round(scene["duration_sec"] - dur, 1)
        distribution.append({"scene_id": sid, "added_sec": added_here})
        notes.append(f"Extended {sid} by {added_here:.1f}s for audio tail.")

    added = round(shortage - max(remaining, 0.0), 3)
    new_total = round(sum(_duration(scene) for scene in scenes), 1)
    scenes_doc["scenes"] = scenes
    scenes_doc["total_duration_sec"] = new_total
    if remaining > 0:
        return {"changed": added > 0, "added_sec": added, "reason": "insufficient_scene_room", "shortage_sec": shortage, "notes": notes, "tail_repair_distribution": distribution}
    return {"changed": True, "added_sec": added, "reason": "extended_for_audio_tail", "notes": notes, "tail_repair_distribution": distribution}


def probe_audio_duration_sec(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if rate <= 0:
                return None
            return handle.getnframes() / float(rate)
    except Exception:
        return None


def audio_sync_summary(
    render_duration_sec: float,
    narration_audio_sec: float,
    *,
    tail_added_sec: float = 0.0,
    per_scene_padding_sec: list[float] | None = None,
    tail_repair_distribution: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Measurable audio/video sync verdict.

    Thresholds are derived from the real tail-margin/buffer constants (plus a
    small epsilon) so a healthy Short whose video intentionally outlasts the
    narration by the tail margin still PASSes instead of a false WARN.
    """
    pass_delta = round(
        AUDIO_TAIL_MARGIN_SEC + AUDIO_TAIL_REPAIR_BUFFER_SEC + AUDIO_SYNC_EPSILON_SEC,
        3,
    )
    warn_delta = round(pass_delta + 0.5, 3)
    delta = round(abs(float(render_duration_sec) - float(narration_audio_sec)), 3)
    if delta <= pass_delta:
        verdict = "PASS"
    elif delta <= warn_delta:
        verdict = "WARN"
    else:
        verdict = "FAIL"
    return {
        "render_duration_sec": round(float(render_duration_sec), 3),
        "narration_audio_sec": round(float(narration_audio_sec), 3),
        "audio_visual_delta_sec": delta,
        "tail_added_sec": round(float(tail_added_sec), 3),
        "tail_margin_sec": AUDIO_TAIL_MARGIN_SEC,
        "tail_buffer_sec": AUDIO_TAIL_REPAIR_BUFFER_SEC,
        "pass_delta_sec": pass_delta,
        "warn_delta_sec": warn_delta,
        "verdict": verdict,
        "per_scene_padding_sec": list(per_scene_padding_sec or []),
        "tail_repair_distribution": list(tail_repair_distribution or []),
    }


def _scene_id(scene: dict[str, Any], index: int) -> str:
    return str(scene.get("id") or scene.get("scene_id") or f"s{index + 1:02d}")


def _duration(scene: dict[str, Any]) -> float:
    try:
        return float(scene.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _looks_like_checklist_or_explainer(script: dict[str, Any] | None, scenes: list[dict[str, Any]]) -> bool:
    text = " ".join(
        [
            str((script or {}).get("short_format") or ""),
            str((script or {}).get("format") or ""),
            str((script or {}).get("narration") or ""),
            " ".join(_joined_scene_text(scene) for scene in scenes),
        ]
    ).lower()
    return any(term in text for term in ("checklist", "lista", "paso", "revisa", "etiqueta", "por 100 g", "ingrediente"))


def _missing_graphic_candidate(scene: dict[str, Any]) -> bool:
    if str(scene.get("layout") or "").startswith("graphic_"):
        return False
    text = _joined_scene_text(scene).lower()
    label_terms = ("etiqueta", "fibra", "azúcar", "azucar", "azúcares", "azucares", "sal", "proteína", "proteina", "por 100 g")
    comparison_terms = ("mejor", "cuidado", "opción a", "opcion a", "opción b", "opcion b")
    structured_terms = ("1/2", "1/4", "50%", "25%", "paso 1", "paso 2")
    return (
        sum(1 for term in label_terms if term in text) >= 2
        or any(term in text for term in comparison_terms) and (" vs " in text or "opción" in text or "opcion" in text)
        or any(term in text for term in structured_terms)
    )


def count_graphic_scenes(scenes: list[dict[str, Any]]) -> int:
    """Deterministic count of graphic_* layout scenes."""
    return sum(
        1 for s in (scenes or [])
        if str(s.get("layout") or "").startswith("graphic_")
    )


def is_graphic_led(scenes: list[dict[str, Any]], *, script: dict[str, Any] | None = None) -> bool:
    """A Short is intentionally graphic-led when graphics make up at least half of
    its scenes — those legitimately want 3 graphics. Below that, 3 graphics is an
    accident to be flagged."""
    scenes = list(scenes or [])
    if not scenes:
        return False
    graphic_count = count_graphic_scenes(scenes)
    return graphic_count * 2 >= len(scenes)


def is_explicit_graphic_led(script: dict[str, Any] | None) -> bool:
    """A Short is graphic-led ONLY when the input says so explicitly. Being a
    checklist/explainer is NOT enough — those are normal Shorts capped at 2
    graphics. We look for an explicit flag/marker in the plan or script."""
    if not script:
        return False
    if script.get("graphic_led") is True or script.get("is_graphic_led") is True:
        return True
    markers = " ".join(
        str(script.get(k) or "")
        for k in ("style", "mode", "short_format", "format", "visual_style")
    ).lower()
    return "graphic_led" in markers or "graphic-led" in markers


# Highest-value graphic layouts are kept first when trimming below the cap;
# setup/recap graphics (checklist/step_list) are converted to realistic scenes.
_GRAPHIC_KEEP_PRIORITY = [
    "graphic_label_callout",   # "primer ingrediente"
    "graphic_comparison",      # "fibra / azúcar / jarabes"
    "graphic_plate_ratio",
    "graphic_routine_split",
    "graphic_step_list",
    "graphic_checklist",       # setup/recap — convert first
]


def graphic_repair_targets(
    scenes: list[dict[str, Any]], *, max_keep: int = None,
) -> tuple[list[str], list[str]]:
    """Decide which graphics to keep vs. convert when over the cap.

    Returns ``(keep_ids, convert_ids)``. Keeps the highest-value graphics
    (label_callout for the first ingredient, comparison for fibra/azúcar);
    convert ids are the lower-value setup/recap graphics (e.g. graphic_checklist)
    that should become realistic short_tip/short_myth scenes."""
    if max_keep is None:
        max_keep = MAX_GRAPHIC_SCENES_PER_SHORT
    graphics = [
        (i, s) for i, s in enumerate(scenes or [])
        if str(s.get("layout") or "").startswith("graphic_")
    ]

    def rank(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        idx, scene = item
        layout = str(scene.get("layout") or "")
        pri = _GRAPHIC_KEEP_PRIORITY.index(layout) if layout in _GRAPHIC_KEEP_PRIORITY else 99
        return (pri, idx)

    ranked = sorted(graphics, key=rank)
    keep = ranked[:max_keep]
    convert = ranked[max_keep:]
    keep_ids = [_scene_id(s, i) for i, s in keep]
    convert_ids = [_scene_id(s, i) for i, s in convert]
    return keep_ids, convert_ids


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
        return {"scenes_doc": scenes_doc, "changed": False, "notes": [], "removed_ids": [], "merged": False}

    # 1. Drop the most redundant late summary scenes (never the hook or the CTA),
    #    preferring scenes in the back half, until we reach the target count.
    while len(scenes) > target_max:
        # Candidate body scenes: exclude first (hook) and last (CTA).
        body_indices = list(range(1, len(scenes) - 1))
        # Restrict to non-graphic scenes in the back half so we strip late recaps,
        # not core graphics or the opening payoff.
        late_start = max(1, len(scenes) // 2)
        candidates = [
            i for i in body_indices
            if i >= late_start and not str(scenes[i].get("layout") or "").startswith("graphic_")
        ]
        if not candidates:
            candidates = [
                i for i in body_indices
                if not str(scenes[i].get("layout") or "").startswith("graphic_")
            ]
        if not candidates:
            break
        # Pick the most redundant candidate (ties -> latest index).
        best_i = max(
            candidates,
            key=lambda i: (_redundancy_score(scenes[i], [s for j, s in enumerate(scenes) if j != i]), i),
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
        if cta_is_cta and penult_layout in {"short_tip", "short_quote", "short_pain", "short_checklist"}:
            combined_narration = f"{penult.get('narration') or ''} {cta.get('narration') or ''}".strip()
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
        return {"scenes_doc": scenes_doc, "changed": False, "notes": [], "removed_ids": [], "merged": False}

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
        issues.append(SceneValidationIssue(
            type="empty_scenes",
            scene_id=None,
            severity="repairable_error",
            detail="Scenes array is empty.",
            repair_hint="Your JSON contains an empty scenes array. Return 5-8 actual scenes. This is invalid.",
        ))
    elif scene_count < min_count or scene_count > max_count:
        issues.append(SceneValidationIssue(
            type="scene_count",
            scene_id=None,
            severity="repairable_error",
            detail=f"Scene count {scene_count} is outside recommended range {min_count}-{max_count}.",
            repair_hint="Use 5-8 scenes by default, 6-9 for checklist/explainer, 4-6 for simple hook-tip-CTA.",
        ))

    if scenes:
        first_layout = str(scenes[0].get("layout") or "")
        if first_layout != "short_hook":
            issues.append(SceneValidationIssue(
                type="first_scene_layout",
                scene_id=_scene_id(scenes[0], 0),
                severity="blocking_error",
                detail=f"First scene layout is {first_layout!r}; expected short_hook.",
                repair_hint="Regenerate with the first scene as short_hook.",
            ))
        first_motion = str(scenes[0].get("motion") or "").strip()
        if first_motion not in {"push_in", "object_reveal", "face_cut", "text_pop", "crop_shift"}:
            issues.append(SceneValidationIssue(
                type="weak_hook_motion",
                scene_id=_scene_id(scenes[0], 0),
                severity="warning",
                detail="First scene is missing a strong hook motion cue.",
                repair_hint="Use push_in, object_reveal, face_cut, or text_pop for the hook scene.",
            ))
        if not str(scenes[0].get("retention_function") or "").strip():
            issues.append(SceneValidationIssue(
                type="missing_retention_function",
                scene_id=_scene_id(scenes[0], 0),
                severity="warning",
                detail="First scene is missing retention_function metadata.",
                repair_hint="Set first scene retention_function to hook.",
            ))
        cta_text = str((script or {}).get("cta") or "").strip()
        has_cta = bool(cta_text) or any(str(scene.get("layout") or "") == "short_cta" for scene in scenes)
        if has_cta and str(scenes[-1].get("layout") or "") != "short_cta":
            issues.append(SceneValidationIssue(
                type="last_scene_cta",
                scene_id=_scene_id(scenes[-1], scene_count - 1),
                severity="blocking_error",
                detail="CTA exists but the last scene is not short_cta.",
                repair_hint="Append or regenerate a final short_cta scene.",
            ))

    scene_sum = round(sum(_duration(scene) for scene in scenes), 3)
    computed_total = round(sum(_duration(scene) for scene in scenes), 1)
    original_declared = scenes_doc.get("total_duration_sec") if scenes_doc is not None else None
    if original_declared is not None:
        try:
            declared_float = float(original_declared)
            if abs(declared_float - computed_total) > 0.11:
                issues.append(SceneValidationIssue(
                    type="total_duration_normalized",
                    scene_id=None,
                    severity="warning",
                    detail=f"total_duration_sec normalized from {original_declared} to {computed_total}.",
                    repair_hint=None,
                ))
        except (TypeError, ValueError):
            issues.append(SceneValidationIssue(
                type="total_duration_normalized",
                scene_id=None,
                severity="warning",
                detail=f"total_duration_sec normalized from {original_declared!r} to {computed_total}.",
                repair_hint=None,
            ))
    if scenes_doc is not None:
        scenes_doc["total_duration_sec"] = computed_total
    declared = computed_total

    total_for_range = float(declared or scene_sum or 0.0)
    if total_for_range and not (MIN_SHORT_DURATION_SEC <= total_for_range <= MAX_SHORT_DURATION_SEC):
        issues.append(SceneValidationIssue(
            type="duration_range",
            scene_id=None,
            severity="repairable_error",
            detail=f"Total duration {total_for_range:.1f}s is outside hard range 20-60s.",
            repair_hint="Keep final duration within 20-60s; do not stretch individual scenes.",
        ))
    elif total_for_range and not (IDEAL_MIN_SHORT_DURATION_SEC <= total_for_range <= IDEAL_MAX_SHORT_DURATION_SEC):
        issues.append(SceneValidationIssue(
            type="duration_ideal",
            scene_id=None,
            severity="warning",
            detail=f"Total duration {total_for_range:.1f}s is outside ideal 28-38s but within hard range.",
            repair_hint="Render is allowed if pacing and audio-fit are strong.",
        ))

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
            issues.append(SceneValidationIssue(
                type="layout",
                scene_id=sid,
                severity="blocking_error",
                detail=f"Unsupported scene layout {layout!r}.",
                repair_hint="Use only supported short_* or graphic_* layouts.",
            ))
            continue

        if layout.startswith("graphic_"):
            graphic_count += 1
            if index == 0 or index == len(scenes) - 1:
                issues.append(SceneValidationIssue(
                    type="graphic_setup_or_cta",
                    scene_id=sid,
                    severity="repairable_error",
                    detail=f"Graphic scene {sid} is used as setup or CTA.",
                    repair_hint="Use realistic short_* footage for hook/setup/CTA; reserve graphics for proof/payoff moments.",
                ))

        motion = str(scene.get("motion") or "").strip()
        if motion in {"", "none", "static"}:
            static_run += 1
        else:
            static_run = 0
        if static_run > 3:
            issues.append(SceneValidationIssue(
                type="repeated_static_scenes",
                scene_id=sid,
                severity="warning",
                detail="More than 3 consecutive scenes are static or missing motion.",
                repair_hint="Vary motion with crop_shift, push_in, object_reveal, text_pop, or cutaway.",
            ))

        on_screen_raw = str(scene.get("on_screen_text") or "").strip().lower()
        if previous_text and on_screen_raw and on_screen_raw == previous_text:
            issues.append(SceneValidationIssue(
                type="repeated_on_screen_text",
                scene_id=sid,
                severity="warning",
                detail=f"Scene {sid} repeats the previous on_screen_text structure.",
                repair_hint="Change the overlay text or visual beat so the Short does not feel like a slideshow.",
            ))
        previous_text = on_screen_raw or previous_text

        if count_spoken_words(str(scene.get("on_screen_text") or "")) > 6:
            text_heavy_run += 1
        else:
            text_heavy_run = 0
        if text_heavy_run > 2:
            issues.append(SceneValidationIssue(
                type="text_heavy_run",
                scene_id=sid,
                severity="warning",
                detail="Too many consecutive text-heavy scenes.",
                repair_hint="Shorten overlays and move detail to narration, caption, or a single graphic payoff.",
            ))

        if dur > GLOBAL_SCENE_MAX_SEC:
            issues.append(SceneValidationIssue(
                type="duration_cap",
                scene_id=sid,
                severity="repairable_error",
                detail=f"Scene {sid} duration {dur:.1f}s exceeds global hard max {GLOBAL_SCENE_MAX_SEC:.1f}s.",
                repair_hint=f"No scene may exceed 5.0 sec in a normal Short. Split or regenerate {sid}.",
            ))
        target = LAYOUT_DURATION_TARGETS.get(layout)
        if target:
            target_min, target_max, hard_max = target
            if dur > hard_max:
                issues.append(SceneValidationIssue(
                    type="duration_cap",
                    scene_id=sid,
                    severity="repairable_error",
                    detail=f"Scene {sid} ({layout}) duration {dur:.1f}s exceeds hard max {hard_max:.1f}s.",
                    repair_hint=f"No scene may exceed {hard_max:.1f} sec for layout {layout}. Split or regenerate {sid}.",
                ))
            elif dur and not (target_min <= dur <= target_max):
                issues.append(SceneValidationIssue(
                    type="duration_pacing",
                    scene_id=sid,
                    severity="warning",
                    detail=f"Scene {sid} ({layout}) duration {dur:.1f}s is outside target {target_min:.1f}-{target_max:.1f}s.",
                    repair_hint="Allowed if pacing remains strong and hard caps are respected.",
                ))

        narration = str(scene.get("narration") or "")
        estimated_scene_audio = estimate_spanish_narration_sec(narration)
        if narration.strip() and estimated_scene_audio > dur + 0.3:
            issues.append(SceneValidationIssue(
                type="scene_narration_fit",
                scene_id=sid,
                severity="repairable_error",
                detail=f"Scene {sid} narration estimates {estimated_scene_audio:.1f}s for {dur:.1f}s scene (exceeds 0.3s tolerance).",
                repair_hint="Condense narration or increase scene duration within layout cap. Do not exceed hard cap.",
            ))
        elif narration.strip() and estimated_scene_audio > dur:
            issues.append(SceneValidationIssue(
                type="scene_narration_fit",
                scene_id=sid,
                severity="warning",
                detail=f"Scene {sid} narration estimates {estimated_scene_audio:.1f}s for {dur:.1f}s scene.",
                repair_hint="Consider condensing narration slightly or adjusting duration.",
            ))

        on_screen_text = str(scene.get("on_screen_text") or "").strip().upper()
        if on_screen_text in PASSIVE_CTA_TEXTS:
            issues.append(SceneValidationIssue(
                type="passive_cta",
                scene_id=sid,
                severity="repairable_error",
                detail=f"Scene {sid} CTA text '{on_screen_text}' is passive/status-like.",
                repair_hint="Use GUARDA ESTA LISTA, GUÁRDALO PARA LA COMPRA, MÍRALO ANTES DE COMPRAR PAN, or ÚSALO EN EL SÚPER.",
            ))

        # validate source_scene_ids vs covers_items
        covers = scene.get('covers_items') or []
        source_ids = scene.get('source_scene_ids') or []
        if covers and not source_ids and layout not in ('short_hook', 'short_cta', 'short_quote'):
            issues.append(SceneValidationIssue(
                type='missing_source_scene_ids',
                scene_id=sid,
                severity='repairable_error',
                detail=f'Scene {sid} covers items but has empty source_scene_ids.',
                repair_hint='If a scene covers an idea item, it must reference the supporting source_scene_ids.'
            ))
        if source_ids and script:
            valid_ids = {s.get('source_scene_id') for s in (script.get('source_mapped_flow') or []) if s.get('source_scene_id')}
            invalid_ids = [sid for sid in source_ids if valid_ids and sid not in valid_ids]
            if invalid_ids:
                issues.append(SceneValidationIssue(
                    type='invalid_source_scene_ids',
                    scene_id=sid,
                    severity='repairable_error',
                    detail=f'Scene {sid} references invalid source_scene_ids: {invalid_ids}',
                    repair_hint='Use only valid source_scene_ids provided in the SCRIPT context.'
                ))
        if _missing_graphic_candidate(scene):
            missing_graphic_candidates += 1

    if graphic_count > MAX_GRAPHIC_SCENES_PER_SHORT:
        explicit_graphic_led = is_explicit_graphic_led(script)
        keep_ids, convert_ids = graphic_repair_targets(scenes)
        convert_txt = ", ".join(convert_ids) or "the lowest-value graphic(s)"
        keep_txt = ", ".join(keep_ids) or "the highest-value graphics"
        if explicit_graphic_led and graphic_count == 3:
            # Input explicitly opted into a graphic-led Short: 3 is allowed but flagged.
            issues.append(SceneValidationIssue(
                type="graphic_count",
                scene_id=None,
                severity="warning",
                detail="Short has 3 graphic scenes (graphic-led requested). Confirm pacing stays strong.",
                repair_hint="Keep 3 only if intentionally graphic-led; otherwise reduce to 1-2.",
            ))
        else:
            # Normal Short over the 2-graphic cap -> repairable error.
            issues.append(SceneValidationIssue(
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
            ))

    if missing_graphic_candidates and graphic_count >= MAX_GRAPHIC_SCENES_PER_SHORT:
        issues.append(SceneValidationIssue(
            type="missing_graphic_warning",
            scene_id=None,
            severity="warning",
            detail="A stock scene contains visualizable label/checklist structure, but the Short already has 2 graphics.",
            repair_hint="Do not add a third graphic; improve the stock visual_prompt instead.",
        ))

    if script:
        contract = (script or {}).get("idea_contract") or {}
        # Detect if this is a 5-error bread Short
        is_5_error_bread = (
            (contract.get("original_count") == 5 or contract.get("final_count") == 5)
            and any(term in str(script.get("narration") or "").lower() for term in ("pan", "bread", "hogaza"))
        )
        if is_5_error_bread:
            # 1. Enforce total duration >= 25.5s
            if total_for_range and total_for_range < 25.5:
                issues.append(SceneValidationIssue(
                    type="duration_range",
                    scene_id=None,
                    severity="repairable_error",
                    detail=f"Total duration {total_for_range:.1f}s is too short for a 5-error Short (minimum 25.5s required).",
                    repair_hint="Increase individual scene durations to 3.2-4.0s for errors, 4.2-5.0s for payoff, 2.4-2.8s for CTA to reach 26-30s."
                ))
            # 2. Enforce graphic_checklist payoff scene layout (scene right before CTA)
            if len(scenes) >= 2:
                payoff_idx = len(scenes) - 2
                payoff_scene = scenes[payoff_idx]
                payoff_id = _scene_id(payoff_scene, payoff_idx)
                if payoff_scene.get("layout") != "graphic_checklist":
                    issues.append(SceneValidationIssue(
                        type="payoff_layout",
                        scene_id=payoff_id,
                        severity="repairable_error",
                        detail=f"Payoff scene {payoff_id} layout is {payoff_scene.get('layout')!r}; expected graphic_checklist for 5-error bread Short.",
                        repair_hint="Use layout 'graphic_checklist' for the payoff scene to render a readable saveable checklist card."
                    ))

    if script and (script.get("idea_items") or (script.get("idea_contract") or {}).get("must_preserve_count")):
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
    if len(active_issues) == 1 and active_issues[0].type == "duration_cap" and active_issues[0].scene_id:
        only_issue = active_issues[0]
        original = next((scene for scene in scenes if _scene_id(scene, -1) == only_issue.scene_id), {})
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
            original = next((scene for scene in scenes if _scene_id(scene, -1) == issue.scene_id), {})
            layout = original.get("layout") or ""
            if issue.type == "scene_narration_fit":
                if layout == "short_hook":
                    issue_instrs.extend([
                        f"- Fix {issue.scene_id}:",
                        "  - Hook narration is too long for 3.0 sec.",
                        "  - Replace with a 4-6 word hook that preserves the current idea.",
                        "  - Keep the longer idea in on_screen_text or next scene."
                    ])
                elif layout == "graphic_label_callout":
                    issue_instrs.extend([
                        f"- Fix {issue.scene_id}:",
                        "  - Current narration is too long for a single graphic_label_callout scene.",
                        "  - Do not exceed 5.0 sec.",
                        "  - Shorten narration while preserving the current source-supported point.",
                        "  - Move examples/details into layout_payload callouts.",
                        "  - Or split into:",
                        "    s06a short_tip 3.2s: compact setup line.",
                        "    s06b graphic_label_callout 4.2s: compact source-supported label."
                    ])
                elif layout == "short_quote":
                    issue_instrs.extend([
                        f"- Fix {issue.scene_id}:",
                        "  - Quote narration is too long.",
                        "  - Shorten to one source-supported sentence.",
                        "  - Keep nuance in on_screen_text or caption only if readable."
                    ])
                elif layout == "short_cta":
                    issue_instrs.extend([
                        f"- Fix {issue.scene_id}:",
                        "  - CTA narration is too long.",
                        '  - Shorten to: "Guárdalo para la compra." or "Úsalo en el súper."'
                    ])
                else:
                    issue_instrs.append(f"- Fix {issue.scene_id}: {issue.detail}")
                    issue_instrs.append("- Cut this scene's narration to one short sentence (about 6-8 spoken words), or move the extra sentence into the next scene. Do not exceed the layout cap.")
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
                    issue_instrs.append("- No scene may exceed 5.0 sec in a normal Short; split, shorten, or regenerate the scene.")
            if layout != "short_cta":
                suggested_scene_plan.append({
                    "id": f"{issue.scene_id}a",
                    "duration_sec": 3.4,
                    "layout": "short_tip",
                    "on_screen_text": str(original.get("on_screen_text") or "COMPARA CON OTRO")[:32],
                })
                suggested_scene_plan.append({
                    "id": f"{issue.scene_id}b",
                    "duration_sec": 3.2,
                    "layout": "short_tip",
                    "on_screen_text": "ETIQUETA CLARA",
                })
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
                    f"with supermarket/kitchen visuals; keep on_screen_text like \"{ost}\". Do NOT keep it as a graphic."
                )
                suggested_scene_plan.append({
                    "id": cid,
                    "duration_sec": 3.0,
                    "layout": "short_myth",
                    "on_screen_text": ost,
                })
            if not convert_ids:
                issue_instrs.append(
                    "- Convert setup/recap graphics into stock short_tip or short_myth scenes with realistic visuals."
                )
        elif issue.type == "missing_item_coverage":
            repair_modes.append("restore_item_coverage")
            m = re.search(r"item\s+(\w+)", str(issue.detail or ""), re.IGNORECASE)
            item_ref = m.group(1) if m else "the missing item"
            issue_instrs.extend([
                f"- Required idea item {item_ref} is not covered by any scene.",
                f"- Give item {item_ref} its OWN dedicated scene; do not merge or cram it into another scene's narration.",
                "- Every promised idea item must map 1:1 to its own scene so nothing is dropped.",
            ])
        elif issue.type == "passive_cta":
            repair_modes.append("cta_rewrite")
            issue_instrs.append("- Rewrite passive CTA text to an action CTA such as GUARDA ESTA LISTA or GUÁRDALO PARA LA COMPRA.")
        elif issue.type == "audio_fit":
            repair_modes.append("audio_fit")
            contract = (script or {}).get("idea_contract") or {}
            from video_agent.shorts.idea_preservation import allowed_spoken_points_from_contract

            allowed_points = allowed_spoken_points_from_contract(contract)
            issue_instrs.extend([
                "AUDIO-FIT REPAIR PLAN:",
                "- Actual narration audio exceeds video duration.",
                "- Condense narration; do not stretch scenes above caps.",
                (
                    f"- Keep all {allowed_points} promised {contract.get('count_label') or 'items'}."
                    if allowed_points
                    else "- For implicit lists, keep 3-4 spoken checklist points if it improves retention."
                ),
                "- Move supporting detail to on_screen_text or graphic payload.",
                "- Regenerate scenes after script compression."
            ])
        elif issue.type == "script_word_budget":
            repair_modes.append("script_condense")
            issue_instrs.append("- Compress narration while preserving source-supported promised items.")
            issue_instrs.append("- Treat 35s as a soft target; use split_recommended if quality cannot fit the Short ceiling.")
        elif issue.type == "slideshow_risk":
            repair_modes.append("reduce_slideshow_density")
            issue_instrs.append("- Reduce only the exact dense checklist/graphic scene identified by the validator.")
            issue_instrs.append("- Do not convert good footage-led item scenes into short_checklist scenes.")
            if issue.repair_hint:
                issue_instrs.append(f"- {issue.repair_hint}")
        elif issue.type == "payoff_layout":
            repair_modes.append("payoff_checklist")
            issue_instrs.extend([
                f"- Fix {issue.scene_id}:",
                "  - Convert the payoff scene to layout 'graphic_checklist'.",
                "  - Use title: 'MEJOR ASÍ'.",
                "  - Set items to: ['Porción visible', 'Plato pequeño', 'Comida completa'].",
                "  - Set duration_sec to 4.2-5.0 seconds."
            ])
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

ALLOWED_GRAPHIC_VARIANTS = {
    "brand_default",
    "warm_olive",
    "soft_clay",
    "cream_focus",
    "evening_calm",
}

ALLOWED_GRAPHIC_VISUAL_TONES = {
    "calm",
    "focus",
    "warning_soft",
    "positive",
    "evening",
}

ALLOWED_GRAPHIC_BACKGROUND_MODES = {
    "clean",
    "radial",
    "paper",
    "video_blur",
}

ALLOWED_GRAPHIC_SURFACE_STYLES = {
    "none",
    "soft_card",
    "editorial",
    "plate_focus",
}

PLATE_RATIO_TOTAL = 100.0
PLATE_RATIO_EPSILON = 0.01
MAX_GRAPHIC_SCENES_PER_SHORT = 2
GRAPHIC_MIN_DURATION_SEC = 2.5
GRAPHIC_MAX_DURATION_SEC = 5.0
GRAPHIC_LAYOUT_DURATION_TARGETS = {
    "graphic_checklist": (4.2, 5.0, 5.0),
    "graphic_step_list": (3.0, 4.0, 4.5),
    "graphic_plate_ratio": (3.0, 4.5, 5.0),
    "graphic_label_callout": (3.5, 5.0, 5.0),
    "graphic_comparison": (3.5, 4.5, 5.0),
    "graphic_routine_split": (3.5, 5.0, 5.0),
}
PASSIVE_CTA_TEXTS = {
    "CHECKLIST GUARDADA",
    "LISTA COMPLETA",
    "FIN",
    "CONSEJO FINAL",
}
BREAD_LABEL_TOPIC_TERMS = (
    "pan",
    "marrón",
    "marron",
    "integral",
    "etiqueta",
    "ingrediente",
    "fibra",
)
BREAD_LABEL_HOOK_VISUAL_TERMS = (
    "bread",
    "pan",
    "package",
    "packaging",
    "label",
    "ingredient",
    "supermarket",
    "shelf",
    "basket",
)

# Text-density limits (keep in sync with the TypeScript Zod schemas).
_PLATE_LABEL_MAX = 48
_CHECKLIST_ITEM_MAX = 48
_STEP_TEXT_MAX = 56
_FOOTER_MAX = 72
_TITLE_MAX_PHASE15 = 60
_LABEL_CALLOUT_PRODUCT_MAX = 36
_LABEL_CALLOUT_LABEL_MAX = 22
_LABEL_CALLOUT_VALUE_MAX = 26
_LABEL_CALLOUT_NOTE_MAX = 48
_COMPARISON_HEADING_MAX = 24
_COMPARISON_TEXT_MAX = 68
_COMPARISON_BADGE_MAX = 28
_ROUTINE_TOTAL_MAX = 16
_ROUTINE_TIME_MAX = 16
_ROUTINE_TEXT_MAX = 52

FORBIDDEN_HEALTH_MARKETING_WORDS = (
    "veneno",
    "prohibido",
    "nunca",
    "milagro",
    "cura",
    "doctores no quieren",
)


def validate_short_graphic_scenes(scenes: list[dict[str, Any]]) -> list[str]:
    """Validate graphic scenes in place. Raises ``ValueError`` on hard errors.

    Returns a list of non-fatal warnings (e.g. duration / count advisories).
    Also inserts safe compatibility stubs for the rich ``Scene`` fields graphic
    scenes do not use directly, so render props stay schema-compatible.
    """
    warnings: list[str] = []
    graphic_count = 0
    is_bread_label_topic = _looks_like_bread_label_topic(scenes)

    for index, scene in enumerate(scenes):
        sid = scene.get("id", index)
        layout = scene.get("layout")

        _validate_non_graphic_scene_tuning(scene, sid, layout, index, is_bread_label_topic, warnings)

        if "scene_id" in scene and "id" not in scene:
            raise ValueError(
                f"Scene at index {index} uses scene_id but is missing id. "
                "Normalize scene_id -> id before render props."
            )

        if not isinstance(layout, str) or not layout.startswith("graphic_"):
            continue

        graphic_count += 1

        if layout not in SUPPORTED_GRAPHIC_LAYOUTS:
            raise ValueError(
                f"Scene {sid} uses unsupported graphic layout {layout}. "
                f"Supported graphic layouts: {', '.join(sorted(SUPPORTED_GRAPHIC_LAYOUTS))}."
            )

        # Compatibility stubs for the existing rich Scene type.
        scene.setdefault("visual_type", "graphic")
        if not str(scene.get("on_screen_text") or "").strip():
            scene["on_screen_text"] = _title_from_payload(scene.get("layout_payload", {}))
        scene.setdefault("caption", "")
        scene.setdefault("motion", "none")
        scene.setdefault("asset_refs", {})
        if isinstance(scene.get("asset_refs"), dict):
            scene["asset_refs"].setdefault("background", "")

        payload = scene.get("layout_payload")
        if not isinstance(payload, dict):
            raise ValueError(f"Graphic scene {sid} ({layout}) is missing layout_payload.")

        _validate_visual_style_fields(payload, sid, layout)
        _validate_title(payload, sid, layout)
        _validate_footer(payload, sid, layout, warnings)

        if layout == "graphic_plate_ratio":
            _validate_plate_ratio(payload, sid, warnings)
        elif layout == "graphic_checklist":
            _validate_checklist(payload, sid, warnings)
        elif layout == "graphic_step_list":
            _validate_step_list(payload, sid, warnings)
        elif layout == "graphic_label_callout":
            _validate_label_callout(payload, sid, warnings)
        elif layout == "graphic_comparison":
            _validate_comparison(payload, sid)
        elif layout == "graphic_routine_split":
            _validate_routine_split(payload, sid, warnings)

        _validate_graphic_duration(scene, sid, layout, warnings)

    if graphic_count > MAX_GRAPHIC_SCENES_PER_SHORT:
        warnings.append(
            f"Short has {graphic_count} graphic scenes; "
            f"max recommended is {MAX_GRAPHIC_SCENES_PER_SHORT} for MVP."
        )

    return warnings


def _joined_scene_text(scene: dict[str, Any]) -> str:
    payload = scene.get("layout_payload")
    payload_text = ""
    if isinstance(payload, dict):
        payload_text = " ".join(str(v) for v in payload.values() if isinstance(v, (str, int, float)))
    return " ".join(
        str(scene.get(key) or "")
        for key in ("narration", "on_screen_text", "caption", "visual_prompt")
    ) + " " + payload_text


def _looks_like_bread_label_topic(scenes: list[dict[str, Any]]) -> bool:
    text = " ".join(_joined_scene_text(scene).lower() for scene in scenes)
    return any(term in text for term in BREAD_LABEL_TOPIC_TERMS)


def _validate_non_graphic_scene_tuning(
    scene: dict[str, Any],
    sid: Any,
    layout: Any,
    index: int,
    is_bread_label_topic: bool,
    warnings: list[str],
) -> None:
    on_screen_text = str(scene.get("on_screen_text") or "").strip().upper()
    if on_screen_text in PASSIVE_CTA_TEXTS:
        warnings.append(
            f"Scene {sid} CTA text '{on_screen_text}' is passive/status-like; prefer "
            "GUARDA ESTA LISTA or GUÁRDALO PARA LA COMPRA."
        )

    if layout == "short_myth" and float(scene.get("duration_sec") or 0) > 3.0:
        warnings.append(f"Scene {sid} myth/setup duration exceeds 3.0s; keep myth beats short.")

    if on_screen_text == "MITO RÁPIDO" and float(scene.get("duration_sec") or 0) > 3.0:
        warnings.append(f"Scene {sid} keeps generic MITO RÁPIDO too long; use a specific myth statement.")

    if index == 0 and layout == "short_hook" and is_bread_label_topic:
        visual_prompt = str(scene.get("visual_prompt") or "").lower()
        if not any(term in visual_prompt for term in BREAD_LABEL_HOOK_VISUAL_TERMS):
            warnings.append(
                f"Scene {sid} bread/label hook visual is too generic; include bread, package, label, "
                "supermarket shelf, or shopping basket imagery."
            )


def _validate_graphic_duration(scene: dict[str, Any], sid: Any, layout: str, warnings: list[str]) -> None:
    dur = float(scene.get("duration_sec") or 0)
    target_min, target_max, hard_max = GRAPHIC_LAYOUT_DURATION_TARGETS.get(
        layout,
        (GRAPHIC_MIN_DURATION_SEC, GRAPHIC_MAX_DURATION_SEC, GRAPHIC_MAX_DURATION_SEC),
    )

    if dur > hard_max:
        raise ValueError(
            f"Graphic scene {sid} ({layout}) duration {dur}s exceeds hard max {hard_max}s; "
            "graphics must be fast explanatory bursts, not slides."
        )

    if not (target_min <= dur <= target_max):
        warnings.append(
            f"Scene {sid} ({layout}) graphic duration {dur}s is outside the target "
            f"{target_min}-{target_max}s range."
        )


def _validate_optional_choice(
    payload: dict,
    field: str,
    allowed: set[str],
    sid: Any,
    layout: str,
) -> None:
    value = payload.get(field)
    if value is None:
        return
    if not isinstance(value, str) or value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(
            f"Graphic scene {sid} ({layout}) has invalid {field}: {value!r}. "
            f"Allowed values: {allowed_values}."
        )


def _validate_visual_style_fields(payload: dict, sid: Any, layout: str) -> None:
    _validate_optional_choice(payload, "variant", ALLOWED_GRAPHIC_VARIANTS, sid, layout)
    _validate_optional_choice(payload, "visual_tone", ALLOWED_GRAPHIC_VISUAL_TONES, sid, layout)
    _validate_optional_choice(payload, "background_mode", ALLOWED_GRAPHIC_BACKGROUND_MODES, sid, layout)
    _validate_optional_choice(payload, "surface_style", ALLOWED_GRAPHIC_SURFACE_STYLES, sid, layout)


def _validate_title(payload: dict, sid: Any, layout: str) -> None:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Graphic scene {sid} ({layout}) requires a non-empty title.")
    max_len = _TITLE_MAX_PHASE15 if layout in {
        "graphic_label_callout",
        "graphic_comparison",
        "graphic_routine_split",
    } else 48
    if len(title) > max_len:
        raise ValueError(f"Graphic scene {sid} title exceeds {max_len} chars: {len(title)}.")


def _validate_footer(payload: dict, sid: Any, layout: str, warnings: list[str]) -> None:
    footer = payload.get("footer")
    if footer is not None and isinstance(footer, str) and len(footer) > _FOOTER_MAX:
        warnings.append(f"Scene {sid} ({layout}) footer exceeds {_FOOTER_MAX} chars: {len(footer)}.")


def _validate_plate_ratio(payload: dict, sid: Any, warnings: list[str]) -> None:
    segments = payload.get("segments")
    if not isinstance(segments, list) or not (2 <= len(segments) <= 4):
        raise ValueError(f"graphic_plate_ratio scene {sid} requires 2-4 segments.")
    total = sum(float(s.get("value", 0)) for s in segments if isinstance(s, dict))
    if abs(total - PLATE_RATIO_TOTAL) > PLATE_RATIO_EPSILON:
        raise ValueError(
            f"graphic_plate_ratio scene {sid} segments must sum to {int(PLATE_RATIO_TOTAL)} "
            f"+/- {PLATE_RATIO_EPSILON}; got {total}."
        )
    for s in segments:
        label = s.get("label") if isinstance(s, dict) else None
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"graphic_plate_ratio scene {sid} has a segment with an empty label.")
        if len(label) > _PLATE_LABEL_MAX:
            warnings.append(f"Scene {sid} plate label exceeds {_PLATE_LABEL_MAX} chars: '{label}'.")


def _validate_checklist(payload: dict, sid: Any, warnings: list[str]) -> None:
    items = payload.get("items")
    if not isinstance(items, list) or not (2 <= len(items) <= 5):
        raise ValueError(f"graphic_checklist scene {sid} requires 2-5 items.")
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"graphic_checklist scene {sid} has an empty item.")
        if len(item) > _CHECKLIST_ITEM_MAX:
            warnings.append(f"Scene {sid} checklist item exceeds {_CHECKLIST_ITEM_MAX} chars: '{item}'.")


def _validate_step_list(payload: dict, sid: Any, warnings: list[str]) -> None:
    steps = payload.get("steps")
    if not isinstance(steps, list) or not (2 <= len(steps) <= 4):
        raise ValueError(f"graphic_step_list scene {sid} requires 2-4 steps.")
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError(f"graphic_step_list scene {sid} has a non-object step.")
        text = step.get("text")
        label = step.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"graphic_step_list scene {sid} has a step with an empty label.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"graphic_step_list scene {sid} has a step with empty text.")
        if len(text) > _STEP_TEXT_MAX:
            warnings.append(f"Scene {sid} step text exceeds {_STEP_TEXT_MAX} chars: '{text}'.")


def _title_from_payload(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("title") or payload.get("productLabel") or "").strip()


def _warn_if_long(value: Any, max_len: int, label: str, sid: Any, warnings: list[str]) -> None:
    if isinstance(value, str) and len(value) > max_len:
        warnings.append(f"Scene {sid} {label} exceeds {max_len} chars: '{value}'.")


def _require_short_string(value: Any, max_len: int, label: str, sid: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Graphic scene {sid} requires non-empty {label}.")
    if len(value) > max_len:
        raise ValueError(f"Graphic scene {sid} {label} exceeds {max_len} chars: {len(value)}.")
    return value


def _validate_label_callout(payload: dict, sid: Any, warnings: list[str]) -> None:
    product_label = payload.get("productLabel")
    _warn_if_long(product_label, _LABEL_CALLOUT_PRODUCT_MAX, "productLabel", sid, warnings)
    callouts = payload.get("callouts")
    if not isinstance(callouts, list) or not (2 <= len(callouts) <= 4):
        got = len(callouts) if isinstance(callouts, list) else "missing"
        raise ValueError(f"graphic_label_callout scene {sid} callouts must contain 2-4 items, got {got}.")
    for callout in callouts:
        if not isinstance(callout, dict):
            raise ValueError(f"graphic_label_callout scene {sid} has a non-object callout.")
        _require_short_string(callout.get("label"), _LABEL_CALLOUT_LABEL_MAX, "callout.label", sid)
        _require_short_string(callout.get("value"), _LABEL_CALLOUT_VALUE_MAX, "callout.value", sid)
        note = callout.get("note")
        _warn_if_long(note, _LABEL_CALLOUT_NOTE_MAX, "callout.note", sid, warnings)


def _check_forbidden_language(value: Any, sid: Any, field: str) -> None:
    if not isinstance(value, str):
        return
    lower = value.lower()
    for word in FORBIDDEN_HEALTH_MARKETING_WORDS:
        if word in lower:
            raise ValueError(
                f"graphic_comparison scene {sid} contains forbidden health-marketing word "
                f"'{word}' in {field}."
            )


def _validate_comparison(payload: dict, sid: Any) -> None:
    _check_forbidden_language(payload.get("title"), sid, "title")
    _check_forbidden_language(payload.get("footer"), sid, "footer")
    for side_name in ("left", "right"):
        side = payload.get(side_name)
        if not isinstance(side, dict):
            raise ValueError(f"graphic_comparison scene {sid} requires object '{side_name}'.")
        _require_short_string(side.get("heading"), _COMPARISON_HEADING_MAX, f"{side_name}.heading", sid)
        _require_short_string(side.get("text"), _COMPARISON_TEXT_MAX, f"{side_name}.text", sid)
        badge = side.get("badge")
        if badge is not None:
            _require_short_string(badge, _COMPARISON_BADGE_MAX, f"{side_name}.badge", sid)
        for field in ("heading", "text", "badge"):
            _check_forbidden_language(side.get(field), sid, f"{side_name}.{field}")


def _validate_routine_split(payload: dict, sid: Any, warnings: list[str]) -> None:
    total_label = payload.get("totalLabel")
    _warn_if_long(total_label, _ROUTINE_TOTAL_MAX, "totalLabel", sid, warnings)
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not (2 <= len(blocks) <= 4):
        got = len(blocks) if isinstance(blocks, list) else "missing"
        raise ValueError(f"graphic_routine_split scene {sid} blocks must contain 2-4 items, got {got}.")
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError(f"graphic_routine_split scene {sid} has a non-object block.")
        _require_short_string(block.get("time"), _ROUTINE_TIME_MAX, "block.time", sid)
        _require_short_string(block.get("text"), _ROUTINE_TEXT_MAX, "block.text", sid)


def classify_script_validation(errors: list[str]) -> str:
    if not errors:
        return "PASSED"
    if len(errors) == 1 and errors[0] == "audio_fit_over_soft_budget":
        return "REJECTED_AUDIO_FIT"
    return "REJECTED_PARTIAL"



