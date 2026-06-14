"""Shared QA classes, constants and leaf helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "IssueClass",
    "NormalizedIssue",
    "LLM_PROVIDER",
    "_GREETINGS",
    "_DISCLAIMER",
    "_OVERCLAIM",
    "PRODUCT_SCORE_KEYS",
    "NEW_PRODUCT_SCORE_KEYS",
    "NEW_PRODUCT_SCORE_THRESHOLDS",
    "REQUIRED_PRODUCT_SCORE_THRESHOLDS",
    "MIN_PRODUCT_SCORE",
    "MIN_AVERAGE_PRODUCT_SCORE",
    "KEY_PRODUCT_DIMENSIONS",
    "TIER_HARD_FLOOR",
    "TIER_RETENTION_FLOOR",
    "TIER_VISUAL_FIRST_FLOOR",
    "TIER_REPAIR_AVERAGE",
    "TIER_REPAIR_KEY",
    "TIER_NATURAL_SPANISH_MIN",
    "TIER_PUBLISH_AVERAGE",
    "TIER_PUBLISH_KEY",
    "TIER_PUBLISH_NATURAL_SPANISH",
    "MIN_PRODUCT_SCORE_RENDER_BLOCK",
    "SOFT_PACING_SCORE",
    "PRODUCT_REPAIR_PACING_THRESHOLD",
    "SIMPLIFY_SCENE_COUNT_THRESHOLD",
    "_GRAPHIC_COUNT_COMPLAINT_PATTERNS",
    "_load",
    "_word_count",
    "_parse_gemini",
    "get_short_rule_context",
]


class IssueClass:
    HARD_BLOCKER = "hard_blocker"
    REPAIRABLE_BLOCKER = "repairable_blocker"
    SOFT_WARNING = "soft_warning"
    STALE_OR_SUPPRESSED = "stale_or_suppressed"


@dataclass
class NormalizedIssue:
    issue_class: str
    reason: str
    source: str  # scene_validation | gemini_scene_qa | script_qa | renderer | audio
    scene_id: str | None
    issue_type: str
    detail: str
    repair_hint: str | None = None
    include_in_retry_feedback: bool = True
    trigger_regeneration: bool = True

    def to_dict(self) -> dict:
        d = {
            "issue_class": self.issue_class,
            "reason": self.reason,
            "source": self.source,
            "scene_id": self.scene_id,
            "issue_type": self.issue_type,
            "detail": self.detail,
            "repair_hint": self.repair_hint,
            "include_in_retry_feedback": self.include_in_retry_feedback,
            "trigger_regeneration": self.trigger_regeneration,
        }
        if self.issue_class == IssueClass.STALE_OR_SUPPRESSED:
            d["original_detail"] = self.detail
        return d


LLM_PROVIDER = "gemini"
_GREETINGS = [
    "hola",
    "bienvenid",
    "hoy vamos a",
    "en este short",
    "en este vídeo",
    "en este video",
    "buenas",
]
_DISCLAIMER = [
    "no sustituye",
    "consulta a tu médico",
    "consulta siempre a tu médico",
    "profesional de salud",
]
_OVERCLAIM = [
    "cura",
    "curar",
    "para siempre",
    "garantizado",
    "milagro",
    "elimina para siempre",
    "diagnóstico",
    "tratamiento",
]
PRODUCT_SCORE_KEYS = [
    "audience_fit_45_plus",
    "hook_strength",
    "visual_specificity",
    "clarity",
    "retention_pacing",
    "natural_spanish",
    "saveability",
]
NEW_PRODUCT_SCORE_KEYS = [
    "hook_specificity",
    "micro_tension",
    "human_naturalness",
    "visual_rhythm",
    "identity_resonance",
    "commentability",
]
NEW_PRODUCT_SCORE_THRESHOLDS = {
    "hook_specificity": {"pass": 75, "warn": 55, "fail": 54},
    "micro_tension": {"pass": 70, "warn": 50, "fail": 49},
    "human_naturalness": {"pass": 75, "warn": 55, "fail": 54},
    "visual_rhythm": {"pass": 70, "warn": 50, "fail": 49},
    "identity_resonance": {"pass": 70, "warn": 50, "fail": 49},
    "commentability": {"pass": 65, "warn": 45, "fail": 44},
}
REQUIRED_PRODUCT_SCORE_THRESHOLDS = {
    "hook_strength": 9.0,
    "clarity": 9.0,
    "retention_pacing": 9.0,
    "visual_specificity": 9.0,
    "audience_fit_45_plus": 9.0,
    "natural_spanish": 9.0,
    "saveability": 8.5,
}
MIN_PRODUCT_SCORE = 9.0
MIN_AVERAGE_PRODUCT_SCORE = 8.9
KEY_PRODUCT_DIMENSIONS = (
    "hook_strength",
    "clarity",
    "retention_pacing",
    "visual_specificity",
    "audience_fit_45_plus",
    "saveability",
)
TIER_HARD_FLOOR = 7.0  # any dimension below this hard-blocks
TIER_RETENTION_FLOOR = 7.5  # retention_pacing below this hard-blocks
TIER_VISUAL_FIRST_FLOOR = 7.5  # visual_specificity floor for visual-first Shorts
TIER_REPAIR_AVERAGE = 8.2  # average below this triggers repair/retry
TIER_REPAIR_KEY = 8.0  # any key dimension below this triggers repair
TIER_NATURAL_SPANISH_MIN = 8.0  # natural_spanish must reach this to pass normal QA
TIER_PUBLISH_AVERAGE = 8.6
TIER_PUBLISH_KEY = 8.5
TIER_PUBLISH_NATURAL_SPANISH = 9.0
MIN_PRODUCT_SCORE_RENDER_BLOCK = 8.5
SOFT_PACING_SCORE = 8.0
PRODUCT_REPAIR_PACING_THRESHOLD = 9.0
SIMPLIFY_SCENE_COUNT_THRESHOLD = 9
_GRAPHIC_COUNT_COMPLAINT_PATTERNS = [
    "at most 2 graphic",
    "at most two graphic",
    "already has 2 graphic",
    "already has two graphic",
    "maximum 2 graphic",
    "maximum of 2 graphic",
    "max 2 graphic",
    "no more than 2 graphic",
    "more than 2 graphic",
    "exceeds 2 graphic",
    "exceeds the allowed 2 graphic",
    "exceed 2 graphic",
    "exceed the allowed 2 graphic",
    "only 2 graphic",
    "only 2 scenes use graphic",
    "only two scenes use graphic",
    "limit of 2 graphic",
    "allowed 2 graphic",
    "2 scenes use graphic",
    "two scenes use graphic",
    "2 graphics already",
    "two graphics already",
    "too many graphic",
]


def get_short_rule_context(idea: dict, script: dict) -> dict:
    title = str(idea.get("title") or "")
    fmt = str(idea.get("format") or "")
    hook = str(script.get("hook") or "")
    viewer_pain = str(idea.get("viewer_pain") or "")
    original_count = idea.get("original_count")

    is_five_errors_bread_short = (
        "5 errores" in title.lower()
        or "cinco errores" in title.lower()
        or (fmt in {"mistakes", "errors"} and original_count == 5)
    )

    is_bread_shopping_checklist = (
        "compra" in title.lower() or "paquete" in hook.lower() or "etiqueta" in viewer_pain.lower()
    ) and fmt == "checklist"

    is_bread_topic = "pan" in title.lower() or "pan" in hook.lower() or "pan" in viewer_pain.lower()

    is_toast_assembly = "tostada" in title.lower() or "toast" in title.lower()

    return {
        "is_bread_topic": is_bread_topic,
        "is_five_errors_bread_short": is_five_errors_bread_short,
        "is_bread_shopping_checklist": is_bread_shopping_checklist,
        "is_toast_assembly": is_toast_assembly,
        "format": fmt,
        "hook_text": hook,
    }


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _word_count(text: str) -> int:
    return len([w for w in str(text).split() if w.strip()])


def _parse_gemini(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}
