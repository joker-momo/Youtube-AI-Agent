"""Visual planner: scene role, bucket choice, shot-type rotation, graphic card planning.

Spec v5.6 §9–§10. scene_index is 0-based everywhere.
"""

from __future__ import annotations

from math import floor
from typing import Any

from .helpers import deterministic_argmax, normalize_text, visual_seed
from .loader import classify_video_length


# --- Scene helpers --------------------------------------------------------

def scene_text(scene: dict[str, Any]) -> str:
    return " ".join([
        str(scene.get("narration_text") or ""),
        str(scene.get("on_screen_text") or ""),
        str(scene.get("visual_prompt") or ""),
        str(scene.get("visual_brief") or ""),
    ])


def detect_scene_role(scene: dict[str, Any], visual_dna: dict[str, Any]) -> str:
    text = normalize_text(scene_text(scene))
    best_role = "explanation"
    best_score = 0
    for lang in ("es", "en"):
        for role, terms in visual_dna.get("role_keywords", {}).get(lang, {}).items():
            score = sum(1 for term in terms if normalize_text(term) in text)
            if score > best_score:
                best_role = role
                best_score = score
    return best_role


# --- Graphic-card gating ---------------------------------------------------

def graphic_card_bucket_renderable(
    visual_config: dict[str, Any] | None,
    renderer_caps: dict[str, Any] | None,
) -> bool:
    """True iff ``local_graphic_card`` may be the actual render bucket for a scene."""
    card_cfg = ((visual_config or {}).get("graphic_cards") or {})
    if card_cfg.get("enabled") is not True:
        return False
    mode = str(card_cfg.get("rollout_mode", "disabled"))
    if mode in {"disabled", "report_only"}:
        return False
    if mode in {"auto_if_supported", "enforce"}:
        return bool((renderer_caps or {}).get("graphic_cards", False))
    return False


def graphic_card_bucket_report_plannable(visual_config: dict[str, Any] | None) -> bool:
    """True iff cards may be planned for the report without changing render output."""
    card_cfg = ((visual_config or {}).get("graphic_cards") or {})
    return card_cfg.get("enabled") is True and str(card_cfg.get("rollout_mode")) == "report_only"


# --- Bucket selection ------------------------------------------------------

def allowed_visual_buckets(
    visual_dna: dict[str, Any],
    visual_config: dict[str, Any] | None = None,
    renderer_caps: dict[str, Any] | None = None,
) -> list[str]:
    """Return buckets eligible for the *render* path.

    Backward-compatible: when called with the old two-argument form
    (``renderer_caps`` only) the second positional argument is treated as
    ``renderer_caps`` for existing callers.
    """
    # Allow old (visual_dna, renderer_caps) call shape from earlier integrations.
    if (
        renderer_caps is None
        and isinstance(visual_config, dict)
        and "graphic_cards" in visual_config
        and visual_config.get("graphic_cards") is not None
        and not isinstance(visual_config.get("graphic_cards"), dict)
    ):
        # legacy renderer_caps shape (bool flag mapping)
        renderer_caps = visual_config
        visual_config = None
    if (
        renderer_caps is None
        and isinstance(visual_config, dict)
        and "graphic_cards" not in visual_config
    ):
        # If caller only passed renderer_caps as second arg
        if all(isinstance(v, (bool, int, str, list)) for v in visual_config.values()):
            renderer_caps = visual_config
            visual_config = None

    buckets = list(visual_dna.get("visual_buckets", {}).keys())
    if visual_config is None:
        # Legacy behaviour: keep card bucket iff renderer says cards are available.
        if not (renderer_caps or {}).get("graphic_cards", False):
            return [b for b in buckets if b != "local_graphic_card"]
        return buckets

    if not graphic_card_bucket_renderable(visual_config, renderer_caps):
        return [b for b in buckets if b != "local_graphic_card"]
    return buckets


def bucket_keyword_score(text_value: str, bucket_cfg: dict[str, Any]) -> int:
    text = normalize_text(text_value)
    score = 0
    for term in bucket_cfg.get("keyword_triggers", {}).get("es", []):
        if normalize_text(term) in text:
            score += 2
    for term in bucket_cfg.get("keyword_triggers", {}).get("en", []):
        if normalize_text(term) in text:
            score += 1
    return score


