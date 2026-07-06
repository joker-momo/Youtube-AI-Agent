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


def acceptable_funnel_ctas(
    short_plan: dict[str, Any],
    source_map: dict[str, Any] | None = None,
    *,
    channel_config: dict[str, Any] | None = None,
    long_video_title: str = "",
) -> list[str]:
    """Every CTA phrasing the deterministic gates accept, preferred first.

    Contract (kept in sync with the script prompt):
    - An explicit, SPECIFIC funnel cta (source_map/short_plan operator override)
      is strict — it is the only accepted phrasing (unchanged legacy behavior).
    - Otherwise the model writes a NATURAL CTA that names the parent long
      video's content in its own words, so no exact phrase can be predicted —
      the gate only requires the channel direction (the word 'canal'). The
      resolved topic template stays FIRST as the preferred sentence for the
      deterministic repair, and the plain defaults remain acceptable.
      (bug-484: an exact-phrase gate disagreed with the prompt and rejected
      every otherwise-valid candidate, collapsing the spoken CTA to generic.)
    """
    from video_agent.shorts.source_map import is_generic_cta, resolve_funnel_cta

    funnel_cfg = ((channel_config or {}).get("shorts") or {}).get("funnel") or {}
    explicit = str(
        ((source_map or {}).get("funnel") or {}).get("cta")
        or ((short_plan or {}).get("funnel") or {}).get("cta")
        or ""
    ).strip()
    if explicit and not is_generic_cta(funnel_cfg, explicit):
        return [explicit]

    accepted: list[str] = []
    if channel_config is not None:
        has_url = bool(
            ((source_map or {}).get("funnel") or {}).get("long_video_url")
            or (short_plan or {}).get("long_video_url")
        )
        resolved = resolve_funnel_cta(
            funnel_cfg, short_plan or {}, has_url=has_url, extra_text=long_video_title
        )
        if resolved:
            accepted.append(resolved)
    for fallback in (
        explicit,  # a generic explicit stays acceptable
        str(funnel_cfg.get("default_cta_without_url") or ""),
        str(funnel_cfg.get("default_cta_with_url") or ""),
        "Vídeo completo en el canal.",
    ):
        fallback = fallback.strip()
        if fallback and fallback not in accepted:
            accepted.append(fallback)
    # Natural CTAs name the long video's content in the model's own words; any
    # phrasing that carries the channel direction satisfies the funnel.
    accepted.append("canal")
    return accepted


def funnel_cta_max_words(channel_config: dict[str, Any] | None) -> int:
    """Configured spoken-CTA word budget (funnel.cta_max_words), default 12.

    12 gives a natural complete Spanish sentence room to name the long video's
    topic ("Descubre el error del aceite en ayunas en el canal." = 10 words);
    the old 8-word cap forced telegraphic fragments.
    """
    try:
        return int(
            (((channel_config or {}).get("shorts") or {}).get("funnel") or {}).get(
                "cta_max_words", 12
            )
        )
    except (TypeError, ValueError):
        return 12


