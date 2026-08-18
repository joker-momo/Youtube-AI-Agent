from __future__ import annotations

import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AudioProbe:
    duration_sec: float
    channels: int
    sample_rate: int
    sample_width: int
    frames: int


def probe_wav(path: Path) -> AudioProbe:
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getcomptype() != "NONE":
                raise ValueError("compressed WAV narration is not supported")
            channels = audio.getnchannels()
            sample_rate = audio.getframerate()
            sample_width = audio.getsampwidth()
            frames = audio.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"invalid WAV narration: {path.name}") from exc
    if channels not in {1, 2} or sample_rate <= 0 or sample_width not in {1, 2, 3, 4}:
        raise ValueError(f"unsupported WAV narration format: {path.name}")
    if frames <= 0:
        raise ValueError(f"empty WAV narration: {path.name}")
    return AudioProbe(
        duration_sec=frames / sample_rate,
        channels=channels,
        sample_rate=sample_rate,
        sample_width=sample_width,
        frames=frames,
    )


def compile_audio_timing(
    locale: str,
    scenes: list[dict[str, Any]],
    audio_files: dict[str, Path],
) -> dict[str, Any]:
    expected_ids = [str(scene["id"]) for scene in scenes]
    if set(expected_ids) != set(audio_files):
        raise ValueError("scene narration files do not match localized scene IDs")
    start = 0.0
    timings: list[dict[str, Any]] = []
    for scene_id in expected_ids:
        duration = probe_wav(audio_files[scene_id]).duration_sec
        timings.append(
            {
                "id": scene_id,
                "startSec": round(start, 6),
                "durationSec": round(duration, 6),
            }
        )
        start += duration
    return {
        "schemaVersion": "localized-audio-timing-v2/v1",
        "locale": locale,
        "totalDurationSec": round(start, 6),
        "scenes": timings,
    }


def concatenate_wav(
    audio_files: list[Path],
    destination: Path,
) -> AudioProbe:
    if not audio_files:
        raise ValueError("at least one narration file is required")
    probes = [probe_wav(path) for path in audio_files]
    signature = {
        (probe.channels, probe.sample_rate, probe.sample_width) for probe in probes
    }
    if len(signature) != 1:
        raise ValueError("scene narration WAV formats must match")
    channels, sample_rate, sample_width = signature.pop()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(sample_width)
            output.setframerate(sample_rate)
            for source in audio_files:
                with wave.open(str(source), "rb") as audio:
                    output.writeframes(audio.readframes(audio.getnframes()))
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return probe_wav(destination)