def would_exceed_bucket_ratio(
    bucket_id: str,
    current_counts: dict[str, int],
    scene_count: int,
    bucket_cfg: dict[str, Any],
    length_profile: str,
) -> bool:
    if length_profile != "long":
        return False
    max_ratio = bucket_cfg.get("long_max_ratio_per_video")
    if max_ratio is None or scene_count <= 0:
        return False
    projected = int(current_counts.get(bucket_id, 0)) + 1
    return (projected / scene_count) > float(max_ratio)


def under_minimum_target(
    bucket_id: str,
    current_counts: dict[str, int],
    scene_index: int,
    scene_count: int,
    bucket_cfg: dict[str, Any],
    length_profile: str,
) -> bool:
    if length_profile != "long":
        return False
    min_target = int(bucket_cfg.get("long_min_per_video") or 0)
    if min_target <= 0:
        return False
    remaining = max(0, scene_count - scene_index)
    current = int(current_counts.get(bucket_id, 0))
    return current < min_target and remaining >= (min_target - current)


def choose_visual_bucket(
    scene: dict[str, Any],
    scene_index: int,
    scene_count: int,
    channel_id: str,
    job_id: str,
    topic: str | None,
    visual_dna: dict[str, Any],
    current_counts: dict[str, int],
    renderer_caps: dict[str, Any] | None = None,
    visual_config: dict[str, Any] | None = None,
) -> str:
    base_seed = visual_seed(channel_id, job_id, scene, scene_index, topic)
    role = detect_scene_role(scene, visual_dna)
    allowed = allowed_visual_buckets(visual_dna, visual_config, renderer_caps)
    length_profile = classify_video_length(scene_count, visual_dna)

    scores: dict[str, float] = {}
    for bucket_id in allowed:
        cfg = visual_dna["visual_buckets"][bucket_id]
        score = float(cfg.get("weight", 1.0))
        score += bucket_keyword_score(scene_text(scene), cfg) * 0.25
        if bucket_id in visual_dna.get("role_to_buckets", {}).get(role, []):
            score += 0.75
        if would_exceed_bucket_ratio(bucket_id, current_counts, scene_count, cfg, length_profile):
            score -= 1.00
        if under_minimum_target(bucket_id, current_counts, scene_index, scene_count, cfg, length_profile):
            score += 0.50
        scores[bucket_id] = score

    if not scores:
        return "persona_moment"
    return deterministic_argmax(scores, seed=base_seed)


def fallback_bucket_for_index(
    index: int,
    visual_dna: dict[str, Any],
    visual_config: dict[str, Any] | None = None,
    renderer_caps: dict[str, Any] | None = None,
) -> str:
    seq = [
        b for b in visual_dna.get("default_bucket_sequence", [])
        if b in allowed_visual_buckets(visual_dna, visual_config, renderer_caps)
    ]
    return seq[index % len(seq)] if seq else "persona_moment"


# --- Shot type assignment --------------------------------------------------

def would_exceed_shot_type_ratio(
    shot_type: str,
    current_shot_counts: dict[str, int],
    scene_count: int,
    visual_dna: dict[str, Any],
    length_profile: str,
) -> bool:
    if length_profile != "long":
        return False
    cfg = visual_dna.get("shot_types", {}).get(shot_type, {})
    max_ratio = cfg.get("long_max_ratio")
    if max_ratio is None or scene_count <= 0:
        return False
    projected = int(current_shot_counts.get(shot_type, 0)) + 1
    return (projected / scene_count) > float(max_ratio)


def under_shot_type_minimum_target(
    shot_type: str,
    current_shot_counts: dict[str, int],
    scene_index: int,
    scene_count: int,
    visual_dna: dict[str, Any],
    length_profile: str,
) -> bool:
    if length_profile != "long":
        return False
    cfg = visual_dna.get("shot_types", {}).get(shot_type, {})
    min_ratio = cfg.get("long_min_ratio")
    if min_ratio is None:
        return False
    min_target = int(scene_count * float(min_ratio))
    if min_target <= 0:
        return False
    remaining = max(0, scene_count - scene_index)
    current = int(current_shot_counts.get(shot_type, 0))
    return current < min_target and remaining >= (min_target - current)


