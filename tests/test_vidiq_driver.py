from __future__ import annotations

from video_agent.browser_worker.drivers.vidiq import parse_vidiq_overlay

SAMPLE = """Search Companion

Keyword Score

26

Overall Score

LOW

VOLUME

Very low

COMPETITION

Low

VPH over time

Views per hour of all videos containing this search term

Not enough search data available for this keyword

Search Term Statistics

SEARCH TERM:

“rutina nocturna 45”

HIGHEST VIEWS

235.8k

Related keywords

cuidado de la piel
64
cuidado facial
56
skincare
66
best camera settings
85
Unlock more related keywords
"""


def test_parse_score_volume_competition():
    parsed = parse_vidiq_overlay(SAMPLE)
    assert parsed["score"] == 26
    assert parsed["volume"].lower().startswith("very low")
    assert parsed["competition"].lower().startswith("low")


def test_parse_related_keywords():
    parsed = parse_vidiq_overlay(SAMPLE)
    related = parsed["related"]
    assert len(related) == 4
    by_kw = {r["keyword"]: r["score"] for r in related}
    assert by_kw["cuidado de la piel"] == 64
    assert by_kw["skincare"] == 66
    assert by_kw["best camera settings"] == 85


def test_parse_missing_fields_returns_none():
    parsed = parse_vidiq_overlay("Search Companion\n\nKeyword Score\n\n")
    assert parsed["score"] is None
    assert parsed["volume"] is None
    assert parsed["competition"] is None
    assert parsed["related"] == []
