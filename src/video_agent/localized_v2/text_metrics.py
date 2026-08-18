from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import regex

LATIN_LOCALES = frozenset({"en-US", "fr-FR", "pt-BR"})
CJK_LOCALES = frozenset({"ko-KR", "ja-JP"})


@dataclass(frozen=True, slots=True)
class TextMeasure:
    locale: str
    strategy: str
    units: int
    estimated_words: float
    estimated_duration_sec: float


@dataclass(frozen=True, slots=True)
class TextBudget:
    locale: str
    strategy: str
    target_units: int
    target_duration_sec: int


def _unicode_words(text: str) -> list[str]:
    return regex.findall(
        r"[\p{L}\p{N}]+(?:[’'-][\p{L}\p{N}]+)*",
        text,
    )


def _script_graphemes(text: str) -> list[str]:
    return [
        grapheme
        for grapheme in regex.findall(r"\X", text)
        if regex.search(r"[\p{L}\p{N}]", grapheme)
    ]


def measure_text(text: str, locale_pack: dict[str, Any]) -> TextMeasure:
    locale = str(locale_pack["locale"])
    metrics = locale_pack["textMetrics"]
    narration = locale_pack["narration"]
    if locale in LATIN_LOCALES:
        strategy = "unicode-words"
        units = len(_unicode_words(text))
        estimated_words = float(units)
    elif locale in CJK_LOCALES:
        strategy = "script-graphemes"
        units = len(_script_graphemes(text))
        estimated_words = units / float(metrics["charsPerWord"])
    else:
        raise ValueError(f"unsupported localized V2 text metric locale: {locale}")
    estimated_duration = (
        estimated_words
        / float(narration["wordsPerMinute"])
        * 60.0
        * float(metrics["expansionRatio"])
    )
    return TextMeasure(
        locale=locale,
        strategy=strategy,
        units=units,
        estimated_words=estimated_words,
        estimated_duration_sec=estimated_duration,
    )


def budget_for_duration(
    target_duration_sec: int,
    locale_pack: dict[str, Any],
) -> TextBudget:
    locale = str(locale_pack["locale"])
    metrics = locale_pack["textMetrics"]
    words = (
        target_duration_sec
        / 60.0
        * float(locale_pack["narration"]["wordsPerMinute"])
        / float(metrics["expansionRatio"])
    )
    if locale in LATIN_LOCALES:
        strategy = "unicode-words"
        units = round(words)
    elif locale in CJK_LOCALES:
        strategy = "script-graphemes"
        units = round(words * float(metrics["charsPerWord"]))
    else:
        raise ValueError(f"unsupported localized V2 text metric locale: {locale}")
    return TextBudget(
        locale=locale,
        strategy=strategy,
        target_units=max(1, units),
        target_duration_sec=target_duration_sec,
    )
