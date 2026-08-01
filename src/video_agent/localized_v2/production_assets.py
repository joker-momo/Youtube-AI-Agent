from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx

from video_agent.assets.service import StockAssetService
from video_agent.localized_v2.assets import (
    MAX_MEDIA_BYTES,
    AssetResponse,
    VisualAssetProvider,
)
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.providers import (
    BrowserProviderConfig,
    validate_browser_provider_config,
)


class ImageAssetProvider(Protocol):
    transport: str
    browser_config: BrowserProviderConfig | None

    def graphic(self, scene: dict[str, Any], context: dict[str, Any]) -> AssetResponse: ...

    def thumbnail(self, seo: dict[str, Any], context: dict[str, Any]) -> AssetResponse: ...


class BrowserImageProvider:
    """Synchronous image adapter for the dedicated V2 browser worker."""

    transport = "browser"

    def __init__(
        self,
        config: BrowserProviderConfig,
        *,
        runtime_paths: RuntimePaths,
        expected_endpoint: str,
        get: Callable[..., Any] = httpx.get,
        post: Callable[..., Any] = httpx.post,
        response_timeout_ms: int = 360_000,
    ) -> None:
        self.browser_config = validate_browser_provider_config(
            config,
            expected_endpoint=expected_endpoint,
            runtime_paths=runtime_paths,
        )
        self.runtime_paths = runtime_paths
        self._post = post
        self.response_timeout_ms = max(1_000, min(900_000, response_timeout_ms))
        try:
            response = get(f"{self.browser_config.endpoint}/health", timeout=5.0)
            payload = response.json()
            profile = Path(str(payload["profileRoot"])).resolve()
        except Exception as exc:
            raise RuntimeError(
                "localized V2 browser worker identity could not be verified"
            ) from exc
        if (
            response.status_code != 200
            or payload.get("service") != "localized-v2-browser-worker"
            or payload.get("sessionNamespace")
            != self.browser_config.session_namespace
            or profile != self.browser_config.profile_root.resolve()
        ):
            raise RuntimeError("localized V2 browser worker identity mismatch")

    @staticmethod
    def _content_type(body: bytes) -> str:
        if body.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if body.startswith(b"\xff\xd8\xff") and body.endswith(b"\xff\xd9"):
            return "image/jpeg"
        if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
            return "image/webp"
        raise RuntimeError("localized V2 browser worker returned invalid image bytes")

    def _generate(self, prompt: str, *, artifact: str) -> AssetResponse:
        output = (
            self.runtime_paths.work
            / "browser-images"
            / f"{artifact}-{uuid.uuid4().hex}.png"
        ).resolve()
        if not output.is_relative_to(self.runtime_paths.work.resolve()):
            raise RuntimeError("localized V2 browser image path escaped runtime work")
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = self._post(
                f"{self.browser_config.endpoint}/chatgpt/image",
                json={
                    "prompt": prompt,
                    "project_name": "localized-v2",
                    "out_path": str(output),
                    "response_timeout_ms": self.response_timeout_ms,
                    "aspect_ratio": "16:9",
                },
                timeout=self.response_timeout_ms / 1000.0 + 30.0,
            )
            if response.status_code not in {200, 201}:
                raise RuntimeError(
                    f"localized V2 browser worker returned HTTP {response.status_code}"
                )
            payload = response.json()
            returned = Path(str(payload.get("local_path") or output)).resolve()
            if returned != output or not output.is_file():
                raise RuntimeError("localized V2 browser worker returned an unsafe image path")
            body = output.read_bytes()
            return AssetResponse(
                status=200,
                content_type=self._content_type(body),
                body=body,
                source_url="https://chatgpt.com/",
                metadata=json.dumps(
                    {"provider": "chatgpt", "artifact": artifact},
                    separators=(",", ":"),
                ),
            )
        finally:
            output.unlink(missing_ok=True)

    def graphic(self, scene: dict[str, Any], context: dict[str, Any]) -> AssetResponse:
        prompt = (
            "Create one clean, topic-faithful 16:9 editorial graphic for a health "
            "education video. No subtitles, no logos, no medical promises. "
            f"Locale: {context['locale']}. Topic: {context['topic']}. "
            f"Scene: {scene['visualPrompt']}. Avoid: {', '.join(context.get('avoid') or [])}."
        )
        return self._generate(prompt, artifact=f"{scene['id']}-graphic")

    def thumbnail(self, seo: dict[str, Any], context: dict[str, Any]) -> AssetResponse:
        prompt = (
            "Create one polished 16:9 YouTube thumbnail image for a trustworthy "
            "health education channel. Keep the composition simple, legible, and "
            "topic-faithful. Do not add logos or medical promises. "
            f"Locale: {context['locale']}. Topic: {context['topic']}. "
            f"Title: {seo.get('title', '')}."
        )
        return self._generate(prompt, artifact="thumbnail")


