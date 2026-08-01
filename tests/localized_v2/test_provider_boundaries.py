from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.localized_v2.assets import (
    AssetBoundaryError,
    AssetResponse,
    materialize_asset,
)
from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.prompts import PromptEnvelope
from video_agent.localized_v2.providers import (
    BrowserProviderConfig,
    BrowserStructuredProvider,
    validate_browser_provider_config,
)


def _paths(tmp_path: Path) -> RuntimePaths:
    legacy = tmp_path / "legacy-jobs"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "v2-runtime", legacy_jobs_root=legacy)
    paths.initialize()
    return paths


def test_browser_provider_accepts_only_v2_endpoint_profile_and_session(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    valid = BrowserProviderConfig(
        endpoint="http://127.0.0.1:8793",
        profile_root=paths.browser_profile,
        session_namespace="localized-v2:healthy-life-en",
    )

    assert validate_browser_provider_config(
        valid,
        expected_endpoint="http://127.0.0.1:8793",
        runtime_paths=paths,
        legacy_endpoints=frozenset({"http://127.0.0.1:8001"}),
    ) == valid

    invalid = (
        BrowserProviderConfig(
            "http://127.0.0.1:8001",
            paths.browser_profile,
            "localized-v2:healthy-life-en",
        ),
        BrowserProviderConfig(
            "http://127.0.0.1:8793",
            tmp_path / "legacy-profile",
            "localized-v2:healthy-life-en",
        ),
        BrowserProviderConfig(
            "http://127.0.0.1:8793",
            paths.browser_profile,
            "vida-plena-active-session",
        ),
    )
    for config in invalid:
        with pytest.raises(ValueError):
            validate_browser_provider_config(
                config,
                expected_endpoint="http://127.0.0.1:8793",
                runtime_paths=paths,
                legacy_endpoints=frozenset({"http://127.0.0.1:8001"}),
            )


@pytest.mark.parametrize(
    "response",
    [
        AssetResponse(503, "image/png", b"x", "https://assets.example/a"),
        AssetResponse(200, "text/html", b"<html>", "https://assets.example/a"),
        AssetResponse(200, "image/png", b"", "https://assets.example/a"),
        AssetResponse(200, "image/png", b"not-png", "https://assets.example/a"),
        AssetResponse(
            200,
            "image/png",
            b"\x89PNG\r\n\x1a\npayload",
            "https://user:secret@assets.example/a",
        ),
        AssetResponse(
            200,
            "image/png",
            b"\x89PNG\r\n\x1a\npayload",
            "https://assets.example/a",
            "<script>alert(1)</script>",
        ),
    ],
)
def test_invalid_provider_media_is_never_materialized(
    response: AssetResponse, tmp_path: Path
) -> None:
    output = tmp_path / "assets"

    with pytest.raises(AssetBoundaryError):
        materialize_asset(
            response,
            expected_kind="image",
            output_dir=output,
            basename="candidate",
        )

    assert not list(output.glob("*")) if output.exists() else True


def test_valid_media_is_saved_atomically_with_original_bytes(tmp_path: Path) -> None:
    body = b"\x89PNG\r\n\x1a\noriginal-resolution-payload"
    result = materialize_asset(
        AssetResponse(
            200,
            "image/png; charset=binary",
            body,
            "https://assets.example/graphic",
            "calm educational graphic",
        ),
        expected_kind="image",
        output_dir=tmp_path,
        basename="scene-01-graphic",
    )

    assert result.path.read_bytes() == body
    assert not list(tmp_path.glob(".*.tmp"))


class _Response:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


def test_browser_structured_provider_uses_only_dedicated_v2_endpoint(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    calls: list[dict] = []

    def post(url: str, *, json: object, timeout: float) -> _Response:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _Response(200, {"raw_response": '{"title":"健やかな習慣"}'})

    provider = BrowserStructuredProvider(
        BrowserProviderConfig(
            endpoint="http://127.0.0.1:8793",
            profile_root=paths.browser_profile,
            session_namespace="localized-v2:healthy-life-ja",
        ),
        runtime_paths=paths,
        expected_endpoint="http://127.0.0.1:8793",
        post=post,
    )
    prompt = PromptEnvelope(
        stage="idea",
        system="Write only Japanese JSON.",
        payload={"topic": "健やかな習慣"},
        artifact_kind=ArtifactKind.IDEA,
    )

    raw = provider.generate(prompt)

    assert raw == '{"title":"健やかな習慣"}'
    assert calls[0]["url"] == "http://127.0.0.1:8793/chatgpt/send"
    assert calls[0]["json"]["response_timeout_ms"] == 300_000
    assert "Write only Japanese JSON." in calls[0]["json"]["prompt"]
    assert "健やかな習慣" in calls[0]["json"]["prompt"]
    assert calls[0]["timeout"] == 330.0


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (503, {"detail": {"secret": "must-not-surface"}}),
        (200, {"unexpected": "shape"}),
        (200, {"raw_response": 42}),
    ],
)
def test_browser_structured_provider_rejects_http_and_shape_failures(
    tmp_path: Path,
    status: int,
    payload: object,
) -> None:
    paths = _paths(tmp_path)
    provider = BrowserStructuredProvider(
        BrowserProviderConfig(
            endpoint="http://127.0.0.1:8793",
            profile_root=paths.browser_profile,
            session_namespace="localized-v2:healthy-life-en",
        ),
        runtime_paths=paths,
        expected_endpoint="http://127.0.0.1:8793",
        post=lambda *_args, **_kwargs: _Response(status, payload),
    )
    prompt = PromptEnvelope(
        stage="idea",
        system="Return JSON.",
        payload={"topic": "healthy aging"},
        artifact_kind=ArtifactKind.IDEA,
    )

    with pytest.raises(RuntimeError) as error:
        provider.generate(prompt)

    assert "must-not-surface" not in str(error.value)
