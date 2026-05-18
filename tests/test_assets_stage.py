from pathlib import Path

from PIL import Image

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
    assert copied_background.read_bytes() == local_image.read_bytes()
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
    assert manifest["scenes"][0]["background"].endswith("scene-01.png")


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


def test_prepare_assets_falls_back_to_placeholder_when_stock_provider_fails(tmp_path):
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
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        channel_id="vida-plena-45",
    )

    assert Path(manifest["scenes"][0]["background"]).exists()
    assert manifest["scenes"][0]["source"] == "generated_placeholder"