_PREFERRED_SHOTS_BY_BUCKET = {
    "macro_texture": ["macro", "closeup"],
    "spain_daily_life": ["wide", "medium"],
    "persona_moment": ["medium", "medium_closeup"],
    "body_signal": ["closeup", "medium_closeup"],
    "food_practical": ["closeup", "medium"],
    "sleep_stress": ["medium", "closeup"],
    "gentle_movement": ["wide", "medium"],
}


def choose_shot_type(
    scene: dict[str, Any],
    bucket: str,
    scene_index: int,
    previous_shot_types: list[str],
    visual_dna: dict[str, Any],
    renderer_caps: dict[str, Any] | None = None,
    *,
    scene_count: int | None = None,
    current_shot_counts: dict[str, int] | None = None,
    visual_config: dict[str, Any] | None = None,
) -> str:
    """Spec §10 three-pass selection with config-driven consecutive limit."""
    if bucket == "local_graphic_card":
        return "graphic"

    if scene_count is None:
        # Legacy call-shape: caller has no idea of scene_count; fall back to
        # a simple consecutive-only loop using bucket preferences.
        seq = list(_PREFERRED_SHOTS_BY_BUCKET.get(bucket) or visual_dna.get("default_shot_sequence", ["medium"]))
        if not (renderer_caps or {}).get("graphic_cards", False):
            seq = [s for s in seq if s != "graphic"]
        max_consecutive = int(
            ((visual_config or {}).get("diversity", {}) or {}).get("max_same_shot_type_consecutive", 2)
            or 2
        )
        max_consecutive = max(1, max_consecutive)
        for candidate in seq:
            if (
                len(previous_shot_types) >= max_consecutive
                and all(s == candidate for s in previous_shot_types[-max_consecutive:])
            ):
                continue
            return candidate
        return "medium"

    length_profile = classify_video_length(scene_count, visual_dna)
    counts = current_shot_counts or {}
    max_consecutive = int(
        ((visual_config or {}).get("diversity", {}) or {}).get("max_same_shot_type_consecutive", 2)
        or 2
    )
    max_consecutive = max(1, max_consecutive)
    can_render_cards = graphic_card_bucket_renderable(visual_config, renderer_caps)

    seq = list(_PREFERRED_SHOTS_BY_BUCKET.get(bucket) or visual_dna.get("default_shot_sequence", ["medium"]))
    if not can_render_cards:
        seq = [s for s in seq if s != "graphic"]

    def violates_consecutive(candidate: str) -> bool:
        if len(previous_shot_types) < max_consecutive:
            return False
        return all(s == candidate for s in previous_shot_types[-max_consecutive:])

    # Pass 1: prefer bucket-preferred shots that respect ratio + consecutive limit.
    for candidate in seq:
        if violates_consecutive(candidate):
            continue
        if would_exceed_shot_type_ratio(candidate, counts, scene_count, visual_dna, length_profile):
            continue
        return candidate

    # Pass 2: prioritize shot types still short of their long-form minimum.
    for candidate in visual_dna.get("default_shot_sequence", []):
        if candidate == "graphic" and not can_render_cards:
            continue
        if under_shot_type_minimum_target(
            candidate, counts, scene_index, scene_count, visual_dna, length_profile
        ):
            return candidate

    # Pass 3: deterministic fallback list, avoiding consecutive violation.
    for candidate in ["medium", "wide", "closeup", "medium_closeup", "macro"]:
        if violates_consecutive(candidate):
            continue
        return candidate
    return "medium"


# --- Largest-remainder allocation ------------------------------------------

def normalize_long_minimums_largest_remainder(
    bucket_mins: dict[str, int],
    scene_count: int,
    priority_order: list[str],
) -> dict[str, int]:
    max_reserved = int(scene_count * 0.60)
    total = sum(bucket_mins.values())
    if total <= max_reserved:
        return dict(bucket_mins)

    raw = {k: (v * max_reserved / total) for k, v in bucket_mins.items()}
    base = {k: floor(v) for k, v in raw.items()}
    remaining = max_reserved - sum(base.values())

    priority_index = {bucket: i for i, bucket in enumerate(priority_order)}
    ranked = sorted(
        raw.keys(),
        key=lambda k: (-(raw[k] - base[k]), priority_index.get(k, 999), k),
    )
    for bucket in ranked[:remaining]:
        base[bucket] += 1

    for bucket in priority_order:
        if (
            bucket_mins.get(bucket, 0) > 0
            and base.get(bucket, 0) == 0
            and sum(base.values()) < max_reserved
        ):
            base[bucket] = 1

    return base


