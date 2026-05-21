"""
SEO/CTR scorer for YouTube title + thumbnail text combinations.
Scores 0-100 total (title 0-50, thumbnail 0-50).
"""

from __future__ import annotations

_POWER_WORDS = {
    "secreto", "mejor", "peor", "error", "nunca", "siempre",
    "cómo", "qué", "gratis", "rápido", "fácil", "verdad", "clave", "por qué",
}

_EMOTION_WORDS = {
    "DUERME", "INSOMNIO", "DOLOR", "SECRETO", "NUNCA", "AHORA", "MEJOR",
    "PEOR", "HOY", "YA", "FATAL", "VERDAD", "ALERTA", "CUIDADO",
}


def _title_score(variant: dict) -> dict:
    """Return scoring breakdown for the title field. Includes a 'total' key (0-50)."""
    title: str = variant.get("title", "")
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


def _thumbnail_score(variant: dict) -> dict:
    """Return scoring breakdown for the thumbnail_text field. Includes 'total' and 'all_caps' keys."""
    text: str = variant.get("thumbnail_text", "")
    words = text.split()
    word_count = len(words)
    length = len(text)

    # Word count scoring
    if 3 <= word_count <= 5:
        word_count_score = 20
    elif word_count in (2, 6):
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

    total = word_count_score + caps_score + emotion_score + length_score

    return {
        "word_count_score": word_count_score,
        "caps_score": caps_score,
        "emotion_score": emotion_score,
        "length_score": length_score,
        "all_caps": all_caps,
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
