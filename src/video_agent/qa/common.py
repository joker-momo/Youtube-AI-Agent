from __future__ import annotations

import re


def pass_result(scores: dict[str, int] | None = None) -> dict:
    return {"verdict": "PASS", "scores": scores or {}, "issues": [], "retry_action": None}


def revise_result(issue_type: str, message: str, retry_action: str) -> dict:
    return {
        "verdict": "REVISE",
        "scores": {},
        "issues": [{"type": issue_type, "severity": "medium", "message": message}],
        "retry_action": retry_action,
    }


_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


def average_sentence_words(text: str) -> float:
    """Average word count per sentence.

    Splits on one-or-more terminal punctuation chars so ``...`` counts as
    a single boundary instead of three empty sentences.
    """
    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    if not sentences:
        return 0.0
    return sum(len(sentence.split()) for sentence in sentences) / len(sentences)
