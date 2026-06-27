from __future__ import annotations

from types import SimpleNamespace

import pytest

from video_agent.shorts.assets import ShortSpanAssetService
from video_agent.shorts.visual_acquisition import SpanSearchBudget


def _candidate(
    asset_id: str,
    *,
    provider: str = "pexels_video",
    tags: list[str] | None = None,
    duration_sec: float = 10.0,
    width: int = 1920,
    height: int = 1080,
) -> dict:
    return {
        "provider": provider,
        "provider_asset_id": asset_id,
        "media_type": "video",
        "download_url": f"https://cdn.example.invalid/{asset_id}.mp4",
        "source_url": f"https://example.invalid/{asset_id}",
        "width": width,
        "height": height,
        "duration_sec": duration_sec,
        "fps": 30,
        "tags": tags or [],
        "quality": "hd",
    }


class _Stock:
    def __init__(self, candidates: list[dict]) -> None:
        self.candidates = candidates
        self.searches: list[tuple[str, str, dict]] = []

    def search(self, provider, query, filters, exclude_ids=None):
        self.searches.append((provider, query, filters))
        return {"provider": provider, "query": query}

    def normalize(self, provider, response):
        return [dict(c, provider=provider) for c in self.candidates]


def _service(candidates: list[dict]) -> tuple[ShortSpanAssetService, _Stock]:
    # PR C span search/selection lives on ShortSpanAssetService after the P4
    # asset-layer decoupling (it was a delegating wrapper on StockAssetService).
    stock = _Stock(candidates)
    svc = ShortSpanAssetService.for_visual_config(
        visual_config={"providers": ["pexels_video"], "strategy": "auto"},
        stock_client=stock,
        download_client=SimpleNamespace(
            download=lambda *a, **k: pytest.fail("PR C must not download")
        ),
    )
    svc.core.cache = SimpleNamespace(get=lambda *a, **k: None, set=lambda *a, **k: None)
    return svc, stock


def _span_context() -> dict:
    return {
        "visual_span_id": "vs02",
        "scene_ids": ["s02", "s03"],
        "planned_duration_sec": 7.4,
        "duration_bucket": "5_to_8_sec",
        "trim_margin_sec": 1.0,
        "visual_importance": "critical",
        "required_subject_tags": ["adult_45_plus"],
        "required_action_tags": ["gentle_walking"],
        "required_environment_tags": ["outdoor_path"],
        "forbidden_evidence_tags": ["visible_injury"],
        "query_plan": {
            "primary": "mature adult gentle walking outdoor path",
            "alternates": ["older adult slow walk park"],
            "equivalent_action": [],
        },
    }


def test_span_metadata_search_deduplicates_by_provider_id_and_never_downloads_by_default() -> None:
    svc, stock = _service(
        [
            _candidate("same", tags=["older adult", "walking", "park"]),
            _candidate("same", tags=["duplicate through another query"]),
            _candidate("other", tags=["older adult", "walking", "park"]),
        ]
    )

    candidates = svc.get_visual_span_candidates(
        acquisition_context=_span_context(),
        budget=SpanSearchBudget(
            max_queries=2,
            metadata_candidates_per_query=5,
            max_unique_metadata_candidates=4,
            max_download_candidates=0,
            max_provider_retries=1,
        ),
    )

    assert [c["provider_asset_id"] for c in candidates] == ["same", "other"]
    assert candidates[0]["query_origins"][0]["query_class"] == "primary"
    assert candidates[0]["dedup_basis"] == "provider_asset_id"
    assert candidates[0]["local_path"] is None
    assert candidates[0]["public_ref"] is None
    assert "download_url" not in candidates[0]
    assert "content_hash" not in candidates[0]
    assert len(stock.searches) == 2


def test_span_metadata_search_can_deduplicate_by_metadata_identity_without_content_hash() -> None:
    first = _candidate("", tags=["older adult", "walking", "park"])
    first.pop("provider_asset_id")
    second = dict(first)
    svc, _stock = _service([first, second])

    candidates = svc.get_visual_span_candidates(
        acquisition_context=_span_context(),
        budget=SpanSearchBudget(1, 5, 5, 0, 1),
    )

    assert len(candidates) == 1
    assert candidates[0]["dedup_basis"] == "provider_metadata_identity"
    assert "content_hash" not in candidates[0]


def test_provisional_selection_is_not_render_eligible_and_unknown_absence_is_not_pass() -> None:
    svc, _stock = _service(
        [
            _candidate("good", tags=["older adult", "walking", "park"], duration_sec=12.0),
        ]
    )
    candidates = svc.get_visual_span_candidates(
        acquisition_context=_span_context(),
        budget=SpanSearchBudget(1, 5, 5, 0, 1),
    )

    selection = svc.select_provisional_span_candidate(
        acquisition_context=_span_context(),
        candidates=candidates,
        recent_visual_memory={},
        config={"download_policy": "none"},
    )

    assert selection["provisional_candidate_id"] == "pexels_video-good"
    assert selection["render_eligible"] is False
    assert selection["requires_local_validation"] is True
    assert selection["metadata_selection_status"] == "metadata_promising"
    evidence = selection["evidence_records"]
    injury = [r for r in evidence if r["requirement"] == "forbidden_evidence:visible_injury"][0]
    assert injury["status"] == "UNKNOWN"
    assert injury["capability_source"] == "provider_metadata"


def test_metadata_confirmed_forbidden_evidence_rejects_candidate() -> None:
    svc, _stock = _service(
        [
            _candidate(
                "injury", tags=["older adult", "visible injury", "walking"], duration_sec=12.0
            ),
        ]
    )
    candidates = svc.get_visual_span_candidates(
        acquisition_context=_span_context(),
        budget=SpanSearchBudget(1, 5, 5, 0, 1),
    )

    selection = svc.select_provisional_span_candidate(
        acquisition_context=_span_context(),
        candidates=candidates,
        recent_visual_memory={},
        config={"download_policy": "none"},
    )

    assert selection["metadata_selection_status"] == "metadata_rejected"
    assert selection["provisional_candidate_id"] is None
    assert selection["rejection_reasons"]["metadata_confirmed_forbidden_evidence"] == 1
