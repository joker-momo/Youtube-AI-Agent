from pathlib import Path

import pytest
from PIL import Image

from video_agent.assets.audio_ops import _choose_bgm_track
from video_agent.assets.stock_core import _candidate_score

# The graphic/defer/relabel tests below exercise the SHORT fork — that logic
# moved out of the long stage in P4 (asset-layer decoupling), so they drive the
# short prepare_assets and patch the short ShortSceneResolver, not the long stage.
from video_agent.shorts.assets.prepare import prepare_assets as short_prepare_assets
from video_agent.stages.assets import prepare_assets

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


def test_prepare_assets_graphic_primary_image_does_not_replace_video_background(
    tmp_path, monkeypatch
):
    job_dir = tmp_path / "jobs" / "job-graphic-video-background"
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True)
    primary_image = assets_dir / "scene-01.png"
    Image.new("RGB", (640, 360), (12, 34, 56)).save(primary_image)
    stock_video = tmp_path / "matched-broll.mp4"
    stock_video.write_bytes(b"native-video")
    resolved_scene_ids = []

    def resolve_video(_self, scene, _channel_id, _job_id):
        resolved_scene_ids.append(scene["id"])
        return {
            "local_path": str(stock_video),
            "provider": "pexels_video",
            "provider_asset_id": "video-01",
            "asset_tier": "pexels_video",
            "asset_selection": {"asset_match_status": "strict_match"},
        }

    monkeypatch.setattr(
        "video_agent.stages.assets.StockAssetService.get_scene_asset",
        resolve_video,
    )
    doc = {
        "total_duration_sec": 10,
        "scenes": [
            {
                "id": "scene-01",
                "layout": "warning",
                "duration_sec": 10,
                "on_screen_text": "Graphic foreground",
                "visual_prompt": "mature patient discussing surgery with a clinician",
                "graphic": {
                    "needed": True,
                    "image_ref": "assets/graphic-scene-01.png",
                },
                "asset_refs": {
                    "primary": "assets/scene-01.png",
                    "primary_source": "chatgpt_image",
                },
            }
        ],
    }

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "auto",
            "providers": ["pexels_video"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        render_tts=False,
    )

    scene = manifest["scenes"][0]
    assert resolved_scene_ids == ["scene-01"]
    assert scene["source"] == "asset_library"
    assert scene["provider"] == "pexels_video"
    assert scene["media_kind"] == "video"
    assert doc["scenes"][0]["asset_refs"]["background_media_kind"] == "video"


def test_prepare_assets_non_graphic_primary_image_still_overrides_stock(
    tmp_path,
):
    job_dir = tmp_path / "jobs" / "job-primary-image-background"
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True)
    primary_image = assets_dir / "scene-01.png"
    Image.new("RGB", (640, 360), (12, 34, 56)).save(primary_image)
    doc = {
        "total_duration_sec": 10,
        "scenes": [
            {
                "id": "scene-01",
                "layout": "subtitle",
                "duration_sec": 10,
                "on_screen_text": "Primary image background",
                "asset_refs": {
                    "primary": "assets/scene-01.png",
                    "primary_source": "chatgpt_image",
                },
            }
        ],
    }

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "auto",
            "providers": ["pexels_video"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        stock_client=ExplodingStockClient(),
        render_tts=False,
    )

    scene = manifest["scenes"][0]
    assert scene["source"] == "asset_refs_primary"
    assert scene["media_kind"] == "image"


