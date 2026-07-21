"""
SEO/CTR scorer for YouTube title + thumbnail text combinations.
Scores 0-100 total (title 0-50, thumbnail 0-50).

Thumbnail scoring implements the audience-fit copy contract
(docs/specs/2026-07-12-long-form-thumbnail-copy-audience-fit.md): a clear,
self-contained Spanish micro-promise for viewers aged 45-75 must outrank a
context-free curiosity fragment, deterministically and offline.
"""

from __future__ import annotations

import re
import unicodedata

_POWER_WORDS = {
    "secreto", "mejor", "peor", "error", "nunca", "siempre",
    "cómo", "qué", "gratis", "rápido", "fácil", "verdad", "clave", "por qué",
    "oculto", "oculta", "inesperado", "inesperada", "decisivo", "decisiva",
    "sorprendente", "silencioso", "silenciosa", "señal", "señales",
    "hábito", "hábitos", "riesgo", "advertencia", "cambia", "cambio",
    "descubre", "revela", "evita", "ignoras", "sabías", "realidad",
}

_EMOTION_WORDS = {
    "DUERME", "INSOMNIO", "DOLOR", "SECRETO", "NUNCA", "AHORA", "MEJOR",
    "PEOR", "HOY", "YA", "FATAL", "VERDAD", "ALERTA", "CUIDADO",
    "OCULTO", "OCULTA", "INESPERADO", "SORPRENDENTE", "SILENCIOSO",
    "RIESGO", "ADVERTENCIA", "CAMBIA", "DESCUBRE", "REVELA", "EVITA",
    "SEÑAL", "SEÑALES", "REALIDAD", "ERROR", "CLAVE",
}


def _title_score(variant: dict) -> dict:
    """Return scoring breakdown for the title field. Includes a 'total' key (0-50)."""
    title = str(variant.get("title") or "")
    words = title.split()
    word_count = len(words)
    length = len(title)

    # Word count scoring
    if 6 <= word_count <= 10:
        word_count_score = 15
    elif 4 <= word_count <= 12:
        word_count_score = 8
    else:
        word_count_score = 0

    # Contains a digit
    digit_score = 10 if any(ch.isdigit() for ch in title) else 0

    # Contains question mark or inverted question mark
    question_score = 8 if ("?" in title or "¿" in title) else 0

    # Contains a power word (case-insensitive, multi-word phrase too)
    title_lower = title.lower()
    power_score = 0
    for pw in _POWER_WORDS:
        if pw in title_lower:
            power_score = 10
            break

    # Length 40-70 chars
    length_score = 7 if 40 <= length <= 70 else 0

    total = word_count_score + digit_score + question_score + power_score + length_score

    return {
        "word_count_score": word_count_score,
        "digit_score": digit_score,
        "question_score": question_score,
        "power_score": power_score,
        "length_score": length_score,
        "total": total,
    }


# ── audience-fit semantics (accent-normalized Spanish token/phrase sets) ─────
# Signal classes per the copy contract (C2): a standalone micro-promise carries
# at least TWO of topic / pain / outcome / action / honest specificity.

