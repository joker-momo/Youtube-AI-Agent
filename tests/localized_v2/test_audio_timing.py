from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.localized_v2.audio.timing import (
    compile_audio_timing,
    concatenate_wav,
    probe_wav,
)

from .audio_fixtures import write_silence_wav


def test_measured_audio_is_sequential_timing_authority(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    write_silence_wav(first, 0.25)
    write_silence_wav(second, 0.5)
    scenes = [{"id": "first"}, {"id": "second"}]

    timing = compile_audio_timing(
        "en-US",
        scenes,
        {"first": first, "second": second},
    )

    assert timing["scenes"] == [
        {"id": "first", "startSec": 0.0, "durationSec": 0.25},
        {"id": "second", "startSec": 0.25, "durationSec": 0.5},
    ]
    assert timing["totalDurationSec"] == 0.75


def test_narration_concatenation_preserves_measured_duration(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    combined = tmp_path / "narration.wav"
    write_silence_wav(first, 0.125)
    write_silence_wav(second, 0.375)

    result = concatenate_wav([first, second], combined)

    assert result.duration_sec == pytest.approx(0.5)
    assert probe_wav(combined).duration_sec == pytest.approx(0.5)


def test_audio_timing_rejects_scene_file_mismatch(tmp_path: Path) -> None:
    narration = tmp_path / "opening.wav"
    write_silence_wav(narration, 0.25)

    with pytest.raises(ValueError, match="scene narration files"):
        compile_audio_timing(
            "en-US",
            [{"id": "opening"}, {"id": "later"}],
            {"opening": narration},
        )