def test_prepare_assets_graphic_native_video_primary_stays_preferred(
    tmp_path,
):
    job_dir = tmp_path / "jobs" / "job-graphic-native-video"
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True)
    primary_video = assets_dir / "scene-01.mp4"
    primary_video.write_bytes(b"native-video")
    doc = {
        "total_duration_sec": 10,
        "scenes": [
            {
                "id": "scene-01",
                "duration_sec": 10,
                "on_screen_text": "Graphic over native video",
                "graphic": {"needed": True},
                "asset_refs": {
                    "primary": "assets/scene-01.mp4",
                    "primary_source": "external_video",
                },
            }
        ],
    }

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "auto",
            "providers": ["pexels_video"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        stock_client=ExplodingStockClient(),
        render_tts=False,
    )

    scene = manifest["scenes"][0]
    assert scene["source"] == "asset_refs_primary"
    assert scene["source_path"] == str(primary_video.resolve())
    assert scene["media_kind"] == "video"
    assert doc["scenes"][0]["asset_refs"]["background_media_kind"] == "video"


@pytest.mark.parametrize("stock_result", [None, {"provider": "graphic_fallback"}])
def test_prepare_assets_graphic_primary_image_is_fallback_when_stock_has_no_video(
    tmp_path, monkeypatch, stock_result
):
    job_dir = tmp_path / "jobs" / "job-graphic-primary-fallback"
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True)
    primary_image = assets_dir / "scene-01.png"
    Image.new("RGB", (640, 360), (12, 34, 56)).save(primary_image)

    monkeypatch.setattr(
        "video_agent.stages.assets.StockAssetService.get_scene_asset",
        lambda _self, _scene, _channel_id, _job_id: stock_result,
    )
    doc = {
        "total_duration_sec": 10,
        "scenes": [
            {
                "id": "scene-01",
                "duration_sec": 10,
                "on_screen_text": "Graphic with image fallback",
                "graphic": {"needed": True},
                "asset_refs": {
                    "primary": "assets/scene-01.png",
                    "primary_source": "chatgpt_image",
                },
            }
        ],
    }

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "auto",
            "providers": ["pexels_video"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        render_tts=False,
    )

    scene = manifest["scenes"][0]
    assert scene["source"] == "asset_refs_primary"
    assert scene["source_path"] == str(primary_image.resolve())
    assert scene["media_kind"] == "image"
    assert doc["scenes"][0]["asset_refs"]["background_media_kind"] == "image"


