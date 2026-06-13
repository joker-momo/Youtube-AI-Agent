from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from video_agent.assets.service import StockAssetService


def _cand(asset_id: str, tags: list[str], provider: str = "pexels_video") -> dict:
    return {
        "provider": provider,
        "provider_asset_id": asset_id,
        "asset_id": asset_id,
        "media_type": "video",
        "width": 1920,
        "height": 1080,
        "tags": tags,
        "quality": "fullhd",
    }


class _Stock:
    def __init__(self, by_provider):
        self.by_provider = by_provider

    def search(self, provider, query, filters, exclude_ids=None):
        return {"provider": provider}

    def normalize(self, provider, response):
        return list(self.by_provider.get(provider, []))


def _service(by_provider, *, visual_config=None, library_candidates=None):
    svc = StockAssetService(
        visual_config={
            "providers": ["pexels_video"],
            "photo_providers": ["pexels"],
            "strategy": "auto",
            **(visual_config or {}),
        },
        stock_client=_Stock(by_provider),
        download_client=SimpleNamespace(),
    )
    svc.cache = SimpleNamespace(get=lambda *a, **k: None, set=lambda *a, **k: None)
    svc.library = SimpleNamespace(
        root=Path("/tmp/stub_lib_quality"),
        record_usage=lambda *a, **k: None,
        get_by_provider_id=lambda *a, **k: None,
        is_file_valid=lambda a: True,
        search_by_query=lambda *a, **k: list(library_candidates or []),
    )
    svc._ensure_asset = lambda candidate, query: {  # type: ignore[assignment]
        **candidate,
        "provider": candidate["provider"],
        "asset_id": candidate["provider_asset_id"],
    }
    return svc


def _first_frame_scene() -> dict:
    return {
        "id": "s01",
        "layout": "short_hook",
        "motion": "push_in",
        "duration_sec": 3,
        "visual_importance": "critical",
        "visual_prompt": "close up supermarket bread package ingredient label hand checking",
        "first_frame_plan": {
            "strategy": "evidence_closeup",
            "must_show": ["bread package", "ingredient label", "hand"],
            "must_avoid": ["smiling", "wide shot", "generic kitchen", "centered stock pose"],
            "roi_target": "ingredient label",
        },
    }


def test_asset_quality_downranks_stock_portrait_for_first_frame_evidence():
    weak_stock = _cand(
        "portrait",
        ["bread", "package", "label", "supermarket", "smiling", "family", "wide", "portrait"],
    )
    evidence = _cand(
        "evidence",
        ["bread", "package", "label", "supermarket", "hand", "checking", "close up", "ingredient"],
    )
    svc = _service({"pexels_video": [weak_stock, evidence]})

    asset = svc.get_scene_asset(_first_frame_scene(), channel_id="ch", job_id="job")

    assert asset is not None
    assert asset["asset_id"] == "evidence"
    selection = asset["asset_selection"]
    assert selection["base_raw_score"] >= 0
    assert 0 <= selection["quality_norm"] <= 1
    assert 0 <= selection["final_norm"] <= 1
    assert "stock_feeling_penalty" in selection["quality_dimensions"]


def test_asset_candidate_total_cap_is_cumulative_across_cache_and_provider_tiers():
    library_candidates = [
        _cand(f"cache-{i}", ["bread", "airplane", "runway"], provider="pexels")
        for i in range(8)
    ]
    provider_candidates = [
        _cand(f"provider-{i}", ["bread", "package", "label", "supermarket", "hand", "checking"])
        for i in range(5)
    ]
    svc = _service(
        {"pexels_video": provider_candidates},
        library_candidates=library_candidates,
        visual_config={
            "shorts_quality": {
                "asset_selection": {
                    "max_library_cache_candidates": 8,
                    "max_asset_candidates_per_provider": 12,
                    "max_candidate_metadata_score_total": 10,
                }
            }
        },
    )

    asset = svc.get_scene_asset(_first_frame_scene(), channel_id="ch", job_id="job")

    assert asset is not None
    assert asset["asset_id"] == "provider-0"
    assert asset["asset_selection"]["candidate_count"] == 2
    assert asset["asset_selection"]["candidate_budget"]["scored_total"] == 10
    assert asset["asset_selection"]["candidate_budget"]["remaining"] == 0


def test_asset_query_includes_first_frame_and_required_evidence_terms():
    class RecordingStock(_Stock):
        def __init__(self, by_provider):
            super().__init__(by_provider)
            self.queries: list[str] = []

        def search(self, provider, query, filters, exclude_ids=None):
            self.queries.append(query)
            return super().search(provider, query, filters, exclude_ids=exclude_ids)

    stock = RecordingStock({
        "pexels_video": [
            _cand("evidence", ["bread", "package", "label", "supermarket", "hand", "checking"])
        ]
    })
    svc = StockAssetService(
        visual_config={"providers": ["pexels_video"], "photo_providers": ["pexels"], "strategy": "auto"},
        stock_client=stock,
        download_client=SimpleNamespace(),
    )
    svc.cache = SimpleNamespace(get=lambda *a, **k: None, set=lambda *a, **k: None)
    svc.library = SimpleNamespace(
        root=Path("/tmp/stub_lib_query"),
        record_usage=lambda *a, **k: None,
        get_by_provider_id=lambda *a, **k: None,
        is_file_valid=lambda a: True,
        search_by_query=lambda *a, **k: [],
    )
    svc._ensure_asset = lambda candidate, query: {  # type: ignore[assignment]
        **candidate,
        "provider": candidate["provider"],
        "asset_id": candidate["provider_asset_id"],
    }
    scene = {
        **_first_frame_scene(),
        "visual_prompt": "bread",
        "required_visual_evidence": {
            "required_actions": ["hand checking"],
            "required_objects": ["ingredient label"],
        },
    }

    svc.get_scene_asset(scene, channel_id="ch", job_id="job")

    assert stock.queries
    query = stock.queries[0].lower()
    assert "ingredient label" in query
    assert "hand checking" in query


def test_asset_quality_disabled_preserves_base_ranking():
    low_base_good_quality = _cand(
        "low-base",
        ["bread", "package", "label", "supermarket", "hand", "checking"],
    )
    low_base_good_quality["width"] = 640
    low_base_good_quality["height"] = 360
    high_base_stock = _cand(
        "high-base",
        ["bread", "package", "label", "supermarket", "checking", "smiling", "family", "wide", "portrait"],
    )
    svc = _service(
        {"pexels_video": [low_base_good_quality, high_base_stock]},
        visual_config={"asset_selection": {"enable_quality_scoring": False}},
    )

    asset = svc.get_scene_asset(_first_frame_scene(), channel_id="ch", job_id="job")

    assert asset is not None
    assert asset["asset_id"] == "high-base"
    assert asset["asset_selection"]["quality_scoring_enabled"] is False
