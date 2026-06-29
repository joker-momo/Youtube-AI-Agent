"""Thumbnail prompt planner — spec v1.3.

Topic-aware planner for the ChatGPT thumbnail image flow used by
`auto_thumbnail_image_stage`. Phase 1: planner module with deterministic
classifier, category presets, and prompt builder. Phase 2 wires this into
the stage; backward-compatible wrapper kept in stages.py.

See docs/specs/thumbnail-prompt-planner-v1.3.md.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# ---------------------------------------------------------------------------
# §6.1 Text normalization (accent-insensitive)
# ---------------------------------------------------------------------------

def normalize_for_thumbnail_classification(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = str(text or "").lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# §5.2 Category triggers (accent-stripped)
# ---------------------------------------------------------------------------

CATEGORY_TRIGGERS: dict[str, list[str]] = {
    "food_choice": [
        "pan", "tostada", "desayuno", "cena", "comida", "plato",
        "aceite", "yogur", "fruta", "verduras", "arroz", "pasta", "integral",
    ],
    "functional_foods_superfoods": [
        "cafe", "chia", "avena", "yogur", "curcuma", "frutos secos",
        "limon", "aceite de oliva",
    ],
    "shopping_label_choice": [
        "etiqueta", "supermercado", "compra", "elegir", "mejor pan",
        "integral", "producto", "ingredientes",
    ],
    "protein_muscle": [
        "proteina", "musculo", "fuerza", "sarcopenia", "masa muscular",
        "despues de los 60",
    ],
    "fiber_digestion": [
        "fibra", "digestion", "estrenimiento", "hinchazon", "intestino", "barriga",
    ],
    "hydration": [
        "agua", "hidratacion", "sed", "piel seca", "beber", "vaso de agua",
    ],
    "blood_sugar_diabetes": [
        "azucar", "glucosa", "diabetes", "prediabetes", "pico de azucar",
        "insulina", "regular su azucar",
    ],
    "blood_pressure_circulation_heart": [
        "presion alta", "tension", "circulacion", "corazon",
        "pantorrilla", "piernas", "sangre",
    ],
    "sleep_rest": [
        "dormir", "sueno", "insomnio", "despertar cansado", "noche",
        "descanso", "rutina nocturna",
    ],
    "energy_fatigue": [
        "cansancio", "cansado", "energia", "bajon", "fatiga", "agotamiento",
    ],
    "movement_stiffness": [
        "rigidez", "rigido", "estirar", "movilidad", "cuello", "espalda",
        "cadera", "hombros", "levantarte",
    ],
    "joint_pain_body_signal": [
        "dolor", "rodilla", "espalda", "manos", "cuello", "articulaciones",
        "senales del cuerpo",
    ],
    "walking_cardio": [
        "caminar", "paseo", "pasos", "andar", "escaleras", "cardio",
        "caminar despues de comer",
    ],
    "stress_mind": [
        "estres", "ansiedad", "mente acelerada", "calma", "respiracion",
        "preocupacion",
    ],
    "brain_memory_cognition": [
        "memoria", "demencia", "alzheimer", "olvido", "cerebro",
        "senales tempranas", "concentracion",
    ],
    "weight_loss_metabolism": [
        "adelgazar", "perder peso", "grasa", "metabolismo", "barriga",
        "cintura", "truco para adelgazar",
    ],
    "aging_longevity_bad_habits": [
        "envejecer", "envejecimiento", "mas rapido", "malos habitos",
        "te hace viejo", "longevidad",
    ],
    "daily_routine": [
        "rutina", "habito", "manana", "tarde", "noche", "cada dia", "todos los dias",
    ],
    "mistake_warning": [
        "error", "errores", "malo", "malos", "evita", "nunca", "no hagas",
        "cuidado", "ignorado", "nadie reconoce",
    ],
    "myth_truth": [
        "mito", "verdad", "no es", "nadie te dice", "revela", "engana", "confunde",
    ],
    "general_45plus_lifestyle": [],
}


# ---------------------------------------------------------------------------
# §6.2 Category priority (safety-critical first; body-signal above aging)
# ---------------------------------------------------------------------------

CATEGORY_PRIORITY: list[str] = [
    "brain_memory_cognition",
    # blood_pressure_circulation_heart ranks above blood_sugar_diabetes so
    # tied scores resolve toward the circulation/heart axis when both fire
    # — matches spec §17.1 oracle ("café + azúcar + circulación" → pressure).
    "blood_pressure_circulation_heart",
    "blood_sugar_diabetes",
    "weight_loss_metabolism",
    "joint_pain_body_signal",
    "movement_stiffness",
    "protein_muscle",
    "walking_cardio",
    "sleep_rest",
    "stress_mind",
    "fiber_digestion",
    "hydration",
    "aging_longevity_bad_habits",
    "functional_foods_superfoods",
    "shopping_label_choice",
    "food_choice",
    "energy_fatigue",
    "daily_routine",
    "mistake_warning",
    "myth_truth",
    "general_45plus_lifestyle",
]


# ---------------------------------------------------------------------------
# §6.3 Scoring
# ---------------------------------------------------------------------------

def score_categories(text: str) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Count substring hits per category. ``text`` already normalized."""
    scores: dict[str, int] = {}
    matches: dict[str, list[str]] = {}
    for category, triggers in CATEGORY_TRIGGERS.items():
        matched = [trigger for trigger in triggers if trigger and trigger in text]
        scores[category] = len(matched)
        matches[category] = matched
    return scores, matches


