"""Resolve the per-video target audience age from the idea, not a hardcode.

The channel is branded "45+", but a specific idea can target another age
("si tienes MÁS DE 60 AÑOS ...", "carencias después de los 70"). Content
builders (script, scenes, thumbnail, scene images, graphic cards) must depict
the age the idea asks for. This module extracts an explicit target age from the
idea's text signals (topic, title_seed, target_keyword, hook, seo title). When
no explicit age is present, it falls back to the channel's configured
``audience.age_range`` floor, then to 45 — so generic ideas keep the existing
behaviour.

Only the FLOOR age is resolved (the "45+" style target): videos speak to
"adults N and older", so a single min-age drives every descriptor.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "resolve_target_min_age",
    "audience_context",
    "resolve_audience_context",
    "DEFAULT_MIN_AGE",
]

DEFAULT_MIN_AGE = 45
# Plausible human target-age band; anything outside is a year, a count, etc.
_MIN_PLAUSIBLE_AGE = 30
_MAX_PLAUSIBLE_AGE = 95

# High-confidence "target audience" phrases that scope the video to an age.
# Spanish (Spain-first) + English. The capture group is the age.
_TARGET_PATTERNS = [
    re.compile(
        r"(?:más|mas)\s+de\s+(?:los\s+)?(\d{2})|"
        r"mayores\s+de\s+(?:los\s+)?(\d{2})|"
        r"(?:después|despues)\s+de\s+los\s+(\d{2})|"
        r"a\s+partir\s+de\s+los\s+(\d{2})|"
        r"(?:pasados|tras|sobre|cumplidos)\s+los\s+(\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:aged|age|over|after|past|older\s+than)\s+(\d{2})",
        re.IGNORECASE,
    ),
]
# Weaker fallbacks: a bare "60 años" / "60+" mention.
_LOOSE_PATTERNS = [
    re.compile(r"(\d{2})\s*(?:años|anos)", re.IGNORECASE),
    re.compile(r"(\d{2})\s*\+"),
]


def _first_plausible(match: re.Match[str]) -> int | None:
    for group in match.groups():
        if group is None:
            continue
        age = int(group)
        if _MIN_PLAUSIBLE_AGE <= age <= _MAX_PLAUSIBLE_AGE:
            return age
    return None


def _detect_age_in_text(text: str) -> int | None:
    """Return the explicit target age in ``text``, or None.

    Targeting phrases ("más de 60", "over 50") win over loose mentions
    ("60 años" anywhere), so a phrase like "come 5 veces al día tras los 60"
    resolves to 60, not 5.
    """
    for pattern in _TARGET_PATTERNS:
        for match in pattern.finditer(text):
            age = _first_plausible(match)
            if age is not None:
                return age
    for pattern in _LOOSE_PATTERNS:
        for match in pattern.finditer(text):
            age = _first_plausible(match)
            if age is not None:
                return age
    return None


def _channel_floor(channel_config: dict[str, Any] | None) -> int | None:
    audience = (channel_config or {}).get("audience") or {}
    age_range = audience.get("age_range")
    if isinstance(age_range, (list, tuple)) and age_range:
        try:
            return int(age_range[0])
        except (TypeError, ValueError):
            return None
    return None


def resolve_target_min_age(
    channel_config: dict[str, Any] | None,
    *signals: str,
    default: int = DEFAULT_MIN_AGE,
) -> int:
    """Resolve the video's target floor age.

    Order: explicit age in any ``signals`` (idea topic/title/keyword/hook, seo
    title) → channel ``audience.age_range`` floor → ``default`` (45).
    """
    for signal in signals:
        if not signal:
            continue
        age = _detect_age_in_text(str(signal))
        if age is not None:
            return age
    floor = _channel_floor(channel_config)
    if floor is not None:
        return floor
    return default


def audience_context(min_age: int) -> dict[str, Any]:
    """Descriptor strings for prompts, all tracking ``min_age``."""
    return {
        "min_age": min_age,
        "es_plus": f"adultos {min_age}+",
        "en_plus": f"adults {min_age}+",
        # A decade-wide band from the floor for on-image subject styling
        # ("a Mediterranean Spanish adult aged 60-70").
        "subject_band": f"{min_age}-{min_age + 10}",
    }


def resolve_audience_context(
    channel_config: dict[str, Any] | None,
    *signals: str,
    default: int = DEFAULT_MIN_AGE,
) -> dict[str, Any]:
    """One-call resolve → descriptor bundle."""
    return audience_context(
        resolve_target_min_age(channel_config, *signals, default=default)
    )
