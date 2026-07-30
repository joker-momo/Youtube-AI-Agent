from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.providers import (
    BrowserProviderConfig,
    validate_browser_provider_config,
)
from video_agent.localized_v2.visual.context import build_visual_context

MAX_MEDIA_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024
MIME_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


class AssetBoundaryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AssetResponse:
    status: int
    content_type: str
    body: bytes
    source_url: str
    metadata: str = ""


@dataclass(frozen=True, slots=True)
class MaterializedAsset:
    path: Path
    source_url: str
    media_kind: str
    sha256: str


class VisualAssetProvider(Protocol):
    name: str
    transport: str
    browser_config: BrowserProviderConfig | None

    def background(self, scene: dict[str, Any], context: dict[str, Any]) -> AssetResponse: ...

    def graphic(self, scene: dict[str, Any], context: dict[str, Any]) -> AssetResponse: ...

    def thumbnail(self, seo: dict[str, Any], context: dict[str, Any]) -> AssetResponse: ...


class AssetStageError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        locale: str,
        provider: str,
        artifact: str,
        message: str,
    ):
        super().__init__(message)
        self.code = code
        self.locale = locale
        self.provider = provider
        self.artifact = artifact

    def to_failure(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "locale": self.locale,
            "stage": "assets",
            "provider": self.provider,
            "artifact": self.artifact,
            "message": str(self),
            "retryable": self.code in {
                "ASSET_PROVIDER_ERROR",
                "PROVIDER_HTTP_ERROR",
            },
        }


def _valid_magic(content_type: str, body: bytes) -> bool:
    if content_type == "image/jpeg":
        return body.startswith(b"\xff\xd8\xff") and body.endswith(b"\xff\xd9")
    if content_type == "image/png":
        return body.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return body.startswith(b"RIFF") and body[8:12] == b"WEBP"
    if content_type == "video/mp4":
        return len(body) >= 12 and body[4:8] == b"ftyp"
    if content_type == "video/webm":
        return body.startswith(b"\x1a\x45\xdf\xa3")
    return False


def validate_asset_response(response: AssetResponse, *, expected_kind: str) -> str:
    if response.status < 200 or response.status >= 300:
        raise AssetBoundaryError("PROVIDER_HTTP_ERROR", "asset provider returned non-success status")
    content_type = response.content_type.split(";", 1)[0].strip().lower()
    if content_type not in MIME_SUFFIXES:
        raise AssetBoundaryError("INVALID_MEDIA_MIME", "asset provider returned unsupported MIME")
    if not response.body:
        raise AssetBoundaryError("EMPTY_MEDIA", "asset provider returned an empty body")
    if len(response.body) > MAX_MEDIA_BYTES:
        raise AssetBoundaryError("MEDIA_TOO_LARGE", "asset provider media exceeds size limit")
    actual_kind = "image" if content_type.startswith("image/") else "video"
    if actual_kind != expected_kind:
        raise AssetBoundaryError("MEDIA_KIND_MISMATCH", "asset media kind does not match its role")
    if not _valid_magic(content_type, response.body):
        raise AssetBoundaryError("INVALID_MEDIA_BYTES", "asset media signature is invalid")
    parsed = urlsplit(response.source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise AssetBoundaryError("INVALID_SOURCE_URL", "asset source URL is not safe")
    encoded_metadata = response.metadata.encode("utf-8", errors="strict")
    if len(encoded_metadata) > MAX_METADATA_BYTES:
        raise AssetBoundaryError("INVALID_METADATA", "asset metadata exceeds size limit")
    lowered = response.metadata.casefold()
    if any(marker in lowered for marker in ("<script", "javascript:", "<iframe", "onerror=")):
        raise AssetBoundaryError("HOSTILE_METADATA", "asset metadata contains executable markup")
    return content_type


def materialize_asset(
    response: AssetResponse,
    *,
    expected_kind: str,
    output_dir: Path,
    basename: str,
) -> MaterializedAsset:
    if Path(basename).name != basename or not basename:
        raise AssetBoundaryError("INVALID_ASSET_NAME", "asset basename is invalid")
    content_type = validate_asset_response(response, expected_kind=expected_kind)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{basename}{MIME_SUFFIXES[content_type]}"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=output_dir)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(response.body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return MaterializedAsset(
        path=destination,
        source_url=response.source_url,
        media_kind=expected_kind,
        sha256=hashlib.sha256(response.body).hexdigest(),
    )


