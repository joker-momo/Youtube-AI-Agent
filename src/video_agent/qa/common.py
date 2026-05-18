from __future__ import annotations


def pass_result(scores: dict[str, int] | None = None) -> dict:
    return {"verdict": "PASS", "scores": scores or {}, "issues": [], "retry_action": None}


def revise_result(issue_type: str, message: str, retry_action: str) -> dict:
    return {
        "verdict": "REVISE",
        "scores": {},
        "issues": [{"type": issue_type, "severity": "medium", "message": message}],
        "retry_action": retry_action,
    }


def average_sentence_words(text: str) -> float:
    sentences = [part.strip() for part in text.replace("?", ".").replace("!", ".").split(".") if part.strip()]
    if not sentences:
        return 0.0
    return sum(len(sentence.split()) for sentence in sentences) / len(sentences)
