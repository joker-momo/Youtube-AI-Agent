import json
import pytest
from video_agent.operator import _score_and_sort_seo_variants


def test_score_and_sort_fills_top_level_fields():
    seo = {
        "job_id": "j1",
        "title": "",
        "thumbnail_text": "",
        "title_variants": [
            {"title": "Tips", "thumbnail_text": "tips"},
            {"title": "5 secretos para dormir mejor después de los 45", "thumbnail_text": "DUERME MEJOR HOY"},
        ]
    }
    result = _score_and_sort_seo_variants(seo)
    # Sorted best-first
    assert result["title_variants"][0]["score"] >= result["title_variants"][1]["score"]
    # Top-level fields filled with winner
    assert result["title"] == result["title_variants"][0]["title"]
    assert result["thumbnail_text"] == result["title_variants"][0]["thumbnail_text"]


def test_score_and_sort_noop_when_no_variants():
    seo = {"title": "original", "thumbnail_text": "ORIGINAL"}
    result = _score_and_sort_seo_variants(seo)
    assert result["title"] == "original"
    assert result["thumbnail_text"] == "ORIGINAL"


def test_score_and_sort_adds_score_to_each_variant():
    seo = {
        "title_variants": [
            {"title": "5 secretos para dormir mejor a los 45", "thumbnail_text": "DUERME MEJOR HOY"},
            {"title": "Tips básicos", "thumbnail_text": "tips sleep"},
        ]
    }
    result = _score_and_sort_seo_variants(seo)
    for v in result["title_variants"]:
        assert "score" in v
        assert isinstance(v["score"], int)
        assert "score_breakdown" in v
