"""Shorts Autopilot v5 — Phase 5: music resolution + ffmpeg mix command."""
from __future__ import annotations

from pathlib import Path
import json


def _cfg(tmp_path: Path) -> dict:
    mdir = tmp_path / "assets" / "music"
    mdir.mkdir(parents=True)
    (mdir / "floating_home_brian_bolger.mp3").write_bytes(b"m")
    return {
        "music_library": {
            "tracks": {
                "shorts_sleep_stress": {
                    "file": "assets/music/floating_home_brian_bolger.mp3",
                    "default_volume_db_below_voice": 15,
                    "title": "Floating Home",
                    "artist": "Brian Bolger",
                    "source": "YouTube Studio Audio Library",
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
    assert p == tmp_path / "assets" / "music" / "floating_home_brian_bolger.mp3"
    assert p.exists()


def test_resolve_music_file_missing_track_returns_none(tmp_path: Path):
    from video_agent.shorts import audio_mixer
    cfg = _cfg(tmp_path)
    assert audio_mixer.resolve_music_file("nope", cfg, repo_root=tmp_path) is None


def test_track_volume_db_prefers_track_then_global(tmp_path: Path):
    from video_agent.shorts import audio_mixer
    cfg = _cfg(tmp_path)
    # track-level 15 wins over global 20
    assert audio_mixer.track_volume_db_below_voice("shorts_sleep_stress", cfg) == 15
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
    assert "sidechaincompress" in s
    assert "loudnorm=" in s
    assert "alimiter=" in s
    assert "amix" in s


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
    expected = {
        "shorts_movement": ("Find Your Way", "assets/music/find_your_way_anno_domini_beats.mp3"),
        "shorts_daily_habit": ("Fresh Fallen Snow", "assets/music/fresh_fallen_snow_chris_haugen.mp3"),
        "shorts_sleep_stress": ("Floating Home", "assets/music/floating_home_brian_bolger.mp3"),
        "shorts_deep_calm": ("Ether", "assets/music/ether_silent_partner.mp3"),
    }
    tracks = cfg["music_library"]["tracks"]
    assert set(tracks) == set(expected)
    for track, (title, file_path) in expected.items():
        assert tracks[track]["title"] == title
        assert tracks[track]["file"] == file_path
        p = audio_mixer.resolve_music_file(track, cfg)
        assert p is not None and p.exists(), track


def test_all_channel_variants_share_the_four_track_library():
    from video_agent.contracts import repo_root
    from video_agent.utils.json_io import read_yaml

    root = repo_root()
    configs = [
        root / "configs/vida-plena-45/channel.yaml",
        root / "configs/vida-plena-45-t-pexels/channel.yaml",
        root / "configs/vida-plena-45-t-pixabay/channel.yaml",
        root / "configs/vida-plena-45-t-coverr/channel.yaml",
    ]
    expected = {
        "shorts_movement",
        "shorts_daily_habit",
        "shorts_sleep_stress",
        "shorts_deep_calm",
    }
    for config_path in configs:
        cfg = read_yaml(config_path)
        assert set(cfg["music_library"]["tracks"]) == expected
        assert "music" not in cfg["shorts"]["tts"]
    assert not (root / "asset_library/source/bgm").exists()


def test_mix_short_audio_updates_canonical_manifest_and_selection(tmp_path, monkeypatch):
    from video_agent.shorts import audio_mixer

    cfg = _cfg(tmp_path)
    short_dir = tmp_path / "short-01"
    narration = short_dir / "audio" / "short_narration.wav"
    narration.parent.mkdir(parents=True)
    narration.write_bytes(b"voice")
    json_dir = short_dir / "json"
    json_dir.mkdir()
    (json_dir / "assets_manifest.json").write_text(
        json.dumps({"audio": {"narration": "stale", "music": "stale"}, "scenes": []})
    )

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"mixed")

    monkeypatch.setattr(audio_mixer.subprocess, "run", fake_run)

    out = audio_mixer.mix_short_audio(
        short_dir, narration, "shorts_sleep_stress", cfg, 30.0
    )

    assert out == short_dir / "audio" / "short_mix.m4a"
    manifest = json.loads((json_dir / "assets_manifest.json").read_text())
    assert (
        manifest["audio"]["narration"]
        == "jobs/short-01/audio/short_mix.m4a"
    )
    assert manifest["audio"]["music"] is None
    assert manifest["audio"]["music_selection"]["track_key"] == "shorts_sleep_stress"
    selection = json.loads((json_dir / "music_selection.json").read_text())
    assert selection["title"] == "Floating Home"
    assert selection["output"] == "audio/short_mix.m4a"


def test_synthesize_short_narration_forces_dynamic_sync_off(tmp_path: Path, monkeypatch):
    """The Remotion ShortVideo plays ONE narration track at frame 0 while scenes
    are timed by planned duration_sec. Sync therefore requires per-scene audio
    padded to the planned duration (dynamic_sync=False), NOT audio-accurate
    durations. Guard that the shorts audio path forces this regardless of config.
    """
    from video_agent.shorts import audio

    captured: dict = {}

    def fake_prepare_assets(*, job_dir, scene_doc, tts_config, **kwargs):
        captured["tts_config"] = tts_config
        (job_dir / "assets").mkdir(parents=True, exist_ok=True)
        (job_dir / "assets" / "narration.wav").write_bytes(b"\0\0")

    monkeypatch.setattr("video_agent.shorts.assets.prepare.prepare_assets", fake_prepare_assets)

    short_dir = tmp_path / "short"
    short_dir.mkdir()
    cfg = {
        "shorts": {
            "tts": {
                "provider": "kokoro",
                "voice_id": "ef_dora",
                "speed": 1.07,
                "music": {"preferred_track": "legacy.mp3"},
            }
        }
    }
    audio.synthesize_short_narration(short_dir, {"scenes": []}, cfg)

    assert captured["tts_config"].get("dynamic_sync") is False
    assert "music" not in captured["tts_config"]
    # must not mutate the caller's config object
    assert "dynamic_sync" not in cfg["shorts"]["tts"]
    assert cfg["shorts"]["tts"]["music"]["preferred_track"] == "legacy.mp3"


def test_regen_fallback_forces_ai_strategy_for_rejected_native_scenes(tmp_path: Path, monkeypatch):
    """bug-477: scenes whose native stock was rejected by local QA must be forced
    onto the AI-image route. Merely enabling the AI tier (_skip_ai_fallback=False)
    is not enough — a stock_ok scene re-selects the same rejected clip at Tier 1.
    Forcing asset_strategy=ai_image_preferred skips the stock tiers. Graphic scenes
    already force it via layout and must be left untouched."""
    from video_agent.shorts import audio

    seen: dict = {}

    def fake_prepare_assets(*, job_dir, scene_doc, only_scene_ids=None, **kwargs):
        seen["only_scene_ids"] = only_scene_ids
        seen["scene_doc"] = scene_doc

    monkeypatch.setattr("video_agent.shorts.assets.prepare.prepare_assets", fake_prepare_assets)
    monkeypatch.setattr(audio, "_short_asset_context", lambda short_dir, cfg: {})
    monkeypatch.setattr(audio, "_persist_prepared_short_scenes", lambda short_dir, scenes: None)

    scenes = {
        "scenes": [
            {"id": "s04", "layout": "short_tip", "asset_strategy": "stock_ok"},
            {"id": "s06", "layout": "short_cta", "asset_strategy": "stock_ok"},
            {"id": "s03", "layout": "graphic_step_list", "asset_strategy": "graphic_fallback"},
            {"id": "s01", "layout": "short_hook", "asset_strategy": "stock_ok"},  # not in set
        ]
    }
    audio.regen_fallback_backgrounds(tmp_path, scenes, {"shorts": {}}, {"s04", "s06", "s03"})

    by_id = {s["id"]: s for s in scenes["scenes"]}
    # Rejected native scenes forced onto the AI route.
    assert by_id["s04"]["asset_strategy"] == "ai_image_preferred"
    assert by_id["s04"]["_skip_ai_fallback"] is False
    assert by_id["s06"]["asset_strategy"] == "ai_image_preferred"
    # Graphic scene already forces AI via layout — left as graphic_fallback.
    assert by_id["s03"]["asset_strategy"] == "graphic_fallback"
    assert by_id["s03"]["_skip_ai_fallback"] is False
    # A scene not in the regen set is untouched.
    assert by_id["s01"]["asset_strategy"] == "stock_ok"
    assert "_skip_ai_fallback" not in by_id["s01"]
    assert seen["only_scene_ids"] == {"s04", "s06", "s03"}


def test_regen_fallback_noop_on_empty_scene_ids(tmp_path: Path, monkeypatch):
    from video_agent.shorts import audio

    called = {"n": 0}
    monkeypatch.setattr(
        "video_agent.shorts.assets.prepare.prepare_assets",
        lambda **k: called.__setitem__("n", called["n"] + 1),
    )
    audio.regen_fallback_backgrounds(tmp_path, {"scenes": []}, {}, set())
    assert called["n"] == 0
