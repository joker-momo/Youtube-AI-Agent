"""Pexels VIDEO has NO tags/alt metadata, so the tag-overlap strict gate
(`_candidate_score` → `matched_terms` → `_passes_strict_gate`) rejected every
video and the "prefer video" design (providers=["pexels_video"]) only ever
yielded animated photos. The fix: when a candidate has no tags, fall back to its
own free-text metadata, then to query provenance (Pexels ranked it for our
demographic-forced query), so video matches while photos stay unchanged.
"""

from __future__ import annotations

from video_agent.assets.stock_core import _candidate_score, _force_elderly_demographic

_DEMO = {"elderly", "senior", "mature", "older", "grandmother", "grandfather"}


def _elderly_query() -> str:
    return _force_elderly_demographic("Mature woman sixties healthy ingredients on kitchen counter")


def test_pexels_video_no_tags_matches_via_query_provenance():
    """A Pexels VIDEO (tags=[], no alt) must produce matched_terms so it clears the
    tag-overlap + demographic strict gate instead of always falling to photo."""
    q = _elderly_query()
    video = {"width": 1920, "height": 1080, "tags": [], "provider": "pexels_video"}
    res = _candidate_score(q, video)
    assert res["matched_terms"], "video with empty tags must match via query provenance"
    assert "tag_match" in res["reasons"]
    # the demographic strict gate keys on a demo term being in matched_terms
    assert _DEMO & set(res["matched_terms"]), "matched_terms must carry the forced demographic"


def test_photo_with_tags_unchanged():
    """A photo whose provider metadata yields tags still matches via its OWN tags —
    the fallback must not kick in or alter the existing photo behaviour."""
    q = _elderly_query()
    photo = {"width": 1920, "height": 1080, "tags": ["senior", "kitchen", "vegetables"]}
    res = _candidate_score(q, photo)
    assert "senior" in res["matched_terms"]


def test_photo_with_only_alt_text_matches():
    """Pexels photos expose `alt` (rich) but no `tags`; the fallback should use alt
    before query provenance so real metadata still drives the match."""
    q = _elderly_query()
    photo = {
        "width": 1920,
        "height": 1080,
        "tags": [],
        "alt": "Senior woman preparing healthy vegetables in a bright kitchen",
    }
    res = _candidate_score(q, photo)
    assert res["matched_terms"]
    assert {"senior", "kitchen", "vegetables"} & set(res["matched_terms"])
