from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.localized_v2.audio.capabilities import (
    AudioCapabilityError,
    VoiceCapabilityRegistry,
    VoiceSpec,
)
from video_agent.localized_v2.audio.tts import LocalizedTTS, TTSFailure

from .audio_fixtures import FakeTTSBackend


def test_unsupported_voice_fails_before_backend_call(tmp_path: Path) -> None:
    backend = FakeTTSBackend()
    tts = LocalizedTTS(
        {"kokoro": backend},
        VoiceCapabilityRegistry(frozenset()),
    )
    voice = VoiceSpec("kokoro", "a", "af_heart", 1.0)

    with pytest.raises(AudioCapabilityError):
        tts.synthesize_scenes(
            locale="en-US",
            voice=voice,
            scenes=[{"id": "opening", "narration": "A calm opening."}],
            output_dir=tmp_path,
        )

    assert backend.calls == []
    assert list(tmp_path.iterdir()) == []


def test_exact_voice_is_used_without_provider_fallback(tmp_path: Path) -> None:
    kokoro = FakeTTSBackend()
    melo = FakeTTSBackend()
    tts = LocalizedTTS(
        {"kokoro": kokoro, "melo": melo},
        VoiceCapabilityRegistry(frozenset({("kokoro", "a", "af_heart")})),
    )

    outputs = tts.synthesize_scenes(
        locale="en-US",
        voice=VoiceSpec("kokoro", "a", "af_heart", 0.95),
        scenes=[{"id": "opening", "narration": "A calm opening."}],
        output_dir=tmp_path,
    )

    assert outputs["opening"].is_file()
    assert kokoro.calls[0]["voiceId"] == "af_heart"
    assert kokoro.calls[0]["speed"] == 0.95
    assert melo.calls == []


def test_same_provider_routes_each_language_to_its_qualified_backend(
    tmp_path: Path,
) -> None:
    korean = FakeTTSBackend()
    japanese = FakeTTSBackend()
    tts = LocalizedTTS(
        {("melo", "KR"): korean, ("melo", "JP"): japanese},
        VoiceCapabilityRegistry(
            frozenset(
                {
                    ("melo", "KR", "KR-voice"),
                    ("melo", "JP", "JP-voice"),
                }
            )
        ),
    )

    tts.synthesize_scenes(
        locale="ko-KR",
        voice=VoiceSpec("melo", "KR", "KR-voice", 1.0),
        scenes=[{"id": "korean", "narration": "건강한 습관입니다."}],
        output_dir=tmp_path / "ko",
    )
    tts.synthesize_scenes(
        locale="ja-JP",
        voice=VoiceSpec("melo", "JP", "JP-voice", 1.0),
        scenes=[{"id": "japanese", "narration": "健やかな習慣です。"}],
        output_dir=tmp_path / "ja",
    )

    assert [call["language"] for call in korean.calls] == ["KR"]
    assert [call["language"] for call in japanese.calls] == ["JP"]


def test_missing_backend_fails_as_capability_before_synthesis(tmp_path: Path) -> None:
    voice = VoiceSpec("kokoro", "a", "af_heart", 1.0)
    tts = LocalizedTTS(
        {},
        VoiceCapabilityRegistry(frozenset({("kokoro", "a", "af_heart")})),
    )

    with pytest.raises(AudioCapabilityError):
        tts.synthesize_scenes(
            locale="en-US",
            voice=voice,
            scenes=[{"id": "opening", "narration": "A calm opening."}],
            output_dir=tmp_path,
        )

    assert list(tmp_path.iterdir()) == []


def test_tts_failure_removes_unpromoted_partial_file(tmp_path: Path) -> None:
    backend = FakeTTSBackend(fail=True)
    tts = LocalizedTTS(
        {"kokoro": backend},
        VoiceCapabilityRegistry(frozenset({("kokoro", "a", "af_heart")})),
    )

    with pytest.raises(TTSFailure) as error:
        tts.synthesize_scenes(
            locale="en-US",
            voice=VoiceSpec("kokoro", "a", "af_heart", 1.0),
            scenes=[{"id": "opening", "narration": "A calm opening."}],
            output_dir=tmp_path,
        )

    assert error.value.to_failure()["locale"] == "en-US"
    assert error.value.to_failure()["provider"] == "kokoro"
    assert list(tmp_path.iterdir()) == []