def pick_primary_category(scores: dict[str, int]) -> str:
    """Highest score wins; ties resolved via ``CATEGORY_PRIORITY``."""
    if not scores:
        return "general_45plus_lifestyle"
    best_score = max(scores.values())
    if best_score <= 0:
        return "general_45plus_lifestyle"
    tied = [cat for cat, score in scores.items() if score == best_score]
    return min(tied, key=lambda cat: CATEGORY_PRIORITY.index(cat))


# ---------------------------------------------------------------------------
# §6.4 Secondary category with low-signal filtering
# ---------------------------------------------------------------------------

LOW_SIGNAL_SECONDARY_CATEGORIES: set[str] = {
    "daily_routine",
    "mistake_warning",
    "myth_truth",
}


def pick_secondary_category(scores: dict[str, int], primary: str) -> str | None:
    """Return a deterministic secondary category, or None.

    Generic intent categories (daily_routine, mistake_warning, myth_truth)
    require score >= 2 to be considered secondary, to avoid one-trigger noise.
    """
    primary_score = int(scores.get(primary, 0))

    candidates: list[str] = []
    for cat, score in scores.items():
        if cat == primary or cat == "general_45plus_lifestyle":
            continue
        if score <= 0:
            continue
        if cat in LOW_SIGNAL_SECONDARY_CATEGORIES and score < 2:
            continue
        if primary_score >= 2 and score < max(1, primary_score * 0.5):
            if cat in LOW_SIGNAL_SECONDARY_CATEGORIES:
                continue
        candidates.append(cat)

    if not candidates:
        return None
    return min(candidates, key=lambda cat: CATEGORY_PRIORITY.index(cat))


# ---------------------------------------------------------------------------
# §6.5 Risk level inference
# ---------------------------------------------------------------------------

MEDICAL_SENSITIVE_KEYWORDS: list[str] = [
    "demencia",
    "alzheimer",
    "diabetes",
    "presion alta",
    "tension alta",
    "corazon",
    "azucar",
    "glucosa",
    "insulina",
]

MEDICAL_SENSITIVE_CATEGORIES: set[str] = {
    "brain_memory_cognition",
    "blood_sugar_diabetes",
    "blood_pressure_circulation_heart",
}

SOFT_HEALTH_CATEGORIES: set[str] = {
    "weight_loss_metabolism",
    "joint_pain_body_signal",
    "movement_stiffness",
    "walking_cardio",
    "sleep_rest",
    "stress_mind",
    "energy_fatigue",
    "protein_muscle",
    "fiber_digestion",
    "hydration",
}


