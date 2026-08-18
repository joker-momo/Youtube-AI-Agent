from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.tts import KokoroTTSClient


class KokoroBackend:
    """Thin V2-only adapter around the deterministic low-level Kokoro client."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        sample_rate: int = 24_000,
    ) -> None:
        self.client = client or KokoroTTSClient()
        self.sample_rate = sample_rate

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speed: float,
        output_path: Path,
    ) -> None:
        self.client.synthesize(
            text,
            output_path,
            {
                "provider": "kokoro",
                "lang_code": language,
                "voice_id": voice_id,
                "speed": speed,
                "sample_rate": self.sample_rate,
            },
        )
