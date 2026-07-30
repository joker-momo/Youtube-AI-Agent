from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALIZED_ROOT = REPO_ROOT / "remotion" / "src" / "localized-v2"


def test_v2_entrypoint_reuses_stable_channel_video_without_copying_it() -> None:
    wrapper = (LOCALIZED_ROOT / "LocalizedV2ChannelVideo.tsx").read_text(
        encoding="utf-8"
    )
    root = (LOCALIZED_ROOT / "Root.tsx").read_text(encoding="utf-8")

    assert "from '../ChannelVideo'" in wrapper
    assert "LocalizedV2ChannelVideo" in root
    assert "ChannelVideoStandard" not in root
    assert "Bienvenido" not in wrapper
    assert "Gracias por ver" not in wrapper
    assert "SubtitleOverlay" not in wrapper
    assert "outro_video_path: null" in wrapper
    assert "<MediaVideo" in wrapper


def test_v2_font_manifest_covers_every_supported_locale() -> None:
    manifest = json.loads(
        (
            REPO_ROOT
            / "remotion"
            / "public"
            / "localized-v2"
            / "fonts"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert set(manifest["locales"]) == {
        "en-US",
        "fr-FR",
        "pt-BR",
        "ko-KR",
        "ja-JP",
    }
    assert manifest["locales"]["ko-KR"]["family"] == "Noto Sans KR"
    assert manifest["locales"]["ja-JP"]["family"] == "Noto Sans JP"


def test_v2_renderer_has_no_bottom_subtitle_or_transcription_dependency() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in LOCALIZED_ROOT.rglob("*")
        if path.suffix in {".ts", ".tsx"}
    ).casefold()

    assert "whisper" not in source
    assert "word_highlight" not in source
    assert "subtitleoverlay" not in source
    assert "word_segments" in source  # explicitly rejected by the V2 contract
    assert "enabled: true" in source  # adversarial test fixture
    assert "subtitles must remain disabled" in source