_TOPIC_OBJECTS = {
    "cafe", "cafeina", "te", "partido", "partidos", "mundial", "futbol",
    "aceite", "oliva", "alimento", "alimentos", "comida", "comidas", "cena",
    "cenas", "desayuno", "pan", "agua", "siesta", "pantalla", "pantallas",
    "movil", "azucar", "fruta", "verdura", "verduras", "plato", "etiqueta",
    "envase", "vitamina", "vitaminas", "proteina", "caminata", "paseo",
    # Body-posture & relaxation objects (bug-547): this is a sleep/mobility
    # wellness channel, not only a nutrition one. These are concrete, depictable
    # thumbnail subjects — legs, a chair, a posture, breathing, bedding — every
    # bit as anchoring as "café" or "pan".
    "pierna", "piernas", "rodilla", "rodillas", "espalda", "cuello", "hombro",
    "hombros", "cadera", "caderas", "pies", "silla", "postura", "posturas",
    "respiracion", "almohada", "almohadas", "colchon", "cama", "manta",
    "estiramiento", "estiramientos",
}
_PAIN_TERMS = {
    "sueno", "insomnio", "dolor", "dolores", "cansancio", "fatiga", "estres",
    "ansiedad", "duermes", "empeora", "pesadez", "rigidez", "molestia",
    "molestias", "tension", "tensiones", "hinchazon", "calambre", "calambres",
}
# Sleep-disruption symptom stems (bug-547): DESPERTAR / DESPERTARES / DESPIERTAS
# / DESPERTARSE — waking through the night IS the pain this audience feels, the
# same class as insomnio, just a form the fixed set could not spell out.
_PAIN_STEMS = ("despert",)
_OUTCOME_PHRASES = (
    "duerme mejor", "dormir mejor", "descansar mejor", "mas energia",
    "fuerza", "energia", "descanso",
)
# Reduction-framed benefit (bug-547): relief from a discomfort is the core
# wellness promise, and "menos tensión" is the exact mirror of the already
# accepted "alivia la tensión". A reduction word paired with a discomfort noun
# is an outcome; a reduction word paired with a plain ingredient ("menos sal")
# is not — sal is a topic object, not a symptom, so it stays out of this set.
_REDUCTION_TERMS = {"menos", "sin", "reduce", "reducir", "reduces", "baja", "calma", "quita"}
_DISCOMFORT_TERMS = {
    "tension", "tensiones", "dolor", "dolores", "rigidez", "fatiga",
    "cansancio", "estres", "ansiedad", "hinchazon", "pesadez", "molestia",
    "molestias", "insomnio", "calambre", "calambres",
}
# Care/improvement verb stems (prefix-matched on whole words): CUIDA/CUIDAR,
# ALIVIA, PROTEGE, FORTALECE, RECUPERA, MEJORA, REFUERZA...
_OUTCOME_STEMS = (
    "cuid", "alivi", "proteg", "protej", "fortalec", "recuper", "refuerz",
    "mejor",
)
_ACTION_PHRASES = (
    "cuando", "como", "que elegir", "que pasa", "evita", "elige", "tomarlo",
    "toma", "prueba", "prepara", "reduce", "empieza",
)
_TIMING_TERMS = {"hoy", "manana", "noche", "tarde", "antes", "despues", "tras"}
_AGENCY_PHRASES = (
    "como", "cuando", "que elegir", "que pasa", "para cuidar", "puede afectar",
    "prueba", "paso a paso", "sin prisa",
)
# Agency also speaks through dignified care verbs (CUIDA, ALIVIA, PROTEGE...).
_AGENCY_STEMS = ("cuid", "alivi", "proteg", "protej", "fortalec")
# Tokens that can never serve as TOPIC evidence via title overlap: the action/
# decision vocabulary (already counted as the action signal — re-counting the
# same verb as a topic double-counts one idea) and bare quantifiers/comparatives.
_NON_TOPIC_OVERLAP = {
    "elegir", "elige", "eliges", "evita", "evitar", "toma", "tomar", "tomarlo",
    "prueba", "probar", "prepara", "preparar", "reduce", "reducir", "empieza",
    "empezar", "cuando", "como", "menos", "mas", "mucho", "mucha", "poco",
    "poca", "mayor", "menor", "mejor", "peor", "grande", "pequeno",
}
# Words that never count as topic anchoring when they overlap with the title
# (generic marketing/filler/timing vocabulary).
_OVERLAP_STOPWORDS = {
    "despues", "antes", "mejor", "mejores", "todos", "todas", "tienes",
    "puede", "pueden", "forma", "practica", "practico", "sencillos",
    "sencilla", "sencillo", "manana", "noche", "tarde", "habito", "habitos",
    "senal", "senales", "clave", "claves", "gestos", "cosas", "video",
    "canal", "cada", "hacer", "saber", "influye", "afecta", "comes", "pasa",
}
# Deictic / context-free hooks that require the title to make sense (C1).
_DEICTIC_TERMS = {
    "esto", "eso", "clave", "secreto", "gestos", "huecos", "aqui", "asi",
    "hora",
}
# Fear / miracle / fake-authority / unsupported-certainty markers (C6).
_TRUST_VIOLATIONS = (
    "arruina", "destruye", "mata", "matando", "muerte", "mortal", "cura",
    "milagro", "garantizado", "garantiza", "medicos ocultan", "doctores",
    "detiene tu corazon", "veneno", "peligro",
)


