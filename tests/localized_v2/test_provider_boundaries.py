from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.localized_v2.assets import (
    AssetBoundaryError,
    AssetResponse,
    materialize_asset,
)
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.providers import (
    BrowserProviderConfig,
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
