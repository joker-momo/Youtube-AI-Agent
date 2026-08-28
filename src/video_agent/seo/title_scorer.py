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


def _classify_title_device(title: str) -> str:
    """Stable CTR-device classification for set-level diversity checks.

    Not a quality signal by itself — a title can use any device well or
    badly. Used only to detect when a set of variants leans on the same
    device instead of the three distinct devices the SEO prompt requires.
    """
    if "?" in title or "¿" in title:
        return "curiosity_question"
    norm = _normalize_es(title)
    if "no es " in norm or "(no es" in norm or "no es lo que" in norm:
        return "contrast"
    return "keyword_first"


def _title_semantic_evidence(title: str) -> dict:
    """Topic/stake/payoff/specificity/naturalness evidence for a title.

    Reuses the same audience-fit signal classes as the thumbnail scorer
    (`_semantic_signals`) so a title and its thumbnail speak the same
    vocabulary of topic/pain/outcome/action/specificity, then adds
    honest-claim reason codes (unsupported science/authority, or a bare
    power word carrying no other substance).
    """
    text_norm = _normalize_es(title)
    words = set(re.findall(r"[a-z0-9]+", text_norm))
    signals = _semantic_signals(text_norm, words, set())
    # Titles are full sentences where a bare "mejor" is often just a generic
    # comparative adjective ("el secreto mejor guardado"), not an outcome
    # claim. Trust the explicit outcome phrases/stems for a title's payoff,
    # excluding the lone "mejor" stem match that inflates generic titles —
    # a genuine "duerme/dormir mejor" claim is still caught by the phrase.
    has_payoff = (
        any(p in text_norm for p in _OUTCOME_PHRASES)
        or any(w.startswith(stem) for w in words for stem in _OUTCOME_STEMS if stem != "mejor")
        or (bool(words & _REDUCTION_TERMS) and bool(words & _DISCOMFORT_TERMS))
    )

    reason_codes: list[str] = []
    score = 0.0
    if signals["topic"]:
        score += 4
    if signals["pain"]:
        score += 14
    else:
        reason_codes.append("missing_stake")
    if has_payoff:
        score += 16
    else:
        reason_codes.append("missing_payoff")
    if signals["specificity"]:
        score += 3
    if signals["action"]:
        score += 3

    has_power_word = any(pw in text_norm for pw in _POWER_WORDS)
    if score == 0 and has_power_word:
        reason_codes.append("power_word_without_substance")

    if any(p in text_norm for p in _TRUST_VIOLATIONS):
        score = max(0.0, score - 20)
        reason_codes.append("unsupported_claim")

    return {
        "semantic_score": min(40.0, score),
        "signals": signals,
        "reason_codes": reason_codes,
    }


def _package_alignment_evidence(title: str, thumbnail_text: str) -> dict:
    """Bounded 0 to -20 adjustment for title/thumbnail package coherence.

    'pain_mismatch': the title names a concrete pain/stake but the
    thumbnail neither repeats it nor offers a recognized outcome that
    addresses it — the copy pivoted to a different, unrelated promise.
    'unsupported_outcome': the thumbnail makes an honesty-contract claim
    (cure/guarantee/diagnosis/unsupported authority) the video cannot back.
    """
    title_norm = _normalize_es(title)
    thumb_norm = _normalize_es(thumbnail_text)
    title_words = set(re.findall(r"[a-z0-9]+", title_norm))
    thumb_words = set(re.findall(r"[a-z0-9]+", thumb_norm))

    def _pain_tokens(words: set[str]) -> set[str]:
        """Canonicalize pain/stake words to a shared tag so morphological
        variants of the same concept ('pierdes' the verb vs. 'pérdida' the
        noun) compare equal instead of failing on exact-string mismatch."""
        tokens: set[str] = set()
        for w in words:
            stem_hit = next((stem for stem in _PAIN_STEMS if w.startswith(stem)), None)
            if stem_hit is not None:
                tokens.add(stem_hit)
            elif w in _PAIN_STEM_ALIASES:
                tokens.add(_PAIN_STEM_ALIASES[w])
            elif w in _PAIN_TERMS or w in _DISCOMFORT_TERMS:
                tokens.add(w)
        return tokens

    title_pain = _pain_tokens(title_words)
    thumb_pain = _pain_tokens(thumb_words)
    thumb_signals = _semantic_signals(thumb_norm, thumb_words, title_words)

    reason_codes: list[str] = []
    penalty = 0.0
    shares_pain = bool(title_pain & thumb_pain)
    if title_pain and not shares_pain and not thumb_signals["outcome"]:
        reason_codes.append("pain_mismatch")
        penalty += 12

    if any(p in thumb_norm for p in _TRUST_VIOLATIONS):
        reason_codes.append("unsupported_outcome")
        penalty += 20

    return {
        "shared_pain_tokens": sorted(title_pain & thumb_words),
        "reason_codes": reason_codes,
        "penalty": min(20.0, penalty),
    }