def infer_risk_level(primary: str, normalized_text: str) -> str:
    if primary in MEDICAL_SENSITIVE_CATEGORIES:
        return "medical_sensitive"
    if any(kw in normalized_text for kw in MEDICAL_SENSITIVE_KEYWORDS):
        return "medical_sensitive"
    if primary in SOFT_HEALTH_CATEGORIES:
        return "soft_health"
    return "lifestyle"


# ---------------------------------------------------------------------------
# §6.6 Age signal — phrase-level only
# ---------------------------------------------------------------------------

AGE_CONTEXT_PATTERNS: dict[str, list[str]] = {
    "60+": [
        r"despues de los\s+60\b",
        r"despues de\s+60\b",
        r"mayores de\s+60\b",
        r"mas de\s+60\b",
        r"a partir de los\s+60\b",
        r"\b60\s*\+",
    ],
    "50+": [
        r"despues de los\s+50\b",
        r"despues de\s+50\b",
        r"mayores de\s+50\b",
        r"mas de\s+50\b",
        r"a partir de los\s+50\b",
        r"\b50\s*\+",
    ],
    "45+": [
        r"despues de los\s+45\b",
        r"despues de\s+45\b",
        r"mayores de\s+45\b",
        r"mas de\s+45\b",
        r"a partir de los\s+45\b",
        r"\b45\s*\+",
    ],
}


def infer_age_signal(normalized_text: str) -> str:
    """Phrase-level age detection. Falls through to 'unknown' on no match.

    Standalone numbers (e.g. ``60 minutos``, ``60%``) are intentionally not
    treated as age signals — only context phrases such as ``después de los 60``.
    """
    for signal in ("60+", "50+", "45+"):
        for pattern in AGE_CONTEXT_PATTERNS[signal]:
            if re.search(pattern, normalized_text):
                return signal
    return "unknown"


AGE_RANGE_BY_SIGNAL: dict[str, str] = {
    "45+": "45–60",
    "50+": "50–65",
    "60+": "55–70",
    "unknown": "45–65",
}


# ---------------------------------------------------------------------------
# §6.7 Full classifier
# ---------------------------------------------------------------------------

def classify_thumbnail_topic(title: str, thumbnail_text: str = "") -> dict[str, Any]:
    original = f"{title} {thumbnail_text}"
    text = normalize_for_thumbnail_classification(original)

    scores, matches = score_categories(text)
    primary = pick_primary_category(scores)
    secondary = pick_secondary_category(scores, primary)
    risk_level = infer_risk_level(primary, text)
    age_signal = infer_age_signal(text)

    return {
        "primary_category": primary,
        "secondary_category": secondary,
        "keywords": matches.get(primary, []),
        "matched_keywords_by_category": matches,
        "risk_level": risk_level,
        "age_signal": age_signal,
    }


# ---------------------------------------------------------------------------
# §11 Visual presets
# ---------------------------------------------------------------------------

