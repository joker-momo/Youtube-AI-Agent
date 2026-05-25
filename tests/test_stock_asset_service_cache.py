"""Tests for StockAssetService library-cache scoring threshold."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from video_agent.assets.service import StockAssetService


class _StubLibrary:
    """In-memory stand-in for AssetLibrary used by the service."""

    def __init__(self, candidates: list[dict]):
        self._candidates = candidates
        self.root = Path("/tmp/stub_library")
        self.recorded: list[dict] = []

    def search_by_query(self, query, *, media_type=None, exclude_asset_ids=None, limit=10):
        return list(self._candidates)

    def is_file_valid(self, asset):
        return True

    def record_usage(self, asset_id, *, channel_id, job_id, scene_id, scene_intent):
        self.recorded.append(
            {
                "asset_id": asset_id,
                "channel_id": channel_id,
                "job_id": job_id,
                "scene_id": scene_id,
            }
        )


def _service_with_library(candidates: list[dict]) -> StockAssetService:
    svc = StockAssetService(
        visual_config={"providers": ["pexels"], "strategy": "auto"},
        stock_client=SimpleNamespace(),
        download_client=SimpleNamespace(),
    )
    svc.library = _StubLibrary(candidates)  # type: ignore[assignment]
    return svc


def _scene(**overrides):
    base = {"id": "scene-01", "motion": "slow_zoom"}
    base.update(overrides)
    return base


def _candidate(asset_id, tags, width=1920, height=1080):
    return {
        "provider": "pexels",
        "provider_asset_id": asset_id,
        "asset_id": asset_id,
        "media_type": "video",
        "width": width,
        "height": height,
        "tags": list(tags),
        "quality": "fullhd",
    }


def test_library_cache_rejects_low_score_candidates():
    """Token overlap alone (e.g. a single generic word) must not be enough."""
    candidate = _candidate("airplane-1", tags=["airplane", "takeoff", "runway"])
    svc = _service_with_library([candidate])

    result = svc._try_library_cache(
        "bedroom night routine evening home",
        media_type="video",
        channel_id="ch1",
        job_id="job1",
        scene=_scene(),
    )

    assert result is None  # rejected — no matched terms


def test_library_cache_rejects_single_generic_tag_overlap():
    """One overlapping generic word (e.g. 'scene', 'person') must not bypass the rule."""
    # Resolution + landscape + quality bonuses push score >= 65 by themselves,
    # but matched_terms only has one generic word — should still be rejected.
    candidate = _candidate(
        "bellagio-1",
        tags=["fountain", "bellagio", "vegas", "city", "night"],
    )
    svc = _service_with_library([candidate])

    result = svc._try_library_cache(
        "bedroom night routine evening home wellness",
        media_type="video",
        channel_id="ch1",
        job_id="job1",
        scene=_scene(),
    )

    assert result is None  # only 'night' matched — too weak


def test_library_cache_returns_high_score_candidate_with_real_overlap():
    candidate = _candidate(
        "bedroom-1",
        tags=["bedroom", "night", "routine", "evening", "home", "calm"],
    )
    svc = _service_with_library([candidate])

    result = svc._try_library_cache(
        "bedroom night routine evening home",
        media_type="video",
        channel_id="ch1",
        job_id="job1",
        scene=_scene(),
    )

    assert result is not None
    assert result["asset_id"] == "bedroom-1"
    sel = result["asset_selection"]
    assert sel["source"] == "library_cache"
    assert sel["score"] >= svc._MIN_LIBRARY_CACHE_SCORE
    assert "library_cache_hit" in sel["reasons"]
    assert sel["matched_terms"]


def test_library_cache_picks_best_score_among_candidates():
    weak = _candidate("weak-1", tags=["airplane", "takeoff"])
    strong = _candidate(
        "strong-1",
        tags=["bedroom", "night", "routine", "evening", "home", "calm"],
    )
    svc = _service_with_library([weak, strong])

    result = svc._try_library_cache(
        "bedroom night routine evening home",
        media_type="video",
        channel_id="ch1",
        job_id="job1",
        scene=_scene(),
    )

    assert result is not None
    assert result["asset_id"] == "strong-1"


def test_library_cache_returns_none_when_all_candidates_used():
    candidate = _candidate("used-1", tags=["bedroom", "night", "routine"])
    svc = _service_with_library([candidate])
    svc.used_provider_ids.add(("pexels", "used-1"))

    result = svc._try_library_cache(
        "bedroom night routine",
        media_type="video",
        channel_id="ch1",
        job_id="job1",
        scene=_scene(),
    )

    assert result is None
