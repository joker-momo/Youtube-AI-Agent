"""Tests for the 3-tier visual asset cascade (video -> photo -> AI-gen).

Root cause of weak hook visuals: a stock video search for an action-specific
prompt ("turning a bread package to read the label") returns no clip that
actually depicts the action, so the service silently accepted a generic
resolution-only match ("slicing bread"). The cascade now descends to a Pexels
photo, then to AI image generation, and only as an anti-blank last resort
accepts a weak stock match (flagged weak_match=True).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from video_agent.assets.service import StockAssetService


class _FakeStock:
    def __init__(self, by_provider):
        self.by_provider = by_provider

    def search(self, provider, query, filters, exclude_ids=None):
        return {"provider": provider}

    def normalize(self, provider, response):
        return list(self.by_provider.get(provider, []))


def _cand(asset_id, provider, tags, media_type="video"):
    return {
        "provider": provider,
        "provider_asset_id": asset_id,
        "asset_id": asset_id,
        "media_type": media_type,
        "width": 1920,
        "height": 1080,
        "tags": list(tags),
    }


def _service(by_provider, *, image_gen_fn=None):
    svc = StockAssetService(
        visual_config={"providers": ["pexels_video"], "strategy": "auto"},
        stock_client=_FakeStock(by_provider),
        download_client=SimpleNamespace(),
        image_gen_fn=image_gen_fn,
    )
    import uuid
    svc.cache = SimpleNamespace(get=lambda *a, **k: None, set=lambda *a, **k: None)
    svc.library = SimpleNamespace(
        root=Path(f"/tmp/stub_lib_{uuid.uuid4().hex}"),
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
    return svc


# A bread-label scene whose action ("turning package to read label") stock video
# cannot satisfy. Query terms include bread / package / label / supermarket.
def _scene():
    return {
        "id": "s01",
        "motion": "push_in",
        "duration_sec": 3,
        "visual_prompt": "supermarket bread package label, hand turning package to read the back label",
    }


_STRICT_TAGS = ["bread", "package", "label", "supermarket", "read", "turning"]
_WEAK_TAGS = ["airplane", "sky", "runway"]


def test_tier1_video_strict_match_is_used():
    svc = _service({"pexels_video": [_cand("vid-ok", "pexels_video", _STRICT_TAGS)]})
    asset = svc.get_scene_asset(_scene(), channel_id="ch", job_id="job")
    assert asset is not None
    assert asset["asset_id"] == "vid-ok"
    assert asset["asset_selection"].get("weak_match") is False


def test_descends_to_photo_when_video_has_no_strict_match():
    svc = _service({
        "pexels_video": [_cand("vid-weak", "pexels_video", _WEAK_TAGS)],
        "pexels": [_cand("photo-ok", "pexels", _STRICT_TAGS, media_type="photo")],
    })
    asset = svc.get_scene_asset(_scene(), channel_id="ch", job_id="job")
    assert asset is not None
    assert asset["asset_id"] == "photo-ok"
    assert asset["media_type"] == "photo"
    assert asset["asset_selection"].get("weak_match") is False


def test_descends_to_ai_gen_when_no_strict_stock():
    calls = {"n": 0}

    def fake_gen(prompt, out_path):
        calls["n"] += 1
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"fake-image" * 200)

    svc = _service(
        {
            "pexels_video": [_cand("vid-weak", "pexels_video", _WEAK_TAGS)],
            "pexels": [_cand("photo-weak", "pexels", _WEAK_TAGS, media_type="photo")],
        },
        image_gen_fn=fake_gen,
    )
    asset = svc.get_scene_asset(_scene(), channel_id="ch", job_id="job")
    assert asset is not None
    assert calls["n"] == 1
    assert asset["asset_selection"].get("source") == "ai_generated"
    assert asset.get("media_type") == "image"


def test_weak_stock_fallback_when_no_ai_and_no_strict():
    # No photo provider data, no image_gen -> key scene should graphic_fallback
    svc = _service({"pexels_video": [_cand("vid-weak", "pexels_video", _WEAK_TAGS)]})
    asset = svc.get_scene_asset(_scene(), channel_id="ch", job_id="job")
    assert asset is not None
    assert asset["asset_tier"] == "graphic_fallback"
    assert asset["asset_selection"]["asset_match_status"] == "graphic_fallback"

def test_prepare_assets_regression_weak_match_blocked_on_hook_fallback():
    # prompt asks hand turning bread package/back label,
    # Pexels returns slicing bread,
    # expected weak_match rejected for short_hook,
    # fallback to ai_generated or graphic_fallback.
    svc = _service({"pexels_video": [_cand("vid-weak", "pexels_video", ["slice", "cutting"])]})
    key_scene = {
        "id": "s02",
        "motion": "push_in",
        "duration_sec": 3,
        "visual_prompt": "hand turning bread package to show back label",
        "layout": "short_hook"
    }
    # With no AI enabled in mock, it should fallback to graphic_fallback
    asset = svc.get_scene_asset(key_scene, channel_id="ch", job_id="job")
    assert asset is not None
    assert asset["asset_tier"] == "graphic_fallback"

def test_prepare_assets_regression_weak_match_contradictory_blocked():
    # Non-key scene with contradictory match should skip the contradictory weak candidate.
    # Since there are no other candidates, it should block (return None).
    svc = _service({"pexels_video": [_cand("vid-weak", "pexels_video", ["sleep", "bed"])]})
    contradictory_scene = {
        "id": "s03",
        "motion": "pan_left",
        "duration_sec": 3,
        "visual_prompt": "a beautiful supermarket store",
        "layout": "default"
    }
    asset = svc.get_scene_asset(contradictory_scene, channel_id="ch", job_id="job")
    assert asset is None
