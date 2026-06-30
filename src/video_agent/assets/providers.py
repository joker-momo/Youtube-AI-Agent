from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from video_agent.contracts import load_env

DEFAULT_HEADERS = {"User-Agent": "Youtube-AI-Agent-MVP/0.1"}

# Cinematic-prompt boilerplate that bloats queries. Pexels tolerates long
# natural-language queries, but Pixabay rejects q>100 chars (HTTP 400) and
# Coverr's keyword/tag search returns 0 hits for descriptive sentences. We
# strip this filler and keep the concrete scene nouns for those two providers.
_QUERY_BOILERPLATE = {
    "vertical", "horizontal", "realistic", "cinematic", "closeup", "close", "up",
    "shot", "footage", "scene", "natural", "soft", "warm", "atmosphere", "cozy",
    "neutral", "calm", "peaceful", "low", "light", "lighting", "texture", "skin",
    "expression", "gesture", "intentional", "showing", "around", "sitting",
    "looking", "forward", "edge", "small", "beside", "nearby", "folded", "domestic",
    "setting", "details", "detail", "decor", "and", "the", "with", "for", "from",
    "near", "next", "very", "some", "that", "this", "her", "his", "their",
    "spain", "spanish", "european", "senior", "elderly", "around", "about",
    "no", "not", "without",
    # Added camera/framing/action verbs
    "style", "sequence", "sequencestyle", "flat", "apartment", "facing", "turned",
    "closing", "opening", "having", "holding", "taking", "getting", "making"
}


def keywordize_query(query: str, max_terms: int = 6, max_chars: int = 100) -> str:
    """Reduce a long cinematic prompt to a few concrete keywords.

    Used for providers (Pixabay, Coverr) whose search engines need short
    keyword queries. Drops aspect-ratio tokens, pure numbers and boilerplate;
    preserves the first ``max_terms`` meaningful nouns; caps to ``max_chars``.
    """
    cleaned = (query or "").lower().replace("/", " ").replace(":", " ").replace("-", " ").replace("_", " ")
    raw_tokens = [t for t in cleaned.split()]
    seen: set[str] = set()
    kept: list[str] = []
    for tok in raw_tokens:
        word = "".join(ch for ch in tok if ch.isalpha())
        if len(word) < 3 or word in _QUERY_BOILERPLATE or word in seen:
            continue
        seen.add(word)
        kept.append(word)
        if len(kept) >= max_terms:
            break
    result = " ".join(kept)
    if len(result) > max_chars:
        result = result[:max_chars].rsplit(" ", 1)[0]
    return result or (query or "")[:max_chars]


def _read_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = Request(url, headers=DEFAULT_HEADERS | (headers or {}))
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_pexels_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for photo in response.get("photos", []):
        src = photo.get("src", {})
        photographer = photo.get("photographer")
        alt = photo.get("alt")
        results.append(
            {
                "provider": "pexels",
                "provider_asset_id": str(photo["id"]),
                "media_type": "photo",
                "download_url": src.get("large2x") or src.get("large") or src.get("original"),
                "source_url": photo.get("url"),
                "width": photo.get("width"),
                "height": photo.get("height"),
                "tags": [alt] if alt else [],
                "photographer": photographer,
                "photographer_url": photo.get("photographer_url"),
                "attribution": f"Photo by {photographer} on Pexels" if photographer else "Photo from Pexels",
                "quality": "large2x" if src.get("large2x") else "large",
                "license": "Pexels License",
            }
        )
    return [item for item in results if item["download_url"]]


def normalize_pixabay_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for hit in response.get("hits", []):
        user = hit.get("user")
        user_id = hit.get("user_id")
        tags = [tag.strip() for tag in (hit.get("tags") or "").split(",") if tag.strip()]
        user_url = (
            f"https://pixabay.com/users/{quote(str(user), safe='')}-{user_id}/"
            if user and user_id
            else None
        )
        quality = "fullhd" if hit.get("fullHDURL") else "large"
        results.append(
            {
                "provider": "pixabay",
                "provider_asset_id": str(hit["id"]),
                "media_type": "photo",
                "download_url": hit.get("fullHDURL") or hit.get("largeImageURL") or hit.get("webformatURL"),
                "source_url": hit.get("pageURL"),
                "width": hit.get("imageWidth"),
                "height": hit.get("imageHeight"),
                "tags": tags,
                "photographer": user,
                "photographer_url": user_url,
                "attribution": f"Image by {user} from Pixabay" if user else "Image from Pixabay",
                "quality": quality,
                "license": "Pixabay Content License",
            }
        )
    return [item for item in results if item["download_url"]]


