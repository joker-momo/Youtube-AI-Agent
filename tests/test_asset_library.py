from __future__ import annotations

from PIL import Image

from video_agent.assets.library import AssetLibrary


def candidate() -> dict:
    return {
        "provider": "pexels",
        "provider_asset_id": "6793199",
        "media_type": "photo",
        "source_url": "https://www.pexels.com/photo/a-woman-sitting-on-bed-6793199/",
        "width": 6550,
        "height": 4367,
        "tags": ["sleep mask"],
        "photographer": "Yaroslav Shuraev",
        "photographer_url": "https://www.pexels.com/@yaroslav-shuraev",
        "attribution": "Photo by Yaroslav Shuraev on Pexels",
        "quality": "large2x",
    }


def test_asset_library_stores_photo_metadata_and_reuses_existing_file(tmp_path):
    source = tmp_path / "download.jpg"
    Image.new("RGB", (640, 360), (10, 20, 30)).save(source, quality=90)
    library = AssetLibrary(tmp_path / "asset_library")

    first = library.store_photo(candidate(), source, original_query="sleep wellness")
    second = library.store_photo(candidate(), source, original_query="sleep wellness")

    assert first["asset_id"] == second["asset_id"]
    assert (tmp_path / "asset_library" / first["file_path"]).exists()
    assert first["width"] == 640
    assert first["height"] == 360
    assert first["aspect_ratio"] == "16:9"
    assert first["file_hash"] == second["file_hash"]
    assert first["attribution"] == "Photo by Yaroslav Shuraev on Pexels"


def test_asset_library_redownloads_when_existing_file_is_corrupt(tmp_path):
    source = tmp_path / "download.jpg"
    Image.new("RGB", (640, 360), (10, 20, 30)).save(source, quality=90)
    library = AssetLibrary(tmp_path / "asset_library")

    first = library.store_photo(candidate(), source, original_query="sleep wellness")
    stored = tmp_path / "asset_library" / first["file_path"]
    stored.write_bytes(b"corrupt")
    second = library.store_photo(candidate(), source, original_query="sleep wellness")

    assert second["asset_id"] == first["asset_id"]
    assert stored.read_bytes() == source.read_bytes()


def test_asset_library_records_usage_and_increments_count(tmp_path):
    source = tmp_path / "download.jpg"
    Image.new("RGB", (640, 360), (10, 20, 30)).save(source, quality=90)
    library = AssetLibrary(tmp_path / "asset_library")
    asset = library.store_photo(candidate(), source, original_query="sleep wellness")

    library.record_usage(asset["asset_id"], channel_id="vida-plena-45", job_id="job-1", scene_id="scene-01")
    updated = library.get_by_asset_id(asset["asset_id"])

    assert updated is not None
    assert updated["use_count"] == 1
    assert library.list_usage(asset["asset_id"])[0]["scene_id"] == "scene-01"
