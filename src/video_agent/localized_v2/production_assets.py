from __future__ import annotations

import hashlib
import json
import os
import tempfile
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

STOCK_CREDENTIAL_KEYS = frozenset({"PEXELS_API_KEY", "PIXABAY_API_KEY"})


def load_stock_provider_credentials(env_path: Path) -> frozenset[str]:
    """Load only stock-video credentials from an explicit, read-only env file."""

    path = env_path.expanduser().resolve()
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        raise RuntimeError("localized V2 stock credential file is missing or invalid")
    discovered: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in STOCK_CREDENTIAL_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            discovered.add(key)
            os.environ.setdefault(key, value)
    if not any(os.environ.get(key) for key in STOCK_CREDENTIAL_KEYS):
        raise RuntimeError("localized V2 requires at least one stock video credential")
    return frozenset(discovered)


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
        cache_root = (self.runtime_paths.cache / "browser-images").resolve()
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cached_path = cache_root / f"{cache_key}.bin"
        if cached_path.is_file():
            cached_body = cached_path.read_bytes()
            try:
                content_type = self._content_type(cached_body)
                if not cached_body or len(cached_body) > MAX_MEDIA_BYTES:
                    raise RuntimeError("cached browser image size is invalid")
            except RuntimeError:
                cached_path.unlink(missing_ok=True)
            else:
                return AssetResponse(
                    status=200,
                    content_type=content_type,
                    body=cached_body,
                    source_url="https://chatgpt.com/",
                    metadata=json.dumps(
                        {
                            "provider": "chatgpt",
                            "artifact": artifact,
                            "cacheHit": True,
                        },
                        separators=(",", ":"),
                    ),
                )
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
                # The isolated browser worker performs one bounded recovery retry.
                # Keep the outer transport alive for both bounded attempts.
                timeout=(self.response_timeout_ms / 1000.0 * 2) + 60.0,
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
            content_type = self._content_type(body)
            if not body or len(body) > MAX_MEDIA_BYTES:
                raise RuntimeError("localized V2 browser image size is invalid")
            cache_root.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{cache_key}.",
                dir=cache_root,
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, cached_path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
            return AssetResponse(
                status=200,
                content_type=content_type,
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
        providers = [
            provider
            for provider, credential in (
                ("pexels_video", "PEXELS_API_KEY"),
                ("pixabay_video", "PIXABAY_API_KEY"),
            )
            if os.environ.get(credential)
        ]
        if not providers:
            raise RuntimeError("localized V2 has no configured stock video provider")
        service = StockAssetService(
            {
                "providers": providers,
                # Repeating the video provider suppresses the legacy photo tier:
                # V2's render contract requires a real video behind every scene.
                "photo_providers": providers,
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
    def _stock_scene(scene: dict[str, Any], query: str) -> dict[str, Any]:
        query = query.strip()
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
        brief = scene.get("searchBrief") or {}
        queries = [str(query).strip() for query in brief.get("queries") or []]
        if not queries:
            queries = [str(scene.get("visualPrompt") or "").strip()]
        asset = None
        for query in queries:
            if not query:
                continue
            candidate = self.service.get_scene_asset(
                self._stock_scene(scene, query),
                str(_context.get("channelId") or self.channel_id),
                str(_context.get("jobId") or self.job_id),
            )
            if candidate and candidate.get("media_type") == "video":
                asset = candidate
                break
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
