import json
import wave
from pathlib import Path

from video_agent.shorts.infographic.build import run_infographic_short


def _write_wav(path: Path, seconds: float, *, rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


def _deps(qa_text):
    async def image_fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG")
        return {"bytes": 4}

    def llm_fn(prompt):
        # build_short_seo uses a distinct "SEO copywriter" prompt; the poster plan
        # prompt is the other branch.
        if "SEO copywriter" in prompt:
            return json.dumps({
                "title": "Si tienes más de 60: cuida vista",
                "description": "Alimentos para la vista.",
                "hashtags": ["#vista", "#shorts"],
                "pinned_comment": "¿Cuidas tu vista?",
            })
        return json.dumps({
            "poster_format": "category_grid", "title": "Vista",
            "hook_line": "Si tienes más de 60: cuida tu vista",
            "items": [{"label": f"i{n}"} for n in range(6)], "cta": "Sigue",
        })

    def read_text_fn(png):
        return qa_text

    def music_fn(short_dir, music_track, cfg, duration_sec):
        p = Path(short_dir) / "audio" / "infographic_bgm.m4a"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF")
        return p

    def render_fn(short_dir, props):
        out = Path(short_dir) / "outputs" / "short.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out

    return image_fn, llm_fn, read_text_fn, music_fn, render_fn


CFG = {"audience": {"age_range": [45, 75]}, "channel": {"name": "Vida Plena 45+"}}


def test_pass_gate_renders_a_static_poster_with_music_only(tmp_path):
    image_fn, llm_fn, read_text_fn, music_fn, render_fn = _deps("vista i0 i1 i2 i3 i4 i5")
    short_dir = tmp_path / "job-1" / "shorts" / "short-01"
    status = run_infographic_short(
        short_dir, CFG, {"topic": "vista despues de los 60"},
        image_fn=image_fn, llm_fn=llm_fn, read_text_fn=read_text_fn, music_fn=music_fn, render_fn=render_fn)
    assert status["short_type"] == "infographic"
    assert status["rendered"] is True
    assert (short_dir / "outputs" / "short.mp4").exists()
    # SEO artifact written with a valid <=40-char scroll-stopper title.
    seo_file = short_dir / "json" / "short_seo.json"
    assert seo_file.exists()
    assert len(json.loads(seo_file.read_text())["title"]) <= 40
    # Public refs use the short's own dir name (matches materialize_short_job_aliases).
    props = json.loads((short_dir / "json" / "short_render_props.json").read_text())
    assert props["poster"] == "jobs/short-01/assets/poster.png"
    assert props["audio"] == "jobs/short-01/audio/infographic_bgm.m4a"
    assert props["music"] == ""
    assert props["durationInFrames"] == 15 * 30
    assert props["kenBurns"] is False
    assert props["showEngagementCue"] is True
    assert props["engagementCueDurationSec"] == 3.0
    assert status["audio_mode"] == "music_only"


def test_failed_text_qa_blocks_render(tmp_path):
    image_fn, llm_fn, read_text_fn, music_fn, render_fn = _deps("totally different words")
    short_dir = tmp_path / "job-1" / "shorts" / "short-02"
    status = run_infographic_short(
        short_dir, CFG, {"topic": "vista"},
        image_fn=image_fn, llm_fn=llm_fn, read_text_fn=read_text_fn, music_fn=music_fn, render_fn=render_fn,
        max_poster_attempts=2)
    assert status["status"] == "needs_manual_review"
    assert status["rendered"] is False
    assert not (short_dir / "outputs" / "short.mp4").exists()


def test_qa_disabled_renders_without_reader(tmp_path):
    # No read_text_fn -> QA skipped -> renders even though poster text is unverified.
    image_fn, llm_fn, _read, music_fn, render_fn = _deps("garbled unreadable poster")
    short_dir = tmp_path / "job-1" / "shorts" / "short-03"
    status = run_infographic_short(
        short_dir, CFG, {"topic": "vista"},
        image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn)
    assert status["rendered"] is True
    assert status["status"] == "rendered"
    qa = json.loads((short_dir / "json" / "poster_qa.json").read_text())
    assert qa["verdict"] == "skipped"


def test_voice_enabled_extends_duration_to_voice_length_plus_padding(tmp_path):
    image_fn, llm_fn, read_text_fn, music_fn, render_fn = _deps("vista i0 i1 i2 i3 i4 i5")
    short_dir = tmp_path / "job-1" / "shorts" / "short-01"

    def voice_fn(sd, plan, cfg):
        p = Path(sd) / "audio" / "short_narration.wav"
        _write_wav(p, 6.0)
        return p

    captured_mix = {}

    def mix_fn(narration_path, bgm_path, mixed_path, cfg, duration_sec):
        captured_mix["duration_sec"] = duration_sec
        mixed_path.parent.mkdir(parents=True, exist_ok=True)
        mixed_path.write_bytes(b"m4a")
        return True

    cfg = {**CFG, "shorts": {"infographic": {
        "voice": {"enabled": True, "padding_sec": 2.5, "min_duration_sec": 8.0, "max_duration_sec": 45.0},
    }}}
    status = run_infographic_short(
        short_dir, cfg, {"topic": "vista despues de los 60"},
        image_fn=image_fn, llm_fn=llm_fn, read_text_fn=read_text_fn, music_fn=music_fn,
        render_fn=render_fn, voice_fn=voice_fn, mix_fn=mix_fn,
    )
    assert status["rendered"] is True
    assert status["audio_mode"] == "voice_plus_music"
    assert status["voice_duration_sec"] == 6.0
    # P1-D: the tail after the voice must fit the 3s engagement cue, so the
    # effective padding is max(padding_sec, 3.0) — the cue never overlaps speech.
    assert status["duration_sec"] == 9.0  # 6.0 + max(2.5, 3.0)
    assert captured_mix["duration_sec"] == 9.0
    props = json.loads((short_dir / "json" / "short_render_props.json").read_text())
    assert props["audio"] == "jobs/short-01/audio/infographic_mix.m4a"
    assert props["durationInFrames"] == round(9.0 * 30)
    assert props["showEngagementCue"] is True
    assert props["engagementCueDurationSec"] == 3.0


def test_voice_duration_clamped_to_min_and_max(tmp_path):
    image_fn, llm_fn, read_text_fn, music_fn, render_fn = _deps("vista i0 i1 i2 i3 i4 i5")

    def mix_fn(narration_path, bgm_path, mixed_path, cfg, duration_sec):
        mixed_path.parent.mkdir(parents=True, exist_ok=True)
        mixed_path.write_bytes(b"m4a")
        return True

    cfg = {**CFG, "shorts": {"infographic": {
        "voice": {"enabled": True, "padding_sec": 1.0, "min_duration_sec": 10.0, "max_duration_sec": 20.0},
    }}}

    # Very short speech -> clamped up to min_duration_sec.
    short_dir_a = tmp_path / "job-1" / "shorts" / "short-a"

    def voice_fn_short(sd, plan, c):
        p = Path(sd) / "audio" / "short_narration.wav"
        _write_wav(p, 2.0)
        return p

    status_a = run_infographic_short(
        short_dir_a, cfg, {"topic": "vista"}, image_fn=image_fn, llm_fn=llm_fn,
        read_text_fn=read_text_fn, music_fn=music_fn, render_fn=render_fn,
        voice_fn=voice_fn_short, mix_fn=mix_fn,
    )
    assert status_a["duration_sec"] == 10.0

    # Very long speech -> clamped down to max_duration_sec.
    short_dir_b = tmp_path / "job-1" / "shorts" / "short-b"

    def voice_fn_long(sd, plan, c):
        p = Path(sd) / "audio" / "short_narration.wav"
        _write_wav(p, 40.0)
        return p

    status_b = run_infographic_short(
        short_dir_b, cfg, {"topic": "vista"}, image_fn=image_fn, llm_fn=llm_fn,
        read_text_fn=read_text_fn, music_fn=music_fn, render_fn=render_fn,
        voice_fn=voice_fn_long, mix_fn=mix_fn,
    )
    assert status_b["duration_sec"] == 20.0


def test_voice_disabled_by_default_keeps_music_only_behavior(tmp_path):
    """Backward compat: no shorts.infographic.voice config at all -> unchanged
    music_only path (existing channels/tests must not need any changes)."""
    image_fn, llm_fn, read_text_fn, music_fn, render_fn = _deps("vista i0 i1 i2 i3 i4 i5")
    short_dir = tmp_path / "job-1" / "shorts" / "short-01"
    status = run_infographic_short(
        short_dir, CFG, {"topic": "vista"},
        image_fn=image_fn, llm_fn=llm_fn, read_text_fn=read_text_fn, music_fn=music_fn, render_fn=render_fn,
    )
    assert status["audio_mode"] == "music_only"
    assert "voice_duration_sec" not in status
    assert status["duration_sec"] == 15.0


def test_mix_failure_falls_back_to_music_only():
    """If the real mixer fails (bad ffmpeg filter, missing codec...), the Short
    must still publish with the music bed rather than fail outright."""
    import tempfile

    from video_agent.shorts.infographic import build as build_mod

    with tempfile.TemporaryDirectory() as td:
        image_fn, llm_fn, read_text_fn, music_fn, render_fn = _deps("vista i0 i1 i2 i3 i4 i5")
        short_dir = Path(td) / "job-1" / "shorts" / "short-01"

        def voice_fn(sd, plan, cfg):
            p = Path(sd) / "audio" / "short_narration.wav"
            _write_wav(p, 5.0)
            return p

        def failing_mix_fn(narration_path, bgm_path, mixed_path, cfg, duration_sec):
            return False

        cfg = {**CFG, "shorts": {"infographic": {"voice": {"enabled": True}}}}
        status = build_mod.run_infographic_short(
            short_dir, cfg, {"topic": "vista"}, image_fn=image_fn, llm_fn=llm_fn,
            read_text_fn=read_text_fn, music_fn=music_fn, render_fn=render_fn,
            voice_fn=voice_fn, mix_fn=failing_mix_fn,
        )
        assert status["rendered"] is True
        assert status["audio_mode"] == "music_only"


def test_music_bed_loops_one_licensed_track_for_the_static_duration(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from video_agent.shorts.infographic import build as build_mod

    source = tmp_path / "music.mp3"
    source.write_bytes(b"music")
    captured = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            # Track probe (offset seed) vs post-encode validation probe: the
            # encoded bed must report the REQUESTED duration or the guard
            # rejects it as corrupt.
            probed = str(cmd[-1])
            return SimpleNamespace(stdout="15.0" if probed.endswith(".tmp.m4a") else "214.0")
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"m4a")

    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "video_agent.shorts.audio_mixer.resolve_music_file", lambda *_args: source
    )

    out = build_mod.prepare_infographic_music_bed(
        tmp_path,
        "shorts_daily_habit",
        {"shorts": {"infographic": {"music_source": "library", "music_volume_db": -12}}},
        15,
    )

    assert out == tmp_path / "audio" / "infographic_bgm.m4a"
    assert "-stream_loop" in captured["cmd"]
    assert "-t" in captured["cmd"]
    assert "15.00" in captured["cmd"]
    assert any("volume=-12.0dB" in part for part in captured["cmd"])


