"""Pexels query expansion (spec §11). English output only.

Scene briefs may be in channel-language; queries sent to Pexels are normalized
to short English phrases. Bucket queries from visual-dna.yaml are appended.
"""

from __future__ import annotations

import re
from typing import Any

from .helpers import normalize_text, stable_dedupe


_SPANISH_TO_ENGLISH = {
    "mañana": "morning",
    "manana": "morning",
    "tarde": "evening",
    "noche": "night",
    "casa": "home",
    "cocina": "kitchen",
    "dormitorio": "bedroom",
    "calle": "street",
    "parque": "park",
    "mercado": "market",
    "cansado": "tired",
    "cansada": "tired",
    "rigido": "stiff",
    "rigida": "stiff",
    "rigidez": "stiffness",
    "dormir": "sleep",
    "sueño": "sleep",
    "sueno": "sleep",
    "desayuno": "breakfast",
    "cena": "dinner",
    "manos": "hands",
    "espalda": "back",
    "cuello": "neck",
    "piernas": "legs",
    "caminar": "walking",
    "camina": "walking",
    "paseo": "walk",
    "mujer": "woman",
    "hombre": "man",
    "anciano": "older adult",
    "abuela": "grandmother",
    "abuelo": "grandfather",
    "estiramiento": "stretching",
    "estirar": "stretch",
    "movimiento": "movement",
    "comida": "food",
    "ejercicio": "exercise",
    "rutina": "routine",
    "agua": "water",
    "café": "coffee",
    "cafe": "coffee",
    "té": "tea",
    "te": "tea",
    "luz": "light",
    "ventana": "window",
    "barrio": "neighborhood",
    "ciudad": "city",
    "españa": "spain",
    "espana": "spain",
    "madrid": "madrid",
    "barcelona": "barcelona",
}


def normalize_to_english_pexels_query(text: str, visual_dna: dict[str, Any]) -> str:
    """Strip accents, lowercase, replace common Spanish tokens with English."""
    normalized = normalize_text(text)
    if not normalized:
        return ""
    tokens = normalized.split()
    out: list[str] = []
    for token in tokens:
        mapped = _SPANISH_TO_ENGLISH.get(token)
        if mapped:
            out.append(mapped)
        else:
            out.append(token)
    # Drop ASCII punctuation-only tokens.
    out = [tok for tok in out if any(ch.isalnum() for ch in tok)]
    return " ".join(out)


def truncate_query_terms(query: str, max_terms: int) -> str:
    tokens = query.split()
    return " ".join(tokens[:max_terms])


def is_strong_negative_query(query: str, visual_dna: dict[str, Any]) -> bool:
    text = normalize_text(query)
    for phrase in visual_dna.get("negative_patterns", {}).get("strong_phrases_en", []) or []:
        if normalize_text(phrase) in text:
            return True
    return False


def expand_pexels_queries(scene: dict[str, Any], visual_dna: dict[str, Any]) -> list[str]:
    """Generate up to query_policy.max_queries_per_scene English queries.

    Order: scene-derived first (so the API budget hits the most specific
    candidates), then bucket defaults, then de-duped.
    """
    bucket_id = scene.get("visual_bucket") or "persona_moment"
    bucket_cfg = visual_dna.get("visual_buckets", {}).get(bucket_id, {}) or {}

    source_texts = [
        scene.get("visual_brief"),
        scene.get("visual_prompt"),
        scene.get("fallback_visual_query"),
        scene.get("on_screen_text"),
    ]
    normalized_scene_queries = [
        normalize_to_english_pexels_query(text, visual_dna)
        for text in source_texts
        if text
    ]

    bucket_queries = list(bucket_cfg.get("pexels_queries_en", []) or [])
    max_terms = int(visual_dna.get("query_policy", {}).get("max_query_terms", 14))
    max_queries = int(visual_dna.get("query_policy", {}).get("max_queries_per_scene", 4))

    combined: list[str] = []
    combined.extend(normalized_scene_queries)
    combined.extend(bucket_queries)
    combined = [truncate_query_terms(q, max_terms=max_terms) for q in combined]
    combined = [q for q in combined if q and not is_strong_negative_query(q, visual_dna)]
    combined = stable_dedupe(combined)

    return combined[:max_queries]
