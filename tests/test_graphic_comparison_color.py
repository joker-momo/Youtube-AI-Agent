"""Comparison graphic cards must not misuse red=bad / green=good coloring.

Real incident (Mundial job scene-26): a comparison card titled "El punto medio
suele funcionar mejor" put "cena muy pesada" (red) vs "cena demasiado escasa"
(green). Both sides are the BAD extremes, but green universally reads as the
recommended choice, so the card contradicted its own message. Comparison cards
must instruct neutral, equal-weight coloring unless one side is explicitly the
recommended one.
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
    # Explicitly addresses (and forbids defaulting to) the red/green good-bad scheme.
    assert "red" in text and "green" in text
    assert "neutral" in text
    assert "do not" in text or "not default" in text or "never" in text


def test_comparison_card_kind_mentions_neutral():
    assert "neutral" in _CARD_KIND["comparison"].lower()