def _title_score(variant: dict) -> dict:
    """Return scoring breakdown for the title field. Includes a 'total' key (0-50).

    Surface form (word count + length) contributes at most 10 of the 50
    points; the remaining 40 come from semantic evidence (topic, stake,
    payoff, specificity, naturalness) so a title cannot out-score a
    genuinely stronger one just by hitting a punctuation/length template.
    """
    title = str(variant.get("title") or "")
    words = title.split()
    word_count = len(words)
    length = len(title)

    # Legacy surface fields — kept verbatim for existing callers/tests that
    # read these exact keys; they no longer drive `total` unconditionally.
    if 6 <= word_count <= 10:
        word_count_score = 15
    elif 4 <= word_count <= 12:
        word_count_score = 8
    else:
        word_count_score = 0
    digit_score = 10 if any(ch.isdigit() for ch in title) else 0
    question_score = 8 if ("?" in title or "¿" in title) else 0
    title_lower = title.lower()
    power_score = 0
    for pw in _POWER_WORDS:
        if pw in title_lower:
            power_score = 10
            break
    length_score = 7 if 40 <= length <= 70 else 0

    # Bounded surface contribution to `total` (at most 10): word count and
    # length only — digit/question presence is classification evidence
    # inside the semantic score (specificity/device), not a direct point.
    surface_score = min(
        10,
        (6 if 6 <= word_count <= 10 else 3 if 4 <= word_count <= 12 else 0)
        + (4 if 40 <= length <= 70 else 0),
    )

    device = _classify_title_device(title)
    semantic = _title_semantic_evidence(title)
    total = int(round(min(50.0, max(0.0, surface_score + semantic["semantic_score"]))))

    return {
        "word_count_score": word_count_score,
        "digit_score": digit_score,
        "question_score": question_score,
        "power_score": power_score,
        "length_score": length_score,
        "surface_score": surface_score,
        "device": device,
        "semantic_score": semantic["semantic_score"],
        "reason_codes": semantic["reason_codes"],
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
    # Energy-slump family (packaging-CTR): a familiar post-meal/afternoon
    # energy crash is as concrete a pain as insomnia or muscle stiffness.
    "bajon",
    # Loss/consequence noun form (packaging-CTR): Spanish's e->ie stem
    # change means "pérdida" (noun) doesn't share a prefix with "pierdes"
    # (verb) — see _PAIN_STEM_ALIASES for cross-form alignment matching.
    "perdida",
}
# Symptom stems (bug-547): cover inflected forms without whitelisting one exact
# thumbnail sentence. DESPERTAR / DESPERTARES / DESPIERTAS describe disrupted
# sleep; HAMBRE / HAMBRIENTO / HAMBRIENTA describe the appetite pain that a
# concrete food choice promises to address. PIERD- (pierdes/pierde/pierden)
# covers the loss/consequence framing a title uses as its personal stake.
_PAIN_STEMS = ("despert", "hambr", "pierd")
# Cross-form aliases for package-alignment matching only (packaging-CTR): a
# word that doesn't share the verb stem above but names the same concept —
# e.g. "pérdida" (noun) vs "pierdes" (verb, e->ie stem change) — canonicalizes
# to that stem's tag so title/thumbnail alignment isn't fooled by inflection.
_PAIN_STEM_ALIASES: dict[str, str] = {"perdida": "pierd"}
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
    "molestias", "insomnio", "calambre", "calambres", "hambre", "bajon",
}
# Wellness-outcome families (prefix-matched on normalized whole words):
# - care/improvement: CUIDA/CUIDAR, ALIVIA, PROTEGE, FORTALECE, RECUPERA...
# - appetite satisfaction: SACIA/SACIAR/SACIANTE/SACIEDAD and
#   SATISFACE/SATISFECHO/SATISFACCION.
#
# Store morphological stems rather than literal generated phrases so one
# production regression covers the useful Spanish inflections without teaching
# the scorer a specific thumbnail sentence.
_OUTCOME_STEMS = (
    "cuid", "alivi", "proteg", "protej", "fortalec", "recuper", "refuerz",
    "mejor", "saci", "satisf",
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
    # Unsupported scientific/medical-authority framing (packaging-CTR): a
    # generic "science says" claim or a named diagnosis the video cannot
    # actually support is the same dishonest-certainty family as "cura".
    "verdad cientifica", "diagnostico",
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
    alignment = _package_alignment_evidence(
        str(variant.get("title") or ""), str(variant.get("thumbnail_text") or "")
    )

    title_total = title_detail["total"]
    thumbnail_total = thumbnail_detail["total"]
    score = min(100, max(0, title_total + thumbnail_total - int(round(alignment["penalty"]))))

    return {
        "score": score,
        "breakdown": {
            "title_score": title_total,
            "thumbnail_score": thumbnail_total,
            "title_detail": title_detail,
            "thumbnail_detail": thumbnail_detail,
            "alignment": alignment,
        },
    }


def score_variants(variants: list[dict]) -> list[dict]:
    """
    Score a list of variant dicts, add 'score' and 'score_breakdown' fields,
    and return sorted descending by score.

    `score_breakdown["set_reason_codes"]` flags set-level issues that only
    exist relative to sibling variants — currently, more than one variant
    using the same title device (the SEO prompt requires three distinct
    devices: curiosity/question, contrast, keyword-first).
    """
    devices = [_classify_title_device(str(v.get("title") or "")) for v in variants]
    device_counts: dict[str, int] = {}
    for device in devices:
        device_counts[device] = device_counts.get(device, 0) + 1

    result = []
    for v, device in zip(variants, devices, strict=True):
        scored = score_variant(v)
        entry = dict(v)
        entry["score"] = scored["score"]
        breakdown = dict(scored["breakdown"])
        set_reason_codes = []
        if device_counts[device] > 1:
            set_reason_codes.append("duplicate_title_device")
        breakdown["set_reason_codes"] = set_reason_codes
        entry["score_breakdown"] = breakdown
        result.append(entry)

    result.sort(key=lambda x: x["score"], reverse=True)
    return result
