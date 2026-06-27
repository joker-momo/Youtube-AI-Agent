"""Shared audio primitives (narration synth + bgm mix + silent wav + progress).

Pure plumbing used by BOTH the long-video and Shorts asset pipelines (the long
prepare_assets and the Shorts narration pass both drive _synthesize_narration_and_mix
via prepare_assets(render_tts=True)). Must not import from video_agent.stages or
video_agent.shorts (leaf module — see tests/test_asset_layer_boundary.py).
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

from video_agent.contracts import ARTIFACT_AUDIO_QA, repo_root
from video_agent.qa.tts_report import audio_qa_report, build_tts_report
from video_agent.tts import build_tts_client, synthesize_scene_track
from video_agent.utils.json_io import write_json


def _write_silent_wav(path: Path, duration_sec: int, sample_rate: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = duration_sec * sample_rate
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        chunk = b"\x00\x00" * sample_rate
        for _ in range(math.ceil(frame_count / sample_rate)):
            handle.writeframes(chunk)


def _choose_bgm_track(job_dir: Path, music_cfg: dict[str, Any]) -> Path | None:
    configured_file = str(music_cfg.get("file") or "").strip()
    if not configured_file:
        return None
    configured = Path(configured_file)
    if not configured.is_absolute():
        configured = repo_root() / configured
    return configured.resolve() if configured.is_file() else None


def _mix_bgm_with_narration(
    narration_path: Path,
    bgm_path: Path,
    mixed_path: Path,
    *,
    voice_gain_db: float = -4.5,
    bgm_gain_db: float = -24.0,
    duck_db: float = 8.0,
    target_lufs: float = -13.6,
    target_tp: float = 0.0,
    target_lra: float = 4.8,
    out_sample_rate: int = 44100,
    out_bitrate: str = "128k",
    stereo: bool = True,
) -> bool:
    if not narration_path.exists() or not bgm_path.exists():
        return False
    ratio = max(3.0, min(12.0, 1.5 + duck_db * 0.7))
    pan = "pan=stereo|c0=c0|c1=c0," if stereo else ""
    # Apply alimiter after loudnorm: hard ceiling at TP-aware level to prevent
    # clipping when mixed peaks ride the loudness target.
    limiter_ceiling_db = min(target_tp, -1.0)
    filter_complex = (
        f"[0:a]volume={voice_gain_db}dB[vox];"
        f"[1:a]volume={bgm_gain_db}dB,aloop=loop=-1:size=2147483647[bgmraw];"
        f"[bgmraw][vox]sidechaincompress=threshold=0.03:ratio={ratio:.2f}:attack=20:release=300:makeup=1[bgmduck];"
        f"[vox][bgmduck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
        f"{pan}loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra},"
        f"alimiter=limit={limiter_ceiling_db}dB[out]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(narration_path),
        "-i",
        str(bgm_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-c:a",
        "aac",
        "-b:a",
        out_bitrate,
        "-ar",
        str(out_sample_rate),
        str(mixed_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return mixed_path.exists() and mixed_path.stat().st_size > 0


def _write_audio_progress(job_dir: Path, percent: float, stage: str) -> None:
    try:
        from video_agent.storage.atomic import atomic_write_json
        progress_dir = job_dir / "json"
        progress_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(progress_dir / "audio_progress.json", {"percent": percent, "stage": stage})
    except Exception:
        pass


def _synthesize_narration_and_mix(
    job_dir: Path,
    scene_doc: dict[str, Any],
    *,
    tts_config: dict[str, Any] | None,
    tts_client: Any | None,
    assets_dir: Path,
    public_assets_dir: Path,
) -> tuple[dict[str, Any], str, str | None]:
    """TTS synthesis + optional BGM mix. Returns (audio_metadata, narration_ref, music_ref)."""
    _write_audio_progress(job_dir, 50.0, "tts")
    tts_config = tts_config or {"provider": "mock-local"}
    music_cfg = (tts_config.get("music") or {}) if isinstance(tts_config, dict) else {}
    tts_provider = tts_config.get("provider", "mock-local")
    narration_path = assets_dir / "narration.wav"
    tts_durations_path = assets_dir / "tts_durations.json"
    audio_metadata = {"provider": "mock-local", "source": "silent_placeholder", "sample_rate": 44100}
    if tts_provider == "mock-local":
        _write_silent_wav(narration_path, int(scene_doc["total_duration_sec"]))
    elif narration_path.exists() and narration_path.stat().st_size > 0:
        # Narration already synthesized — skip re-synthesis, but restore per-scene durations.
        audio_metadata = {
            "provider": tts_provider,
            "source": "tts_cached",
            "sample_rate": tts_config.get("sample_rate", 24000),
        }
        # Restore per-scene duration_sec from saved file so scene timings match speech exactly.
        if tts_durations_path.exists():
            try:
                from video_agent.utils.json_io import read_json as _rj
                saved = _rj(tts_durations_path)  # {scene_id: duration_sec}
                for scene in scene_doc["scenes"]:
                    if scene["id"] in saved:
                        scene["duration_sec"] = saved[scene["id"]]
                scene_doc["total_duration_sec"] = int(
                    round(sum(float(s["duration_sec"]) for s in scene_doc["scenes"]))
                )
            except Exception:
                pass  # Non-fatal: fall back to original durations
    else:
        client = tts_client or build_tts_client(tts_config)
        try:
            audio_metadata = synthesize_scene_track(scene_doc, narration_path, tts_config, client) | {"source": "tts"}
            # Persist per-scene durations so future re-renders (tts_cached) stay in sync.
            from video_agent.utils.json_io import write_json as _wj
            _wj(tts_durations_path, {s["id"]: s["duration_sec"] for s in scene_doc["scenes"]})
            try:
                report = build_tts_report(scene_doc["scenes"], audio_metadata, tts_config)
                _wj(job_dir / "tts_report.json", report)
            except Exception:
                pass
        except Exception:
            # Fallback for environments without optional TTS runtime deps
            # or network/model bootstrap failures in external providers.
            _write_silent_wav(narration_path, int(scene_doc["total_duration_sec"]))
            audio_metadata = {
                "provider": "mock-local",
                "source": "silent_placeholder",
                "sample_rate": 44100,
            }
    _write_audio_progress(job_dir, 95.0, "mixing")
    public_narration_path = public_assets_dir / "narration.wav"
    shutil.copy2(narration_path, public_narration_path)
    public_narration_ref = f"jobs/{job_dir.name}/assets/narration.wav"
    public_music_ref = None

    bgm_track = _choose_bgm_track(job_dir, music_cfg)
    if bgm_track is not None:
        bgm_copy = assets_dir / f"bgm{bgm_track.suffix.lower()}"
        shutil.copy2(bgm_track, bgm_copy)
        public_bgm_copy = public_assets_dir / bgm_copy.name
        shutil.copy2(bgm_copy, public_bgm_copy)
        public_music_ref = f"jobs/{job_dir.name}/assets/{bgm_copy.name}"
        mixed_path = assets_dir / "narration_mixed.m4a"
        voice_gain_db = float(music_cfg.get("voice_gain_db", -4.5))
        bgm_gain_db = float(music_cfg.get("level_db", -24.0))
        duck_db = float(music_cfg.get("duck_db", 8.0))
        target_lufs = float(music_cfg.get("target_lufs", -13.6))
        target_tp = float(music_cfg.get("target_tp_dbtp", 0.0))
        target_lra = float(music_cfg.get("target_lra", 4.8))
        out_sr = int(music_cfg.get("sample_rate", 44100))
        out_br = str(music_cfg.get("bitrate", "128k"))
        out_stereo = bool(music_cfg.get("stereo", True))
        if _mix_bgm_with_narration(
            narration_path,
            bgm_copy,
            mixed_path,
            voice_gain_db=voice_gain_db,
            bgm_gain_db=bgm_gain_db,
            duck_db=duck_db,
            target_lufs=target_lufs,
            target_tp=target_tp,
            target_lra=target_lra,
            out_sample_rate=out_sr,
            out_bitrate=out_br,
            stereo=out_stereo,
        ):
            public_mixed_path = public_assets_dir / mixed_path.name
            shutil.copy2(mixed_path, public_mixed_path)
            public_narration_ref = f"jobs/{job_dir.name}/assets/{mixed_path.name}"
            audio_metadata = {
                **audio_metadata,
                "mix": {
                    "bgm_enabled": True,
                    "bgm_track": bgm_track.name,
                    "voice_gain_db": voice_gain_db,
                    "bgm_gain_db": bgm_gain_db,
                    "duck_db": duck_db,
                    "target_lufs": target_lufs,
                    "target_tp": target_tp,
                    "target_lra": target_lra,
                    "sample_rate": out_sr,
                    "bitrate": out_br,
                    "stereo": out_stereo,
                    "limiter_ceiling_db": min(target_tp, -1.0),
                },
            }
            try:
                br_kbps = int(re.sub(r"[^0-9]", "", str(out_br)) or 0) or None
                qa_report = audio_qa_report(
                    integrated_lufs=target_lufs,
                    true_peak_dbtp=target_tp,
                    bitrate_kbps=br_kbps,
                    duration_sec=None,
                    codec="aac",
                    sample_rate=out_sr,
                )
                write_json(job_dir / ARTIFACT_AUDIO_QA, qa_report)
            except Exception:
                pass
        else:
            audio_metadata = {**audio_metadata, "mix": {"bgm_enabled": False, "error": "ffmpeg_mix_failed"}}

    return audio_metadata, public_narration_ref, public_music_ref