class LocalizedAssetPipeline:
    def __init__(
        self,
        provider: VisualAssetProvider,
        *,
        runtime_paths: RuntimePaths | None = None,
        browser_worker_url: str | None = None,
        legacy_browser_endpoints: frozenset[str] = frozenset(),
        active_legacy_sessions: frozenset[str] = frozenset(),
    ):
        self.provider = provider
        if provider.transport == "browser":
            if (
                provider.browser_config is None
                or runtime_paths is None
                or browser_worker_url is None
            ):
                raise ValueError("browser asset providers require explicit V2 isolation")
            validate_browser_provider_config(
                provider.browser_config,
                expected_endpoint=browser_worker_url,
                runtime_paths=runtime_paths,
                legacy_endpoints=legacy_browser_endpoints,
                active_legacy_sessions=active_legacy_sessions,
            )
        elif provider.transport != "direct" or provider.browser_config is not None:
            raise ValueError("unsupported or inconsistent asset provider transport")

    def _call(
        self,
        locale: str,
        artifact: str,
        callback,
    ) -> AssetResponse:
        try:
            response = callback()
        except Exception as exc:
            raise AssetStageError(
                "ASSET_PROVIDER_ERROR",
                locale=locale,
                provider=self.provider.name,
                artifact=artifact,
                message=f"asset provider failed with {type(exc).__name__}",
            ) from exc
        if not isinstance(response, AssetResponse):
            raise AssetStageError(
                "INVALID_ASSET_RESPONSE",
                locale=locale,
                provider=self.provider.name,
                artifact=artifact,
                message="asset provider returned an invalid response object",
            )
        return response

    def build(
        self,
        *,
        locale_pack: dict[str, Any],
        topic: str,
        scenes: dict[str, Any],
        seo: dict[str, Any],
        output_dir: Path,
        promoted_root: Path,
        market_relevant: bool = False,
        market_evidence: tuple[str, ...] = (),
    ) -> dict[str, Path]:
        locale = str(locale_pack["locale"])
        context = build_visual_context(
            topic,
            locale_pack,
            market_relevant=market_relevant,
            evidence=market_evidence,
        )
        provider_context = {
            "locale": context.locale,
            "topic": context.topic,
            "peopleContext": context.people_context,
            "marketContext": context.market_context,
            "evidence": list(context.evidence),
            "avoid": list(context.avoid),
        }
        outputs: dict[str, Path] = {}
        manifest_items: list[dict[str, Any]] = []

        def add(
            *,
            scene_id: str,
            role: str,
            kind: str,
            response: AssetResponse,
            basename: str,
        ) -> None:
            try:
                asset = materialize_asset(
                    response,
                    expected_kind=kind,
                    output_dir=output_dir,
                    basename=basename,
                )
            except AssetBoundaryError as exc:
                raise AssetStageError(
                    exc.code,
                    locale=locale,
                    provider=self.provider.name,
                    artifact=f"{scene_id}:{role}",
                    message=str(exc),
                ) from exc
            outputs[asset.path.name] = asset.path
            manifest_items.append(
                {
                    "sceneId": scene_id,
                    "role": role,
                    "mediaKind": kind,
                    "path": str(promoted_root / asset.path.name),
                    "source": asset.source_url,
                    "sha256": asset.sha256,
                }
            )

        for scene in scenes["scenes"]:
            scene_id = str(scene["id"])
            background = self._call(
                locale,
                f"{scene_id}:background",
                lambda scene=scene: self.provider.background(scene, provider_context),
            )
            add(
                scene_id=scene_id,
                role="background",
                kind="video",
                response=background,
                basename=f"{scene_id}-background",
            )
            if scene["visualType"] == "graphic":
                graphic = self._call(
                    locale,
                    f"{scene_id}:graphic",
                    lambda scene=scene: self.provider.graphic(scene, provider_context),
                )
                add(
                    scene_id=scene_id,
                    role="graphic",
                    kind="image",
                    response=graphic,
                    basename=f"{scene_id}-graphic",
                )
        thumbnail = self._call(
            locale,
            "video:thumbnail",
            lambda: self.provider.thumbnail(seo, provider_context),
        )
        add(
            scene_id="video",
            role="thumbnail",
            kind="image",
            response=thumbnail,
            basename="thumbnail",
        )
        manifest = {
            "schemaVersion": "localized-assets-v2/v1",
            "locale": locale,
            "assets": manifest_items,
        }
        manifest_path = output_dir / "asset-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        outputs[manifest_path.name] = manifest_path
        return outputs
