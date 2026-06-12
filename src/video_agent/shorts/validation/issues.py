"""Pre-render validation for Shorts graphic scenes (spec v7 §18).

Runs after ``build_short_scenes`` + ``run_short_scenes_qa`` and before render
props are written / Remotion is invoked. Catches unsupported graphic layouts and
malformed payloads early, with clear errors, so bad scenes never reach the
renderer. Mirrors the Zod checks in ``remotion/src/graphics/graphic-payloads.ts``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import re
import wave
from pathlib import Path
from typing import Any

DEFAULT_SPANISH_WPS = 2.25
AUDIO_TAIL_MARGIN_SEC = 0.6
AUDIO_TAIL_EPSILON_SEC = 0.05
AUDIO_TAIL_REPAIR_BUFFER_SEC = 0.1
# Slack on top of the intentional tail margin+buffer so the sync PASS threshold
# does not sit right on the deterministic tail size (avoids false WARN from
# rounding). See audio_sync_summary().
AUDIO_SYNC_EPSILON_SEC = 0.25
MIN_SHORT_DURATION_SEC = 20.0
MAX_SHORT_DURATION_SEC = 60.0
IDEAL_MIN_SHORT_DURATION_SEC = 28.0
IDEAL_MAX_SHORT_DURATION_SEC = 38.0
GLOBAL_SCENE_MAX_SEC = 5.5

SUPPORTED_GRAPHIC_LAYOUTS = {
    "graphic_plate_ratio",
    "graphic_checklist",
    "graphic_step_list",
    "graphic_label_callout",
    "graphic_comparison",
    "graphic_routine_split",
}

SUPPORTED_SHORT_LAYOUTS = {
    "short_hook",
    "short_pain",
    "short_tip",
    "short_checklist",
    "short_myth",
    "short_quote",
    "short_cta",
}

SUPPORTED_SCENE_LAYOUTS = SUPPORTED_SHORT_LAYOUTS | SUPPORTED_GRAPHIC_LAYOUTS

LAYOUT_DURATION_TARGETS = {
    "short_hook": (1.8, 2.8, 3.0),
    "short_myth": (2.0, 4.0, 4.2),
    "short_tip": (3.2, 4.0, 4.5),
    "short_checklist": (3.0, 4.5, 5.0),
    "short_pain": (3.2, 4.0, 4.5),
    "short_cta": (2.0, 2.6, 2.8),
    "graphic_checklist": (4.2, 5.0, 5.0),
    "graphic_step_list": (3.0, 4.0, 4.5),
    "graphic_label_callout": (3.5, 5.0, 5.0),
    "graphic_comparison": (3.5, 5.0, 5.0),
    "graphic_plate_ratio": (3.0, 4.5, 5.0),
    "graphic_routine_split": (3.5, 5.0, 5.0),
}


@dataclass
class SceneValidationIssue:
    type: str
    scene_id: str | None
    severity: str  # "blocking_error" | "repairable_error" | "warning"
    detail: str
    repair_hint: str | None = None
    instructions: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def issues_to_dicts(issues: list[SceneValidationIssue]) -> list[dict[str, Any]]:
    return [issue.to_dict() for issue in issues]


def has_blocking_or_repairable(issues: list[SceneValidationIssue]) -> bool:
    return any(issue.severity in {"blocking_error", "repairable_error"} for issue in issues)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+(?:['’][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+)?", str(text or ""))


def count_spoken_words(text: str) -> int:
    return len(_words(text))


def count_sentences(text: str) -> int:
    parts = [p for p in re.split(r"[.!?¡¿]+|\n+", str(text or "")) if p.strip()]
    return len(parts)


def estimate_spanish_narration_sec(text: str, wps: float = DEFAULT_SPANISH_WPS) -> float:
    words = count_spoken_words(text)
    if words == 0:
        return 0.0
    sentence_pause = 0.18 * count_sentences(text)
    return (words / float(wps or DEFAULT_SPANISH_WPS)) + sentence_pause


def max_spoken_words_for_duration(target_video_sec: float, wps: float = DEFAULT_SPANISH_WPS) -> int:
    return int(math.floor(float(target_video_sec or 35.0) * float(wps or DEFAULT_SPANISH_WPS) * 0.88))