def validate_full_short_script_candidate(
    script: dict[str, Any],
    short_plan: dict[str, Any],
    source_map: dict[str, Any] | None = None,
    *,
    channel_config: dict[str, Any] | None = None,
    long_video_title: str = "",
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

    accepted_ctas = acceptable_funnel_ctas(
        short_plan,
        source_map,
        channel_config=channel_config,
        long_video_title=long_video_title,
    )

    cta_text = str(script.get("cta") or "").strip()
    if not has_cta_beat and not cta_text:
        errors.append("missing_cta")
    elif cta_text:
        word_count = len(re.findall(r"\w+", cta_text))
        if word_count > funnel_cta_max_words(channel_config):
            errors.append("cta_too_long_exceeds_8_words")
        if not any(normalize_str(c) in normalize_str(cta_text) for c in accepted_ctas):
            errors.append("missing_expected_funnel_cta")

    # The spoken CTA is the final CTA beat's narration (it feeds TTS via the
    # scenes), NOT the cta metadata field. Enforce that the channel direction is
    # actually spoken — otherwise the Short never drives viewers to the channel,
    # breaking the funnel. The cta field passing is not sufficient.
    cta_beat_narr = ""
    for b in beats:
        if isinstance(b, dict) and str(b.get("purpose") or "").lower() == "cta":
            cta_beat_narr = str(b.get("narration") or "")
            break
    if cta_beat_narr and not any(
        normalize_str(c) in normalize_str(cta_beat_narr) for c in accepted_ctas
    ):
        errors.append("cta_beat_missing_channel_direction")

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


def cta_beat_has_channel_direction(
    script: dict[str, Any],
    short_plan: dict[str, Any],
    source_map: dict[str, Any] | None = None,
    *,
    channel_config: dict[str, Any] | None = None,
    long_video_title: str = "",
) -> bool:
    """True when the spoken CTA beat narration carries the channel direction.

    Mirrors the ``cta_beat_missing_channel_direction`` gate so other modules
    (e.g. QA normalization) can treat the deterministic check as authoritative.
    """
    accepted = acceptable_funnel_ctas(
        short_plan, source_map, channel_config=channel_config, long_video_title=long_video_title
    )

    def norm(text: str) -> str:
        return re.sub(r"\W+", " ", text.lower()).strip()

    for b in script.get("beats") or []:
        if isinstance(b, dict) and str(b.get("purpose") or "").lower() == "cta":
            narr = norm(str(b.get("narration") or ""))
            return any(norm(c) in narr for c in accepted)
    return False


def repair_cta_beat_channel_direction(
    script: dict[str, Any],
    short_plan: dict[str, Any],
    source_map: dict[str, Any] | None = None,
    *,
    channel_config: dict[str, Any] | None = None,
    long_video_title: str = "",
) -> bool:
    """Deterministic fallback: guarantee the spoken CTA beat names the channel.

    Used when regeneration cannot get the model to include the channel direction
    in the CTA beat. Prefers the validated ``cta`` field (already <= 8 words and
    contains the channel reference); otherwise builds a compact CTA from the
    preferred funnel direction. Keeps the cta field and global narration in sync.

    Returns True when a repair was applied.
    """
    accepted = acceptable_funnel_ctas(
        short_plan, source_map, channel_config=channel_config, long_video_title=long_video_title
    )
    preferred_cta = accepted[0]

    def norm(text: str) -> str:
        return re.sub(r"\W+", " ", text.lower()).strip()

    beats = list(script.get("beats") or [])
    cta_beat = next(
        (b for b in beats if isinstance(b, dict) and str(b.get("purpose") or "").lower() == "cta"),
        None,
    )
    if cta_beat is None:
        return False

    old_narr = str(cta_beat.get("narration") or "")
    if old_narr and any(norm(c) in norm(old_narr) for c in accepted):
        return False  # already compliant

    # Prefer the validated cta field (within budget, includes the channel direction).
    cta_field = str(script.get("cta") or "").strip()
    if (
        cta_field
        and any(norm(c) in norm(cta_field) for c in accepted)
        and len(re.findall(r"\w+", cta_field)) <= funnel_cta_max_words(channel_config)
    ):
        new_narr = cta_field
    else:
        new_narr = preferred_cta if preferred_cta.strip().endswith((".", "!", "?")) else preferred_cta + "."

    cta_beat["narration"] = new_narr
    script["cta"] = new_narr

    # Keep the global narration concatenation consistent with the repaired beat.
    global_narr = str(script.get("narration") or "")
    if global_narr:
        if old_narr and old_narr in global_narr:
            script["narration"] = global_narr.replace(old_narr, new_narr)
        else:
            script["narration"] = " ".join(
                str(b.get("narration") or "").strip()
                for b in beats
                if isinstance(b, dict) and str(b.get("narration") or "").strip()
            )

    script.setdefault("planner_warnings", []).append("cta_beat_channel_direction_repaired")
    return True


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
