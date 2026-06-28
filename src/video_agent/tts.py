from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Protocol

from video_agent.qa.spoken_text import normalize_spoken_text


class TTSClient(Protocol):
    def synthesize(self, text: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any]: ...


def _pitch_ratio(semitones: float) -> float:
    """Frequency ratio for a pitch shift of ``semitones`` (negative = lower)."""
    return 2.0 ** (float(semitones) / 12.0)


def _build_pitch_resample_cmd(
    src: str, dst: str, semitones: float, *, in_sr: int, out_sr: int
) -> list[str]:
    """ffmpeg command to pitch-shift by ``semitones`` and resample to ``out_sr`` (mono).

    Uses ``asetrate`` (shifts pitch + formants + rate) then ``atempo`` to restore the
    original duration — duration-preserving, dependency-free. A zero shift skips the
    pitch stage and only resamples.
    """
    if abs(float(semitones)) < 1e-6:
        af = f"aresample={int(out_sr)}"
    else:
        ratio = _pitch_ratio(semitones)
        new_sr = int(round(int(in_sr) * ratio))
        atempo = 1.0 / ratio
        af = f"asetrate={new_sr},atempo={atempo:.6f},aresample={int(out_sr)}"
    return ["ffmpeg", "-y", "-i", str(src), "-af", af, "-ac", "1", str(dst), "-loglevel", "error"]


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
    seed = f"{scene_idx}:{seg_idx}".encode()
    raw = int(hashlib.sha256(seed).hexdigest()[:8], 16)
    signed = (raw / 0xFFFFFFFF) * 2.0 - 1.0  # [-1, 1]
    jitter = signed * (jitter_pct / 100.0)
    return max(0.85, min(1.15, base_speed * (1.0 + jitter)))