THUMBNAIL_VISUAL_PRESETS: dict[str, dict[str, Any]] = {
    "food_choice": {
        "scene": "Spanish home kitchen or dining table",
        "main_prop": "bread, toast, Mediterranean plate, olive oil, fruit, or yogurt — the specific food from the title",
        "avoid": ["junk-food caricature", "fake plastic food"],
    },
    "functional_foods_superfoods": {
        "scene": "Spanish kitchen table with the specific functional food in clear focus",
        "main_prop": "the specific food or drink object (coffee cup, chia bowl, oats, etc.) — no product labels",
        "avoid": ["product labels", "branded packaging"],
    },
    "shopping_label_choice": {
        "scene": "Spanish supermarket aisle",
        "main_prop": "a hand comparing two products with labels intentionally blurred",
        "avoid": ["clear brand names", "logos"],
    },
    "protein_muscle": {
        "scene": "Spanish home with a simple exercise corner",
        "main_prop": "simple protein meal, resistance band, or a light dumbbell — realistic adult, not bodybuilder",
        "avoid": ["bodybuilder", "extreme gym", "unrealistic abs"],
    },
    "fiber_digestion": {
        "scene": "Spanish kitchen table with vegetables and legumes",
        "main_prop": "legumes, vegetables, or a water glass with a gentle belly cue",
        "avoid": ["medical intestine graphics", "toilet humor", "embarrassing imagery"],
    },
    "hydration": {
        "scene": "morning kitchen or bedside with daylight",
        "main_prop": "a glass of water or a bottle, naturally placed",
        "avoid": ["dehydration stock cliches"],
    },
    "blood_sugar_diabetes": {
        "scene": "Spanish kitchen or dining table after a meal",
        "main_prop": "food choice related to the title (chia, bread, plate) or a post-meal walking cue",
        "avoid": ["syringe", "insulin injection", "hospital", "scary blood glucose monitor close-up", "medical panic"],
    },
    "blood_pressure_circulation_heart": {
        "scene": "Spanish home or park path with gentle movement context",
        "main_prop": "walking shoes, calf exercise cue, or a coffee cup when the topic mentions coffee",
        "avoid": ["heart attack imagery", "ECG monitor", "emergency room", "fake red veins"],
    },
    "sleep_rest": {
        "scene": "warm-lit bedroom in the evening",
        "main_prop": "bedside lamp, alarm clock, herbal tea, or phone face down",
        "avoid": ["sleeping pills as main visual"],
    },
    "energy_fatigue": {
        "scene": "morning kitchen or sofa with sunlight",
        "main_prop": "coffee cup or a daily-life cue showing realization of an energy-draining habit",
        "avoid": ["energy drink branding"],
    },
    "movement_stiffness": {
        "scene": "Spanish home living room or quiet park",
        "main_prop": "gentle stretching cue, chair stretch, or a yoga mat in a home setting",
        "avoid": ["injury drama", "hospital brace", "severe pain"],
    },
    "joint_pain_body_signal": {
        "scene": "Spanish home interior",
        "main_prop": "close-up of hand on knee, shoulder, or back — expressive but non-medical",
        "avoid": ["x-ray graphics", "scary inflammation overlay"],
    },
    "walking_cardio": {
        "scene": "Mediterranean park path or quiet Spanish street",
        "main_prop": "walking shoes, stairs, or a gentle outdoor cue",
        "avoid": ["marathon running cliches"],
    },
    "stress_mind": {
        "scene": "quiet Spanish sofa or balcony with warm light",
        "main_prop": "notebook, tea cup, phone face down, or a calm breathing posture",
        "avoid": ["panic attack imagery", "crying", "despair", "psychiatric clinic"],
    },
    "brain_memory_cognition": {
        "scene": "Spanish home with dignified, focused atmosphere",
        "main_prop": "keys, calendar, reading glasses, or a notebook — alongside a concerned but dignified adult",
        "avoid": ["scary brain CGI", "hospital", "dementia stigma", "helpless or frail senior imagery"],
    },
    "weight_loss_metabolism": {
        "scene": "Spanish home with a simple daily-life habit cue",
        "main_prop": "walking shoes, plate choice, or a subtle measuring-tape detail",
        "avoid": ["before/after body transformation", "body shame", "extreme scale panic"],
    },
    "aging_longevity_bad_habits": {
        "scene": "Spanish home with a bad-habit contrast cue",
        "main_prop": "late-night phone, poor snack, or sitting-too-long cue — tired realization",
        "avoid": ["decrepit elderly stereotype", "scary aging face morph", "humiliation"],
    },
    "daily_routine": {
        "scene": "Spanish kitchen counter or living room with daily-life objects",
        "main_prop": "calendar, checklist, or a morning/evening routine object",
        "avoid": ["productivity-bro cliches"],
    },
    "mistake_warning": {
        "scene": "Spanish home with a clear right-vs-wrong choice cue",
        "main_prop": "object showing the warned-against habit alongside a better alternative",
        "avoid": ["finger-wagging", "panic"],
    },
    "myth_truth": {
        "scene": "Spanish home with a simple side-by-side contrast",
        "main_prop": "two choices or two objects representing appearance vs reality",
        "avoid": ["clickbait shock visuals"],
    },
    "general_45plus_lifestyle": {
        "scene": "Spanish home, expressive but realistic 45–65 adult",
        # §5 fallback rule: no specific prop — avoid forcing food/sleep/exercise.
        "main_prop": "no specific prop required; use only one simple daily-life object if clearly supported by the title",
        "avoid": [],
    },
}


