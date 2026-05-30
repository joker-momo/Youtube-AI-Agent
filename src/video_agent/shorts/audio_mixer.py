"""Mix Kokoro narration with local background music under voice (ffmpeg).

The ffmpeg command is built by a pure function so it is unit-testable; the
mix itself shells out. Voice stays dominant: only the music is attenuated.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from video_agent.shorts import paths


def _repo_root() -> Path:
    from video_agent.contracts import repo_root

    return repo_root()


def resolve_music_file(music_track: str, channel_config: dict, repo_root: Path | None = None) -> Path | None:
    tracks = ((channel_config.get("music_library") or {}).get("tracks")) or {}
    entry = tracks.get(music_track)
    if not entry or not entry.get("file"):
        return None
    root = repo_root or _repo_root()
    return (root / entry["file"]).resolve() if not Path(entry["file"]).is_absolute() else Path(entry["file"])


def track_volume_db_below_voice(music_track: str, channel_config: dict) -> int:
    tracks = ((channel_config.get("music_library") or {}).get("tracks")) or {}
    entry = tracks.get(music_track) or {}
    if entry.get("default_volume_db_below_voice") is not None:
        return int(entry["default_volume_db_below_voice"])
    return int(((channel_config.get("shorts") or {}).get("music") or {}).get("volume_db_below_voice", 20))


def build_mix_command(
    *,
    narration_wav: Path,
    music_file: Path,
    out_path: Path,
    duration_sec: float,
    volume_db_below_voice: int,
    fade_in_ms: int,
    fade_out_ms: int,
) -> list[str]:
    dur = round(float(duration_sec), 2)
    fin = round(fade_in_ms / 1000.0, 3)
    fout = round(fade_out_ms / 1000.0, 3)
    fade_out_start = max(0.0, dur - fout)
    bg = (
        f"[1:a]aloop=loop=-1:size=2000000000,atrim=0:{dur},"
        f"volume=-{int(volume_db_below_voice)}dB,"
        f"afade=t=in:st=0:d={fin},afade=t=out:st={fade_out_start}:d={fout}[bg]"
    )
    mix = "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]"
    filter_complex = f"{bg};{mix}"
    return [
        "ffmpeg",
        "-y",
        "-i", str(narration_wav),
        "-i", str(music_file),
        "-filter_complex", filter_complex,
        "-map", "[a]",
        "-c:a", "aac",
        "-b:a", "192k",
        str(out_path),
    ]


def mix_short_audio(
    short_dir: Path,
    narration_wav: Path,
    music_track: str | None,
    channel_config: dict,
    duration_sec: float,
) -> Path:
    audio_dir = short_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    out_path = audio_dir / "short_mix.m4a"

    music_file = resolve_music_file(music_track, channel_config) if music_track else None
    mcfg = (channel_config.get("shorts") or {}).get("music") or {}

    if not music_file or not Path(music_file).exists() or not mcfg.get("enabled", True):
        # No music → narration becomes the mix (voice only).
        shutil.copyfile(narration_wav, out_path)
        return out_path

    cmd = build_mix_command(
        narration_wav=narration_wav,
        music_file=Path(music_file),
        out_path=out_path,
        duration_sec=duration_sec,
        volume_db_below_voice=track_volume_db_below_voice(music_track, channel_config),
        fade_in_ms=int(mcfg.get("fade_in_ms", 120)),
        fade_out_ms=int(mcfg.get("fade_out_ms", 450)),
    )
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path
