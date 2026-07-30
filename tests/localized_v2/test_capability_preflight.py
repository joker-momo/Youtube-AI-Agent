from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.localized_v2.preflight import (
    CapabilityInventory,
    run_preflight,
)


def _channel() -> dict:
    return {
        "channelId": "healthy-life-en",
        "locale": "en-US",
        "brand": {
            "introClip": "brand/intro.mp4",
            "disclaimerClip": "brand/disclaimer.mp4",
            "outroClip": "brand/outro.mp4",
        },
        "voice": {
            "provider": "kokoro",
            "language": "a",
            "voiceId": "af_heart",
        },
        "render": {"concurrency": "auto", "subtitles": {"enabled": False}},
    }


def _locale_pack() -> dict:
    return {
        "locale": "en-US",
        "medicalSafety": {
            "softClaims": ["research suggests"],
            "prohibitedClaims": ["cures disease", "guaranteed result"],
            "disclaimer": "Educational information only.",
        },
        "fonts": {"families": ["Inter"], "requiredCodepoints": ["0041"]},
        "textMetrics": {"charsPerWord": 5.0, "expansionRatio": 1.0},
    }


def _brand_files(root: Path) -> None:
    brand = root / "brand"
    brand.mkdir()
    for name in ("intro.mp4", "disclaimer.mp4", "outro.mp4"):
        (brand / name).write_bytes(b"fixture")


def _inventory(root: Path) -> CapabilityInventory:
    return CapabilityInventory(
        media_root=root,
        voices=frozenset({("kokoro", "a", "af_heart")}),
        fonts=frozenset({"Inter"}),
    )


@pytest.mark.parametrize(
    ("mutation", "capability"),
    [
        ("voice", "voice"),
        ("font", "font"),
        ("safety", "medicalSafety"),
        ("metrics", "textMetrics"),
    ],
)
def test_missing_capability_fails_closed(
    mutation: str, capability: str, tmp_path: Path
) -> None:
    channel = _channel()
    locale_pack = _locale_pack()
    _brand_files(tmp_path)
    inventory = _inventory(tmp_path)
    if mutation == "voice":
        inventory = CapabilityInventory(
            media_root=tmp_path,
            voices=frozenset(),
            fonts=inventory.fonts,
        )
    elif mutation == "font":
        inventory = CapabilityInventory(
            media_root=tmp_path,
            voices=inventory.voices,
            fonts=frozenset(),
        )
    elif mutation == "safety":
        locale_pack["medicalSafety"] = {}
    else:
        locale_pack["textMetrics"] = {}

    result = run_preflight(channel, locale_pack, inventory)

    assert result.ok is False
    failure = next(item for item in result.failures if item.capability == capability)
    assert failure.locale == "en-US"
    assert failure.code
    assert failure.remediation


def test_preflight_is_pure_and_creates_no_job_or_queue(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    _brand_files(media_root)
    jobs_root = tmp_path / "jobs-v2"
    queue_path = tmp_path / "queue-v2.db"

    result = run_preflight(_channel(), _locale_pack(), _inventory(media_root))

    assert result.ok is True
    assert not jobs_root.exists()
    assert not queue_path.exists()


def test_preflight_rejects_locale_mismatch(tmp_path: Path) -> None:
    _brand_files(tmp_path)
    locale_pack = _locale_pack()
    locale_pack["locale"] = "fr-FR"

    result = run_preflight(_channel(), locale_pack, _inventory(tmp_path))

    assert result.ok is False
    assert result.failures[0].code == "LOCALE_MISMATCH"


def test_preflight_requires_voice_only_render_contract(tmp_path: Path) -> None:
    _brand_files(tmp_path)
    channel = _channel()
    channel["render"]["subtitles"]["enabled"] = True
    channel["render"]["concurrency"] = 4

    result = run_preflight(channel, _locale_pack(), _inventory(tmp_path))

    codes = {failure.code for failure in result.failures}
    assert codes == {"SUBTITLES_NOT_ALLOWED", "INVALID_RENDER_CONCURRENCY"}
