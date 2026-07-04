from __future__ import annotations

import time

from PIL import Image

import video_agent.assets.library as library_module
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


def test_is_file_valid_caches_hash_for_unchanged_file(tmp_path, monkeypatch):
    """bug-470: is_file_valid() used to re-hash the full file on EVERY call.
    Render's asset selection calls this once per (scene, candidate) -- with 85
    scenes and overlapping candidate pools, the same handful of library videos
    were being SHA256'd dozens of times per render, turning pre-Remotion asset
    prep into 20+ minutes of pure re-hashing. Repeat validation of an unchanged
    file (same size + mtime) must hit the cache, not re-hash."""
    source = tmp_path / "download.jpg"
    Image.new("RGB", (640, 360), (10, 20, 30)).save(source, quality=90)
    library = AssetLibrary(tmp_path / "asset_library")
    asset = library.store_photo(candidate(), source, original_query="sleep wellness")

    calls = {"n": 0}
    real_sha256 = library_module._sha256

    def _counting_sha256(path):
        calls["n"] += 1
        return real_sha256(path)

    monkeypatch.setattr(library_module, "_sha256", _counting_sha256)

    assert library.is_file_valid(asset) is True
    assert library.is_file_valid(asset) is True
    assert library.is_file_valid(asset) is True
    assert calls["n"] == 1  # only the first call actually hashed the file


def test_is_file_valid_rehashes_after_file_changes_on_disk(tmp_path, monkeypatch):
    """A cached validity result must not survive an actual on-disk change --
    the cache key is (size, mtime), so overwriting the file forces a real
    re-hash instead of trusting a stale cached True/False."""
    source = tmp_path / "download.jpg"
    Image.new("RGB", (640, 360), (10, 20, 30)).save(source, quality=90)
    library = AssetLibrary(tmp_path / "asset_library")
    asset = library.store_photo(candidate(), source, original_query="sleep wellness")

    calls = {"n": 0}
    real_sha256 = library_module._sha256

    def _counting_sha256(path):
        calls["n"] += 1
        return real_sha256(path)

    monkeypatch.setattr(library_module, "_sha256", _counting_sha256)

    assert library.is_file_valid(asset) is True
    assert calls["n"] == 1

    stored = tmp_path / "asset_library" / asset["file_path"]
    time.sleep(0.01)
    stored.write_bytes(b"corrupt-but-different-size-and-mtime")

    assert library.is_file_valid(asset) is False  # size/hash actually changed
    assert calls["n"] == 2  # forced a real re-hash, did not trust the cache


def test_search_by_query_hashes_only_scored_candidates_not_whole_library(tmp_path, monkeypatch):
    """bug-470 follow-up (real stall, 2026-07-04): search_by_query used to run
    is_file_valid (full SHA256) on EVERY non-excluded library row BEFORE
    scoring -- the first call in a run swept the whole library (1000+ videos,
    ~15GB) through the hasher and stalled asset prep ~10 min at one scene.
    Validation must happen AFTER scoring, only on ranked candidates, stopping
    at `limit` -- zero-overlap assets must never be hashed."""
    library = AssetLibrary(tmp_path / "asset_library")

    # One asset that MATCHES the query, several that don't overlap at all.
    for i, query in enumerate(
        ["sleep wellness bedroom", "airplane takeoff runway", "racing car engine", "city skyline drone"]
    ):
        source = tmp_path / f"download{i}.jpg"
        Image.new("RGB", (640, 360), (10 * i, 20, 30)).save(source, quality=90)
        cand = candidate() | {"provider_asset_id": str(1000 + i), "tags": query.split()}
        library.store_photo(cand, source, original_query=query)

    calls: list[str] = []
    real_sha256 = library_module._sha256

    def _counting_sha256(path):
        calls.append(str(path))
        return real_sha256(path)

    monkeypatch.setattr(library_module, "_sha256", _counting_sha256)
    library._valid_cache.clear()  # simulate a fresh run (store_photo warmed it)

    results = library.search_by_query("sleep wellness", media_type="photo", limit=10)

    assert len(results) == 1
    assert results[0]["original_query"] == "sleep wellness bedroom"
    # Only the single scored candidate was hashed -- not the 3 zero-overlap assets.
    assert len(calls) == 1


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