def test_prepare_assets_graphic_local_directory_keeps_accurate_source_label(
    tmp_path,
):
    job_dir = tmp_path / "jobs" / "job-graphic-local-directory"
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True)
    primary_image = assets_dir / "scene-01.png"
    Image.new("RGB", (640, 360), (12, 34, 56)).save(primary_image)
    source_dir = tmp_path / "image-library"
    source_dir.mkdir()
    local_image = source_dir / "scene-01.jpg"
    Image.new("RGB", (640, 360), (65, 43, 21)).save(local_image)
    doc = {
        "total_duration_sec": 10,
        "scenes": [
            {
                "id": "scene-01",
                "duration_sec": 10,
                "on_screen_text": "Graphic over local background",
                "graphic": {"needed": True},
                "asset_refs": {
                    "primary": "assets/scene-01.png",
                    "primary_source": "chatgpt_image",
                },
            }
        ],
    }

    manifest = prepare_assets(
        job_dir,
        STYLE_DNA,
        doc,
        visual_config={
            "strategy": "auto",
            "source_dir": str(source_dir),
            "providers": ["pexels_video"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        stock_client=ExplodingStockClient(),
        render_tts=False,
    )

    scene = manifest["scenes"][0]
    assert scene["source"] == "local_directory"
    assert scene["source_path"] == str(local_image.resolve())
    assert scene["media_kind"] == "image"


def test_prepare_assets_full_pass_does_not_partially_replace_assets_when_interrupted(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    (workspace / "remotion").mkdir(parents=True)
    job_dir = workspace / "jobs" / "job-interrupted-assets"
    assets_dir = job_dir / "assets"
    public_assets_dir = (
        workspace / "remotion" / "public" / "jobs" / job_dir.name / "assets"
    )
    assets_dir.mkdir(parents=True)
    public_assets_dir.mkdir(parents=True)

    old_assets = {}
    for scene_id in ("scene-01", "scene-02"):
        payload = f"old-{scene_id}".encode()
        old_assets[scene_id] = payload
        (assets_dir / f"{scene_id}.mp4").write_bytes(payload)
        (public_assets_dir / f"{scene_id}.mp4").write_bytes(payload)

    new_video = tmp_path / "new-scene-01.mp4"
    new_video.write_bytes(b"new-scene-01")

    def interrupt_on_second_scene(_self, scene, _channel_id, _job_id):
        if scene["id"] == "scene-01":
            return {
                "local_path": str(new_video),
                "provider": "pexels",
                "asset_tier": "pexels_video",
            }
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "video_agent.stages.assets.StockAssetService.get_scene_asset",
        interrupt_on_second_scene,
    )

    scenes = {
        "total_duration_sec": 20,
        "scenes": [
            {
                "id": scene_id,
                "duration_sec": 10,
                "on_screen_text": scene_id,
                "asset_refs": {},
            }
            for scene_id in ("scene-01", "scene-02")
        ],
    }

    with pytest.raises(KeyboardInterrupt):
        prepare_assets(
            job_dir,
            STYLE_DNA,
            scenes,
            visual_config={
                "strategy": "auto",
                "query_cache_path": str(tmp_path / "query-cache.db"),
                "asset_library_path": str(tmp_path / "asset-library"),
            },
            render_tts=False,
        )

    for scene_id, payload in old_assets.items():
        assert (assets_dir / f"{scene_id}.mp4").read_bytes() == payload
        assert (public_assets_dir / f"{scene_id}.mp4").read_bytes() == payload


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
    from video_agent.assets import providers

    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.setattr(providers, "load_env", lambda: None)
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
        "video_agent.shorts.assets.scene_resolver.ShortSceneResolver.get_scene_asset",
        lambda self, scene, channel_id, job_id: {
            "provider": "graphic_fallback",
            "asset_tier": "graphic_fallback",
            "asset_selection": {"asset_match_status": "graphic_fallback"},
        },
    )

    with pytest.raises(RuntimeError, match=r"s01.*graphic_checklist.*ChatGPT"):
        short_prepare_assets(
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


def test_defer_graphic_ai_skips_chatgpt_and_makes_placeholder(tmp_path, monkeypatch):
    """Step 5: with defer_graphic_ai=True the background pass must NOT force a
    ChatGPT image for a graphic scene (no RequiredGeneratedImageError); it lays
    down a placeholder so the post-QA unified pass can generate every needed
    image in one batch."""
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
        "video_agent.shorts.assets.scene_resolver.ShortSceneResolver.get_scene_asset",
        lambda self, scene, channel_id, job_id: {
            "provider": "graphic_fallback",
            "asset_tier": "graphic_fallback",
            "asset_selection": {"asset_match_status": "graphic_fallback"},
        },
    )

    manifest = short_prepare_assets(
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
        defer_graphic_ai=True,
    )

    scene = manifest["scenes"][0]
    # Deferred: still a graphic layout (not converted yet), no ChatGPT image.
    assert doc["scenes"][0]["layout"] == "graphic_checklist"
    assert scene["source"] == "generated_placeholder"
    assert Path(scene["background"]).exists()


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
        "video_agent.shorts.assets.scene_resolver.ShortSceneResolver.get_scene_asset",
        lambda self, scene, channel_id, job_id: {
            "provider": "ai_generated",
            "local_path": str(generated),
            "asset_tier": "ai_image",
            "asset_selection": {"asset_match_status": "ai_generated"},
        },
    )

    short_prepare_assets(
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


# --------------------------------------------------------------------------- #
# Long-form semantic source-window selection (spec 2026-09-24)
# --------------------------------------------------------------------------- #
import json as _json  # noqa: E402
import subprocess as _subprocess  # noqa: E402
from types import SimpleNamespace as _NS  # noqa: E402

import video_agent.stages.assets as assets_stage  # noqa: E402
from video_agent.visual.source_window_selection import (  # noqa: E402
    SCENE_TRIM_KEYS,
    LongSourceWindowSelectionError,
    SourceWindowPolicyError,
)

SOURCE_WINDOW_POLICY = {
    "enabled": True,
    "min_surplus_sec": 6.0,
    "max_candidate_windows": 12,
    "technical_samples_per_window": 3,
    "semantic_top_k": 3,
    "semantic_samples_per_window": 3,
    "min_score": 70.0,
    "weights": {"semantic": 0.60, "technical": 0.20, "motion": 0.10, "continuity": 0.10},
    "semantic": {
        "adapter": "clip",
        "device": "auto",
        "model": "google/siglip2-base-patch16-224",
        "enforce_age_band_45_plus": True,
    },
}


def _synthetic_video(path: Path, *, black_sec: float, content_sec: float) -> Path:
    """Local synthetic fixture: optional black lead, then moving test pattern."""
    inputs = []
    if black_sec > 0:
        inputs += ["-f", "lavfi", "-i", f"color=c=black:s=320x180:r=30:d={black_sec}"]
    inputs += ["-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={content_sec}"]
    n = 2 if black_sec > 0 else 1
    concat = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p"
    _subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
         "-filter_complex", concat, "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True,
    )
    return path