# --- Report-only graphic card planning -------------------------------------

def infer_basic_card_type(scene: dict[str, Any], role: str) -> str:
    text = normalize_text(scene_text(scene))
    if "tres" in text or "three" in text or "lista" in text or "checklist" in text:
        return "checklist"
    if role == "explanation":
        return "habit_matrix"
    if role in {"recap", "transition"}:
        return "timeline"
    return "checklist"


def graphic_card_target_for_report(
    scene_count: int,
    video_length_profile: str,
    visual_config: dict[str, Any],
    renderer_caps: dict[str, Any],
    report_only: bool = False,
) -> int:
    card_cfg = ((visual_config or {}).get("graphic_cards") or {})
    if card_cfg.get("enabled") is not True:
        return 0
    if video_length_profile != "long":
        return int(card_cfg.get("min_per_short_video", 0) or 0)
    mode = str(card_cfg.get("rollout_mode", "disabled"))
    if mode == "disabled":
        return 0
    if mode == "report_only":
        return int(card_cfg.get("min_per_long_video", 4) or 0)
    if mode in {"auto_if_supported", "enforce"} and (renderer_caps or {}).get("graphic_cards", False):
        return int(card_cfg.get("min_per_long_video", 4) or 0)
    return 0


def plan_report_only_graphic_cards(
    scenes: list[dict[str, Any]],
    visual_dna: dict[str, Any],
    visual_config: dict[str, Any],
    renderer_caps: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return scene-level card suggestions for the diagnostic report only.

    Never mutates scene buckets, asset_refs, or render output. Only the
    visual-diversity-report.json consumes these entries.
    """
    if not graphic_card_bucket_report_plannable(visual_config):
        return []

    length_profile = classify_video_length(len(scenes), visual_dna)
    target = graphic_card_target_for_report(
        scene_count=len(scenes),
        video_length_profile=length_profile,
        visual_config=visual_config,
        renderer_caps=renderer_caps,
        report_only=True,
    )
    if target <= 0:
        return []

    graphic_cfg = visual_dna.get("visual_buckets", {}).get("local_graphic_card", {}) or {}
    role_to_buckets = visual_dna.get("role_to_buckets", {}) or {}

    candidates: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        role = detect_scene_role(scene, visual_dna)
        score = 0.0
        if "local_graphic_card" in role_to_buckets.get(role, []):
            score += 0.75
        score += bucket_keyword_score(scene_text(scene), graphic_cfg) * 0.25
        if score > 0:
            candidates.append({
                "scene_id": scene.get("id"),
                "scene_index": index,
                "role": role,
                "score": round(score, 4),
                "suggested_card_type": infer_basic_card_type(scene, role),
            })

    candidates.sort(key=lambda item: (-item["score"], item["scene_index"]))
    return candidates[:target]


# --- Orchestrator ----------------------------------------------------------

def plan_scenes(
    scenes: list[dict[str, Any]],
    channel_id: str,
    job_id: str,
    topic: str | None,
    visual_dna: dict[str, Any],
    renderer_caps: dict[str, Any] | None = None,
    visual_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Assign visual_bucket + shot_type to every scene. Does not mutate inputs."""
    renderer_caps = renderer_caps or {}
    scene_count = len(scenes)
    bucket_counts: dict[str, int] = {}
    shot_counts: dict[str, int] = {}
    previous_shots: list[str] = []
    plans: list[dict[str, Any]] = []

    for index, scene in enumerate(scenes):
        bucket = choose_visual_bucket(
            scene,
            index,
            scene_count,
            channel_id,
            job_id,
            topic,
            visual_dna,
            bucket_counts,
            renderer_caps=renderer_caps,
            visual_config=visual_config,
        )
        shot = choose_shot_type(
            scene,
            bucket,
            index,
            previous_shots,
            visual_dna,
            renderer_caps=renderer_caps,
            scene_count=scene_count,
            current_shot_counts=shot_counts,
            visual_config=visual_config,
        )
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        shot_counts[shot] = shot_counts.get(shot, 0) + 1
        previous_shots.append(shot)
        plans.append({
            "scene_id": scene.get("id", f"scene_{index:03d}"),
            "scene_index": index,
            "role": detect_scene_role(scene, visual_dna),
            "visual_bucket": bucket,
            "shot_type": shot,
        })
    return plans
