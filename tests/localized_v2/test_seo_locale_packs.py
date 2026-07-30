from __future__ import annotations

import builtins
import importlib

import pytest

from video_agent.localized_v2.seo import scorer
from video_agent.localized_v2.seo.scorer import score_title

from .locale_fixtures import locale_pack


@pytest.mark.parametrize(
    ("locale", "title"),
    [
        ("en-US", "Healthy Aging: One Sustainable Daily Habit"),
        ("fr-FR", "Bien vieillir grâce à une habitude réaliste"),
        ("pt-BR", "Envelhecimento saudável com uma rotina possível"),
        ("ko-KR", "건강한 노화를 위한 현실적인 습관"),
        ("ja-JP", "健やかな年齢の重ね方と毎日の習慣"),
    ],
)
def test_locale_title_scoring_preserves_native_unicode(
    locale: str, title: str
) -> None:
    result = score_title(title, locale_pack(locale))

    assert result.title == title
    assert result.locale == locale
    assert result.graphemes > 0
    assert result.within_limit
    assert result.cue_hits


def test_title_scorer_imports_without_legacy_spanish_scorer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args, **kwargs):
        if "seo_title" in name or "vida_plena" in name:
            raise AssertionError(f"legacy scorer imported: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(scorer)

    assert scorer.score_title(
        "Healthy Aging Daily Routine", locale_pack("en-US")
    ).score > 0
