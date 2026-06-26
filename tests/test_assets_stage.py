from pathlib import Path

import pytest
from PIL import Image

from video_agent.assets.service import _candidate_score
from video_agent.stages.assets import prepare_assets
from video_agent.stages.assets import _choose_bgm_track


STYLE_DNA = {
    "palette": {
        "background": "#F6F1E8",
        "primary": "#2F6B57",
        "secondary": "#D98C5F",
        "accent": "#F2C94C",
        "text": "#26332F",
    }
}


def test_choose_bgm_track_prefers_explicit_canonical_file(tmp_path):
    track = tmp_path / "ether_silent_partner.mp3"
    track.write_bytes(b"music")

    assert _choose_bgm_track(
        tmp_path / "job",
        {"file": str(track)},
    ) == track


def scene_doc() -> dict:
    return {
        "total_duration_sec": 10,
        "scenes": [
            {
                "id": "scene-01",
                "on_screen_text": "Local image",
                "asset_refs": {"background": "assets/scene-01.jpg"},
            }
        ],
    }


def test_prepare_assets_uses_local_directory_image_when_available(tmp_path):
    source_dir = tmp_path / "image-library"
    source_dir.mkdir()
    local_image = source_dir / "scene-01.jpg"
    Image.new("RGB", (640, 360), (12, 34, 56)).save(local_image, quality=90)

    job_dir = tmp_path / "jobs" / "job-1"
    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        scene_doc(),
        visual_config={"strategy": "local_directory", "source_dir": str(source_dir)},
    )

    copied_background = Path(manifest["scenes"][0]["background"])
    assert copied_background.suffix == ".mp4"
    assert copied_background.exists()
    assert copied_background.stat().st_size > 0
    assert manifest["scenes"][0]["source"] == "local_directory"
    assert manifest["scenes"][0]["source_path"] == str(local_image.resolve())


class ExplodingStockClient:
    def search(self, provider, query, filters):
        raise AssertionError("stock API should not be called when a local image exists")

    def normalize(self, provider, response):
        raise AssertionError("stock API should not be called when a local image exists")


def test_prepare_assets_auto_prefers_local_directory_before_stock_api(tmp_path):
    source_dir = tmp_path / "image-library"
    source_dir.mkdir()
    local_image = source_dir / "scene-01.png"
    Image.new("RGB", (640, 360), (12, 34, 56)).save(local_image)

    job_dir = tmp_path / "jobs" / "job-auto-local"
    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        scene_doc(),
        visual_config={
            "strategy": "auto",
            "source_dir": str(source_dir),
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        stock_client=ExplodingStockClient(),
    )

    assert manifest["scenes"][0]["source"] == "local_directory"
    assert manifest["scenes"][0]["background"].endswith("scene-01.mp4")


def test_prepare_assets_records_stock_errors_when_falling_back_to_placeholder(tmp_path):
    class MissingKeyStockClient:
        def search(self, provider, query, filters):
            raise RuntimeError(f"{provider.upper()}_API_KEY is required for provider={provider}")

        def normalize(self, provider, response):
            return []

    job_dir = tmp_path / "jobs" / "job-missing-stock-key"
    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        scene_doc(),
        visual_config={
            "strategy": "auto",
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        stock_client=MissingKeyStockClient(),
        image_gen_fn=lambda p, o: None,
    )

    scene = manifest["scenes"][0]
    assert scene["source"] == "generated_placeholder"
    assert [error["provider"] for error in scene["stock_errors"]] == ["pexels", "pixabay"]
    assert "PEXELS_API_KEY is required" in scene["stock_errors"][0]["message"]


def test_candidate_score_ignores_stopword_matches():
    score = _candidate_score(
        "caminar con los adultos for wellness",
        {
            "width": 640,
            "height": 360,
            "tags": ["with los con for"],
            "quality": "large",
        },
    )

    assert score["matched_terms"] == []
    assert "generic_match_ignored" in score["reasons"]


def test_candidate_score_rewards_scene_term_matches():
    score = _candidate_score(
        "caminar parque sombra zapatos estables",
        {
            "width": 1920,
            "height": 1080,
            "tags": ["older adults walking in park shade with comfortable shoes"],
            "quality": "fullhd",
        },
    )

    assert score["score"] >= 75
    assert "strong_scene_term_match" in score["reasons"]
    assert {"caminar", "parque", "sombra", "zapatos"} & set(score["matched_terms"])


def test_candidate_score_penalizes_unrelated_spa_terms():
    score = _candidate_score(
        "caminar parque sombra zapatos estables",
        {
            "width": 3840,
            "height": 2160,
            "tags": ["spa massage therapy relax"],
            "quality": "fullhd",
        },
    )

    assert "negative_keyword_penalty" in score["reasons"]
    assert "massage" in score["penalized_terms"]


