from __future__ import annotations

import pytest

from video_agent.localized_v2.visual import (
    VisualContext,
    build_visual_context,
    collect_search_queries,
    validate_visual_context,
)
from video_agent.localized_v2.visual.context import VisualLocalizationError

from .locale_fixtures import locale_pack


def test_market_is_omitted_when_topic_has_no_local_evidence() -> None:
    context = build_visual_context(
        "how sleep routines support wellbeing",
        locale_pack("ja-JP"),
        market_relevant=False,
    )

    assert context.market_context is None
    assert "日本" not in context.topic


def test_market_requires_explicit_relevance_evidence() -> None:
    with pytest.raises(VisualLocalizationError, match="explicit topic evidence"):
        build_visual_context(
            "food label guidance",
            locale_pack("fr-FR"),
            market_relevant=True,
        )

    context = build_visual_context(
        "understanding local food labels",
        locale_pack("fr-FR"),
        market_relevant=True,
        evidence=("The episode explains France-specific label terminology.",),
    )
    assert context.market_context == "France"


def test_visual_context_rejects_stereotype_guidance() -> None:
    context = VisualContext(
        locale="ko-KR",
        topic="healthy daily movement",
        people_context="irrelevant ceremonial clothing",
        market_context=None,
        evidence=(),
        avoid=("irrelevant ceremonial clothing", "stereotypes"),
    )

    with pytest.raises(VisualLocalizationError, match="prohibited stereotype"):
        validate_visual_context(context)


def test_search_queries_remain_english_and_deduplicated() -> None:
    artifact = {
        "scenes": [
            {
                "searchBrief": {
                    "language": "en",
                    "queries": ["adult walking outdoors", "adult walking outdoors"],
                }
            },
            {
                "searchBrief": {
                    "language": "en",
                    "queries": ["healthy meal preparation"],
                }
            },
        ]
    }

    assert collect_search_queries(artifact) == (
        "adult walking outdoors",
        "healthy meal preparation",
    )
