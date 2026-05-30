"""Shorts Autopilot v5 — Phase 5: music resolution + ffmpeg mix command."""
from __future__ import annotations

from pathlib import Path


def _cfg(tmp_path: Path) -> dict:
    mdir = tmp_path / "assets" / "music"
    mdir.mkdir(parents=True)
    (mdir / "nimbus_eveningland.mp3").write_bytes(b"m")
    return {
        "music_library": {
            "tracks": {
                "shorts_sleep_stress": {
                    "file": "assets/music/nimbus_eveningland.mp3",
                    "default_volume_db_below_voice": 22,
                }
            }
        },
        "shorts": {
            "music": {
                "volume_db_below_voice": 20,
                "fade_in_ms": 120,
                "fade_out_ms": 450,
                "allow_render_without_license_metadata": True,
            }
        },
    }


def test_resolve_music_file(tmp_path: Path):
    from video_agent.shorts import audio_mixer
    cfg = _cfg(tmp_path)
    p = audio_mixer.resolve_music_file("shorts_sleep_stress", cfg, repo_root=tmp_path)
    assert p == tmp_path / "assets" / "music" / "nimbus_eveningland.mp3"
    assert p.exists()


def test_resolve_music_file_missing_track_returns_none(tmp_path: Path):
    from video_agent.shorts import audio_mixer
    cfg = _cfg(tmp_path)
    assert audio_mixer.resolve_music_file("nope", cfg, repo_root=tmp_path) is None


def test_track_volume_db_prefers_track_then_global(tmp_path: Path):
    from video_agent.shorts import audio_mixer
    cfg = _cfg(tmp_path)
    # track-level 22 wins over global 20
    assert audio_mixer.track_volume_db_below_voice("shorts_sleep_stress", cfg) == 22
    # unknown track → global 20
    assert audio_mixer.track_volume_db_below_voice("nope", cfg) == 20


def test_build_mix_command_has_inputs_volume_fade_and_duration(tmp_path: Path):
    from video_agent.shorts import audio_mixer
    narr = tmp_path / "n.wav"
    music = tmp_path / "m.mp3"
    out = tmp_path / "mix.m4a"
    cmd = audio_mixer.build_mix_command(
        narration_wav=narr, music_file=music, out_path=out,
        duration_sec=32.0, volume_db_below_voice=20, fade_in_ms=120, fade_out_ms=450,
    )
    assert cmd[0] == "ffmpeg"
    s = " ".join(cmd)
    assert str(narr) in s and str(music) in s and str(out) in s
    # music attenuated by 20 dB below voice
    assert "volume=-20dB" in s
    # looped + trimmed to duration
    assert "32" in s
    # fades present
    assert "afade=t=in" in s and "afade=t=out" in s
    # mixed
    assert "amix" in s or "amerge" in s


def test_build_mix_command_voice_not_attenuated(tmp_path: Path):
    from video_agent.shorts import audio_mixer
    cmd = audio_mixer.build_mix_command(
        narration_wav=tmp_path / "n.wav", music_file=tmp_path / "m.mp3", out_path=tmp_path / "o.m4a",
        duration_sec=30.0, volume_db_below_voice=18, fade_in_ms=120, fade_out_ms=450,
    )
    s = " ".join(cmd)
    # only music gets negative volume; ensure exactly one volume= filter at -18dB
    assert s.count("volume=-18dB") == 1


def test_real_channel_music_tracks_resolve_to_existing_files():
    from pathlib import Path
    from video_agent.contracts import repo_root
    from video_agent.utils.json_io import read_yaml
    from video_agent.shorts import audio_mixer
    cfg = read_yaml(repo_root() / "configs/vida-plena-45/channel.yaml")
    for track in ("shorts_movement", "shorts_daily_habit", "shorts_sleep_stress", "shorts_deep_calm"):
        p = audio_mixer.resolve_music_file(track, cfg)
        assert p is not None and p.exists(), track