class FakeStockClient:
    def search(self, provider, query, filters):
        return {"photos": [{"id": 6793199}]}

    def normalize(self, provider, response):
        return [
            {
                "provider": "pexels",
                "provider_asset_id": "6793199",
                "media_type": "photo",
                "download_url": "https://example.test/scene.jpg",
                "source_url": "https://www.pexels.com/photo/example-6793199/",
                "width": 640,
                "height": 360,
                "tags": ["sleep"],
                "photographer": "Example Photographer",
                "photographer_url": "https://www.pexels.com/@example",
                "attribution": "Photo by Example Photographer on Pexels",
                "quality": "large2x",
                "license": "Pexels License",
            }
        ]


class FakeDownloadClient:
    def download(self, url, output_path):
        Image.new("RGB", (640, 360), (90, 80, 70)).save(output_path, quality=90)


class FallbackStockClient:
    def search(self, provider, query, filters):
        if provider == "pexels":
            raise RuntimeError("pexels unavailable")
        return {"hits": [{"id": 42}]}

    def normalize(self, provider, response):
        return [
            {
                "provider": "pixabay",
                "provider_asset_id": "42",
                "media_type": "photo",
                "download_url": "https://example.test/pixabay.jpg",
                "source_url": "https://pixabay.com/photos/example-42/",
                "width": 1920,
                "height": 1080,
                "tags": ["calm", "sleep"],
                "photographer": "Pixabay Artist",
                "photographer_url": "https://pixabay.com/users/example-42/",
                "attribution": "Image by Pixabay Artist from Pixabay",
                "quality": "fullhd",
                "license": "Pixabay Content License",
            }
        ]


class MultiProviderStockClient:
    def __init__(self):
        self.searched_providers = []

    def search(self, provider, query, filters):
        self.searched_providers.append(provider)
        if provider == "pexels":
            return {"photos": [{"id": "pexels-low"}]}
        return {"hits": [{"id": "pixabay-high"}]}

    def normalize(self, provider, response):
        if provider == "pexels":
            return [
                {
                    "provider": "pexels",
                    "provider_asset_id": "pexels-low",
                    "media_type": "photo",
                    "download_url": "https://example.test/pexels-low.jpg",
                    "source_url": "https://www.pexels.com/photo/pexels-low/",
                    "width": 640,
                    "height": 360,
                    "tags": ["generic"],
                    "photographer": "Pexels Photographer",
                    "photographer_url": "https://www.pexels.com/@low",
                    "attribution": "Photo by Pexels Photographer on Pexels",
                    "quality": "large",
                    "license": "Pexels License",
                }
            ]
        return [
            {
                "provider": "pixabay",
                "provider_asset_id": "pixabay-high",
                "media_type": "photo",
                "download_url": "https://example.test/pixabay-high.jpg",
                "source_url": "https://pixabay.com/photos/pixabay-high/",
                "width": 3840,
                "height": 2160,
                "tags": ["calm", "sleep", "wellness", "bedroom"],
                "photographer": "Pixabay Artist",
                "photographer_url": "https://pixabay.com/users/example-42/",
                "attribution": "Image by Pixabay Artist from Pixabay",
                "quality": "fullhd",
                "license": "Pixabay Content License",
            }
        ]


def test_prepare_assets_uses_stock_photo_api_and_records_attribution(tmp_path):
    doc = scene_doc()
    doc["scenes"][0]["visual_prompt"] = "calm sleep wellness bedroom"
    job_dir = tmp_path / "jobs" / "job-stock"

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "stock_photo_api",
            "providers": ["pexels"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        channel_id="vida-plena-45",
        stock_client=FakeStockClient(),
        download_client=FakeDownloadClient(),
    )

    scene = manifest["scenes"][0]
    assert Path(scene["background"]).exists()
    assert scene["source"] == "asset_library"
    assert scene["provider"] == "pexels"
    assert scene["provider_asset_id"] == "6793199"
    assert scene["source_url"] == "https://www.pexels.com/photo/example-6793199/"
    assert scene["attribution"] == "Photo by Example Photographer on Pexels"


def test_prepare_assets_falls_back_to_second_stock_provider(tmp_path):
    doc = scene_doc()
    doc["scenes"][0]["visual_prompt"] = "calm sleep wellness bedroom"
    job_dir = tmp_path / "jobs" / "job-provider-fallback"

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "stock_photo_api",
            "providers": ["pexels", "pixabay"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        channel_id="vida-plena-45",
        stock_client=FallbackStockClient(),
        download_client=FakeDownloadClient(),
    )

    scene = manifest["scenes"][0]
    assert scene["source"] == "asset_library"
    assert scene["provider"] == "pixabay"
    assert scene["provider_asset_id"] == "42"