def test_music_bed_uses_original_procedural_source_when_configured(tmp_path, monkeypatch):
    from video_agent.shorts.infographic import build as build_mod

    captured = {}

    def fake_create(short_dir, *, duration_sec, seed_key, bitrate):
        captured.update({
            "short_dir": Path(short_dir), "duration_sec": duration_sec,
            "seed_key": seed_key, "bitrate": bitrate,
        })
        output = Path(short_dir) / "audio" / "infographic_bgm.m4a"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"m4a")
        return output

    monkeypatch.setattr("video_agent.shorts.original_bgm.create_original_bgm", fake_create)

    out = build_mod.prepare_infographic_music_bed(
        tmp_path / "short-01",
        "procedural_original",
        {"shorts": {"infographic": {"music_source": "procedural_original", "music_bitrate": "160k"}}},
        15,
    )

    assert out.name == "infographic_bgm.m4a"
    assert captured == {
        "short_dir": tmp_path / "short-01", "duration_sec": 15,
        "seed_key": "short-01", "bitrate": "160k",
    }


def test_mix_voice_with_music_applies_no_second_bgm_attenuation(monkeypatch, tmp_path):
    """P1-B single-attenuation contract: prepare_infographic_music_bed already
    encodes music_volume_db into the bed, so the voice mixer must pass 0dB —
    the old code re-applied -14dB and produced a nearly silent -27.8dB bed on a
    real render (double attenuation)."""
    from video_agent.shorts.infographic import build as build_mod

    captured = {}

    def fake_mix(narration_path, bgm_path, mixed_path, **kwargs):
        captured.update(kwargs)
        mixed_path.parent.mkdir(parents=True, exist_ok=True)
        mixed_path.write_bytes(b"m4a")
        return True

    monkeypatch.setattr(build_mod, "_mix_bgm_with_narration", fake_mix)
    narration = tmp_path / "short_narration.wav"
    _write_wav(narration, 5.0)
    bgm = tmp_path / "infographic_bgm.m4a"
    bgm.write_bytes(b"m4a")

    cfg = {"shorts": {
        "infographic": {"music_volume_db": -14.0},
        "music": {"voice_gain_db": -4.5, "duck_db": 8.0},
    }}
    build_mod._mix_voice_with_music(narration, bgm, tmp_path / "out.m4a", cfg, 7.5)
    assert captured["bgm_gain_db"] == 0.0  # bed is pre-attenuated; never twice
    # Gentler than the narrated-Shorts default (8dB): music stays audible under
    # voice instead of near-silent for the whole speech portion (bug-519).
    assert captured["duck_db"] == 4.0


def test_mix_voice_with_music_defaults_to_audible_bgm_level_without_config(monkeypatch, tmp_path):
    from video_agent.shorts.infographic import build as build_mod

    captured = {}

    def fake_mix(narration_path, bgm_path, mixed_path, **kwargs):
        captured.update(kwargs)
        mixed_path.parent.mkdir(parents=True, exist_ok=True)
        mixed_path.write_bytes(b"m4a")
        return True

    monkeypatch.setattr(build_mod, "_mix_bgm_with_narration", fake_mix)
    narration = tmp_path / "short_narration.wav"
    _write_wav(narration, 5.0)
    bgm = tmp_path / "infographic_bgm.m4a"
    bgm.write_bytes(b"m4a")

    build_mod._mix_voice_with_music(narration, bgm, tmp_path / "out.m4a", {}, 7.5)
    # Must NOT silently fall through to the narrated pipeline's quiet -24dB default.
    assert captured["bgm_gain_db"] > -20.0
