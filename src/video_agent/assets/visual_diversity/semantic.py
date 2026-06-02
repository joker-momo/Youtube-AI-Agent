"""Deterministic token + token-synonym + phrase-synonym matching (spec §12)."""

from __future__ import annotations

import re
from typing import Any

from .helpers import normalize_text


def normalize_terms(text: str, visual_dna: dict[str, Any]) -> set[str]:
    """Tokenize normalized text preserving age numbers, hyphen, and plus.

    Drops short tokens (<=2 chars), stopwords, and punctuation-only artifacts.
    """
    text = normalize_text(text)
    # Keep hyphen and plus so "middle-aged" and "45+" survive. Phrase synonyms
    # still cover multi-token phrases.
    raw_terms = re.findall(r"[a-z0-9+-]+", text)

    policy = visual_dna.get("token_policy", {}) or {}
    stopwords = set(policy.get("stopwords", {}).get("en", []) or [])
    stopwords.update(policy.get("stopwords", {}).get("es", []) or [])

    preserve_numeric = set(policy.get("preserve_numeric_terms", []) or [])
    preserve_short = set(policy.get("preserve_short_terms", []) or [])

    terms: set[str] = set()
    for term in raw_terms:
        # Drop punctuation-only artifacts such as "---" or "+++".
        if not any(ch.isalnum() for ch in term):
            continue
        if term in preserve_numeric or term in preserve_short:
            terms.add(term)
            continue
        if len(term) <= 2:
            continue
        if term in stopwords:
            continue
        terms.add(term)
    return terms


def phrase_hits(text: str, phrases: list[str]) -> set[str]:
    normalized = normalize_text(text)
    hits: set[str] = set()
    for phrase in phrases:
        p = normalize_text(phrase)
        if p and p in normalized:
            hits.add(p)
    return hits


def semantic_match_score(
    query: str,
    candidate_text: str,
    visual_dna: dict[str, Any],
) -> tuple[float, int, int, int, list[str]]:
    """Return (score, direct_hits, synonym_hits, phrase_hits_capped, matched_terms)."""
    q_terms = normalize_terms(query, visual_dna)
    c_terms = normalize_terms(candidate_text, visual_dna)

    token_synonyms = visual_dna.get("synonyms", {}).get("tokens", {}) or {}
    phrase_synonyms = visual_dna.get("synonyms", {}).get("phrases", {}) or {}

    if not q_terms or not c_terms:
        return 0.0, 0, 0, 0, []

    direct_hits = q_terms & c_terms

    synonym_hits: set[str] = set()
    for q in q_terms:
        syns = {normalize_text(s) for s in token_synonyms.get(q, []) or []}
        if syns & c_terms:
            synonym_hits.add(q)

    # v5.6: phrase synonyms are candidate evidence, not query self-confirmation.
    # Count a label only when BOTH the query and the candidate text contain a
    # phrase belonging to the same label.
    phrase_hit_count = 0
    phrase_matched_labels: list[str] = []
    for label, phrases in phrase_synonyms.items():
        query_hits = phrase_hits(query, phrases)
        candidate_hits = phrase_hits(candidate_text, phrases)
        if query_hits and candidate_hits:
            phrase_hit_count += 1
            phrase_matched_labels.append(label)

    phrase_hit_count_capped = min(phrase_hit_count, 3)

    weighted_hits = (
        len(direct_hits)
        + 0.75 * len(synonym_hits - direct_hits)
        + 0.50 * phrase_hit_count_capped
    )
    denom = max(3, min(len(q_terms), 12))
    score = min(1.0, weighted_hits / denom)
    matched_terms = sorted(direct_hits | synonym_hits | set(phrase_matched_labels))

    return score, len(direct_hits), len(synonym_hits), phrase_hit_count_capped, matched_terms


def passes_semantic_gate(score: float, direct_hits: int, synonym_hits: int, phrase_hits_count: int) -> bool:
    return score >= 0.34 and (direct_hits + synonym_hits + phrase_hits_count) >= 2
