from __future__ import annotations

import wave
from pathlib import Path


def write_silence_wav(
    path: Path,
    duration_sec: float,
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
    sample_width: int = 2,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, round(duration_sec * sample_rate))
    frame = b"\x00" * sample_width * channels
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(sample_width)
        audio.setframerate(sample_rate)
        audio.writeframes(frame * frames)


class FakeTTSBackend:
    def __init__(self, *, duration_sec: float = 0.25, fail: bool = False):
        self.duration_sec = duration_sec
        self.fail = fail
        self.calls: list[dict] = []

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speed: float,
        output_path: Path,
    ) -> None:
        self.calls.append(
            {
                "text": text,
                "language": language,
                "voiceId": voice_id,
                "speed": speed,
            }
        )
        if self.fail:
            raise RuntimeError("synthetic backend failure")
        write_silence_wav(output_path, self.duration_sec)
