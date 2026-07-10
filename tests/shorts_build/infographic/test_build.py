import json
from pathlib import Path

from video_agent.shorts.infographic.build import run_infographic_short


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


def test_music_bed_loops_one_licensed_track_for_the_static_duration(tmp_path, monkeypatch):
    from video_agent.shorts.infographic import build as build_mod

    source = tmp_path / "music.mp3"
    source.write_bytes(b"music")
    captured = {}

    def fake_run(cmd, **kwargs):
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
