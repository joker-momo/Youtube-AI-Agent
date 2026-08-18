from __future__ import annotations

import unicodedata
from dataclasses import dataclass


def _grapheme_count(value: str) -> int:
    count = 0
    joined = False
    for char in unicodedata.normalize("NFC", value):
        category = unicodedata.category(char)
        codepoint = ord(char)
        if category.startswith("M") or 0xFE00 <= codepoint <= 0xFE0F:
            continue
        if char == "\u200d":
            joined = True
            continue
        if not joined:
            count += 1
        joined = False
    return count


@dataclass(frozen=True, slots=True)
class TitleScore:
    title: str
    locale: str
    graphemes: int
    score: int
    cue_hits: tuple[str, ...]
    within_limit: bool


def score_title(title: str, locale_pack: dict) -> TitleScore:
    preserved = unicodedata.normalize("NFC", title).strip()
    if not preserved:
        raise ValueError("localized SEO title cannot be empty")
    seo = locale_pack["seo"]
    maximum = int(seo["titleMaxChars"])
    length = _grapheme_count(preserved)
    folded = preserved.casefold()
    cue_hits = tuple(
        cue
        for cue in seo["keywordCues"]
        if unicodedata.normalize("NFC", str(cue)).casefold() in folded
    )
    within_limit = length <= maximum
    length_score = 60 if within_limit else max(0, 60 - (length - maximum) * 4)
    cue_score = min(30, len(cue_hits) * 15)
    readability_score = 10 if any(char.isalpha() for char in preserved) else 0
    return TitleScore(
        title=preserved,
        locale=str(locale_pack["locale"]),
        graphemes=length,
        score=length_score + cue_score + readability_score,
        cue_hits=cue_hits,
        within_limit=within_limit,
    )
