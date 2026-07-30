from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from video_agent.localized_v2.config import (
    ContractValidationError,
    load_channel_config,
    validate_artifact,
)
from video_agent.localized_v2.contracts import ArtifactKind
from video_agent.localized_v2.registry import SUPPORTED_LOCALES, LocaleRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "schemas"


def _locale_pack(locale: str) -> dict:
    language, market = locale.split("-", 1)
    return {
        "schemaVersion": "localized-locale-v2/v1",
        "locale": locale,
        "language": language,
        "market": market,
        "audienceAddress": {"formal": False, "preferred": "you"},
        "lexicalPreferences": {"prefer": ["daily movement"], "avoid": ["miracle cure"]},
        "measurement": {"system": "metric", "temperature": "celsius"},
        "dates": {"order": "DMY"},
        "numbers": {"decimalSeparator": "."},
        "medicalSafety": {
            "softClaims": ["research suggests"],
            "prohibitedClaims": ["cures disease", "guaranteed result"],
            "disclaimer": "Educational information only.",
        },
        "seo": {
            "titleMaxChars": 70,
            "keywordStyle": "natural",
            "keywordCues": ["healthy aging"],
            "thumbnailMaxChars": 30,
            "pinnedCommentStyle": "warm and concise",
        },
        "narration": {"wordsPerMinute": 125, "sentenceMaxWords": 22},
        "fonts": {"families": ["Inter"], "requiredCodepoints": ["0041"]},
        "visuals": {"peopleContext": "locally representative", "avoid": ["stereotypes"]},
        "textMetrics": {"charsPerWord": 5.0, "expansionRatio": 1.0},
    }


def _channel(locale: str = "en-US") -> dict:
    return {
        "schemaVersion": "localized-channel-v2/v1",
        "enabled": True,
        "rolloutOrder": 1,
        "channelId": "healthy-life-en",
        "locale": locale,
        "brand": {
            "name": "Healthy Life",
            "introClip": "brand/intro.mp4",
            "disclaimerClip": "brand/disclaimer.mp4",
            "outroClip": "brand/outro.mp4",
        },
        "voice": {
            "provider": "kokoro",
            "language": "a",
            "voiceId": "af_heart",
            "speed": 1.0,
        },
        "render": {
            "composition": "LocalizedV2ChannelVideo",
            "concurrency": "auto",
            "subtitles": {"enabled": False},
        },
        "content": {"type": "long_form", "targetDurationSec": 840},
        "canary": {
            "status": "APPROVED",
            "checks": {
                "audio": "PASS",
                "font": "PASS",
                "render": "PASS",
                "humanReview": "PASS",
                "dashboardLifecycle": "PASS",
            },
            "evidence": [
                "canary/audio.json",
                "canary/font.json",
                "canary/render.json",
                "canary/human-review.json",
                "canary/dashboard-lifecycle.json",
            ],
        },
    }


def test_supported_bcp47_tags_resolve_to_exactly_one_pack(tmp_path: Path) -> None:
    assert SUPPORTED_LOCALES == ("en-US", "fr-FR", "pt-BR", "ko-KR", "ja-JP")
    for locale in SUPPORTED_LOCALES:
        (tmp_path / f"{locale}.yaml").write_text(
            yaml.safe_dump(_locale_pack(locale), allow_unicode=True),
            encoding="utf-8",
        )

    registry = LocaleRegistry(tmp_path, SCHEMA_ROOT)

    for locale in SUPPORTED_LOCALES:
        assert registry.resolve(locale)["locale"] == locale
    with pytest.raises(KeyError, match="unsupported locale"):
        registry.resolve("es-ES")


def test_unknown_channel_field_is_rejected(tmp_path: Path) -> None:
    payload = _channel()
    payload["legacyChannelFallback"] = "vida-plena-45"
    path = tmp_path / "channel.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ContractValidationError) as error:
        load_channel_config(path, SCHEMA_ROOT)

    assert error.value.code == "INVALID_CHANNEL_CONFIG"
    assert "legacyChannelFallback" in str(error.value)


def test_v2_config_validation_never_reads_legacy_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "channel.yaml"
    path.write_text(yaml.safe_dump(_channel()), encoding="utf-8")
    reads: list[str] = []
    original = Path.read_text

    def recording_read_text(self: Path, *args, **kwargs) -> str:
        reads.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", recording_read_text)

    loaded = load_channel_config(path, SCHEMA_ROOT)

    assert loaded["locale"] == "en-US"
    assert not any("vida-plena-45" in read for read in reads)
    assert not any(read.endswith("channel-config.schema.json") for read in reads)


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (ArtifactKind.IDEA, {"schemaVersion": "localized-idea-v2/v1"}),
        (ArtifactKind.SCRIPT, {"schemaVersion": "localized-script-v2/v1"}),
        (ArtifactKind.SCENES, {"schemaVersion": "localized-scenes-v2/v1"}),
        (ArtifactKind.SEO, {"schemaVersion": "localized-seo-v2/v1"}),
        (ArtifactKind.QA, {"schemaVersion": "localized-qa-v2/v1"}),
        (ArtifactKind.AUDIO_TIMING, {"schemaVersion": "localized-audio-timing-v2/v1"}),
        (ArtifactKind.ASSET_MANIFEST, {"schemaVersion": "localized-assets-v2/v1"}),
        (ArtifactKind.RENDER_PROPS, {"schemaVersion": "localized-render-props-v2/v1"}),
    ],
)
def test_incomplete_artifacts_fail_before_promotion(
    kind: ArtifactKind, payload: dict
) -> None:
    with pytest.raises(ContractValidationError) as error:
        validate_artifact(payload, kind, SCHEMA_ROOT)

    assert error.value.code == "INVALID_ARTIFACT"
    assert error.value.details["artifactKind"] == kind.value


def test_artifact_unknown_fields_fail_closed() -> None:
    payload = {
        "schemaVersion": "localized-script-v2/v1",
        "locale": "en-US",
        "title": "A calm daily habit",
        "sections": [{"id": "intro", "narration": "Start with one small step."}],
        "spanishFallback": "Nunca",
    }

    with pytest.raises(ContractValidationError, match="spanishFallback"):
        validate_artifact(json.loads(json.dumps(payload)), ArtifactKind.SCRIPT, SCHEMA_ROOT)