def _normalize_es(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _title_content_tokens(title: str) -> set[str]:
    """Concrete content tokens of the paired title (for topic alignment)."""
    words = set(re.findall(r"[a-z0-9]+", _normalize_es(title)))
    return {
        w for w in words
        if len(w) >= 5 and w not in _OVERLAP_STOPWORDS and not w.isdigit()
    }


def _semantic_signals(text_norm: str, words: set[str], title_words: set[str]) -> dict:
    """Which of the contract's signal classes the copy carries.

    Topic anchoring generalizes beyond the fixed noun list: a meaningful
    content-token overlap with the PAIRED TITLE also counts (the spec allows
    using the title for topic alignment) — so MEMORIA / RODILLAS / COLÁGENO /
    AVENA anchor without hardcoding every channel noun."""
    # Title overlap counts as topic evidence only for OBJECT-like tokens: not
    # pain terms, not action/quantifier vocabulary, not care-verb conjugations —
    # a fragment like 'QUÉ ELEGIR, MENOS SAL' must not have its own action verb
    # ('elegir') re-counted as the concrete object it is missing.
    title_overlap = {
        w for w in (words & title_words)
        if w not in _PAIN_TERMS
        and w not in _NON_TOPIC_OVERLAP
        and not any(w.startswith(stem) for stem in _OUTCOME_STEMS)
        and not any(w.startswith(stem) for stem in _AGENCY_STEMS)
        and not any(w.startswith(stem) for stem in _PAIN_STEMS)
    }
    has_topic = bool(words & _TOPIC_OBJECTS) or bool(title_overlap)
    has_pain = bool(words & _PAIN_TERMS) or any(
        w.startswith(stem) for w in words for stem in _PAIN_STEMS
    )
    has_outcome = (
        any(p in text_norm for p in _OUTCOME_PHRASES)
        or any(w.startswith(stem) for w in words for stem in _OUTCOME_STEMS)
        # reduction of a discomfort ("menos tensión", "sin rigidez") is a benefit
        or (bool(words & _REDUCTION_TERMS) and bool(words & _DISCOMFORT_TERMS))
    )
    has_action = any(p in text_norm for p in _ACTION_PHRASES)
    # Timing words only count as honest specificity when anchored to a concrete
    # topic or outcome — a dangling 'DESPUÉS' is the deictic fragment the
    # contract bans, not specificity.
    has_specificity = any(ch.isdigit() for ch in text_norm) or (
        bool(words & _TIMING_TERMS) and (has_topic or has_outcome)
    )
    return {
        "topic": has_topic,
        "pain": has_pain,
        "outcome": has_outcome,
        "action": has_action,
        "specificity": has_specificity,
    }


def _thumbnail_semantics(text: str, title: str = "") -> dict:
    """Auditable audience-quality components for a thumbnail candidate.

    - standalone_value_score: concrete topic (8) + 6 per other signal class;
    - audience_fit_score: 8 when the copy speaks with practical, dignified
      agency (CÓMO / CUÁNDO / QUÉ ELEGIR / PARA CUIDAR / PUEDE AFECTAR…);
    - vagueness_penalty: 12 when the copy carries fewer than two signal
      classes (a context-free fragment that needs the title to mean anything);
    - trust_penalty: 15 for fear/miracle/authority/unsupported certainty.
    """
    text_norm = _normalize_es(text)
    words = set(re.findall(r"[a-z0-9]+", text_norm))
    signals = _semantic_signals(text_norm, words, _title_content_tokens(title))

    standalone = (8 if signals["topic"] else 0) + 6 * sum(
        1 for k in ("pain", "outcome", "action", "specificity") if signals[k]
    )
    audience_fit = 8 if (
        any(p in text_norm for p in _AGENCY_PHRASES)
        or any(w.startswith(stem) for w in words for stem in _AGENCY_STEMS)
    ) else 0
    signal_count = sum(signals.values())
    # Context-free when it carries fewer than two signal classes, OR when it
    # leans on a deictic hook (ESTO / DESPUÉS / LA HORA / CLAVE...) without a
    # concrete topic object to anchor it (contract C1).
    deictic_without_topic = bool(words & _DEICTIC_TERMS) and not signals["topic"]
    vagueness = 12 if (signal_count < 2 or deictic_without_topic) else 0
    trust = 15 if any(p in text_norm for p in _TRUST_VIOLATIONS) else 0
    return {
        "standalone_value_score": standalone,
        "audience_fit_score": audience_fit,
        "vagueness_penalty": vagueness,
        "trust_penalty": trust,
    }


def _thumbnail_score(variant: dict) -> dict:
    """Return scoring breakdown for the thumbnail_text field. Includes 'total' and 'all_caps' keys."""
    text = str(variant.get("thumbnail_text") or "")
    words = text.split()
    word_count = len(words)
    length = len(text)

    # Word count scoring — the copy contract targets 4-7 display words; three
    # words are allowed when already concrete (legacy 3-word score preserved,
    # still above 7 so brevity ordering tests keep meaning).
    if 3 <= word_count <= 6:
        word_count_score = 20
    elif word_count == 7:
        word_count_score = 12
    elif word_count == 2:
        word_count_score = 10
    else:
        word_count_score = 0

    # All alphabetic characters are uppercase
    alpha_chars = [ch for ch in text if ch.isalpha()]
    all_caps = bool(alpha_chars) and all(ch.isupper() for ch in alpha_chars)
    caps_score = 10 if all_caps else 0

    # Contains an emotion word (checked against uppercase split words)
    upper_words = {w.upper() for w in words}
    emotion_score = 12 if upper_words & _EMOTION_WORDS else 0

    # Length 10-30 chars
    length_score = 8 if 10 <= length <= 30 else 0

    semantics = _thumbnail_semantics(text, str(variant.get("title") or ""))
    classic_total = word_count_score + caps_score + emotion_score + length_score
    # Semantic quality outweighs surface form (spec: a vague all-caps 3-5-word
    # phrase must not beat a clear 4-7-word phrase just for being shorter), so
    # the classic surface components are down-weighted and the audited
    # semantic components are added on top. Range stays 0-50.
    total = int(round(
        min(
            50.0,
            max(
                0.0,
                classic_total * 0.6
                + semantics["standalone_value_score"]
                + semantics["audience_fit_score"]
                - semantics["vagueness_penalty"]
                - semantics["trust_penalty"],
            ),
        )
    ))

    return {
        "word_count_score": word_count_score,
        "caps_score": caps_score,
        "emotion_score": emotion_score,
        "length_score": length_score,
        "all_caps": all_caps,
        **semantics,
        "total": total,
    }


def score_variant(variant: dict) -> dict:
    """
    Score a single variant dict with 'title' and 'thumbnail_text' keys.

    Returns:
        {
            "score": int,  # 0-100
            "breakdown": {
                "title_score": int,
                "thumbnail_score": int,
                "title_detail": dict,
                "thumbnail_detail": dict,
            }
        }
    """
    title_detail = _title_score(variant)
    thumbnail_detail = _thumbnail_score(variant)

    title_total = title_detail["total"]
    thumbnail_total = thumbnail_detail["total"]
    score = min(100, title_total + thumbnail_total)

    return {
        "score": score,
        "breakdown": {
            "title_score": title_total,
            "thumbnail_score": thumbnail_total,
            "title_detail": title_detail,
            "thumbnail_detail": thumbnail_detail,
        },
    }


def score_variants(variants: list[dict]) -> list[dict]:
    """
    Score a list of variant dicts, add 'score' and 'score_breakdown' fields,
    and return sorted descending by score.
    """
    result = []
    for v in variants:
        scored = score_variant(v)
        entry = dict(v)
        entry["score"] = scored["score"]
        entry["score_breakdown"] = scored["breakdown"]
        result.append(entry)

    result.sort(key=lambda x: x["score"], reverse=True)
    return result
