"""Thumbnail prompt planner — spec v1.3.

Topic-aware planner for the ChatGPT thumbnail image flow used by
`auto_thumbnail_image_stage`. Phase 1: planner module with deterministic
classifier, category presets, and prompt builder. Phase 2 wires this into
the stage; backward-compatible wrapper kept in stages.py.

See docs/specs/thumbnail-prompt-planner-v1.3.md.
"""

from __future__ import annotations

import colorsys
import hashlib
import math
import re
import unicodedata
from typing import Any

from video_agent.style_dna import is_valid_hex

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
    "surgery_medical_decision": [
        "cirugia", "operacion", "operar", "quirofano", "quirurgic",
        "preoperator", "posoperator",
    ],
    "food_choice": [
        "pan", "tostada", "desayuno", "cena", "comida", "plato",
        "aceite", "yogur", "fruta", "verduras", "arroz", "pasta", "integral",
        "hambre", "antojo", "apetito", "picoteo",
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
    "surgery_medical_decision",
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
    "cirugia",
    "operacion",
    "operar",
    "quirofano",
    "quirurgic",
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
    "surgery_medical_decision",
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
    "surgery_medical_decision": {
        "scene": (
            "bright specialist consultation room beside a hospital surgical "
            "admissions area"
        ),
        "main_prop": (
            "a clearly visible surgical consent form and surgery appointment "
            "folder while a doctor and mature patient discuss the decision"
        ),
        "avoid": [
            "open surgery",
            "blood",
            "exposed organs",
            "incisions",
            "needles as the focal point",
            "fear-based emergency imagery",
            "generic gym or food props",
        ],
    },
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
# Topic-first visual props (2026-07-12)
#
# Category presets describe the topic FAMILY, not the video: a salt/heart hook
# classified as blood_pressure_circulation_heart used to get "walking shoes on
# a park path". For a 45–75 viewer the image must restate the hook message by
# itself, so the concrete objects the hook/title actually NAME become the
# dominant prop and drive the scene; the preset drops to secondary context.
# ---------------------------------------------------------------------------

_TOPIC_SCENES: dict[str, str] = {
    "clinic": (
        "bright specialist consultation room beside a hospital surgical "
        "admissions area, dignified and non-gory"
    ),
    "kitchen": (
        "Spanish home kitchen or dining table, bright and uncluttered, "
        "with the topic food in clear focus"
    ),
    "market": "Spanish supermarket aisle, labels intentionally blurred",
    "outdoor": "quiet Mediterranean park path or Spanish street in warm daylight",
    "bedroom": "warm-lit Spanish bedroom in the evening",
    "living": "Spanish living room or sofa with warm natural light",
    "home": "Spanish home interior, simple and uncluttered",
}

# (exact words, word stems, English visual object, scene key). Order matters
# only within a text scan — first named object wins the scene. Words match
# whole tokens; stems match token prefixes; never free substrings, so "pan"
# cannot fire inside "pantalla".
_TOPIC_VISUAL_VOCABULARY: tuple[tuple[frozenset[str], tuple[str, ...], str, str], ...] = (
    (
        frozenset({"cirugia", "cirugias", "operacion", "operaciones", "operar", "quirofano"}),
        ("quirurg", "preoperator", "posoperator"),
        "a surgical consent form and a clearly visible surgery appointment folder",
        "clinic",
    ),
    (frozenset({"sal", "salero", "sodio"}), (), "a salt shaker and visibly salty foods", "kitchen"),
    (frozenset({"pan", "panes", "tostada", "tostadas"}), (), "loaves of bread or toast", "kitchen"),
    (frozenset({"cafe", "cafeina"}), (), "a cup of coffee", "kitchen"),
    (frozenset({"aceite", "oliva"}), (), "a bottle of olive oil", "kitchen"),
    (frozenset({"avena"}), (), "a bowl of oats", "kitchen"),
    (frozenset({"chia"}), (), "a bowl of chia pudding", "kitchen"),
    (frozenset({"yogur"}), (), "a cup of natural yogurt", "kitchen"),
    (frozenset({"huevo", "huevos"}), (), "fresh eggs", "kitchen"),
    (frozenset({"leche"}), (), "a glass of milk", "kitchen"),
    (frozenset({"queso"}), (), "a piece of cheese", "kitchen"),
    (frozenset({"fruta", "frutas"}), (), "fresh fruit", "kitchen"),
    (frozenset({"verdura", "verduras", "ensalada"}), (), "fresh vegetables or a salad plate", "kitchen"),
    (frozenset({"pescado", "salmon", "sardinas"}), (), "a plate of fish", "kitchen"),
    (frozenset({"carne"}), (), "a lean meat dish", "kitchen"),
    (frozenset(), ("legumbr",), "a bowl of legumes", "kitchen"),
    (frozenset({"nueces"}), ("frutos",), "a handful of nuts", "kitchen"),
    (frozenset(), ("azucar",), "sugar cubes beside a sugar bowl", "kitchen"),
    (frozenset({"agua"}), ("hidrat",), "a clear glass of water", "kitchen"),
    (frozenset({"infusion", "manzanilla"}), (), "a cup of herbal tea", "kitchen"),
    (frozenset({"sopa", "caldo"}), (), "a bowl of soup", "kitchen"),
    (frozenset({"cena", "cenas"}), (), "a light dinner plate", "kitchen"),
    (frozenset(), ("desayun",), "a simple breakfast table", "kitchen"),
    (frozenset({"plato", "platos", "alimento", "alimentos", "comida", "comidas"}), (), "a prepared plate of everyday food", "kitchen"),
    (frozenset({"fibra"}), (), "fiber-rich vegetables and legumes", "kitchen"),
    (frozenset({"colageno"}), (), "collagen-rich foods such as broth, fish, and citrus", "kitchen"),
    (frozenset(), ("protein",), "a simple high-protein meal", "kitchen"),
    (frozenset(), ("vitamin",), "vitamin-rich fresh foods", "kitchen"),
    (frozenset({"corazon"}), (), "heart-healthy fresh foods (vegetables, olive oil, fish)", "kitchen"),
    (frozenset({"etiqueta", "etiquetas", "envase", "supermercado"}), (), "two food packages compared label to label, labels blurred", "market"),
    # "pasos" is deliberately absent: "en 5 pasos" means PROCESS steps far more
    # often than walking in this channel's copy (a salt-audit poster once got
    # walking shoes from it); caminar/paseo/andar carry the walking topics.
    (frozenset({"paseo", "andar"}), ("camin",), "walking shoes mid-step", "outdoor"),
    (frozenset({"escaleras"}), (), "home stairs being climbed", "home"),
    (frozenset({"ejercicio", "ejercicios"}), ("estir",), "a simple home stretching pose", "living"),
    (frozenset(), ("muscul",), "a light dumbbell or resistance band", "living"),
    (frozenset({"pantalla", "pantallas", "movil", "telefono"}), (), "a glowing phone or screen at night", "bedroom"),
    (frozenset({"dormir", "sueno", "insomnio", "siesta"}), ("duerm",), "a bed with a warm bedside lamp", "bedroom"),
    (frozenset({"rodilla", "rodillas"}), (), "a hand resting on a knee", "home"),
    (frozenset({"espalda"}), (), "a hand pressed to the lower back", "home"),
    (frozenset({"memoria", "olvido", "olvidos"}), (), "house keys and a wall calendar", "home"),
)

_TOPIC_TOKEN_RE = re.compile(r"[a-z]+")


def _match_topic_entry(token: str) -> tuple[str, str] | None:
    for words, stems, phrase, scene in _TOPIC_VISUAL_VOCABULARY:
        if token in words or any(token.startswith(stem) for stem in stems):
            return phrase, scene
    return None


def derive_topic_props(title: str, thumbnail_text: str = "") -> list[str]:
    """Concrete visual objects the hook/title name, hook-first, max 3.

    Deterministic and offline: whole-token (or stem-prefix) matches against a
    curated Spanish→visual vocabulary. Hook text is scanned before the title
    because the hook IS the message the image must restate."""
    return [phrase for phrase, _ in _derive_topic_visuals(title, thumbnail_text)]


def _derive_topic_visuals(title: str, thumbnail_text: str) -> list[tuple[str, str]]:
    ordered_text = f"{thumbnail_text} {title}"
    tokens = _TOPIC_TOKEN_RE.findall(
        normalize_for_thumbnail_classification(ordered_text)
    )
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token in tokens:
        hit = _match_topic_entry(token)
        if hit and hit[0] not in seen:
            seen.add(hit[0])
            out.append(hit)
        if len(out) >= 3:
            break
    return out


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

_SURGERY_MEDICAL_DECISION_AVOID_BOILERPLATE: list[str] = [
    "medical emergency",
    "pills as main visual",
    "syringe",
    "fear-based medical imagery",
    "graphic operating-room procedure",
]


def merge_avoid_lists(
    primary_preset: dict,
    secondary_preset: dict | None,
    risk_level: str,
    primary_category: str | None = None,
) -> list[str]:
    """Union of primary + secondary + risk-level avoid terms, deduped."""
    avoid: list[str] = []
    avoid.extend(primary_preset.get("avoid") or [])
    if secondary_preset:
        avoid.extend(secondary_preset.get("avoid") or [])
    if primary_category == "surgery_medical_decision":
        avoid.extend(_SURGERY_MEDICAL_DECISION_AVOID_BOILERPLATE)
    elif risk_level == "medical_sensitive":
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
    "surgery_medical_decision": "surgery and shared medical decision-making",
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

def describe_strategy(strategy: str, primary_category: str | None = None) -> str:
    if strategy == "face_driven":
        return (
            "FACE-DRIVEN. Use one candid mature face as the emotional anchor, "
            "captured in a believable unscripted moment with natural eye direction "
            "toward the topic prop. Keep the gesture subtle: no pointing pose, no "
            "arrow, badge, or reaction-face exaggeration. The topic prop remains "
            "clearly readable but secondary."
        )
    if strategy == "object_driven":
        return (
            "OBJECT-DRIVEN. Build a tactile editorial still life around the exact "
            "topic object. Use a close or overhead camera angle and let scale, light, "
            "and natural hand placement guide the eye. Use no presenter, no arrow, "
            "no badge, and no decorative symbol; the object itself carries the hook."
        )
    if strategy == "comparison_driven":
        if primary_category == "surgery_medical_decision":
            return (
                "COMPARISON-DRIVEN. Show the OPERAR versus ESPERAR decision as "
                "two dignified, non-gory clinical choice zones: proceeding with "
                "the planned surgery versus watchful waiting and follow-up. "
                "Communicate the contrast visually without extra printed labels; "
                "the exact thumbnail wording remains the only text."
            )
        return (
            "COMPARISON-DRIVEN. Contrast two concrete choices or states in one "
            "coherent editorial frame through object placement, lighting, and space. "
            "Use no duplicated presenter, no printed labels, no cross/check symbols, "
            "and no body-shaming or medical-fear shorthand."
        )
    return "FACE-DRIVEN. Use a clear expressive face and one topic-relevant visual cue."


VISUAL_STRATEGIES: dict[int, str] = {
    1: "face_driven",
    2: "object_driven",
    3: "comparison_driven",
}


ART_DIRECTION_BY_STRATEGY: dict[str, str] = {
    "face_driven": (
        "HUMAN STORY: intimate eye-level crop with one candid presenter on one side "
        "and the topic prop on the other; quiet lived-in background; compact text in "
        "the clean negative space."
    ),
    "object_driven": (
        "OBJECT PROOF: no presenter; close or overhead view of the exact topic object, "
        "with hands only when a real action must be shown; different camera angle and "
        "background from the human-story variant."
    ),
    "comparison_driven": (
        "DECISION CONTRAST: two concrete choices in one balanced frame, no duplicated "
        "presenter and no symbolic verdict graphics; communicate the contrast using "
        "real objects, spacing, and light."
    ),
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
    elif primary_category == "surgery_medical_decision":
        category_hint = (
            "Make surgery visually unmistakable through shared medical "
            "decision-making, a surgical consent form, and a surgery appointment "
            "folder. Keep the consultation dignified and non-gory; avoid blood, "
            "open procedures, exposed organs, and emergency panic. "
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
            "no presenter; natural hands of a Mediterranean Spanish adult aged "
            f"{age_range} only if the action requires them"
        )
    if strategy == "comparison_driven":
        return (
            "no duplicated presenter; use real objects or one subtle partial figure "
            f"of a Mediterranean Spanish adult aged {age_range}"
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


def _normalize_color_hex(value: str) -> str:
    value = str(value).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    return f"#{value.upper()}"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = _normalize_color_hex(value).lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_distance(left: str, right: str) -> float:
    return math.dist(_hex_to_rgb(left), _hex_to_rgb(right))


def _shift_thumbnail_color(value: str, hue_shift: float) -> str:
    red, green, blue = (component / 255 for component in _hex_to_rgb(value))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    hue = (hue + hue_shift) % 1.0
    saturation = min(0.68, max(0.38, saturation))
    lightness = min(0.62, max(0.34, lightness))
    shifted = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#" + "".join(f"{round(component * 255):02X}" for component in shifted)


def resolve_thumbnail_variant_colors(
    channel_config: dict,
    topic_accent: str | None,
    seed_text: str,
) -> list[str]:
    """Return three deterministic, visibly distinct, brand-grounded accents."""
    palette = ((channel_config or {}).get("style") or {}).get("palette") or {}
    candidates = [
        topic_accent,
        ((channel_config or {}).get("thumbnail") or {}).get("accent_color"),
        palette.get("accent"),
        palette.get("secondary"),
        palette.get("primary"),
    ]
    colors: list[str] = []
    for candidate in candidates:
        if not is_valid_hex(candidate):
            continue
        color = _normalize_color_hex(str(candidate))
        if all(_rgb_distance(color, existing) >= 48 for existing in colors):
            colors.append(color)

    fallback = resolve_thumbnail_accent_color(channel_config)
    base = colors[0] if colors else (
        _normalize_color_hex(fallback) if is_valid_hex(fallback) else "#F2C94C"
    )
    for shift in (0.16, -0.18, 0.34, -0.34):
        if len(colors) >= 3:
            break
        candidate = _shift_thumbnail_color(base, shift)
        if all(_rgb_distance(candidate, existing) >= 48 for existing in colors):
            colors.append(candidate)

    digest = hashlib.sha256(normalize_for_thumbnail_classification(seed_text).encode("utf-8")).digest()
    offset = digest[0] % len(colors)
    rotated = colors[offset:] + colors[:offset]
    return [rotated[index % len(rotated)] for index in range(3)]


# ---------------------------------------------------------------------------
# §10 Prompt builder
# ---------------------------------------------------------------------------

_CHANNEL_DESCRIPTION_DEFAULT = (
    "Vida Plena 45+, practical wellness, nutrition and lifestyle "
    "for Spanish adults over 45."
)


def build_thumbnail_prompt(plan: dict) -> str:
    return (
        "Create an authentic editorial YouTube thumbnail from a candid photograph, "
        "16:9, 1920x1080. It must feel observed and specific, not like a generic "
        "AI thumbnail template.\n"
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
        f"Variant art direction:\n{plan.get('variant_art_direction', '')}\n"
        "\n"
        f"Scene:\n{plan.get('scene', '')}\n"
        "\n"
        "Subject:\n"
        + (
            "RECURRING PRESENTER FOR THIS FACE-LED VARIANT: a natural-looking mature "
            "Mediterranean Spanish woman (around 55-65) with a silver-gray bob. Preserve "
            "recognizable identity while keeping pores, asymmetry, expression lines, and "
            "a candid non-model pose.\n"
            if plan.get("persona_locked")
            else ""
        )
        + f"{plan.get('persona', '')}\n"
        "Place the subject according to the visual strategy.\n"
        "Face should be clear when the strategy is face_driven.\n"
        "Expression should match the hook without staged shock or exaggerated urgency.\n"
        "Do not make the person look frail, sick, helpless, or like a sad senior stereotype.\n"
        "\n"
        "Main prop:\n"
        f"{plan.get('main_prop', '')}\n"
        "The prop must make the topic instantly clear at thumbnail size.\n"
        "Use realistic physical objects only.\n"
        "No icons, stickers, emojis, medical diagrams, or product labels.\n"
        "\n"
        "Message match (MANDATORY):\n"
        "The viewer is a Spanish adult aged 45-75 scanning a phone feed. From the "
        "IMAGE ALONE — before reading any text — they must understand what this "
        f"video offers. The image must SHOW the same message the hook text states "
        f"(\"{plan.get('thumbnail_text', '')}\"): depict the exact objects the hook "
        "names, doing or showing the thing the hook promises. Also feature the "
        "SPECIFIC, distinctive subject named in the Topic above — the concrete "
        "event, activity, place, food, or object that makes THIS exact video "
        "unique (e.g. if the topic mentions watching a football match, a TV "
        "showing a match must be visibly present). Never fall back to a GENERIC "
        "category scene that could illustrate any video in this niche.\n"
        "\n"
        "Composition (SIMPLE beats clever):\n"
        "Design for YouTube thumbnail readability at feed size.\n"
        "Use only the people allowed by the variant art direction, the topic prop(s), "
        "and a clean, uncluttered background. "
        "At most 3 distinct objects in the whole frame; every object must help "
        "tell the hook's message, remove everything else.\n"
        "Use clear tonal separation while staying photographic, calm, and trustworthy.\n"
        "Reserve clean space for the hook text. Keep ONE clear focal point.\n"
        "Do not add directional graphics, verdict symbols, decorative counters, emojis, stickers, logos, "
        "watermarks, decorative labels, or medical diagrams.\n"
        "\n"
        "Text (mobile-first, clear but subordinate to the visual story):\n"
        f"Use this EXACT wording only:\n\"{plan.get('thumbnail_text', '')}\"\n"
        "Preserve Spanish accents and punctuation: ñ, á, é, í, ó, ú, ü, ¿, ¡.\n"
        "Set the wording in 2-3 compact lines covering about 25-35% of the frame. "
        "It must be immediately readable for viewers aged 60+ without covering the "
        "face, action, or evidence object. Use restrained high-contrast editorial "
        "lettering with a subtle shadow or thin keyline only when needed.\n"
        f"Use accent {plan.get('accent_color', '#F2C94C')} only for one key word, "
        "a thin underline, or a small editorial tag. Do not use a solid text box, "
        "giant outline, heavy shadow, or full-width banner.\n"
        "Readability rule: the wording must be legible when the thumbnail is only "
        "about 210 px wide (mobile feed), with no more than 6 words and no full sentence.\n"
        "No other text beyond the wording above.\n"
        "\n"
        "Style:\n"
        "Candid editorial photography, natural window or location light, realistic "
        "skin texture, restrained color, and slight photographic grain.\n"
        "Keep the face and topic prop optically credible; background depth of field is "
        "allowed. No plastic skin, warped anatomy, or extra fingers.\n"
        "\n"
        "Safety and tone:\n"
        f"{plan.get('category_safety_rules', '')}\n"
        "The image should feel like practical, trustworthy guidance, "
        "not fear-based medical content.\n"
    )


# ---------------------------------------------------------------------------
# §13 Orchestrator
# ---------------------------------------------------------------------------

def plan_thumbnail_prompts(seo: dict, channel_config: dict) -> list[dict]:
    """Build up to three deterministic prompt plans for a SEO record."""
    variants = normalize_thumbnail_variants(seo)
    plans: list[dict] = []
    # Per-video topic accent (ChatGPT-chosen in the seo stage, constrained to
    # harmonize with the brand palette) wins over the channel-wide default so
    # each video's thumbnail gets its own highlight colour instead of every
    # video reusing the same static brand accent.
    topic_accent = (seo or {}).get("topic_accent_color")
    variant_colors = resolve_thumbnail_variant_colors(
        channel_config,
        str(topic_accent) if is_valid_hex(topic_accent) else None,
        str((seo or {}).get("title") or ""),
    )
    if len(variants) == 1:
        single_accent = (
            str(topic_accent)
            if is_valid_hex(topic_accent)
            else resolve_thumbnail_accent_color(channel_config)
        )
        variant_colors[0] = _normalize_color_hex(single_accent)
    channel_description = (
        (channel_config or {}).get("description")
        or _CHANNEL_DESCRIPTION_DEFAULT
    )
    # Persona identity lock: when the channel configures a presenter reference
    # photo, the generation stage attaches it and the prompt pins the identity.
    persona_reference = str(
        ((channel_config or {}).get("thumbnail") or {}).get("persona_reference") or ""
    ).strip()

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
            "visual_strategy_description": describe_strategy(
                strategy, profile["primary_category"]
            ),
            "variant_art_direction": ART_DIRECTION_BY_STRATEGY.get(
                strategy, ART_DIRECTION_BY_STRATEGY["face_driven"]
            ),
            "persona": persona,
            "scene": primary_preset["scene"],
            "main_prop": merge_main_prop(primary_preset, secondary_preset),
            "topic_props": [],
            "avoid": merge_avoid_lists(
                primary_preset,
                secondary_preset,
                profile["risk_level"],
                profile["primary_category"],
            ),
            "accent_color": variant_colors[(index - 1) % len(variant_colors)],
            "channel_description": channel_description,
            "persona_reference": persona_reference,
            "persona_locked": bool(persona_reference) and strategy == "face_driven",
        }
        # Topic-first override: when the hook/title name concrete objects,
        # those objects lead the composition and pick the scene; the category
        # preset becomes secondary context only (2026-07-12).
        topic_visuals = _derive_topic_visuals(
            variant["title"], variant["thumbnail_text"]
        )
        if topic_visuals:
            topic_phrases = [phrase for phrase, _ in topic_visuals]
            plan["topic_props"] = topic_phrases
            plan["scene"] = _TOPIC_SCENES[topic_visuals[0][1]]
            plan["main_prop"] = (
                f"{'; '.join(topic_phrases)} — the exact objects the hook text "
                "names. These MUST be the dominant, unmistakable props. "
                f"Category context (secondary only): {plan['main_prop']}"
            )

        plan["category_safety_rules"] = safety_rules_for_category(
            plan["primary_category"], plan["risk_level"], plan["avoid"]
        )
        plan["prompt"] = build_thumbnail_prompt(plan)
        plans.append(plan)

    return plans
