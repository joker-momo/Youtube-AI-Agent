from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol
from urllib.request import Request, urlopen

from video_agent.assets.library import AssetLibrary
from video_agent.assets.providers import DEFAULT_HEADERS, StockPhotoClient
from video_agent.assets.query_cache import QueryCache
from video_agent.contracts import repo_root


class DownloadClient(Protocol):
    def download(self, url: str, output_path: Path) -> None: ...


class UrlDownloadClient:
    def download(self, url: str, output_path: Path) -> None:
        request = Request(url, headers=DEFAULT_HEADERS)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(request, timeout=60) as response, output_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def _resolve_project_path(value: str | None, default: str) -> Path:
    path = Path(value or default)
    if not path.is_absolute():
        path = repo_root() / path
    return path


def _stock_filters(visual_config: dict[str, Any]) -> dict[str, Any]:
    orientation = visual_config.get("orientation", "landscape")
    if orientation == "horizontal":
        orientation = "landscape"
    return {
        "orientation": orientation,
        "per_page": int(visual_config.get("per_page", 10)),
    }


class StockAssetService:
    def __init__(
        self,
        visual_config: dict[str, Any],
        stock_client: StockPhotoClient | None = None,
        download_client: DownloadClient | None = None,
    ) -> None:
        self.visual_config = visual_config
        self.providers = list(visual_config.get("providers") or ["pexels", "pixabay"])
        self.cache = QueryCache(
            _resolve_project_path(visual_config.get("query_cache_path"), "caches/query_cache.db")
        )
        self.library = AssetLibrary(
            _resolve_project_path(visual_config.get("asset_library_path"), "asset_library")
        )
        self.stock_client = stock_client or StockPhotoClient()
        self.download_client = download_client or UrlDownloadClient()
        self.used_provider_ids: set[tuple[str, str]] = set()

    def get_scene_asset(self, scene: dict[str, Any], channel_id: str, job_id: str) -> dict[str, Any] | None:
        query = scene.get("visual_prompt") or scene.get("on_screen_text") or ""
        filters = _stock_filters(self.visual_config)
        ttl_hours = int(self.visual_config.get("query_cache_ttl_hours", 24))
        for provider in self.providers:
            try:
                response = self.cache.get(provider, query, filters)
                if response is None:
                    response = self.stock_client.search(provider, query, filters)
                    self.cache.set(provider, query, filters, response, ttl_hours=ttl_hours)
                candidates = self.stock_client.normalize(provider, response)
            except Exception:
                continue
            for candidate in candidates:
                key = (candidate["provider"], str(candidate["provider_asset_id"]))
                if key in self.used_provider_ids:
                    continue
                try:
                    asset = self._ensure_asset(candidate, query)
                except Exception:
                    continue
                self.used_provider_ids.add(key)
                self.library.record_usage(
                    asset["asset_id"],
                    channel_id=channel_id,
                    job_id=job_id,
                    scene_id=scene["id"],
                    scene_intent=scene.get("motion"),
                )
                return asset
        return None

    def _ensure_asset(self, candidate: dict[str, Any], query: str) -> dict[str, Any]:
        existing = self.library.get_by_provider_id(candidate["provider"], str(candidate["provider_asset_id"]))
        if existing and self.library.is_file_valid(existing):
            return existing

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "download.jpg"
            self.download_client.download(candidate["download_url"], temp_path)
            return self.library.store_photo(candidate, temp_path, original_query=query)
