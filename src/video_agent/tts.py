from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, Protocol


class TTSClient(Protocol):
    def synthesize(self, text: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any]: ...


def synthesize_scene_track(
    scene_doc: dict[str, Any],
    output_path: Path,
    config: dict[str, Any],
    client: TTSClient,
) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    sample_rate = int(config.get("sample_rate", 24000))
    chunks = []
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        for index, scene in enumerate(scene_doc["scenes"], start=1):
            scene_audio_path = temp_root / f"scene-{index:02d}.wav"
            metadata = client.synthesize(scene["narration"], scene_audio_path, config)
            sample_rate = int(metadata.get("sample_rate") or sample_rate)
            audio, read_rate = sf.read(scene_audio_path, dtype="float32")
            if read_rate != sample_rate:
                raise RuntimeError(f"TTS sample-rate mismatch: expected {sample_rate}, got {read_rate}")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            target_frames = max(1, int(float(scene["duration_sec"]) * sample_rate))
            if len(audio) < target_frames:
                audio = np.pad(audio, (0, target_frames - len(audio)))
            else:
                audio = audio[:target_frames]
            chunks.append(audio)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_path, np.concatenate(chunks), sample_rate)
    return {
        "provider": config.get("provider", "kokoro"),
        "voice_id": config.get("voice_id"),
        "lang_code": config.get("lang_code"),
        "speed": float(config.get("speed", 1.0)),
        "sample_rate": sample_rate,
        "duration_sec": round(sum(float(scene["duration_sec"]) for scene in scene_doc["scenes"]), 3),
    }


class KokoroTTSClient:
    def __init__(self) -> None:
        self._pipelines: dict[tuple[str, str], Any] = {}

    def synthesize(self, text: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any]:
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline

        lang_code = config.get("lang_code", "e")
        voice_id = config.get("voice_id", "ef_dora")
        repo_id = config.get("repo_id", "hexgrad/Kokoro-82M")
        speed = float(config.get("speed", 1.0))
        sample_rate = int(config.get("sample_rate", 24000))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        pipeline_key = (lang_code, repo_id)
        if pipeline_key not in self._pipelines:
            self._pipelines[pipeline_key] = KPipeline(lang_code=lang_code, repo_id=repo_id)
        pipeline = self._pipelines[pipeline_key]
        chunks = []
        for _, _, audio in pipeline(text, voice=voice_id, speed=speed):
            chunks.append(np.asarray(audio, dtype=np.float32))
        if not chunks:
            raise RuntimeError("Kokoro produced no audio chunks.")
        sf.write(output_path, np.concatenate(chunks), sample_rate)
        return {
            "provider": "kokoro",
            "voice_id": voice_id,
            "lang_code": lang_code,
            "repo_id": repo_id,
            "speed": speed,
            "sample_rate": sample_rate,
        }


def build_tts_client(config: dict[str, Any]) -> TTSClient | None:
    provider = config.get("provider", "mock-local")
    if provider == "mock-local":
        return None
    if provider == "kokoro":
        return KokoroTTSClient()
    raise ValueError(f"Unsupported TTS provider: {provider}")