def test_prepare_assets_searches_all_providers_and_selects_best_candidate(tmp_path):
    doc = scene_doc()
    doc["scenes"][0]["visual_prompt"] = "calm sleep wellness bedroom"
    job_dir = tmp_path / "jobs" / "job-provider-ranking"
    stock_client = MultiProviderStockClient()

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "stock_photo_api",
            "providers": ["pexels", "pixabay"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        channel_id="vida-plena-45",
        stock_client=stock_client,
        download_client=FakeDownloadClient(),
    )

    scene = manifest["scenes"][0]
    assert stock_client.searched_providers == ["pexels", "pixabay"]
    assert scene["provider"] == "pixabay"
    assert scene["provider_asset_id"] == "pixabay-high"
    assert scene["asset_selection"]["provider_rank"] == 2
    assert scene["asset_selection"]["searched_providers"] == ["pexels", "pixabay"]
    assert scene["asset_selection"]["candidate_count"] == 2


class VideoMissingKeyFallbackStockClient:
    def __init__(self):
        self.searched_providers = []

    def search(self, provider, query, filters):
        self.searched_providers.append(provider)
        if provider == "pexels_video":
            raise RuntimeError("PEXELS_API_KEY is required for provider=pexels_video")
        if provider == "pexels":
            return {"photos": [{"id": "pexels-fallback"}]}
        return {}

    def normalize(self, provider, response):
        if provider != "pexels":
            return []
        return [
            {
                "provider": "pexels",
                "provider_asset_id": "pexels-fallback",
                "media_type": "photo",
                "download_url": "https://example.test/pexels-fallback.jpg",
                "source_url": "https://www.pexels.com/photo/pexels-fallback/",
                "width": 1920,
                "height": 1080,
                "tags": ["calm", "sleep"],
                "photographer": "Pexels Photographer",
                "photographer_url": "https://www.pexels.com/@fallback",
                "attribution": "Photo by Pexels Photographer on Pexels",
                "quality": "large2x",
                "license": "Pexels License",
            }
        ]


def test_prepare_assets_uses_fallback_providers_when_primary_fails(tmp_path):
    doc = scene_doc()
    doc["scenes"][0]["visual_prompt"] = "calm sleep wellness bedroom"
    job_dir = tmp_path / "jobs" / "job-fallback-providers"
    stock_client = VideoMissingKeyFallbackStockClient()

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "stock_photo_api",
            "providers": ["pexels_video"],
            "photo_providers": ["none"],
            "fallback_providers": ["pexels"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        channel_id="vida-plena-45",
        stock_client=stock_client,
        download_client=FakeDownloadClient(),
    )

    scene = manifest["scenes"][0]
    # primary video (key missing) -> photo provider "none" (empty) -> strict
    # fallback search of pexels finds the strong calm+sleep match, which now wins
    # at the strict tier *before* AI (Tier 2b), so the redundant weak re-search of
    # pexels_video no longer runs.
    assert stock_client.searched_providers == ["pexels_video", "none", "pexels"]
    assert scene["source"] == "asset_library"
    assert scene["provider"] == "pexels"
    assert scene["provider_asset_id"] == "pexels-fallback"
    assert scene["asset_selection"]["fallback"] is True
    assert scene["asset_selection"]["searched_providers"] == ["pexels"]


class RankedFakeStockClient:
    def search(self, provider, query, filters):
        return {"photos": [{"id": "low"}, {"id": "high"}]}

    def normalize(self, provider, response):
        return [
            {
                "provider": "pexels",
                "provider_asset_id": "low",
                "media_type": "photo",
                "download_url": "https://example.test/low.jpg",
                "source_url": "https://www.pexels.com/photo/low/",
                "width": 640,
                "height": 360,
                "tags": ["generic"],
                "photographer": "Low Photographer",
                "photographer_url": "https://www.pexels.com/@low",
                "attribution": "Photo by Low Photographer on Pexels",
                "quality": "large",
                "license": "Pexels License",
            },
            {
                "provider": "pexels",
                "provider_asset_id": "high",
                "media_type": "photo",
                "download_url": "https://example.test/high.jpg",
                "source_url": "https://www.pexels.com/photo/high/",
                "width": 3840,
                "height": 2160,
                "tags": ["calm sleep wellness bedroom"],
                "photographer": "High Photographer",
                "photographer_url": "https://www.pexels.com/@high",
                "attribution": "Photo by High Photographer on Pexels",
                "quality": "large2x",
                "license": "Pexels License",
            },
        ]