class StockVideoProvider:
    """V2 visual provider with stock video backgrounds and isolated image generation."""

    name = "localized-v2-stock-and-browser-images"
    def __init__(
        self,
        service: StockAssetService,
        images: ImageAssetProvider,
        *,
        channel_id: str = "",
        job_id: str = "",
    ) -> None:
        self.service = service
        self.images = images
        self.channel_id = channel_id
        self.job_id = job_id
        self.transport = images.transport
        self.browser_config = images.browser_config

    @classmethod
    def build(
        cls,
        runtime_paths: RuntimePaths,
        images: ImageAssetProvider,
        *,
        channel_id: str = "",
        job_id: str = "",
    ) -> StockVideoProvider:
        cache_root = runtime_paths.cache.resolve()
        service = StockAssetService(
            {
                "providers": ["pexels_video"],
                # Repeating the video provider suppresses the legacy photo tier:
                # V2's render contract requires a real video behind every scene.
                "photo_providers": ["pexels_video"],
                "fallback_providers": [],
                "orientation": "landscape",
                "per_page": 10,
                "query_cache_ttl_hours": 24,
                "query_cache_path": str(cache_root / "stock-query-cache.db"),
                "asset_library_path": str(cache_root / "stock-library"),
                "asset_selection": {
                    "enable_quality_scoring": True,
                    "max_asset_candidates_per_provider": 12,
                    "max_library_cache_candidates": 10,
                    "max_candidate_metadata_score_total": 24,
                    "quality_weight": 0.45,
                },
            }
        )
        return cls(service, images, channel_id=channel_id, job_id=job_id)

    @staticmethod
    def _stock_scene(scene: dict[str, Any]) -> dict[str, Any]:
        brief = scene.get("searchBrief") or {}
        queries = brief.get("queries") or []
        query = str(queries[0] if queries else scene.get("visualPrompt") or "").strip()
        if not query:
            raise RuntimeError("localized V2 scene has no stock video search query")
        return {
            "id": str(scene["id"]),
            "visual_prompt": query,
            "asset_strategy": "stock_ok",
            "visual_importance": (
                "critical" if str(scene["id"]) == "opening" else "normal"
            ),
        }

    def background(self, scene: dict[str, Any], _context: dict[str, Any]) -> AssetResponse:
        asset = self.service.get_scene_asset(
            self._stock_scene(scene),
            str(_context.get("channelId") or self.channel_id),
            str(_context.get("jobId") or self.job_id),
        )
        if not asset or asset.get("media_type") != "video":
            raise RuntimeError("localized V2 stock video provider found no real stock video")
        raw_path = asset.get("local_path")
        if raw_path is None and asset.get("file_path"):
            raw_path = self.service.core.library.root / str(asset["file_path"])
        path = Path(str(raw_path)).resolve() if raw_path else None
        if (
            path is None
            or not path.is_file()
            or path.suffix.lower() != ".mp4"
            or path.stat().st_size > MAX_MEDIA_BYTES
        ):
            raise RuntimeError("localized V2 stock video provider returned invalid stock video")
        source_url = str(asset.get("original_url") or "")
        metadata = json.dumps(
            {
                "provider": asset.get("provider"),
                "providerAssetId": asset.get("provider_asset_id"),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return AssetResponse(
            status=200,
            content_type="video/mp4",
            body=path.read_bytes(),
            source_url=source_url,
            metadata=metadata,
        )

    def graphic(self, scene: dict[str, Any], context: dict[str, Any]) -> AssetResponse:
        return self.images.graphic(scene, context)

    def thumbnail(self, seo: dict[str, Any], context: dict[str, Any]) -> AssetResponse:
        return self.images.thumbnail(seo, context)


def require_visual_provider(provider: VisualAssetProvider) -> VisualAssetProvider:
    """Static/runtime contract marker used by production assembly."""

    return provider
