from __future__ import annotations

import pytest

from video_agent.localized_v2.text_metrics import (
    budget_for_duration,
    measure_text,
)

from .locale_fixtures import snapshots


def test_latin_metrics_count_unicode_words() -> None:
    _channel, locale_pack = snapshots("fr-FR")

    measure = measure_text("L’activité régulière peut soutenir le bien-être.", locale_pack)

    assert measure.strategy == "unicode-words"
    assert measure.units == 6
    assert measure.estimated_duration_sec > 0


def test_japanese_without_spaces_has_meaningful_budget() -> None:
    _channel, locale_pack = snapshots("ja-JP")

    measure = measure_text("研究では毎日の軽い運動が役立つ可能性があります。", locale_pack)
    budget = budget_for_duration(840, locale_pack)

    assert measure.strategy == "script-graphemes"
    assert measure.units >= 20
    assert measure.estimated_words > 0
    assert measure.estimated_duration_sec > 0
    assert budget.strategy == "script-graphemes"
    assert budget.target_units > 1000


def test_korean_mixed_hangul_and_latin_is_deterministic() -> None:
    _channel, locale_pack = snapshots("ko-KR")
    text = "연구에 따르면 하루 20분 walking은 도움이 될 수 있습니다."

    first = measure_text(text, locale_pack)
    second = measure_text(text, locale_pack)

    assert first == second
    assert first.strategy == "script-graphemes"
    assert first.units > len(text.split())


@pytest.mark.parametrize("locale", ["en-US", "fr-FR", "pt-BR"])
def test_latin_budget_uses_word_strategy(locale: str) -> None:
    _channel, locale_pack = snapshots(locale)

    budget = budget_for_duration(600, locale_pack)

    assert budget.strategy == "unicode-words"
    assert budget.target_units > 0