def test_prepare_assets_ranks_stock_candidates_and_records_selection_reason(tmp_path):
    doc = scene_doc()
    doc["scenes"][0]["visual_prompt"] = "calm sleep wellness bedroom"
    job_dir = tmp_path / "jobs" / "job-ranked"

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "stock_photo_api",
            "providers": ["pexels"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        channel_id="vida-plena-45",
        stock_client=RankedFakeStockClient(),
        download_client=FakeDownloadClient(),
    )

    scene = manifest["scenes"][0]
    assert scene["provider_asset_id"] == "high"
    assert scene["asset_selection"]["candidate_rank"] == 1
    assert scene["asset_selection"]["score"] > 0
    assert "high_resolution" in scene["asset_selection"]["reasons"]
    assert "tag_match" in scene["asset_selection"]["reasons"]


def test_prepare_assets_auto_uses_stock_photo_api_when_local_image_is_missing(tmp_path):
    doc = scene_doc()
    doc["scenes"][0]["visual_prompt"] = "calm sleep wellness bedroom"
    job_dir = tmp_path / "jobs" / "job-auto-stock"

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "auto",
            "source_dir": str(tmp_path / "missing-image-library"),
            "providers": ["pexels"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        channel_id="vida-plena-45",
        stock_client=FakeStockClient(),
        download_client=FakeDownloadClient(),
    )

    assert manifest["scenes"][0]["source"] == "asset_library"
    assert manifest["scenes"][0]["provider"] == "pexels"


def test_prepare_assets_falls_back_to_placeholder_when_stock_provider_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    doc = scene_doc()
    doc["scenes"][0]["visual_prompt"] = "calm sleep wellness bedroom"
    job_dir = tmp_path / "jobs" / "job-fallback"

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "stock_photo_api",
            "providers": ["pexels"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
        },
        channel_id="vida-plena-45",
        image_gen_fn=lambda p, o: None,
    )

    assert Path(manifest["scenes"][0]["background"]).exists()
    assert manifest["scenes"][0]["source"] == "generated_placeholder"


def test_graphic_scene_fails_when_chatgpt_image_is_not_generated(tmp_path, monkeypatch):
    doc = {
        "total_duration_sec": 4.0,
        "scenes": [
            {
                "id": "s01",
                "layout": "graphic_checklist",
                "duration_sec": 4.0,
                "on_screen_text": "REVISA ESTO",
                "visual_prompt": "premium vertical editorial checklist",
                "layout_payload": {"title": "REVISA ESTO", "items": ["Uno", "Dos"]},
                "asset_refs": {},
            }
        ],
    }

    monkeypatch.setattr(
        "video_agent.stages.assets.StockAssetService.get_scene_asset",
        lambda self, scene, channel_id, job_id: {
            "provider": "graphic_fallback",
            "asset_tier": "graphic_fallback",
            "asset_selection": {"asset_match_status": "graphic_fallback"},
        },
    )

    with pytest.raises(RuntimeError, match=r"s01.*graphic_checklist.*ChatGPT"):
        prepare_assets(
            tmp_path / "shorts" / "short-01",
            STYLE_DNA,
            doc,
            visual_config={
                "strategy": "auto",
                "orientation": "portrait",
                "query_cache_path": str(tmp_path / "query-cache.db"),
                "asset_library_path": str(tmp_path / "asset-library"),
            },
            render_tts=False,
        )


def test_graphic_scene_with_chatgpt_image_becomes_media_layout(tmp_path, monkeypatch):
    generated = tmp_path / "generated.png"
    Image.new("RGB", (1080, 1920), (120, 90, 70)).save(generated)
    doc = {
        "total_duration_sec": 4.0,
        "scenes": [
            {
                "id": "s01",
                "layout": "graphic_comparison",
                "duration_sec": 4.0,
                "on_screen_text": "COMPARA",
                "visual_prompt": "premium vertical editorial comparison",
                "layout_payload": {
                    "title": "COMPARA",
                    "left": {"heading": "MEJOR", "text": "Integral"},
                    "right": {"heading": "REVISA", "text": "Multicereal"},
                },
                "asset_refs": {},
            }
        ],
    }

    monkeypatch.setattr(
        "video_agent.stages.assets.StockAssetService.get_scene_asset",
        lambda self, scene, channel_id, job_id: {
            "provider": "ai_generated",
            "local_path": str(generated),
            "asset_tier": "ai_image",
            "asset_selection": {"asset_match_status": "ai_generated"},
        },
    )

    prepare_assets(
        tmp_path / "shorts" / "short-01",
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "auto",
            "orientation": "portrait",
            "query_cache_path": str(tmp_path / "query-cache.db"),
            "asset_library_path": str(tmp_path / "asset-library"),
        },
        render_tts=False,
    )

    scene = doc["scenes"][0]
    assert scene["layout"] == "short_tip"
    assert scene["generated_image_source_layout"] == "graphic_comparison"
    assert scene["background_mode"] == "generated_image"
    assert scene["asset_refs"]["background"].endswith("/assets/s01.mp4")
