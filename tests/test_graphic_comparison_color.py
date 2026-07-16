"""Comparison graphic cards must not imply a recommended side by styling.

Real incident (Mundial job scene-26): a comparison card titled "El punto medio
suele funcionar mejor" tinted "cena muy pesada" as bad and "cena demasiado
escasa" as good. Both sides are the BAD extremes, but the "good" tint reads as
the recommended choice, so the card contradicted its own message. Comparison
cards must instruct neutral, equal-weight treatment unless one side is
explicitly the recommended one.

bug-542: the instruction must express this WITHOUT naming colours — a negative
"do not use red/green" still conditions the image model on those tokens. These
assertions therefore check the behavioural contract, not colour words.
"""

from __future__ import annotations

from video_agent.orchestrator.stages.graphic_images import _CARD_KIND, _content_lines


def test_comparison_content_lines_demand_neutral_coloring():
    lines = _content_lines(
        "comparison",
        "El punto medio suele funcionar mejor",
        "",
        ["Una cena muy pesada", "Una cena demasiado escasa"],
        "",
    )
    text = " ".join(lines).lower()
    # Equal-weight neutrality is demanded, and the good-vs-bad default is refused…
    assert "neutral" in text and "equal-weight" in text
    assert "do not" in text or "not default" in text or "never" in text
    assert "good-versus-bad" in text or "good versus bad" in text
    # …neither side may read as recommended through ANY visual channel…
    assert "recommended" in text
    for channel in ("tint", "contrast", "iconography", "emphasis"):
        assert channel in text, channel
    # …and the instruction itself names NO colour (bug-542 token leak).
    for colour in ("red", "green", "cream", "navy", "orange", "pink", "yellow", "blue"):
        assert colour not in text, colour


def test_comparison_card_kind_mentions_neutral():
    assert "neutral" in _CARD_KIND["comparison"].lower()
