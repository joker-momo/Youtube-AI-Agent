from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.localized_v2.production_worker import (
    english_registration,
    resolve_brand_clips,
)


def test_english_registration_requires_exactly_one_enabled_english_channel() -> None:
    registration = object()

    assert english_registration({"english": registration}, locale_of=lambda _item: "en-US") is registration

    with pytest.raises(RuntimeError, match="exactly one"):
        english_registration({}, locale_of=lambda _item: "en-US")
    with pytest.raises(RuntimeError, match="exactly one"):
        english_registration(
            {"one": object(), "two": object()},
            locale_of=lambda _item: "en-US",
        )


def test_resolve_brand_clips_uses_only_v2_media_root(tmp_path: Path) -> None:
    media = tmp_path / "media"
    brand = media / "brand" / "en-US"
    brand.mkdir(parents=True)
    for name in ("intro", "disclaimer", "outro"):
        (brand / f"{name}.mp4").write_bytes(b"video")
    channel = {
        "brand": {
            "introClip": "brand/en-US/intro.mp4",
            "disclaimerClip": "brand/en-US/disclaimer.mp4",
            "outroClip": "brand/en-US/outro.mp4",
        }
    }

    clips = resolve_brand_clips(
        channel,
        media,
        probe=lambda path, _root: type("Clip", (), {"path": path, "duration_sec": 1.0})(),
    )

    assert set(clips) == {"intro", "disclaimer", "outro"}
    assert all(clip.path.is_relative_to(media) for clip in clips.values())


def test_resolve_brand_clips_rejects_escape(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    channel = {
        "brand": {
            "introClip": "../legacy/intro.mp4",
            "disclaimerClip": "brand/en-US/disclaimer.mp4",
            "outroClip": "brand/en-US/outro.mp4",
        }
    }

    with pytest.raises(ValueError, match="brand clip"):
        resolve_brand_clips(channel, media, probe=lambda path, _root: path)