def _write_audio_progress(job_dir: Path, percent: float, stage: str) -> None:
    try:
        from video_agent.storage.atomic import atomic_write_json
        progress_dir = job_dir / "json"
        progress_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(progress_dir / "audio_progress.json", {"percent": percent, "stage": stage})
    except Exception:
        pass


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
    # "scene" → synthesize each scene's narration in a single call (lets neural
    # engines like MeloTTS do their own sentence-level prosody); "clause" (default)
    # → split into clauses with humanize pauses (Kokoro path, unchanged).
    segmentation = str(config.get("segmentation", "clause")).lower()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        num_scenes = len(scene_doc["scenes"])
        for index, scene in enumerate(scene_doc["scenes"], start=1):
            _write_audio_progress(
                output_path.parent.parent,
                round(50.0 + ((index - 1) / num_scenes) * 45.0, 1),
                f"tts (scene {index}/{num_scenes})"
            )
            scene_audio: list[np.ndarray] = []
            if index > 1 and scene_lead_in_sec > 0:
                scene_audio.append(np.zeros(int(round(sample_rate * scene_lead_in_sec)), dtype=np.float32))
            narration_text = normalize_spoken_text(str(scene["narration"]))
            if segmentation == "scene":
                full_path = temp_root / f"scene-{index:02d}-full.wav"
                metadata = client.synthesize(narration_text, full_path, dict(config))
                scene_rate = int(metadata.get("sample_rate") or sample_rate)
                if scene_rate != sample_rate:
                    raise RuntimeError(
                        f"TTS sample-rate drift on scene {index}: expected {sample_rate}, got {scene_rate}"
                    )
                audio, read_rate = sf.read(full_path, dtype="float32")
                if read_rate != sample_rate:
                    raise RuntimeError(
                        f"TTS sample-rate mismatch: expected {sample_rate}, got {read_rate}"
                    )
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                scene_audio.append(audio.astype(np.float32))
            else:
                paragraphs = [p for p in narration_text.split("\n\n") if p.strip()]
                if not paragraphs:
                    paragraphs = [str(scene["narration"])]
                seg_counter = 0
                for p_idx, paragraph in enumerate(paragraphs):
                    segments = _split_segments(paragraph)
                    if not segments:
                        segments = [(paragraph, "")]
                    for seg_text, punct in segments:
                        seg_counter += 1
                        segment_path = temp_root / f"scene-{index:02d}-seg-{seg_counter:03d}.wav"
                        seg_cfg = dict(config)
                        if hcfg["enabled"]:
                            seg_cfg["speed"] = _segment_speed(
                                base_speed, index, seg_counter, hcfg["speed_jitter_pct"]
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
                    if hcfg["enabled"] and p_idx < len(paragraphs) - 1:
                        paragraph_sec = max(0.0, hcfg["pause_paragraph_ms"] / 1000.0)
                        if paragraph_sec > 0:
                            scene_audio.append(
                                np.zeros(int(round(sample_rate * paragraph_sec)), dtype=np.float32)
                            )
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
                    # Speech shorter than the planned block → pad with silence.
                    audio = np.pad(audio, (0, target_frames - len(audio)))
                else:
                    # Speech LONGER than the planned block → NEVER hard-truncate
                    # (that clips the final word mid-syllable, esp. the CTA). Keep
                    # the full audio and extend this scene's duration to contain it
                    # so the visual block matches; downstream schedule/tail-repair
                    # use the updated duration. Quality > exact planned timing.
                    actual_sec = len(audio) / sample_rate
                    scene["duration_sec"] = float(round(actual_sec, 2))
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


class MeloTTSClient:
    """MeloTTS provider (Elena voice).

    MeloTTS' dependencies (old transformers/librosa + a Japanese dict) clash with
    the project venv, so it runs in a sidecar venv driven by a persistent worker
    process (model loaded once, JSON-over-stdio). This client lives in the project
    venv: it talks to the worker, then pitch-shifts + resamples the raw 44.1 kHz
    output to the pipeline rate with ffmpeg. Worker starts lazily on first synth.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._language = str(cfg.get("language", "ES"))
        self._device = str(cfg.get("device", "mps"))
        repo_root = Path(__file__).resolve().parents[2]
        self._venv_python = str(
            cfg.get("melo_venv_python") or (repo_root / "tools" / "melo-venv" / "bin" / "python")
        )
        self._worker_script = str(
            cfg.get("melo_worker_script") or (Path(__file__).resolve().parent / "tts_melo_worker.py")
        )
        self._proc: Any = None

    def _ensure_worker(self) -> None:
        if self._proc is not None:
            return
        import json
        import subprocess

        env = dict(os.environ)
        env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        cmd = [
            self._venv_python,
            self._worker_script,
            "--language",
            self._language,
            "--device",
            self._device,
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        ready = self._proc.stdout.readline()
        if not ready:
            raise RuntimeError("MeloTTS worker failed to start (no handshake).")
        handshake = json.loads(ready)
        if not handshake.get("ready"):
            raise RuntimeError(f"MeloTTS worker error on start: {handshake.get('error')}")

    def synthesize(self, text: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any]:
        import json
        import subprocess

        self._ensure_worker()
        speed = float(config.get("speed", 1.0))
        out_sr = int(config.get("sample_rate", config.get("output_sample_rate", 24000)))
        semitones = float(config.get("pitch_shift_semitones", 0.0))
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "melo_raw.wav"
            assert self._proc is not None and self._proc.stdin is not None
            self._proc.stdin.write(
                json.dumps({"text": text, "speed": speed, "out": str(raw_path)}) + "\n"
            )
            self._proc.stdin.flush()
            line = self._proc.stdout.readline() if self._proc.stdout else ""
            if not line:
                raise RuntimeError("MeloTTS worker died during synthesis.")
            resp = json.loads(line)
            if not resp.get("ok"):
                raise RuntimeError(f"MeloTTS worker error: {resp.get('error')}")
            in_sr = int(resp.get("sample_rate", 44100))
            cmd = _build_pitch_resample_cmd(
                str(raw_path), str(output_path), semitones, in_sr=in_sr, out_sr=out_sr
            )
            subprocess.run(cmd, check=True)

        return {
            "provider": "melo",
            "language": self._language,
            "speed": speed,
            "pitch_shift_semitones": semitones,
            "sample_rate": out_sr,
        }

    def close(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
            finally:
                self._proc = None

    def __del__(self) -> None:  # best-effort cleanup
        try:
            self.close()
        except Exception:
            pass


def build_tts_client(config: dict[str, Any]) -> TTSClient | None:
    provider = config.get("provider", "mock-local")
    if provider == "mock-local":
        return None
    if provider == "kokoro":
        return KokoroTTSClient()
    if provider == "melo":
        return MeloTTSClient(config)
    raise ValueError(f"Unsupported TTS provider: {provider}")
