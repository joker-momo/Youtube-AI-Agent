from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from video_agent.localized_v2.brand_assets import (
    BrandAssetError,
    probe_brand_clip,
)
from video_agent.localized_v2.preflight import CapabilityInventory, run_preflight

from .locale_fixtures import channel, locale_pack


def _probe_result(
    *, duration: str = "2.5", streams: list[dict] | None = None, returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    payload = {
        "format": {"duration": duration},
        "streams": streams if streams is not None else [{"codec_type": "video"}],
    }
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=returncode,
        stdout=json.dumps(payload),
        stderr="",
    )


def test_brand_probe_requires_video_stream_and_positive_duration(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    clip = media / "intro.mp4"
    clip.write_bytes(b"media")

    probed = probe_brand_clip(
        clip,
        media,
        runner=lambda *_args, **_kwargs: _probe_result(),
    )
    assert probed.duration_sec == 2.5

    for result in (
        _probe_result(duration="0"),
        _probe_result(streams=[{"codec_type": "audio"}]),
        _probe_result(returncode=1),
    ):
        with pytest.raises(BrandAssetError):
            probe_brand_clip(
                clip,
                media,
                runner=lambda *_args, result=result, **_kwargs: result,
            )


def test_brand_probe_rejects_symlink_and_outside_media_root(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"media")
    link = media / "intro.mp4"
    link.symlink_to(outside)

    with pytest.raises(BrandAssetError):
        probe_brand_clip(
            link,
            media,
            runner=lambda *_args, **_kwargs: _probe_result(),
        )


def test_preflight_rejects_existing_but_unprobed_brand_clips(tmp_path: Path) -> None:
    configured = channel("en-US")
    media = tmp_path / "media"
    for relative in configured["brand"].values():
        if not isinstance(relative, str) or not relative.endswith(".mp4"):
            continue
        path = media / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not-valid-media")
    inventory = CapabilityInventory(
        media_root=media,
        voices=frozenset({("kokoro", "a", "af_heart")}),
        fonts=frozenset({"Inter"}),
        brand_clips=frozenset(),
    )

    result = run_preflight(configured, locale_pack("en-US"), inventory)

    assert not result.ok
    assert {
        failure.capability
        for failure in result.failures
        if failure.code == "INVALID_BRAND_CLIP"
    } == {"intro", "disclaimer", "outro"}
