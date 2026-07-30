from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VoiceSpec:
    provider: str
    language: str
    voice_id: str
    speed: float

    @classmethod
    def from_channel(cls, channel: dict[str, Any]) -> VoiceSpec:
        voice = channel["voice"]
        return cls(
            provider=str(voice["provider"]),
            language=str(voice["language"]),
            voice_id=str(voice["voiceId"]),
            speed=float(voice["speed"]),
        )


class AudioCapabilityError(ValueError):
    def __init__(self, locale: str, voice: VoiceSpec):
        super().__init__(
            f"voice capability unavailable for {locale}: "
            f"{voice.provider}/{voice.language}/{voice.voice_id}"
        )
        self.locale = locale
        self.voice = voice

    def to_failure(self) -> dict[str, Any]:
        return {
            "code": "VOICE_UNAVAILABLE",
            "locale": self.locale,
            "stage": "audio",
            "provider": self.voice.provider,
            "artifact": "narration",
            "voiceId": self.voice.voice_id,
            "message": str(self),
            "retryable": False,
        }


class VoiceCapabilityRegistry:
    def __init__(self, capabilities: frozenset[tuple[str, str, str]]):
        self.capabilities = capabilities

    def require(self, locale: str, voice: VoiceSpec) -> None:
        key = (voice.provider, voice.language, voice.voice_id)
        if key not in self.capabilities:
            raise AudioCapabilityError(locale, voice)