class _FakeSiglip:
    name = "siglip"

    def __init__(self, status: str = "SUPPORTED", confidence: float = 2.0) -> None:
        self.status = status
        self.confidence = confidence
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    def evaluate(self, images, *, required_tags, forbidden_tags, visual_intent, asset_id):
        self.calls.append({"n_images": len(images), "visual_intent": visual_intent,
                           "required_tags": required_tags, "forbidden_tags": forbidden_tags})
        return [{"requirement": "topic:visual_intent", "status": self.status,
                 "confidence": self.confidence}]


def _install_fake_semantics(monkeypatch, adapter: _FakeSiglip | None) -> list[dict]:
    built: list[dict] = []

    def fake_build(local_qa_cfg):
        built.append(dict(local_qa_cfg))
        return None if adapter is None else _NS(adapters=[adapter])

    monkeypatch.setattr(assets_stage, "build_semantic_analyzer", fake_build)
    return built


def _long_job(tmp_path: Path, *, black_sec: float = 3.0, content_sec: float = 14.0,
              scene_sec: float = 4.0, extra_scene: dict | None = None) -> tuple[Path, dict]:
    job_dir = tmp_path / "jobs" / "long-job"
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True)
    _synthetic_video(assets_dir / "source-01.mp4", black_sec=black_sec, content_sec=content_sec)
    scenes = [{
        "id": "scene-01",
        "duration_sec": scene_sec,
        "on_screen_text": "Camina despacio",
        "visual_prompt": "Older adult walking slowly through a bright hallway",
        "asset_refs": {"primary": "assets/source-01.mp4", "primary_source": "external_video"},
    }]
    if extra_scene:
        scenes.append(extra_scene)
    return job_dir, {"total_duration_sec": int(scene_sec), "scenes": scenes}


def _prepare_long(job_dir: Path, doc: dict, **kwargs):
    return prepare_assets(
        job_dir, STYLE_DNA, doc,
        visual_config={"strategy": "local_directory", "source_dir": str(job_dir / "no-library")},
        render_tts=False,
        render_fps=30,
        render_resolution="1920x1080",
        **kwargs,
    )


def _report(job_dir: Path) -> dict:
    return _json.loads((job_dir / "json" / "source_window_selection.json").read_text())