def normalize_pexels_video_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for video in response.get("videos", []):
        user = video.get("user", {})
        # Pick best HD file: highest resolution ≤1920px wide (1080p for render).
        # Avoids downloading huge 4K files that slow the pipeline.
        video_files = [
            f for f in video.get("video_files", [])
            if f.get("file_type") == "video/mp4" and f.get("link")
        ]
        hd = [f for f in video_files if (f.get("width") or 0) <= 1920]
        candidates = hd if hd else video_files
        candidates.sort(key=lambda f: (f.get("width") or 0), reverse=True)
        best = candidates[0] if candidates else None
        if not best:
            continue
        photographer = user.get("name") if user else None
        results.append(
            {
                "provider": "pexels",
                "provider_asset_id": str(video["id"]),
                "media_type": "video",
                "download_url": best["link"],
                "source_url": video.get("url"),
                "width": best.get("width"),
                "height": best.get("height"),
                "duration_sec": video.get("duration"),
                "fps": best.get("fps"),
                "tags": [],
                "photographer": photographer,
                "photographer_url": user.get("url") if user else None,
                "attribution": f"Video by {photographer} on Pexels" if photographer else "Video from Pexels",
                "quality": "hd",
                "license": "Pexels License",
            }
        )
    return [item for item in results if item["download_url"]]


def normalize_pixabay_video_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for hit in response.get("hits", []):
        videos = hit.get("videos") or {}
        # Prefer the largest file ≤1920px wide; Pixabay returns large/medium/small/tiny.
        ordered = [videos.get(k) for k in ("large", "medium", "small", "tiny")]
        ordered = [v for v in ordered if v and v.get("url")]
        hd = [v for v in ordered if (v.get("width") or 0) <= 1920]
        best = (hd or ordered)[0] if (hd or ordered) else None
        if not best:
            continue
        user = hit.get("user")
        user_id = hit.get("user_id")
        tags = [tag.strip() for tag in (hit.get("tags") or "").split(",") if tag.strip()]
        user_url = (
            f"https://pixabay.com/users/{quote(str(user), safe='')}-{user_id}/"
            if user and user_id
            else None
        )
        results.append(
            {
                "provider": "pixabay_video",
                "provider_asset_id": str(hit["id"]),
                "media_type": "video",
                "download_url": best.get("url"),
                "source_url": hit.get("pageURL"),
                "width": best.get("width"),
                "height": best.get("height"),
                "duration_sec": hit.get("duration"),
                "fps": None,
                "tags": tags,
                "photographer": user,
                "photographer_url": user_url,
                "attribution": f"Video by {user} from Pixabay" if user else "Video from Pixabay",
                "quality": "hd",
                "license": "Pixabay Content License",
            }
        )
    return [item for item in results if item["download_url"]]


def normalize_coverr_video_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for hit in response.get("hits", []):
        urls = hit.get("urls") or {}
        download_url = urls.get("mp4") or urls.get("mp4_download")
        if not download_url:
            continue
        try:
            duration = round(float(hit.get("duration"))) if hit.get("duration") is not None else None
        except (TypeError, ValueError):
            duration = None
        tags = [t for t in (hit.get("tags") or []) if t]
        results.append(
            {
                "provider": "coverr_video",
                "provider_asset_id": str(hit.get("id") or hit.get("video_id")),
                "media_type": "video",
                "download_url": download_url,
                "source_url": f"https://coverr.co/videos/{hit.get('base_filename')}" if hit.get("base_filename") else None,
                "width": hit.get("max_width"),
                "height": hit.get("max_height"),
                "duration_sec": duration,
                "fps": hit.get("fps"),
                "is_vertical": hit.get("is_vertical"),
                "tags": tags,
                "photographer": None,
                "photographer_url": None,
                "attribution": "Video from Coverr",
                "quality": "1080p",
                "license": "Coverr License",
            }
        )
    return [item for item in results if item["download_url"]]