def select_visual_preset(category: str) -> dict[str, Any]:
    """Return preset dict for a category. Falls back to general lifestyle."""
    return dict(THUMBNAIL_VISUAL_PRESETS.get(
        category or "general_45plus_lifestyle",
        THUMBNAIL_VISUAL_PRESETS["general_45plus_lifestyle"],
    ))


# ---------------------------------------------------------------------------
# §9.1 / §9.2 Merge helpers
# ---------------------------------------------------------------------------

def stable_dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = str(value).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(value).strip())
    return out


_MEDICAL_SENSITIVE_AVOID_BOILERPLATE: list[str] = [
    "hospital",
    "doctor diagnosis scene",
    "medical emergency",
    "pills as main visual",
    "syringe",
    "fear-based medical imagery",
]


def merge_avoid_lists(
    primary_preset: dict,
    secondary_preset: dict | None,
    risk_level: str,
) -> list[str]:
    """Union of primary + secondary + risk-level avoid terms, deduped."""
    avoid: list[str] = []
    avoid.extend(primary_preset.get("avoid") or [])
    if secondary_preset:
        avoid.extend(secondary_preset.get("avoid") or [])
    if risk_level == "medical_sensitive":
        avoid.extend(_MEDICAL_SENSITIVE_AVOID_BOILERPLATE)
    return stable_dedupe(avoid)


def merge_main_prop(primary_preset: dict, secondary_preset: dict | None) -> str:
    """Combine primary + secondary props without overriding primary scene."""
    primary_prop = str(primary_preset.get("main_prop") or "").strip()
    secondary_prop = str((secondary_preset or {}).get("main_prop") or "").strip()

    if not primary_prop and not secondary_prop:
        return (
            "No specific prop required; use only one simple daily-life object "
            "if clearly supported by the title."
        )
    if not secondary_prop or secondary_prop == primary_prop:
        return primary_prop or secondary_prop
    if not primary_prop:
        return secondary_prop
    return f"{primary_prop}; supporting secondary cue: {secondary_prop}"


# ---------------------------------------------------------------------------
# §9.3 Category labels
# ---------------------------------------------------------------------------

CATEGORY_LABELS: dict[str, str] = {
    "food_choice": "food choice",
    "functional_foods_superfoods": "functional foods and daily nutrition",
    "shopping_label_choice": "shopping and label choice",
    "protein_muscle": "protein and muscle maintenance",
    "fiber_digestion": "fiber and digestion",
    "hydration": "hydration habit",
    "blood_sugar_diabetes": "blood sugar and diabetes prevention lifestyle",
    "blood_pressure_circulation_heart": "blood pressure, circulation, and heart-friendly habits",
    "sleep_rest": "sleep and rest",
    "energy_fatigue": "energy and fatigue",
    "movement_stiffness": "movement and stiffness",
    "joint_pain_body_signal": "body signal and joint discomfort",
    "walking_cardio": "walking and gentle cardio",
    "stress_mind": "stress and calm mind",
    "brain_memory_cognition": "memory and cognitive health",
    "weight_loss_metabolism": "weight loss and metabolism",
    "aging_longevity_bad_habits": "aging, longevity, and bad habits",
    "daily_routine": "daily routine",
    "mistake_warning": "mistake or warning",
    "myth_truth": "myth versus truth",
    "general_45plus_lifestyle": "general lifestyle after 45",
}


def category_label(category: str | None) -> str:
    if not category:
        return "none"
    return CATEGORY_LABELS.get(category, str(category).replace("_", " "))


