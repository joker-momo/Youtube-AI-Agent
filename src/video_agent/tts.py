from __future__ import annotations

import hashlib
import re
from pathlib import Path
import tempfile
from typing import Any, Protocol


class TTSClient(Protocol):
    def synthesize(self, text: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any]: ...


def _humanize_cfg(config: dict[str, Any]) -> dict[str, Any]:
    humanize = config.get("humanize") or {}
    return {
        "enabled": bool(humanize.get("enabled", True)),
        "pause_comma_ms": int(humanize.get("pause_comma_ms", 300)),
        "pause_semicolon_ms": int(humanize.get("pause_semicolon_ms", 450)),
        "pause_sentence_ms": int(humanize.get("pause_sentence_ms", 650)),
        "pause_paragraph_ms": int(humanize.get("pause_paragraph_ms", 900)),
        "speed_jitter_pct": float(humanize.get("speed_jitter_pct", 3.0)),
    }


def _split_segments(text: str) -> list[tuple[str, str]]:
    """Split narration into TTS-friendly segments with trailing punctuation tag."""
    parts: list[tuple[str, str]] = []
    for raw in re.findall(r"[^,;:.!?\n]+[,;:.!?\n]*", text, flags=re.UNICODE):
        token = raw.strip()
        if not token:
            continue
        punct = ""
        for ch in reversed(token):
            if ch in ",;:.!?\n":
                punct = ch
                break
            if ch.isalnum():
                break
        parts.append((token, punct))
    return parts


def _pause_after(punct: str, cfg: dict[str, Any]) -> float:
    if punct == ",":
        return max(0.0, cfg["pause_comma_ms"] / 1000.0)
    if punct in {";", ":"}:
        return max(0.0, cfg["pause_semicolon_ms"] / 1000.0)
    if punct in {".", "!", "?"}:
        return max(0.0, cfg["pause_sentence_ms"] / 1000.0)
    if punct == "\n":
        return max(0.0, cfg["pause_paragraph_ms"] / 1000.0)
    return 0.0


def _segment_speed(base_speed: float, scene_idx: int, seg_idx: int, jitter_pct: float) -> float:
    if jitter_pct <= 0:
        return base_speed
    seed = f"{scene_idx}:{seg_idx}".encode("utf-8")
    raw = int(hashlib.sha256(seed).hexdigest()[:8], 16)
    signed = (raw / 0xFFFFFFFF) * 2.0 - 1.0  # [-1, 1]
    jitter = signed * (jitter_pct / 100.0)
    return max(0.85, min(1.15, base_speed * (1.0 + jitter)))


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
    hcfg = _humanize_cfg(config)
    base_speed = float(config.get("speed", 1.0))
    # Enable dynamic sync by default for optimal audio/video seamlessness
    dynamic_sync = bool(config.get("dynamic_sync", True))
    scene_lead_in_sec = max(0.0, float(config.get("scene_lead_in_sec", 0.0)))

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        for index, scene in enumerate(scene_doc["scenes"], start=1):
            scene_audio: list[np.ndarray] = []
            if index > 1 and scene_lead_in_sec > 0:
                scene_audio.append(np.zeros(int(round(sample_rate * scene_lead_in_sec)), dtype=np.float32))
            segments = _split_segments(str(scene["narration"]))
            if not segments:
                segments = [(str(scene["narration"]), "")]
            for seg_idx, (seg_text, punct) in enumerate(segments, start=1):
                segment_path = temp_root / f"scene-{index:02d}-seg-{seg_idx:03d}.wav"
                seg_cfg = dict(config)
                if hcfg["enabled"]:
                    seg_cfg["speed"] = _segment_speed(
                        base_speed, index, seg_idx, hcfg["speed_jitter_pct"]
                    )
                metadata = client.synthesize(seg_text, segment_path, seg_cfg)
                scene_rate = int(metadata.get("sample_rate") or sample_rate)
                if scene_rate != sample_rate:
                    raise RuntimeError(
                        f"TTS sample-rate drift on scene {index}: expected {sample_rate}, got {scene_rate}"
                    )
                audio, read_rate = sf.read(segment_path, dtype="float32")
                if read_rate != sample_rate:
                    raise RuntimeError(
                        f"TTS sample-rate mismatch: expected {sample_rate}, got {read_rate}"
                    )
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                scene_audio.append(audio.astype(np.float32))
                if hcfg["enabled"]:
                    pause_sec = _pause_after(punct, hcfg)
                    if pause_sec > 0:
                        silence_frames = int(round(sample_rate * pause_sec))
                        scene_audio.append(np.zeros(silence_frames, dtype=np.float32))
            if scene_audio:
                audio = np.concatenate(scene_audio)
            else:
                # If no audio (silent scene), fall back to original duration or 5s default
                fallback_dur = float(scene.get("duration_sec") or 5.0)
                audio = np.zeros(max(1, int(fallback_dur * sample_rate)), dtype=np.float32)

            if dynamic_sync:
                # Dynamically set scene duration in seconds to perfectly match voice duration
                actual_duration = len(audio) / sample_rate
                # Update individual scene duration precisely (e.g. 12.35)
                scene["duration_sec"] = float(round(actual_duration, 2))
            else:
                target_frames = max(1, int(float(scene["duration_sec"]) * sample_rate))
                if len(audio) < target_frames:
                    audio = np.pad(audio, (0, target_frames - len(audio)))
                else:
                    audio = audio[:target_frames]
            chunks.append(audio)

    if dynamic_sync:
        # Update overall total_duration_sec as integer for schema validation compatibility
        scene_doc["total_duration_sec"] = int(round(sum(float(s["duration_sec"]) for s in scene_doc["scenes"])))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_path, np.concatenate(chunks), sample_rate)
    return {
        "provider": config.get("provider", "kokoro"),
        "voice_id": config.get("voice_id"),
        "lang_code": config.get("lang_code"),
        "speed": base_speed,
        "humanize": hcfg,
        "scene_lead_in_sec": round(scene_lead_in_sec, 3),
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
