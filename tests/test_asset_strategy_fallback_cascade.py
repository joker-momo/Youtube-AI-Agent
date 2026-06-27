from unittest.mock import MagicMock

from video_agent.assets.service import StockAssetService


def _mock_service():
    svc = StockAssetService(
        visual_config={
            "providers": ["pexels"],
            "photo_providers": ["pexels_photo"],
            "fallback_providers": [],
        },
        image_gen_fn=MagicMock(return_value=True),
    )
    svc.core.library = MagicMock()
    svc.core.library.root = "/tmp"
    svc.core.library.has_asset.return_value = False

    svc.core._search_and_download = MagicMock(return_value=None)
    svc._ai_generate_scene_asset = MagicMock(
        return_value={
            "provider": "ai_generated",
            "asset_id": "mock_ai_asset",
            "asset_tier": "ai_image",
            "asset_selection": {"asset_match_status": "ai_generated"},
        }
    )
    return svc


def test_fallback_cascade_stock_ok_non_key():
    svc = _mock_service()
    scene = {
        "id": "s1",
        "layout": "short_tip",
        "asset_strategy": "stock_ok",
        "visual_importance": "normal",
    }

    # 1. Test when weak pexels succeeds
    svc.core._search_and_download.return_value = {"provider": "pexels", "asset_selection": {}}

    asset = svc.get_scene_asset(scene, "channel", "job")
    assert asset["provider"] == "pexels"
    assert asset["asset_tier"] in ("pexels_video", "weak_pexels")


def test_fallback_cascade_ai_image_preferred():
    svc = _mock_service()
    scene = {
        "id": "s1",
        "layout": "short_tip",
        "asset_strategy": "ai_image_preferred",
        "visual_importance": "normal",
    }

    svc.core._search_and_download.return_value = None
    asset = svc.get_scene_asset(scene, "channel", "job")

    assert asset is not None
    assert asset["asset_tier"] == "ai_image"
    assert svc._ai_generate_scene_asset.called


def test_ai_image_preferred_ignores_cached_graphic_fallback():
    svc = _mock_service()
    scene = {
        "id": "s1",
        "layout": "short_tip",
        "asset_strategy": "ai_image_preferred",
        "visual_importance": "normal",
    }
    cached_graphic_fallback = {
        "provider": "graphic_fallback",
        "provider_asset_id": "old-fallback",
        "asset_id": "old-fallback",
        "media_type": "generated",
        "file_path": "old.mp4",
        "width": 1080,
        "height": 1920,
        "tags": ["bread", "label", "portion"],
    }
    svc.core.library.search_by_query.return_value = [cached_graphic_fallback]
    svc.core.library.is_file_valid.return_value = True
    svc.core._search_and_download.return_value = None

    asset = svc.get_scene_asset(scene, "channel", "job")

    assert asset["provider"] == "ai_generated"
    assert asset["asset_tier"] == "ai_image"
    assert svc._ai_generate_scene_asset.called


def test_graphic_fallback_strategy_uses_chatgpt_image_not_placeholder():
    svc = _mock_service()
    scene = {
        "id": "s1",
        "layout": "short_tip",
        "asset_strategy": "graphic_fallback",
        "visual_importance": "normal",
    }

    asset = svc.get_scene_asset(scene, "channel", "job")

    assert asset is not None
    assert asset["asset_tier"] == "ai_image"
    assert asset["provider"] == "ai_generated"
    assert svc._ai_generate_scene_asset.called
    assert not svc.core._search_and_download.called


def test_fallback_cascade_key_scene_ai_fallback():
    svc = _mock_service()
    scene = {
        "id": "s1",
        "layout": "short_hook",
        "asset_strategy": "stock_ok",
        "visual_importance": "critical",
    }

    svc.core._search_and_download.return_value = None
    asset = svc.get_scene_asset(scene, "channel", "job")

    assert asset is not None
    assert asset["asset_tier"] == "ai_image"
    assert svc._ai_generate_scene_asset.called


def test_non_key_prefers_ai_over_weak_stock():
    """Regression: a non-key stock_ok scene whose strict stock search fails but
    a weak stock match exists must get an AI image, not the off-topic weak clip.
    AI is only bypassed for weak stock when image generation is unavailable."""
    svc = _mock_service()
    scene = {
        "id": "s1",
        "layout": "short_pain",
        "asset_strategy": "stock_ok",
        "visual_importance": "normal",
    }

    def search_side_effect(*args, require_strict=False, **kwargs):
        if require_strict:
            return None
        return {"provider": "pexels", "asset_selection": {}}

    svc.core._search_and_download.side_effect = search_side_effect
    asset = svc.get_scene_asset(scene, "channel", "job")

    assert asset is not None
    assert asset["asset_tier"] == "ai_image", "non-key scene must prefer AI over weak stock"
    assert svc._ai_generate_scene_asset.called


def test_non_key_falls_back_to_weak_stock_when_ai_unavailable():
    """When image generation is unavailable, a non-key stock_ok scene may still
    accept weak stock as the degraded last resort (no regression to blocking)."""
    svc = _mock_service()
    svc.image_gen_fn = None  # AI tier unavailable
    scene = {
        "id": "s1",
        "layout": "short_pain",
        "asset_strategy": "stock_ok",
        "visual_importance": "normal",
    }

    def search_side_effect(*args, require_strict=False, **kwargs):
        if require_strict:
            return None
        return {"provider": "pexels", "asset_selection": {}}

    svc.core._search_and_download.side_effect = search_side_effect
    asset = svc.get_scene_asset(scene, "channel", "job")

    assert asset is not None
    assert asset["asset_tier"] == "weak_pexels"
