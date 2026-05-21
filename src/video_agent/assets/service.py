from __future__ import annotations

import shutil
import tempfile
import re
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


def _stock_filters(visual_config: dict[str, Any], scene_duration_sec: int | None = None) -> dict[str, Any]:
    orientation = visual_config.get("orientation", "landscape")
    if orientation == "horizontal":
        orientation = "landscape"
    filters: dict[str, Any] = {
        "orientation": orientation,
        "per_page": int(visual_config.get("per_page", 10)),
    }
    if scene_duration_sec is not None:
        # Request clips at least as long as the scene so we don't need to loop
        filters["min_duration_sec"] = max(10, scene_duration_sec)
    return filters


STOPWORDS = {
    "45",
    "adult",
    "adults",
    "after",
    "antes",
    "con",
    "del",
    "despues",
    "dia",
    "for",
    "from",
    "las",
    "los",
    "para",
    "por",
    "the",
    "una",
    "with",
}

NEGATIVE_CONTEXT_TERMS = {"massage", "masaje", "spa", "therapy", "terapia"}
NEGATIVE_ALLOW_TERMS = {"massage", "masaje", "spa", "therapy", "terapia", "therapist", "fisioterapia"}
TERM_SYNONYMS = {
    "agua": {"water"},
    "avena": {"oat", "oats", "oatmeal"},
    "botella": {"bottle"},
    "caminar": {"walk", "walking"},
    "cama": {"bed", "bedroom"},
    "cena": {"dinner"},
    "desayuno": {"breakfast"},
    "dormir": {"sleep", "sleeping"},
    "fruta": {"fruit"},
    "luz": {"light"},
    "parque": {"park"},
    "sombra": {"shade"},
    "zapatos": {"shoe", "shoes"},
}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ0-9]+", text.lower()) if len(token) > 2}


def _query_terms(query: str) -> set[str]:
    return {term for term in _tokens(query) if term not in STOPWORDS}


def _term_matches_tags(term: str, tag_terms: set[str]) -> bool:
    if term in tag_terms:
        return True
    return bool(TERM_SYNONYMS.get(term, set()) & tag_terms)


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
    tag_terms = _tokens(tags_text)
    raw_overlap = sorted(term for term in _tokens(query) if term in tag_terms)
    overlap = sorted(term for term in _query_terms(query) if _term_matches_tags(term, tag_terms))
    if overlap:
        score += min(30, len(overlap) * 8)
        reasons.append("tag_match")
        if len(overlap) >= 2:
            score += min(20, len(overlap) * 5)
            reasons.append("strong_scene_term_match")
    elif raw_overlap:
        reasons.append("generic_match_ignored")

    query_terms = _query_terms(query)
    penalized_terms = sorted(NEGATIVE_CONTEXT_TERMS & tag_terms)
    if penalized_terms and not query_terms.intersection(NEGATIVE_ALLOW_TERMS):
        score -= 25
        reasons.append("negative_keyword_penalty")

    if candidate.get("quality") in {"large2x", "fullhd", "original"}:
        score += 10
        reasons.append("preferred_quality")

    return {
        "score": score,
        "reasons": reasons or ["provider_order"],
        "matched_terms": overlap,
        "penalized_terms": penalized_terms,
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
        self.last_errors: list[dict[str, str]] = []

    def get_scene_asset(self, scene: dict[str, Any], channel_id: str, job_id: str) -> dict[str, Any] | None:
        query = scene.get("visual_prompt") or scene.get("on_screen_text") or ""
        scene_dur = int(scene.get("duration_sec") or 30)
        filters = _stock_filters(self.visual_config, scene_duration_sec=scene_dur)
        ttl_hours = int(self.visual_config.get("query_cache_ttl_hours", 24))
        ranked_candidates = []
        self.last_errors = []
        for provider_order, provider in enumerate(self.providers, start=1):
            try:
                response = self.cache.get(provider, query, filters)
                if response is None:
                    response = self.stock_client.search(provider, query, filters)
                    self.cache.set(provider, query, filters, response, ttl_hours=ttl_hours)
                candidates = self._rank_candidates(
                    query,
                    self.stock_client.normalize(provider, response),
                    provider_order=provider_order,
                )
                ranked_candidates.extend(candidates)
            except Exception as exc:
                self.last_errors.append(
                    {
                        "provider": provider,
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                    }
                )
                continue
        ranked_candidates = sorted(
            ranked_candidates,
            key=lambda item: (-item["score"], item["provider_order"], item["provider_candidate_rank"]),
        )
        for rank, ranked_candidate in enumerate(ranked_candidates, start=1):
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
                "provider_rank": ranked_candidate["provider_order"],
                "provider_candidate_rank": ranked_candidate["provider_candidate_rank"],
                "searched_providers": self.providers,
                "candidate_count": len(ranked_candidates),
                "score": ranked_candidate["score"],
                "reasons": ranked_candidate["reasons"],
                "matched_terms": ranked_candidate["matched_terms"],
            }
            return asset
        return None

    def _rank_candidates(
        self, query: str, candidates: list[dict[str, Any]], provider_order: int
    ) -> list[dict[str, Any]]:
        ranked = []
        for candidate_index, candidate in enumerate(candidates, start=1):
            scoring = _candidate_score(query, candidate)
            ranked.append(
                {
                    "candidate": candidate,
                    "provider_order": provider_order,
                    "provider_candidate_rank": candidate_index,
                    **scoring,
                }
            )
        return ranked

    def _ensure_asset(self, candidate: dict[str, Any], query: str) -> dict[str, Any]:
        # Use normalized provider id (strip _video suffix for library lookup)
        lookup_provider = candidate["provider"].replace("_video", "")
        existing = self.library.get_by_provider_id(lookup_provider, str(candidate["provider_asset_id"]))
        if existing and self.library.is_file_valid(existing):
            return existing

        is_video = candidate.get("media_type") == "video"
        ext = ".mp4" if is_video else ".jpg"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / f"download{ext}"
            self.download_client.download(candidate["download_url"], temp_path)
            if is_video:
                return self.library.store_video(candidate, temp_path, original_query=query)
            return self.library.store_photo(candidate, temp_path, original_query=query)