# ---------------------------------------------------------------------------
# §7.4 Strategy description
# ---------------------------------------------------------------------------

def describe_strategy(strategy: str) -> str:
    if strategy == "face_driven":
        return (
            "FACE-DRIVEN. A real, expressive mature face is the main hook — "
            "confident or concerned-but-hopeful, ideally mid-gesture (e.g. pointing "
            "at the prop). Use a genuine photographic person; this authenticity is "
            "the channel's edge over AI-looking competitors. The topic prop stays "
            "visible but secondary."
        )
    if strategy == "object_driven":
        return (
            "OBJECT-DRIVEN. The topic object is the dominant hook: large, sharp and "
            "instantly understandable at thumbnail size. Add ONE bold red arrow "
            "pointing at the key object. If the topic is a numbered list, tag the "
            "relevant objects with large red number badges 1, 2, 3. A person is "
            "optional (hands-only or absent) — a strong object plus curiosity can "
            "out-click a face."
        )
    if strategy == "comparison_driven":
        return (
            "COMPARISON-DRIVEN. Split the frame into two clear zones — a worse/old "
            "habit versus a better/new one — marked with a large red cross and a "
            "green check, plus small ANTES / DESPUÉS labels. Keep it about everyday "
            "habits or food, never body-shaming or medical fear."
        )
    return "FACE-DRIVEN. Use a clear expressive face and one topic-relevant visual cue."


VISUAL_STRATEGIES: dict[int, str] = {
    1: "face_driven",
    2: "object_driven",
    3: "comparison_driven",
}


# ---------------------------------------------------------------------------
# §11.1 Safety rules
# ---------------------------------------------------------------------------

def safety_rules_for_category(primary_category: str, risk_level: str, avoid: list[str]) -> str:
    avoid_text = ", ".join(avoid[:12])

    category_hint = ""
    if primary_category == "brain_memory_cognition":
        category_hint = (
            "Show cognitive concern with dignity; avoid stigma or helplessness. "
        )
    elif primary_category == "blood_sugar_diabetes":
        category_hint = (
            "Show lifestyle context around food or gentle movement; "
            "avoid injections or diagnosis scenes. "
        )
    elif primary_category == "blood_pressure_circulation_heart":
        category_hint = (
            "Show gentle circulation-friendly lifestyle cues; "
            "avoid emergency heart imagery. "
        )
    elif primary_category == "weight_loss_metabolism":
        category_hint = (
            "Avoid body shame, extreme scales, and transformation imagery. "
        )

    if risk_level == "medical_sensitive":
        return (
            category_hint
            + "Keep the image dignified, practical, and non-alarmist. "
            + "Avoid fear-based medical visuals. "
            + f"Do not show: {avoid_text}."
        )
    if risk_level == "soft_health":
        return (
            category_hint
            + "Keep the image practical and lifestyle-oriented. "
            + "Show realistic daily habits, not diagnosis or treatment scenes. "
            + f"Avoid: {avoid_text}."
        )
    return (
        category_hint
        + "Keep the image lifestyle-oriented, warm, and realistic. "
        + f"Avoid: {avoid_text}."
    )


# ---------------------------------------------------------------------------
# §8 Persona
# ---------------------------------------------------------------------------

def select_thumbnail_persona(profile: dict, strategy: str, variant_index: int) -> str:
    age_range = AGE_RANGE_BY_SIGNAL.get(profile.get("age_signal", "unknown"), "45–65")
    if strategy == "object_driven":
        return (
            f"hands or partial view of a Mediterranean Spanish adult aged {age_range}"
        )
    if strategy == "comparison_driven":
        return (
            f"Mediterranean Spanish adult aged {age_range}, "
            "or two simple choice zones with minimal people"
        )
    return f"natural-looking Mediterranean Spanish adult aged {age_range}"


# ---------------------------------------------------------------------------
# §12 Variant normalization
# ---------------------------------------------------------------------------

