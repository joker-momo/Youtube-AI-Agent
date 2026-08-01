from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from video_agent.localized_v2.config import ContractValidationError
from video_agent.localized_v2.dashboard.bootstrap import (
    load_dashboard_channels,
    load_enabled_channels,
    probe_font_family,
)
from video_agent.localized_v2.runtime import RuntimeSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "schemas"
LOCALE_ROOT = REPO_ROOT / "configs" / "localized-v2" / "locales"
CHANNEL_ROOT = REPO_ROOT / "configs" / "localized-v2" / "channels"


def _settings(root: Path) -> RuntimeSettings:
    return RuntimeSettings(
        root=root,
        host="127.0.0.1",
        port=8792,
        browser_worker_url="http://127.0.0.1:8793",
        browser_cdp_url="http://127.0.0.1:9322",
        busy_timeout_ms=2500,
        lease_seconds=30,
    )


def _approve_english(channel_root: Path, runtime_root: Path) -> None:
    path = channel_root / "pending-en-us" / "channel.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["enabled"] = True
    payload["channelId"] = "healthy-life-en"
    payload["brand"]["name"] = "Healthy Life 45+"
    payload["voice"] = {
        "provider": "kokoro",
        "language": "a",
        "voiceId": "af_heart",
        "speed": 1.0,
    }
    checks = {
        "audio": "PASS",
        "font": "PASS",
        "render": "PASS",
        "humanReview": "PASS",
        "dashboardLifecycle": "PASS",
    }
    payload["canary"] = {
        "status": "APPROVED",
        "checks": checks,
        "evidence": [f"en-US/{name}.json" for name in checks],
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    for name in checks:
        evidence = runtime_root / "canary-evidence" / "en-US" / f"{name}.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"result":"PASS"}\n', encoding="utf-8")


def _capabilities(runtime_root: Path, *, clip: str = "brand/en-US/intro.mp4") -> None:
    payload = {
        "schemaVersion": "localized-capabilities-v2/v1",
        "voices": [
            {"provider": "kokoro", "language": "a", "voiceId": "af_heart"}
        ],
        "fonts": ["Manrope"],
        "brandClips": [
            clip,
            "brand/en-US/disclaimer.mp4",
            "brand/en-US/outro.mp4",
        ],
    }
    path = runtime_root / "capabilities.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    channels = tmp_path / "configs" / "channels"
    locales = tmp_path / "configs" / "locales"
    shutil.copytree(CHANNEL_ROOT, channels)
    shutil.copytree(LOCALE_ROOT, locales)
    return channels, locales, tmp_path / "runtime"


def test_disabled_matrix_bootstraps_without_capability_manifest(tmp_path: Path) -> None:
    channels, locales, runtime = _roots(tmp_path)

    loaded = load_enabled_channels(
        channel_root=channels,
        locale_root=locales,
        schema_root=SCHEMA_ROOT,
        settings=_settings(runtime),
    )

    assert loaded == {}
    assert not (runtime / "capabilities.yaml").exists()


def test_english_font_probe_uses_bundled_remotion_assets() -> None:
    assert probe_font_family("Manrope") is True


def test_explicit_english_qualification_loads_without_production_approval(
    tmp_path: Path,
) -> None:
    channels, locales, runtime = _roots(tmp_path)
    _capabilities(runtime)
    media_root = runtime / "media" / "brand" / "en-US"
    media_root.mkdir(parents=True)
    for name in ("intro.mp4", "disclaimer.mp4", "outro.mp4"):
        (media_root / name).write_bytes(b"media")

    loaded = load_dashboard_channels(
        channel_root=channels,
        locale_root=locales,
        schema_root=SCHEMA_ROOT,
        settings=_settings(runtime),
        clip_probe=lambda path, _root: path,
        font_probe=lambda _family: True,
        voice_probe=lambda _voice, _repo_root: True,
    )

    registration = loaded["healthy-life-en"]
    assert registration.channel["enabled"] is False
    assert registration.channel["qualification"] is True
    assert registration.channel["canary"]["status"] == "PENDING"


def test_approved_channel_loads_only_after_capabilities_are_verified(
    tmp_path: Path,
) -> None:
    channels, locales, runtime = _roots(tmp_path)
    _approve_english(channels, runtime)
    _capabilities(runtime)
    media_root = runtime / "media" / "brand" / "en-US"
    media_root.mkdir(parents=True)
    for name in ("intro.mp4", "disclaimer.mp4", "outro.mp4"):
        (media_root / name).write_bytes(b"media")

    loaded = load_enabled_channels(
        channel_root=channels,
        locale_root=locales,
        schema_root=SCHEMA_ROOT,
        settings=_settings(runtime),
        clip_probe=lambda path, _root: path,
        font_probe=lambda _family: True,
        voice_probe=lambda _voice, _repo_root: True,
    )

    registration = loaded["healthy-life-en"]
    assert registration.channel["enabled"] is True
    assert registration.locale_pack["locale"] == "en-US"
    assert registration.inventory.voices == frozenset(
        {("kokoro", "a", "af_heart")}
    )
    assert registration.inventory.fonts == frozenset({"Manrope"})
    assert len(registration.inventory.brand_clips) == 3


def test_enabled_channel_fails_closed_without_capability_manifest(
    tmp_path: Path,
) -> None:
    channels, locales, runtime = _roots(tmp_path)
    _approve_english(channels, runtime)

    with pytest.raises(ContractValidationError) as error:
        load_enabled_channels(
            channel_root=channels,
            locale_root=locales,
            schema_root=SCHEMA_ROOT,
            settings=_settings(runtime),
        )

    assert error.value.code == "CAPABILITY_MANIFEST_MISSING"


def test_capability_manifest_rejects_media_path_escape(tmp_path: Path) -> None:
    channels, locales, runtime = _roots(tmp_path)
    _approve_english(channels, runtime)
    _capabilities(runtime, clip="../outside.mp4")
    (runtime / "outside.mp4").write_bytes(b"media")

    with pytest.raises(ContractValidationError) as error:
        load_enabled_channels(
            channel_root=channels,
            locale_root=locales,
            schema_root=SCHEMA_ROOT,
            settings=_settings(runtime),
            clip_probe=lambda path, _root: path,
            font_probe=lambda _family: True,
            voice_probe=lambda _voice, _repo_root: True,
        )

    assert error.value.code == "INVALID_CAPABILITY_MANIFEST"


@pytest.mark.parametrize("missing", ["font", "voice"])
def test_declared_capability_must_exist_on_this_runtime(
    tmp_path: Path,
    missing: str,
) -> None:
    channels, locales, runtime = _roots(tmp_path)
    _approve_english(channels, runtime)
    _capabilities(runtime)
    media_root = runtime / "media" / "brand" / "en-US"
    media_root.mkdir(parents=True)
    for name in ("intro.mp4", "disclaimer.mp4", "outro.mp4"):
        (media_root / name).write_bytes(b"media")

    with pytest.raises(ContractValidationError) as error:
        load_enabled_channels(
            channel_root=channels,
            locale_root=locales,
            schema_root=SCHEMA_ROOT,
            settings=_settings(runtime),
            clip_probe=lambda path, _root: path,
            font_probe=lambda _family: missing != "font",
            voice_probe=lambda _voice, _repo_root: missing != "voice",
        )

    assert error.value.code == "INVALID_CAPABILITY_MANIFEST"
    assert missing in str(error.value)
