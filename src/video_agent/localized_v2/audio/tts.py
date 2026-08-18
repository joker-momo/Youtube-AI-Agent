from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

from video_agent.localized_v2.audio.capabilities import (
    AudioCapabilityError,
    VoiceCapabilityRegistry,
    VoiceSpec,
)
from video_agent.localized_v2.audio.timing import probe_wav

MAX_NARRATION_CHARS = 5000


class TTSBackend(Protocol):
    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speed: float,
        output_path: Path,
    ) -> None: ...


class TTSFailure(RuntimeError):
    def __init__(
        self,
        *,
        locale: str,
        provider: str,
        voice_id: str,
        scene_id: str,
        cause: BaseException,
    ):
        super().__init__(
            f"localized narration synthesis failed for {locale} scene {scene_id} "
            f"with {provider}/{voice_id} ({type(cause).__name__})"
        )
        self.locale = locale
        self.provider = provider
        self.voice_id = voice_id
        self.scene_id = scene_id

    def to_failure(self) -> dict[str, Any]:
        return {
            "code": "TTS_FAILED",
            "locale": self.locale,
            "stage": "audio",
            "provider": self.provider,
            "artifact": f"{self.scene_id}.wav",
            "voiceId": self.voice_id,
            "message": str(self),
            "retryable": True,
        }


class LocalizedTTS:
    def __init__(
        self,
        backends: dict[str | tuple[str, str], TTSBackend],
        capabilities: VoiceCapabilityRegistry,
    ):
        self.backends = dict(backends)
        self.capabilities = capabilities

    def _backend(self, locale: str, voice: VoiceSpec) -> TTSBackend:
        backend = self.backends.get((voice.provider, voice.language))
        if backend is None:
            backend = self.backends.get(voice.provider)
        if backend is None:
            raise AudioCapabilityError(locale, voice)
        return backend

    def synthesize_scenes(
        self,
        *,
        locale: str,
        voice: VoiceSpec,
        scenes: list[dict[str, Any]],
        output_dir: Path,
    ) -> dict[str, Path]:
        self.capabilities.require(locale, voice)
        backend = self._backend(locale, voice)
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs: dict[str, Path] = {}
        for scene in scenes:
            scene_id = str(scene["id"])
            text = str(scene["narration"]).strip()
            if not text or len(text) > MAX_NARRATION_CHARS or "\x00" in text:
                raise TTSFailure(
                    locale=locale,
                    provider=voice.provider,
                    voice_id=voice.voice_id,
                    scene_id=scene_id,
                    cause=ValueError("invalid narration text"),
                )
            destination = output_dir / f"{scene_id}.wav"
            temporary = output_dir / f".{scene_id}.tmp.wav"
            temporary.unlink(missing_ok=True)
            try:
                backend.synthesize(
                    text,
                    language=voice.language,
                    voice_id=voice.voice_id,
                    speed=voice.speed,
                    output_path=temporary,
                )
                probe_wav(temporary)
                os.replace(temporary, destination)
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                raise TTSFailure(
                    locale=locale,
                    provider=voice.provider,
                    voice_id=voice.voice_id,
                    scene_id=scene_id,
                    cause=exc,
                ) from exc
            outputs[scene_id] = destination
        return outputs
