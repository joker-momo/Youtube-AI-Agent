"""Small controlled vocabulary for Shorts visual-quality metadata (spec v4.0.3)."""

from __future__ import annotations

from typing import Any

_ALIASES: dict[str, dict[str, str]] = {
    "subjects": {
        "adult_45_plus": "adult_45_plus",
        "mature adult": "adult_45_plus",
        "middle aged": "adult_45_plus",
        "middle-aged": "adult_45_plus",
        "older adult": "adult_45_plus",
        "senior": "adult_45_plus",
        "older couple": "older_couple",
        "health professional": "health_professional",
    },
    "actions": {
        "gentle_walking": "gentle_walking",
        "gentle walking": "gentle_walking",
        "slow walk": "gentle_walking",
        "walking calmly": "gentle_walking",
        "stretching": "stretching",
        "resting": "resting",
        "sleeping": "sleeping",
        "food preparation": "food_preparation",
    },
    "environments": {
        "outdoor_path": "outdoor_path",
        "outdoor path": "outdoor_path",
        "park": "outdoor_path",
        "home bedroom": "home_bedroom",
        "bedroom": "home_bedroom",
        "kitchen": "kitchen",
    },
    "evidence": {
        "low_intensity_movement": "low_intensity_movement",
        "low intensity movement": "low_intensity_movement",
        "hydration": "hydration",
        "visible_injury": "visible_injury",
        "visible injury": "visible_injury",
        "intense_training": "intense_training",
        "intense training": "intense_training",
    },
    "shot_types": {
        "close_up": "close_up",
        "close up": "close_up",
        "medium_shot": "medium_shot",
        "medium shot": "medium_shot",
        "wide_shot": "wide_shot",
        "wide shot": "wide_shot",
    },
    "motion_bands": {
        "near_static": "near_static",
        "low_motion": "low_motion",
        "normal_motion": "normal_motion",
        "high_motion": "high_motion",
        "unstable": "unstable",
    },
    "graphic_concepts": {
        "comparison": "comparison",
        "cause_effect": "cause_effect",
        "before_after": "before_after",
        "short_list": "short_list",
        "single_number": "single_number",
        "two_step_process": "two_step_process",
        "myth_vs_reality": "myth_vs_reality",
    },
}


_TOKEN_LABELS = {
    "adult_45_plus": "mature adult",
}


def _clean(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", " ").replace("_", " ")


def normalize_visual_tokens(
    values: list[Any] | tuple[Any, ...] | set[Any] | str | None,
    *,
    category: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize controlled visual tokens without discarding unknown values.

    Channel config can extend known aliases via ``{category: {raw: canonical}}``.
    Unknown values are not promoted to hard evidence; they are returned as
    warnings so PR C can preserve the planner output without claiming certainty.
    """
    raw_values: list[Any]
    if values is None:
        raw_values = []
    elif isinstance(values, str):
        raw_values = [values]
    else:
        raw_values = list(values)

    aliases = dict(_ALIASES.get(category, {}))
    aliases.update(
        {
            str(k).strip().lower(): str(v).strip()
            for k, v in (config or {}).get(category, {}).items()
        }
    )

    tokens: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        text = str(raw).strip()
        if not text:
            continue
        key = _clean(text)
        canonical = aliases.get(key) or aliases.get(text.strip().lower())
        if canonical:
            if canonical not in seen:
                tokens.append(canonical)
                seen.add(canonical)
        else:
            if text not in unknown:
                unknown.append(text)

    return {
        "tokens": tokens,
        "unknown": unknown,
        "warnings": [f"unknown_{category}:{value}" for value in unknown],
    }


def label_for_token(token: str) -> str:
    """Human-readable provider-query text for a normalized token."""
    normalized = str(token)
    if normalized in _TOKEN_LABELS:
        return _TOKEN_LABELS[normalized]
    return normalized.replace("_45_plus", "").replace("_", " ").strip()
