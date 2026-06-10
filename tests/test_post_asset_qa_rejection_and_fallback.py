import pytest
from unittest.mock import MagicMock
from video_agent.assets.service import StockAssetService

def _mock_service(vision_qa_responses):
    # vision_qa_responses is a list of results to return sequentially
    def mock_vision_qa(scene, local_path):
        if vision_qa_responses:
            return vision_qa_responses.pop(0)
        return {"verdict": "PASS", "confidence": 1.0}

    svc = StockAssetService(
        visual_config={
            "providers": ["pexels"],
            "photo_providers": ["pexels_photo"],
            "fallback_providers": []
        },
        vision_qa_fn=mock_vision_qa
    )
    svc.library = MagicMock()
    svc.library.root = "/tmp"
    svc.library.has_asset.return_value = False
    
    # Mocking _search_and_download is a bit complex because it's called internally.
    # We will just mock it entirely, but wait, the QA logic is INSIDE _search_and_download.
    # So we need to mock the provider search to return dummy candidates, and let _search_and_download run.
    return svc

def test_post_asset_qa_rejection_and_fallback():
    # If we want to test _search_and_download, we need to mock stock_client.search
    stock_client = MagicMock()
    # Return two candidates
    stock_client.search.return_value = {
        "pexels": [
            {"id": "1", "image": "img1.jpg", "url": "http://img1.jpg", "tags": []},
            {"id": "2", "image": "img2.jpg", "url": "http://img2.jpg", "tags": []}
        ]
    }
    
    vision_qa_responses = [
        {"verdict": "FAIL", "missing_evidence": ["person"], "reason": "No person"},
        {"verdict": "PASS", "confidence": 0.9}
    ]
    
    def mock_vision_qa(scene, local_path):
        return vision_qa_responses.pop(0)
        
    svc = StockAssetService(
        visual_config={"providers": ["pexels"]},
        stock_client=stock_client,
        vision_qa_fn=mock_vision_qa
    )
    svc.library = MagicMock()
    # Mock _ensure_asset to return something with local_path
    def mock_ensure_asset(candidate, query):
        return {
            "provider": candidate.get("provider", "pexels"),
            "asset_id": str(candidate.get("provider_asset_id", "1")),
            "local_path": f"/tmp/mock_{candidate.get('provider_asset_id', '1')}.jpg",
            "asset_selection": {}
        }
    svc._ensure_asset = mock_ensure_asset
    
    # For _search_and_download to run candidates, it needs some mocked methods
    svc._is_contradictory = MagicMock(return_value=False)
    
    scene = {"id": "s1", "layout": "short_tip", "asset_strategy": "stock_ok", "visual_importance": "critical", "visual_prompt": "test"}
    
    # Override _search_and_download's rank candidates partially by mocking _rank_candidates
    svc._rank_candidates = MagicMock(return_value=[
        {"candidate": {"provider": "pexels", "provider_asset_id": "1"}, "score": 10, "provider_order": 1, "provider_candidate_rank": 1},
        {"candidate": {"provider": "pexels", "provider_asset_id": "2"}, "score": 10, "provider_order": 1, "provider_candidate_rank": 2}
    ])
    
    asset = svc._search_and_download(providers=["pexels"], query="test", filters={}, ttl_hours=24, scene=scene, channel_id="channel", job_id="job", is_fallback=False, require_strict=False)
    
    assert asset is not None
    # Because candidate 1 failed QA, candidate 2 should be the one returned
    assert asset["asset_id"] == "2"
    
    # And there should be an error logged for candidate 1
    assert len(svc.last_errors) == 1
    assert svc.last_errors[0]["error_type"] == "VisionQAFailure"
    assert "No person" in svc.last_errors[0]["message"]
