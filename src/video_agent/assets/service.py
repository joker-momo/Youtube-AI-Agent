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


def _query_terms(query: str) -> set[str]:
    return {term.lower() for term in query.replace(",", " ").split() if len(term) > 2}


def _candidate_score(query: str, candidate: dict[str, Any]) -> dict[str, Any]:
    score = 0
    reasons = []
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    if width >= 1920 and height >= 1080:
        score += 40
        reasons.append("high_resolution")
    elif width >= 1280 and height >= 720:
        score += 20
        reasons.append("usable_resolution")

    if width and height:
        ratio = width / height
        if abs(ratio - 16 / 9) < 0.12:
            score += 15
            reasons.append("landscape_16_9")

    tags_text = " ".join(str(tag).lower() for tag in candidate.get("tags") or [])
    overlap = sorted(term for term in _query_terms(query) if term in tags_text)
    if overlap:
        score += min(30, len(overlap) * 8)
        reasons.append("tag_match")

    if candidate.get("quality") in {"large2x", "fullhd", "original"}:
        score += 10
        reasons.append("preferred_quality")

    return {"score": score, "reasons": reasons or ["provider_order"], "matched_terms": overlap}


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
                candidates = self._rank_candidates(query, self.stock_client.normalize(provider, response))
            except Exception:
                continue
            for rank, ranked_candidate in enumerate(candidates, start=1):
                candidate = ranked_candidate["candidate"]
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
                asset["asset_selection"] = {
                    "query": query,
                    "candidate_rank": rank,
                    "score": ranked_candidate["score"],
                    "reasons": ranked_candidate["reasons"],
                    "matched_terms": ranked_candidate["matched_terms"],
                }
                return asset
        return None

    def _rank_candidates(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked = []
        for provider_index, candidate in enumerate(candidates):
            scoring = _candidate_score(query, candidate)
            ranked.append({"candidate": candidate, "provider_index": provider_index, **scoring})
        return sorted(ranked, key=lambda item: (item["score"], -item["provider_index"]), reverse=True)

    def _ensure_asset(self, candidate: dict[str, Any], query: str) -> dict[str, Any]:
        existing = self.library.get_by_provider_id(candidate["provider"], str(candidate["provider_asset_id"]))
        if existing and self.library.is_file_valid(existing):
            return existing

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "download.jpg"
            self.download_client.download(candidate["download_url"], temp_path)
            return self.library.store_photo(candidate, temp_path, original_query=query)