def normalize_thumbnail_variants(seo: dict) -> list[dict]:
    title = str(seo.get("title") or "").strip()
    raw_variants = seo.get("title_variants") or []

    variants: list[dict] = []
    for i, v in enumerate(raw_variants[:3], start=1):
        if not isinstance(v, dict):
            continue
        variant_title = str(v.get("title") or title).strip()
        thumbnail_text = str(v.get("thumbnail_text") or "").strip()
        if not thumbnail_text:
            continue
        variants.append(
            {
                "variant_index": i,
                "title": variant_title,
                "thumbnail_text": thumbnail_text,
            }
        )

    if not variants:
        fallback_text = str(seo.get("thumbnail_text") or "").strip()
        if not fallback_text:
            fallback_text = (
                " ".join(title.split()[:5]).upper() or "VIDA PLENA 45+"
            )
        variants.append(
            {
                "variant_index": 1,
                "title": title,
                "thumbnail_text": fallback_text,
            }
        )
    return variants


# ---------------------------------------------------------------------------
# §13.2 Accent color resolution
# ---------------------------------------------------------------------------

def resolve_thumbnail_accent_color(channel_config: dict) -> str:
    thumbnail_cfg = (channel_config or {}).get("thumbnail") or {}
    if thumbnail_cfg.get("accent_color"):
        return str(thumbnail_cfg["accent_color"])
    style = (channel_config or {}).get("style") or {}
    palette = style.get("palette") or {}
    for key in ("accent", "secondary", "primary"):
        if palette.get(key):
            return str(palette[key])
    return "#F2C94C"


def resolve_thumbnail_punch_color(channel_config: dict) -> str:
    """Red box color for the 1-2 word punch line (competitor-proven CTR device)."""
    thumbnail_cfg = (channel_config or {}).get("thumbnail") or {}
    if thumbnail_cfg.get("punch_color"):
        return str(thumbnail_cfg["punch_color"])
    return "#E11D2A"


# ---------------------------------------------------------------------------
# §10 Prompt builder
# ---------------------------------------------------------------------------

_CHANNEL_DESCRIPTION_DEFAULT = (
    "Vida Plena 45+, practical wellness, nutrition and lifestyle "
    "for Spanish adults over 45."
)


def build_thumbnail_prompt(plan: dict) -> str:
    return (
        "Create a complete photorealistic YouTube thumbnail, 16:9, 1920x1080.\n"
        "\n"
        f"Topic:\n\"{plan.get('variant_title', '')}\"\n"
        "\n"
        f"Hook text to render exactly:\n\"{plan.get('thumbnail_text', '')}\"\n"
        "\n"
        f"Channel:\n{plan.get('channel_description') or _CHANNEL_DESCRIPTION_DEFAULT}\n"
        "\n"
        "Visual category:\n"
        f"{plan.get('primary_category_label', '')}; "
        f"secondary cue: {plan.get('secondary_category_label', 'none')}\n"
        "\n"
        f"Visual strategy:\n{plan.get('visual_strategy_description', '')}\n"
        "\n"
        f"Scene:\n{plan.get('scene', '')}\n"
        "\n"
        "Subject:\n"
        f"{plan.get('persona', '')}\n"
        "Place the subject according to the visual strategy.\n"
        "Face should be clear when the strategy is face_driven.\n"
        "Expression should match the hook and topic: concerned but hopeful, "
        "practical urgency, curiosity, or realization.\n"
        "Do not make the person look frail, sick, helpless, or like a sad senior stereotype.\n"
        "\n"
        "Main prop:\n"
        f"{plan.get('main_prop', '')}\n"
        "The prop must make the topic instantly clear at thumbnail size.\n"
        "Use realistic physical objects only.\n"
        "No icons, stickers, emojis, medical diagrams, or product labels.\n"
        "\n"
        "Composition:\n"
        "Design for YouTube thumbnail readability.\n"
        "High contrast, vivid and attention-grabbing while staying photographic "
        "and trustworthy (not over-saturated or fake).\n"
        "Reserve clean space for the hook text. Avoid clutter; keep ONE clear focal point.\n"
        "Strategy graphics: you MAY add ONE bold red arrow, a red cross + green "
        "check, OR large number badges (1, 2, 3) ONLY when the visual strategy "
        "above calls for them, to guide the eye. No emojis, stickers, logos, "
        "watermarks, or medical diagrams.\n"
        "\n"
        "Text (mobile-first, 2-block hierarchy):\n"
        f"Use this EXACT wording only:\n\"{plan.get('thumbnail_text', '')}\"\n"
        "Preserve Spanish accents and punctuation: ñ, á, é, í, ó, ú, ü, ¿, ¡.\n"
        "Split it into at most 2-3 short blocks. The single most important 1-2 "
        f"words go inside a solid {plan.get('punch_color', '#E11D2A')} red box as "
        "the punch line; the rest is huge bold white ALL-CAPS with a thick black "
        f"outline and drop shadow, using accent {plan.get('accent_color', '#F2C94C')} "
        "to highlight one key word.\n"
        "Readability rule: the wording must be legible when the thumbnail is only "
        "about 210 px wide (mobile feed) — letters as large as possible, total "
        "wording no more than 7 words, never a full sentence.\n"
        "No other text beyond the wording above.\n"
        "\n"
        "Style:\n"
        "Photorealistic editorial YouTube thumbnail.\n"
        "Warm natural light, high contrast, crisp details.\n"
        "Sharp face when visible. Sharp topic prop.\n"
        "No blur, no plastic skin, no warped anatomy, no extra fingers.\n"
        "\n"
        "Safety and tone:\n"
        f"{plan.get('category_safety_rules', '')}\n"
        "The image should feel like practical lifestyle advice, "
        "not fear-based medical content.\n"
    )