def _events(job_dir: Path) -> list[dict]:
    path = job_dir / "events.jsonl"
    if not path.exists():
        return []
    return [_json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_prepare_assets_writes_nonzero_trim_and_provenance(tmp_path, monkeypatch):
    adapter = _FakeSiglip()
    built = _install_fake_semantics(monkeypatch, adapter)
    job_dir, doc = _long_job(tmp_path)

    _prepare_long(job_dir, doc, source_window_selection=SOURCE_WINDOW_POLICY)

    scene = _json.loads((job_dir / "json" / "scenes.json").read_text())["scenes"][0]
    refs = scene["asset_refs"]
    report = _report(job_dir)
    item = report["items"][0]
    assert item["status"] == "selected"
    # The 0-3s black lead is never chosen: the window starts after it.
    assert refs["source_trim_before_in_frames"] >= 90
    assert refs["source_trim_before_in_frames"] == item["selected_window_start_in_frames"]
    assert refs["source_trim_end_in_frames"] == item["selected_window_end_in_frames"]
    assert refs["source_trim_timebase_fps"] == 30 == report["fps"]
    assert item["required_duration_in_frames"] == 120 + 15
    assert item["asset_ref"] == "jobs/long-job/assets/scene-01.mp4"
    assert item["technical_candidate_count"] <= 12 and item["semantic_candidate_count"] <= 3
    assert report["aggregate"]["selected_count"] == 1
    # Semantic evidence is per frame and driven by the scene's English visual prompt.
    assert adapter.calls and all(c["n_images"] == 1 for c in adapter.calls)
    assert {c["visual_intent"] for c in adapter.calls} == {doc["scenes"][0]["visual_prompt"]}
    assert built == [{
        "semantic_adapter": "clip", "device": "auto",
        "semantic_models": {"siglip": "google/siglip2-base-patch16-224"},
        "enforce_age_band_45_plus": True,
    }]
    text = _json.dumps(report)
    assert str(tmp_path) not in text
    events = [e for e in _events(job_dir) if e["event"].startswith("LONG_SOURCE_WINDOW_")]
    assert [e["event"] for e in events] == ["LONG_SOURCE_WINDOW_SELECTED"]
    data = events[0]["data"]
    assert data["job_id"] == "long-job" and data["scene_id"] == "scene-01"
    assert data["selected_window_start_in_frames"] == item["selected_window_start_in_frames"]
    assert data["asset_ref"] == item["asset_ref"]


def test_unavailable_semantic_adapter_fails_closed_for_eligible_source(tmp_path, monkeypatch):
    _install_fake_semantics(monkeypatch, None)
    job_dir, doc = _long_job(tmp_path)

    with pytest.raises(LongSourceWindowSelectionError, match="scene-01") as excinfo:
        _prepare_long(job_dir, doc, source_window_selection=SOURCE_WINDOW_POLICY)

    assert excinfo.value.asset_ref == "jobs/long-job/assets/scene-01.mp4"
    assert excinfo.value.rejected_window_counts.get("semantic_capability_unavailable", 0) >= 1
    item = _report(job_dir)["items"][0]
    assert item["status"] == "rejected"
    assert not any(k in doc["scenes"][0]["asset_refs"] for k in SCENE_TRIM_KEYS)
    assert not (job_dir / "json" / "assets_manifest.json").exists()
    assert [e["event"] for e in _events(job_dir) if e["event"].startswith("LONG_SOURCE")] == [
        "LONG_SOURCE_WINDOW_REJECTED"
    ]


def test_contradicted_semantics_fail_closed_instead_of_rendering_frame_zero(tmp_path, monkeypatch):
    _install_fake_semantics(monkeypatch, _FakeSiglip(status="CONTRADICTED", confidence=-2.0))
    job_dir, doc = _long_job(tmp_path)

    with pytest.raises(LongSourceWindowSelectionError, match="scene-01"):
        _prepare_long(job_dir, doc, source_window_selection=SOURCE_WINDOW_POLICY)
    assert _report(job_dir)["items"][0]["rejected_window_counts"]["semantic_contradicted"] >= 1


def test_insufficient_headroom_is_explicit_skip_without_model_or_trim(tmp_path, monkeypatch):
    def explode(_cfg):
        raise AssertionError("semantic model must not load without an eligible source")

    monkeypatch.setattr(assets_stage, "build_semantic_analyzer", explode)
    job_dir, doc = _long_job(tmp_path, black_sec=0.0, content_sec=8.0, scene_sec=4.0)
    doc["scenes"][0]["asset_refs"]["source_trim_before_in_frames"] = 300  # stale

    _prepare_long(job_dir, doc, source_window_selection=SOURCE_WINDOW_POLICY)

    item = _report(job_dir)["items"][0]
    assert item["status"] == "skipped" and item["reason"] == "insufficient_headroom"
    assert item["source_duration_sec"] == pytest.approx(8.0, abs=0.1)
    assert not any(k in doc["scenes"][0]["asset_refs"] for k in SCENE_TRIM_KEYS)
    assert [e["event"] for e in _events(job_dir) if e["event"].startswith("LONG_SOURCE")] == [
        "LONG_SOURCE_WINDOW_SKIPPED"
    ]


def test_non_native_scenes_are_skipped_with_single_reason(tmp_path, monkeypatch):
    _install_fake_semantics(monkeypatch, _FakeSiglip())
    image = tmp_path / "photo.jpg"
    Image.new("RGB", (640, 360), (120, 90, 60)).save(image)
    job_dir, doc = _long_job(tmp_path)
    (job_dir / "assets" / "photo.jpg").write_bytes(image.read_bytes())
    doc["scenes"].append({
        "id": "scene-02", "duration_sec": 4.0, "on_screen_text": "Foto",
        "asset_refs": {"primary": "assets/photo.jpg"},
    })
    doc["scenes"].append({
        "id": "scene-03", "duration_sec": 4.0, "on_screen_text": "Tarjeta",
        "graphic": {"needed": True, "image_ref": "jobs/long-job/assets/card.png"},
        "asset_refs": {"primary": "assets/source-01.mp4", "primary_source": "external_video"},
    })
    doc["scenes"].append({"id": "scene-04", "duration_sec": 4.0, "on_screen_text": "Nada",
                          "asset_refs": {}})

    _prepare_long(job_dir, doc, source_window_selection=SOURCE_WINDOW_POLICY)

    items = {i["scene_id"]: i for i in _report(job_dir)["items"]}
    assert list(items) == ["scene-01", "scene-02", "scene-03", "scene-04"]
    assert items["scene-01"]["status"] == "selected"
    for sid in ("scene-02", "scene-03", "scene-04"):
        assert items[sid]["status"] == "skipped"
        assert items[sid]["reason"] == "non_native_video"
        assert not any(k in doc["scenes"][int(sid[-1]) - 1]["asset_refs"] for k in SCENE_TRIM_KEYS)
    agg = _report(job_dir)["aggregate"]
    assert (agg["eligible_count"], agg["selected_count"], agg["skipped_count"]) == (1, 1, 3)


def test_invalid_source_window_policy_fails_before_media_or_model_work(tmp_path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("no media/model work before policy validation")

    monkeypatch.setattr(assets_stage, "materialize_media", explode)
    monkeypatch.setattr(assets_stage, "build_semantic_analyzer", explode)
    monkeypatch.setattr(assets_stage, "FfmpegWindowSampler", explode)
    job_dir, doc = _long_job(tmp_path)
    bad = {**SOURCE_WINDOW_POLICY, "semantic_top_k": 20}

    with pytest.raises(SourceWindowPolicyError, match=r"visual\.source_window_selection\.semantic_top_k"):
        _prepare_long(job_dir, doc, source_window_selection=bad)


def test_channel_without_source_window_block_keeps_legacy_contract(tmp_path, monkeypatch):
    def explode(_cfg):
        raise AssertionError("selection must not run without the channel block")

    monkeypatch.setattr(assets_stage, "build_semantic_analyzer", explode)
    job_dir, doc = _long_job(tmp_path)

    _prepare_long(job_dir, doc)

    assert not (job_dir / "json" / "source_window_selection.json").exists()
    assert not any(k in doc["scenes"][0]["asset_refs"] for k in SCENE_TRIM_KEYS)
    assert not [e for e in _events(job_dir) if e["event"].startswith("LONG_SOURCE")]
