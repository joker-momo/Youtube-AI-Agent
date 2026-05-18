from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_HEADERS = {"User-Agent": "Youtube-AI-Agent-MVP/0.1"}


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
        user_url = f"https://pixabay.com/users/{user}-{user_id}/" if user and user_id else None
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


class StockPhotoClient:
    def search(self, provider: str, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        if provider == "pexels":
            return self._search_pexels(query, filters)
        if provider == "pixabay":
            return self._search_pixabay(query, filters)
        raise ValueError(f"Unsupported stock photo provider: {provider}")

    def normalize(self, provider: str, response: dict[str, Any]) -> list[dict[str, Any]]:
        if provider == "pexels":
            return normalize_pexels_response(response)
        if provider == "pixabay":
            return normalize_pixabay_response(response)
        raise ValueError(f"Unsupported stock photo provider: {provider}")

    def _search_pexels(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get("PEXELS_API_KEY")
        if not api_key:
            raise RuntimeError("PEXELS_API_KEY is required for provider=pexels")
        params = urlencode(
            {
                "query": query,
                "orientation": filters.get("orientation", "landscape"),
                "per_page": filters.get("per_page", 10),
            }
        )
        return _read_json(f"https://api.pexels.com/v1/search?{params}", {"Authorization": api_key})

    def _search_pixabay(self, query: str, filters: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get("PIXABAY_API_KEY")
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