# ---------------------------------------------------------------------------
# §13 Orchestrator
# ---------------------------------------------------------------------------

def plan_thumbnail_prompts(seo: dict, channel_config: dict) -> list[dict]:
    """Build up to three deterministic prompt plans for a SEO record."""
    variants = normalize_thumbnail_variants(seo)
    plans: list[dict] = []
    accent_color = resolve_thumbnail_accent_color(channel_config)
    punch_color = resolve_thumbnail_punch_color(channel_config)
    channel_description = (
        (channel_config or {}).get("description")
        or _CHANNEL_DESCRIPTION_DEFAULT
    )

    for variant in variants[:3]:
        index = int(variant["variant_index"])
        strategy = VISUAL_STRATEGIES.get(index, "face_driven")
        profile = classify_thumbnail_topic(
            variant["title"], variant["thumbnail_text"]
        )
        primary_preset = select_visual_preset(profile["primary_category"])
        secondary_preset = (
            select_visual_preset(profile["secondary_category"])
            if profile.get("secondary_category")
            else None
        )
        persona = select_thumbnail_persona(profile, strategy, index)

        plan: dict[str, Any] = {
            "variant_index": index,
            "variant_title": variant["title"],
            "thumbnail_text": variant["thumbnail_text"],
            "primary_category": profile["primary_category"],
            "secondary_category": profile.get("secondary_category"),
            "primary_category_label": category_label(profile["primary_category"]),
            "secondary_category_label": category_label(
                profile.get("secondary_category")
            ),
            "risk_level": profile["risk_level"],
            "age_signal": profile["age_signal"],
            "visual_strategy": strategy,
            "visual_strategy_description": describe_strategy(strategy),
            "persona": persona,
            "scene": primary_preset["scene"],
            "main_prop": merge_main_prop(primary_preset, secondary_preset),
            "avoid": merge_avoid_lists(
                primary_preset, secondary_preset, profile["risk_level"]
            ),
            "accent_color": accent_color,
            "punch_color": punch_color,
            "channel_description": channel_description,
        }
        plan["category_safety_rules"] = safety_rules_for_category(
            plan["primary_category"], plan["risk_level"], plan["avoid"]
        )
        plan["prompt"] = build_thumbnail_prompt(plan)
        plans.append(plan)

    return plans
