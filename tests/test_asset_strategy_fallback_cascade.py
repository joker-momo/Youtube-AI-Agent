import pytest
from unittest.mock import MagicMock
from video_agent.assets.service import StockAssetService

def _mock_service():
    svc = StockAssetService(
        visual_config={
            "providers": ["pexels"],
            "photo_providers": ["pexels_photo"],
            "fallback_providers": []
        },
        image_gen_fn=MagicMock(return_value=True)
    )
    svc.library = MagicMock()
    svc.library.root = "/tmp"
    svc.library.has_asset.return_value = False
    
    svc._search_and_download = MagicMock(return_value=None)
    svc._ai_generate_scene_asset = MagicMock(return_value={
        "provider": "ai_generated",
        "asset_id": "mock_ai_asset",
        "asset_tier": "ai_image",
        "asset_selection": {"asset_match_status": "ai_generated"}
    })
    return svc

def test_fallback_cascade_stock_ok_non_key():
    svc = _mock_service()
    scene = {"id": "s1", "layout": "short_tip", "asset_strategy": "stock_ok", "visual_importance": "normal"}
    
    # 1. Test when weak pexels succeeds
    svc._search_and_download.return_value = {"provider": "pexels", "asset_selection": {}}
    
    asset = svc.get_scene_asset(scene, "channel", "job")
    assert asset["provider"] == "pexels"
    assert asset["asset_tier"] in ("pexels_video", "weak_pexels")

def test_fallback_cascade_ai_image_preferred():
    svc = _mock_service()
    scene = {"id": "s1", "layout": "short_tip", "asset_strategy": "ai_image_preferred", "visual_importance": "normal"}
    
    svc._search_and_download.return_value = None
    asset = svc.get_scene_asset(scene, "channel", "job")
    
    assert asset is not None
    assert asset["asset_tier"] == "ai_image"
    assert svc._ai_generate_scene_asset.called

def test_fallback_cascade_graphic_fallback():
    svc = _mock_service()
    scene = {"id": "s1", "layout": "short_tip", "asset_strategy": "graphic_fallback", "visual_importance": "normal"}
    
    asset = svc.get_scene_asset(scene, "channel", "job")
    
    assert asset is not None
    assert asset["asset_tier"] == "graphic_fallback"
    assert not svc._search_and_download.called

def test_fallback_cascade_key_scene_ai_fallback():
    svc = _mock_service()
    scene = {"id": "s1", "layout": "short_hook", "asset_strategy": "stock_ok", "visual_importance": "critical"}
    
    svc._search_and_download.return_value = None
    asset = svc.get_scene_asset(scene, "channel", "job")
    
    assert asset is not None
    assert asset["asset_tier"] == "ai_image"
    assert svc._ai_generate_scene_asset.called
