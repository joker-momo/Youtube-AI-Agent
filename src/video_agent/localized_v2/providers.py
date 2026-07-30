from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from video_agent.localized_v2.config import (
    ContractValidationError,
    validate_artifact,
)
from video_agent.localized_v2.content_safety import validate_localized_content
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.prompts import PromptEnvelope

MAX_STRUCTURED_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class BrowserProviderConfig:
    endpoint: str
    profile_root: Path
    session_namespace: str


def validate_browser_provider_config(
    config: BrowserProviderConfig,
    *,
    expected_endpoint: str,
    runtime_paths: RuntimePaths,
    legacy_endpoints: frozenset[str] = frozenset(),
    active_legacy_sessions: frozenset[str] = frozenset(),
) -> BrowserProviderConfig:
    expected = urlsplit(expected_endpoint)
    actual = urlsplit(config.endpoint)
    if (
        actual.scheme != "http"
        or actual.hostname not in {"127.0.0.1", "::1", "localhost"}
        or actual.username
        or actual.password
        or actual.query
        or actual.fragment
        or actual.path not in {"", "/"}
        or (actual.hostname, actual.port) != (expected.hostname, expected.port)
    ):
        raise ValueError("browser provider must use the dedicated loopback V2 endpoint")
    normalized_endpoint = f"http://{actual.hostname}:{actual.port}"
    normalized_legacy = {
        endpoint.rstrip("/").casefold() for endpoint in legacy_endpoints
    }
    if normalized_endpoint.casefold() in normalized_legacy:
        raise ValueError("browser provider endpoint overlaps the legacy worker")
    expected_profile = runtime_paths.browser_profile.resolve()
    if config.profile_root.resolve() != expected_profile:
        raise ValueError("browser provider must use the dedicated V2 profile root")
    namespace = config.session_namespace.strip()
    if (
        not namespace.startswith("localized-v2:")
        or namespace in active_legacy_sessions
        or any(part in namespace.casefold() for part in ("vida-plena", "legacy"))
    ):
        raise ValueError("browser provider must use an isolated V2 session namespace")
    return BrowserProviderConfig(
        endpoint=normalized_endpoint,
        profile_root=expected_profile,
        session_namespace=namespace,
    )


class StructuredProvider(Protocol):
    name: str

    def generate(self, prompt: PromptEnvelope) -> str | bytes | dict[str, Any]: ...


class ProviderBoundaryError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        locale: str,
        stage: str,
        provider: str,
        artifact: str,
        message: str,
    ):
        super().__init__(message)
        self.code = code
        self.locale = locale
        self.stage = stage
        self.provider = provider
        self.artifact = artifact

    def to_failure(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "locale": self.locale,
            "stage": self.stage,
            "provider": self.provider,
            "artifact": self.artifact,
            "message": str(self),
            "retryable": self.code in {"PROVIDER_ERROR", "INVALID_PROVIDER_RESPONSE"},
        }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _as_object(raw: str | bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        encoded = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_STRUCTURED_RESPONSE_BYTES:
            raise ValueError("structured response exceeds size limit")
        return raw
    if isinstance(raw, str):
        encoded = raw.encode("utf-8")
    elif isinstance(raw, bytes):
        encoded = raw
    else:
        raise TypeError("structured provider response must be JSON text or an object")
    if len(encoded) > MAX_STRUCTURED_RESPONSE_BYTES:
        raise ValueError("structured response exceeds size limit")
    text = encoded.decode("utf-8", errors="strict")
    payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(payload, dict):
        raise TypeError("structured provider response must contain one object")
    return payload


def validate_structured_response(
    raw: str | bytes | dict[str, Any],
    *,
    prompt: PromptEnvelope,
    locale_pack: dict[str, Any],
    schema_root,
    provider: str,
) -> dict[str, Any]:
    try:
        payload = _as_object(raw)
        validate_artifact(payload, prompt.artifact_kind, schema_root)
        validate_localized_content(prompt.artifact_kind, payload, locale_pack)
        return payload
    except ProviderBoundaryError:
        raise
    except (ContractValidationError, TypeError, UnicodeDecodeError, ValueError) as exc:
        raise ProviderBoundaryError(
            "INVALID_PROVIDER_RESPONSE",
            locale=locale_pack["locale"],
            stage=prompt.stage,
            provider=provider,
            artifact=prompt.artifact_kind.value,
            message=f"provider response rejected: {exc}",
        ) from exc