class StockPhotoClient:
    @staticmethod
    def _env_key(name: str) -> str | None:
        value = os.environ.get(name)
        if value:
            return value
        load_env()
        return os.environ.get(name)

    def search(self, provider: str, query: str, filters: dict[str, Any], exclude_ids: set[str] | None = None) -> dict[str, Any]:
        if provider == "pexels":
            return self._search_pexels(query, filters)
        if provider == "pexels_video":
            return self._search_pexels_video(query, filters)
        if provider == "pixabay":
            return self._search_pixabay(query, filters)
        if provider == "pixabay_video":
            return self._search_pixabay_video(query, filters, exclude_ids=exclude_ids)
        if provider == "coverr_video":
            return self._search_coverr_video(query, filters, exclude_ids=exclude_ids)
        raise ValueError(f"Unsupported stock photo provider: {provider}")

    def normalize(self, provider: str, response: dict[str, Any]) -> list[dict[str, Any]]:
        if provider == "pexels":
            return normalize_pexels_response(response)
        if provider == "pexels_video":
            return normalize_pexels_video_response(response)
        if provider == "pixabay":
            return normalize_pixabay_response(response)
        if provider == "pixabay_video":
            return normalize_pixabay_video_response(response)
        if provider == "coverr_video":
            return normalize_coverr_video_response(response)
        raise ValueError(f"Unsupported stock photo provider: {provider}")

    def _search_pexels(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        api_key = self._env_key("PEXELS_API_KEY")
        if not api_key:
            raise RuntimeError("PEXELS_API_KEY is required for provider=pexels")
        kw_query = keywordize_query(query, max_terms=6)
        params = urlencode(
            {
                "query": kw_query,
                "orientation": filters.get("orientation", "landscape"),
                "per_page": filters.get("per_page", 10),
            }
        )
        return _read_json(f"https://api.pexels.com/v1/search?{params}", {"Authorization": api_key})

    def _search_pexels_video(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        api_key = self._env_key("PEXELS_API_KEY")
        if not api_key:
            raise RuntimeError("PEXELS_API_KEY is required for provider=pexels_video")
        min_dur = int(filters.get("min_duration_sec", 10))
        kw_query = keywordize_query(query, max_terms=6)
        params = urlencode(
            {
                "query": kw_query,
                "orientation": filters.get("orientation", "landscape"),
                "per_page": filters.get("per_page", 10),
                "min_duration": min_dur,
            }
        )
        return _read_json(f"https://api.pexels.com/videos/search?{params}", {"Authorization": api_key})

    def _search_pixabay(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        api_key = self._env_key("PIXABAY_API_KEY")
        if not api_key:
            raise RuntimeError("PIXABAY_API_KEY is required for provider=pixabay")
        orientation = filters.get("orientation", "horizontal")
        if orientation == "landscape":
            orientation = "horizontal"
        params = urlencode(
            {
                "key": api_key,
                "q": query,
                "image_type": "photo",
                "orientation": orientation,
                "safesearch": "true",
                "per_page": max(3, int(filters.get("per_page", 10))),
            }
        )
        return _read_json(f"https://pixabay.com/api/?{params}")

    def _search_pixabay_video(self, query: str, filters: dict[str, Any], exclude_ids: set[str] | None = None) -> dict[str, Any]:
        api_key = self._env_key("PIXABAY_API_KEY")
        if not api_key:
            raise RuntimeError("PIXABAY_API_KEY is required for provider=pixabay_video")
        page_size = max(3, int(filters.get("per_page", 10)))
        
        last_exception = None
        for terms_count in (4, 3, 2, 1):
            kw_query = keywordize_query(query, max_terms=terms_count)
            params = urlencode(
                {
                    "key": api_key,
                    "q": kw_query,
                    "video_type": "film",
                    "safesearch": "true",
                    "per_page": page_size,
                }
            )
            try:
                response = _read_json(f"https://pixabay.com/api/videos/?{params}")
                hits = response.get("hits") or []
                if hits:
                    if exclude_ids:
                        non_excluded = [h for h in hits if str(h.get("id")) not in exclude_ids]
                        if not non_excluded:
                            continue
                    return response
            except Exception as e:
                last_exception = e
                if hasattr(e, "code") and e.code in (403, 429):
                    break
        
        if last_exception:
            raise last_exception
        return response

    def _search_coverr_video(self, query: str, filters: dict[str, Any], exclude_ids: set[str] | None = None) -> dict[str, Any]:
        api_key = self._env_key("COVERR_API_KEY")
        if not api_key:
            raise RuntimeError("COVERR_API_KEY is required for provider=coverr_video")
        
        orientation = filters.get("orientation")
        page_size = max(3, int(filters.get("per_page", 10)))
        
        last_exception = None
        for terms_count in (3, 2, 1):
            kw_query = keywordize_query(query, max_terms=terms_count)
            params_dict = {
                "query": kw_query,
                "page_size": page_size,
                "urls": "true",
                "api_key": api_key,
            }
            if orientation in ("portrait", "vertical"):
                params_dict["orientation"] = "vertical"
            
            try:
                response = _read_json(f"https://api.coverr.co/videos?{urlencode(params_dict)}")
                hits = response.get("hits") or []
                if hits:
                    if exclude_ids:
                        non_excluded = [
                            h for h in hits
                            if str(h.get("id") or h.get("video_id")) not in exclude_ids
                        ]
                        if not non_excluded:
                            continue
                    return response
            except Exception as e:
                last_exception = e
                if hasattr(e, "code") and e.code == 403:
                    break
                if "Exceeded query limit" in str(e):
                    break
        
        if last_exception:
            raise last_exception
        return response
