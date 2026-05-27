"""Helpers for spoken-narration normalization and written-style warnings."""
from __future__ import annotations

import re
from collections import Counter

SENTENCE_WORD_LIMIT = 28
PARAGRAPH_WORD_LIMIT = 65
COMMA_LIMIT = 3
REPEAT_ENDING_THRESHOLD = 3

_FORMAL_CONNECTORS = (
    "no obstante",
    "por consiguiente",
    "en consecuencia",
    "asimismo",
    "por ende",
    "en virtud de",
    "a tenor de",
    "habida cuenta",
    "ulteriormente",
)


def normalize_spoken_text(text: str) -> str:
    """Normalize spoken narration while preserving paragraph breaks.

    Rules:
    - CRLF/CR collapsed to LF.
    - 3+ consecutive newlines collapsed to exactly 2.
    - Spaces/tabs inside a line collapsed to a single space.
    - Leading/trailing whitespace on each line stripped.
    - Single and double newlines preserved.
    """
    if not isinstance(text, str) or not text:
        return text or ""
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    lines = s.split("\n")
    out = []
    for line in lines:
        cleaned = re.sub(r"[ \t]+", " ", line).strip()
        out.append(cleaned)
    return "\n".join(out)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[\.\!\?])\s+", text.strip())
    return [p for p in parts if p]


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text, flags=re.UNICODE)


def detect_written_style_narration(
    text: str, scene_id: str | None = None
) -> list[str]:
    """Return non-fatal warnings if the narration reads more like an essay
    than spoken Spanish. Empty list means it looks OK."""
    warnings: list[str] = []
    if not isinstance(text, str) or not text.strip():
        return warnings
    label = f"Scene {scene_id}" if scene_id else "Narration"
    norm = normalize_spoken_text(text)
    paragraphs = [p for p in norm.split("\n\n") if p.strip()]

    for para in paragraphs:
        flat = para.replace("\n", " ")
        if len(_words(flat)) > PARAGRAPH_WORD_LIMIT:
            warnings.append(
                f"{label}: narration paragraph is too dense; add a paragraph break before the key idea."
            )
            break

    for sent in _split_sentences(norm.replace("\n", " ")):
        wc = len(_words(sent))
        if wc > SENTENCE_WORD_LIMIT:
            warnings.append(
                f"{label}: narration has a sentence longer than {SENTENCE_WORD_LIMIT} words; consider splitting for TTS."
            )
            break

    for sent in _split_sentences(norm.replace("\n", " ")):
        if sent.count(",") >= COMMA_LIMIT:
            warnings.append(
                f"{label}: narration sounds essay-like; prefer direct spoken phrasing (too many commas in one sentence)."
            )
            break

    lower = norm.lower()
    for conn in _FORMAL_CONNECTORS:
        if conn in lower:
            warnings.append(
                f"{label}: narration uses formal connector '{conn}'; prefer a direct spoken phrase."
            )
            break

    endings = []
    for sent in _split_sentences(norm.replace("\n", " ")):
        tail_words = _words(sent)[-3:]
        if tail_words:
            endings.append(" ".join(w.lower() for w in tail_words))
    counts = Counter(endings)
    for ending, count in counts.items():
        if count >= REPEAT_ENDING_THRESHOLD:
            warnings.append(
                f"{label}: repeated ending '{ending}' across {count} sentences; vary the closing rhythm."
            )
            break

    return warnings
