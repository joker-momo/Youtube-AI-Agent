"""Module-level tuning constants for shorts validation checks."""

from __future__ import annotations

from video_agent.shorts.validation.issues import *  # noqa: F401,F403

__all__ = [
    "_GRAPHIC_KEEP_PRIORITY",
    "ALLOWED_GRAPHIC_VARIANTS",
    "ALLOWED_GRAPHIC_VISUAL_TONES",
    "ALLOWED_GRAPHIC_BACKGROUND_MODES",
    "ALLOWED_GRAPHIC_SURFACE_STYLES",
    "PLATE_RATIO_TOTAL",
    "PLATE_RATIO_EPSILON",
    "MAX_GRAPHIC_SCENES_PER_SHORT",
    "GRAPHIC_MIN_DURATION_SEC",
    "GRAPHIC_MAX_DURATION_SEC",
    "GRAPHIC_LAYOUT_DURATION_TARGETS",
    "PASSIVE_CTA_TEXTS",
    "BREAD_LABEL_TOPIC_TERMS",
    "BREAD_LABEL_HOOK_VISUAL_TERMS",
    "_PLATE_LABEL_MAX",
    "_CHECKLIST_ITEM_MAX",
    "_STEP_TEXT_MAX",
    "_FOOTER_MAX",
    "_TITLE_MAX_PHASE15",
    "_LABEL_CALLOUT_PRODUCT_MAX",
    "_LABEL_CALLOUT_LABEL_MAX",
    "_LABEL_CALLOUT_VALUE_MAX",
    "_LABEL_CALLOUT_NOTE_MAX",
    "_COMPARISON_HEADING_MAX",
    "_COMPARISON_TEXT_MAX",
    "_COMPARISON_BADGE_MAX",
    "_ROUTINE_TOTAL_MAX",
    "_ROUTINE_TIME_MAX",
    "_ROUTINE_TEXT_MAX",
    "_CARD_BODY_MAX",
    "FORBIDDEN_HEALTH_MARKETING_WORDS",
]


_GRAPHIC_KEEP_PRIORITY = [
    "graphic_label_callout",  # "primer ingrediente"
    "graphic_comparison",  # "fibra / azúcar / jarabes"
    "graphic_plate_ratio",
    "graphic_do_dont",  # worse-vs-better contrast (long-form port)
    "graphic_stat",  # one memorable number (long-form port)
    "graphic_evidence_nugget",  # credible age/health fact (long-form port)
    "graphic_routine_split",
    "graphic_step_list",
    "graphic_recipe_snapshot",  # practical meal example (long-form port)
    "graphic_myth",  # mito/realidad (long-form port)
    "graphic_warning",  # avoid-this list (long-form port)
    "graphic_quote_portrait",  # emotional accent — convert early (long-form port)
    "graphic_checklist",  # setup/recap — convert first
]
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
    "graphic_stat": (2.5, 3.5, 4.0),
    "graphic_myth": (3.0, 4.0, 4.5),
    "graphic_do_dont": (3.5, 4.5, 5.0),
    "graphic_recipe_snapshot": (3.0, 4.5, 5.0),
    "graphic_quote_portrait": (2.5, 4.0, 4.5),
    "graphic_evidence_nugget": (2.5, 3.5, 4.0),
    "graphic_warning": (3.0, 4.0, 4.5),
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
# Body line for the long-form-ported single-body cards (stat/myth/evidence_nugget).
_CARD_BODY_MAX = 90
FORBIDDEN_HEALTH_MARKETING_WORDS = (
    "veneno",
    "prohibido",
    "nunca",
    "milagro",
    "cura",
    "doctores no quieren",
)
