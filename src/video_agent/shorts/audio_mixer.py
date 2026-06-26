"""Mix Kokoro narration with local background music under voice (ffmpeg).

The ffmpeg command is built by a pure function so it is unit-testable; the
mix itself shells out. Voice stays dominant: only the music is attenuated.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json


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
    voice_gain_db: float = -4.5,
    duck_db: float = 8.0,
    target_lufs: float = -13.6,
    target_tp_dbtp: float = -1.0,
    target_lra: float = 4.8,
    sample_rate: int = 44100,
    bitrate: str = "192k",
) -> list[str]:
    dur = round(float(duration_sec), 2)
    fin = round(fade_in_ms / 1000.0, 3)
    fout = round(fade_out_ms / 1000.0, 3)
    fade_out_start = max(0.0, dur - fout)
    ratio = max(3.0, min(12.0, 1.5 + float(duck_db) * 0.7))
    limiter_ceiling = min(float(target_tp_dbtp), -1.0)
    filter_complex = (
        f"[0:a]volume={float(voice_gain_db)}dB[vox];"
        f"[1:a]aloop=loop=-1:size=2000000000,atrim=0:{dur},"
        f"volume=-{int(volume_db_below_voice)}dB,"
        f"afade=t=in:st=0:d={fin},afade=t=out:st={fade_out_start}:d={fout}[bgraw];"
        f"[bgraw][vox]sidechaincompress=threshold=0.03:ratio={ratio:.2f}:"
        "attack=20:release=300:makeup=1[bgduck];"
        f"[vox][bgduck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"loudnorm=I={float(target_lufs)}:TP={float(target_tp_dbtp)}:"
        f"LRA={float(target_lra)},alimiter=limit={limiter_ceiling}dB[a]"
    )
    return [
        "ffmpeg",
        "-y",
        "-i", str(narration_wav),
        "-i", str(music_file),
        "-filter_complex", filter_complex,
        "-map", "[a]",
        "-c:a", "aac",
        "-b:a", bitrate,
        "-ar", str(sample_rate),
        str(out_path),
    ]


def _track_entry(music_track: str | None, channel_config: dict) -> dict:
    tracks = ((channel_config.get("music_library") or {}).get("tracks")) or {}
    return dict(tracks.get(music_track) or {})


def _write_music_artifacts(
    *,
    short_dir: Path,
    music_track: str | None,
    channel_config: dict,
    mixed: bool,
) -> None:
    entry = _track_entry(music_track, channel_config)
    selection = {
        "schema_version": 1,
        "track_key": music_track,
        "title": entry.get("title"),
        "artist": entry.get("artist"),
        "source": entry.get("source"),
        "file": entry.get("file"),
        "reason": "topic_mapping" if mixed else "music_unavailable_or_disabled",
        "mixed": mixed,
        "output": "audio/short_mix.m4a",
    }
    json_dir = short_dir / paths.SHORT_JSON_SUBDIR
    json_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(json_dir / paths.SHORT_MUSIC_SELECTION_FILE, selection)

    manifest_path = json_dir / "assets_manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    old_audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    manifest["audio"] = {
        **old_audio,
        "narration": f"jobs/{short_dir.name}/audio/short_mix.m4a",
        "music": None,
        "music_selection": selection,
    }
    atomic_write_json(manifest_path, manifest)


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
        # Keep one canonical AAC/M4A output even for narration-only fallback.
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", str(narration_wav),
                "-c:a", "aac",
                "-b:a", str(mcfg.get("bitrate", "192k")),
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
        _write_music_artifacts(
            short_dir=short_dir,
            music_track=music_track,
            channel_config=channel_config,
            mixed=False,
        )
        return out_path

    cmd = build_mix_command(
        narration_wav=narration_wav,
        music_file=Path(music_file),
        out_path=out_path,
        duration_sec=duration_sec,
        volume_db_below_voice=track_volume_db_below_voice(music_track, channel_config),
        fade_in_ms=int(mcfg.get("fade_in_ms", 120)),
        fade_out_ms=int(mcfg.get("fade_out_ms", 450)),
        voice_gain_db=float(mcfg.get("voice_gain_db", -4.5)),
        duck_db=float(mcfg.get("duck_db", 8.0)),
        target_lufs=float(mcfg.get("target_lufs", -13.6)),
        target_tp_dbtp=float(mcfg.get("target_tp_dbtp", -1.0)),
        target_lra=float(mcfg.get("target_lra", 4.8)),
        sample_rate=int(mcfg.get("sample_rate", 44100)),
        bitrate=str(mcfg.get("bitrate", "192k")),
    )
    subprocess.run(cmd, check=True, capture_output=True)
    _write_music_artifacts(
        short_dir=short_dir,
        music_track=music_track,
        channel_config=channel_config,
        mixed=True,
    )
    return out_path
