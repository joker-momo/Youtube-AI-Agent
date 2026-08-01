from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from video_agent.localized_v2.launcher_settings import launcher_fields
from video_agent.localized_v2.runtime import load_runtime_settings


def _write_runtime(path: Path, **overrides: object) -> None:
    payload = {
        "schemaVersion": "localized-runtime-v2/v1",
        "root": "runtime/localized-v2",
        "host": "127.0.0.1",
        "port": 8792,
        "browserWorkerUrl": "http://127.0.0.1:8793",
        "browserCdpUrl": "http://127.0.0.1:9322",
        "busyTimeoutMs": 2500,
        "leaseSeconds": 30,
    }
    payload.update(overrides)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def test_runtime_uses_separate_v2_dashboard_worker_and_cdp_ports(tmp_path: Path) -> None:
    config = tmp_path / "runtime.yaml"
    _write_runtime(config)

    settings = load_runtime_settings(config, repo_root=tmp_path)

    assert settings.port == 8792
    assert settings.browser_worker_url == "http://127.0.0.1:8793"
    assert settings.browser_cdp_url == "http://127.0.0.1:9322"


def test_runtime_expands_a_user_scoped_mutable_root(tmp_path: Path) -> None:
    config = tmp_path / "runtime.yaml"
    _write_runtime(config, root="~/Library/Application Support/YBT-Studio/localized-v2")

    settings = load_runtime_settings(config, repo_root=tmp_path)

    assert settings.root == (
        Path.home() / "Library" / "Application Support" / "YBT-Studio" / "localized-v2"
    )


def test_launcher_fields_are_derived_from_runtime_config(tmp_path: Path) -> None:
    config = tmp_path / "runtime.yaml"
    runtime_root = tmp_path / "custom runtime"
    _write_runtime(
        config,
        root=str(runtime_root),
        port=18892,
        browserWorkerUrl="http://127.0.0.1:18893",
        browserCdpUrl="http://127.0.0.1:19322",
    )

    assert launcher_fields(config, tmp_path) == (
        str(runtime_root),
        "127.0.0.1",
        "18892",
        "http://127.0.0.1:18893",
        "18893",
        "http://127.0.0.1:19322",
        "19322",
    )


@pytest.mark.parametrize(
    "browser_cdp_url",
    [
        "http://127.0.0.1:8792",
        "http://127.0.0.1:8793",
        "http://127.0.0.1:9222",
        "https://127.0.0.1:9322",
        "http://example.com:9322",
        "http://user:password@127.0.0.1:9322",
    ],
)
def test_runtime_rejects_shared_legacy_or_non_loopback_cdp_endpoint(
    tmp_path: Path,
    browser_cdp_url: str,
) -> None:
    config = tmp_path / "runtime.yaml"
    _write_runtime(config, browserCdpUrl=browser_cdp_url)

    with pytest.raises(ValueError, match="separate V2 loopback endpoint"):
        load_runtime_settings(config, repo_root=tmp_path)
