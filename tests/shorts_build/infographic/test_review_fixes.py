"""Regression tests for the post-review defects (bridge task 20260711-065504).

P1-A pillar propagation through render_selected_infographic_ideas,
P1-B single music_volume_db application, P1-D cue-after-narration,
P2-A job-scoped excerpt seed, P2-B bounded/validated ffmpeg, and the
mechanical cleanups (offset clamp, stale metadata, bitrate/sample rate).
The executable CTA timing checks live in test_engagement_cue_timing.py.
"""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_agent.shorts import paths
from video_agent.shorts.infographic import build as build_mod


def _write_wav(path: Path, seconds: float, *, rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


def _library_cfg(track_file: Path) -> dict:
    return {
        "audience": {"age_range": [45, 75]},
        "channel": {"name": "Vida Plena 45+"},
        "shorts": {"infographic": {"music_source": "library"}},
        "music_library": {
            "tracks": {
                "shorts_daily_habit": {"file": str(track_file), "title": "Fresh Fallen Snow"},
                "shorts_sleep_stress": {"file": str(track_file), "title": "Floating Home"},
            }
        },
    }


@pytest.fixture()
def tone_track(tmp_path: Path) -> Path:
    """A real 30s audio file so ffprobe/ffmpeg exercise their true code paths."""
    track = tmp_path / "tone_track.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
         "-ar", "44100", str(track)],
        check=True, capture_output=True,
    )
    return track


# --- P1-A: real generated ideas select the food track ------------------------

def test_render_selected_ideas_derives_food_pillar_end_to_end(tmp_path, tone_track):
    """A REAL-shaped idea record (title only, no pillar/topic — exactly what the
    idea generator emits) must reach the food track through the actual
    render_selected_infographic_ideas path, not only via synthetic canonical
    keys (the gap that shipped bug-526)."""
    job_dir = tmp_path / "job-panes"
    (job_dir / "shorts").mkdir(parents=True)
    (job_dir / "shorts" / paths.SHORT_IDEAS_FILE).write_text(json.dumps({
        "ideas": [{
            "idea_id": "idea-01",
            "title": "5 pasos para montar una tostada que te deje satisfecho",
            "format": "numbered_tips",
        }],
    }), encoding="utf-8")

    async def image_fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG")
        return {"bytes": 4}

    def llm_fn(prompt):
        if "SEO copywriter" in prompt:
            return json.dumps({
                "title": "Si tienes más de 60: tu tostada",
                "description": "Tostada completa y saciante.",
                "hashtags": ["#tostada"], "pinned_comment": "¿La pruebas?",
            })
        return json.dumps({
            "poster_format": "numbered_tips", "title": "Tostada",
            "hook_line": "Si tienes más de 60: monta tu tostada",
            "items": [{"label": f"paso {n}"} for n in range(5)], "cta": "Sigue",
        })

    def render_fn(short_dir, props):
        out = Path(short_dir) / "outputs" / "short.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out

    result = build_mod.render_selected_infographic_ideas(
        job_dir, _library_cfg(tone_track), ["idea-01"],
        image_fn=image_fn, llm_fn=llm_fn, render_fn=render_fn,
    )

    assert len(result["shorts"]) == 1
    assert result["shorts"][0]["rendered"] is True
    selections = list((job_dir / "shorts").glob("*/json/" + paths.SHORT_MUSIC_SELECTION_FILE))
    assert len(selections) == 1
    meta = json.loads(selections[0].read_text())
    assert meta["track_key"] == "shorts_daily_habit"
    assert meta["track_title"] == "Fresh Fallen Snow"


def test_pillar_derivation_rejects_lookalike_substrings():
    """Word-boundary matching: 'pan' must not fire inside 'pantalla' and 'sal'
    must not fire inside 'salir' — broad substrings were explicitly rejected in
    review as fragile."""
    from video_agent.shorts.music_selector import derive_pillar_from_text

    assert derive_pillar_from_text("mira la pantalla al salir de casa") == ""
    assert derive_pillar_from_text("come pan integral") == "food"


# --- P1-B: music_volume_db applied exactly once ------------------------------

def test_bed_filter_chain_applies_volume_exactly_once(tmp_path, tone_track, monkeypatch):
    commands: list[list[str]] = []
    real_run = subprocess.run

    def spy_run(cmd, **kwargs):
        if cmd[0] == "ffmpeg" and any("volume=" in str(part) for part in cmd):
            commands.append([str(c) for c in cmd])
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(build_mod.subprocess, "run", spy_run)
    build_mod.prepare_infographic_music_bed(
        tmp_path / "short-01", "shorts_daily_habit", _library_cfg(tone_track), 12.0
    )

    assert len(commands) == 1
    af = next(part for part in commands[0] if "volume=" in part)
    assert af.count("volume=") == 1  # single attenuation in the bed


# --- P1-D: the cue never overlaps narration ----------------------------------

def _stub_pipeline_fns(voice_seconds: float):
    async def image_fn(*, prompt, project_name, out_path, aspect_ratio="16:9"):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG")
        return {"bytes": 4}

    def llm_fn(prompt):
        if "SEO copywriter" in prompt:
            return json.dumps({"title": "Si tienes más de 60: cuida vista",
                               "description": "Alimentos para la vista.",
                               "hashtags": ["#vista"], "pinned_comment": "¿Cuidas tu vista?"})
        return json.dumps({"poster_format": "category_grid", "title": "Vista",
                           "hook_line": "Si tienes más de 60: cuida tu vista",
                           "items": [{"label": f"i{n}"} for n in range(6)],
                           "cta": "Sigue"})

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

    def voice_fn(sd, plan, cfg):
        p = Path(sd) / "audio" / "short_narration.wav"
        _write_wav(p, voice_seconds)
        return p

    def mix_fn(narration_path, bgm_path, mixed_path, cfg, duration_sec):
        mixed_path.parent.mkdir(parents=True, exist_ok=True)
        mixed_path.write_bytes(b"m4a")
        return True

    return image_fn, llm_fn, music_fn, render_fn, voice_fn, mix_fn


def test_cue_starts_exactly_at_narration_end(tmp_path):
    image_fn, llm_fn, music_fn, render_fn, voice_fn, mix_fn = _stub_pipeline_fns(8.67)
    cfg = {"audience": {"age_range": [45, 75]}, "channel": {"name": "VP"},
           "shorts": {"infographic": {"voice": {"enabled": True, "padding_sec": 2.5,
                                                "min_duration_sec": 8.0,
                                                "max_duration_sec": 45.0}}}}
    short_dir = tmp_path / "job" / "shorts" / "short-01"
    status = build_mod.run_infographic_short(
        short_dir, cfg, {"topic": "vista"}, image_fn=image_fn, llm_fn=llm_fn,
        music_fn=music_fn, render_fn=render_fn, voice_fn=voice_fn, mix_fn=mix_fn,
    )
    props = json.loads((short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE).read_text())
    # duration = voice + max(padding, cue) -> the 3s cue owns exactly the tail
    # AFTER the narration; cue_start == narration end, zero overlap.
    assert status["duration_sec"] == pytest.approx(8.67 + 3.0)
    assert props["engagementCueDurationSec"] == 3.0
    cue_start_sec = status["duration_sec"] - props["engagementCueDurationSec"]
    assert cue_start_sec == pytest.approx(status["voice_duration_sec"])


def test_max_duration_clamp_disables_unusable_cue(tmp_path):
    """When the max-duration clamp leaves no usable tail (voice longer than the
    clamp), the cue is disabled instead of talking over the narration."""
    image_fn, llm_fn, music_fn, render_fn, voice_fn, mix_fn = _stub_pipeline_fns(40.0)
    cfg = {"audience": {"age_range": [45, 75]}, "channel": {"name": "VP"},
           "shorts": {"infographic": {"voice": {"enabled": True, "padding_sec": 1.0,
                                                "min_duration_sec": 10.0,
                                                "max_duration_sec": 20.0}}}}
    short_dir = tmp_path / "job" / "shorts" / "short-01"
    status = build_mod.run_infographic_short(
        short_dir, cfg, {"topic": "vista"}, image_fn=image_fn, llm_fn=llm_fn,
        music_fn=music_fn, render_fn=render_fn, voice_fn=voice_fn, mix_fn=mix_fn,
    )
    props = json.loads((short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE).read_text())
    assert status["duration_sec"] == 20.0
    assert props["showEngagementCue"] is False
    assert props["engagementCueDurationSec"] == 0.0


# --- P2-A: excerpt seed carries the parent job identity -----------------------

def test_excerpt_seed_includes_parent_job_so_jobs_do_not_collide(tmp_path, tone_track):
    cfg = _library_cfg(tone_track)
    beds = {}
    for job_name in ("job-a", "job-b"):
        short_dir = tmp_path / job_name / "shorts" / "short-01_idea-01_x"
        build_mod.prepare_infographic_music_bed(short_dir, "shorts_daily_habit", cfg, 12.0)
        meta = json.loads(
            (short_dir / "json" / paths.SHORT_MUSIC_SELECTION_FILE).read_text()
        )
        beds[job_name] = meta

    assert beds["job-a"]["seed_key"] == "job-a|short-01_idea-01_x|shorts_daily_habit"
    assert beds["job-b"]["seed_key"] == "job-b|short-01_idea-01_x|shorts_daily_habit"
    # Identical short basenames in different jobs must hear different excerpts.
    assert beds["job-a"]["excerpt_offset_sec"] != beds["job-b"]["excerpt_offset_sec"]


# --- P2-B: bounded, validated, atomic encode ----------------------------------

def test_non_finite_bed_duration_is_rejected(tmp_path, tone_track):
    with pytest.raises(RuntimeError, match="finite"):
        build_mod.prepare_infographic_music_bed(
            tmp_path / "short-01", "shorts_daily_habit",
            _library_cfg(tone_track), float("nan"),
        )
    with pytest.raises(RuntimeError, match="finite"):
        build_mod.prepare_infographic_music_bed(
            tmp_path / "short-01", "shorts_daily_habit",
            _library_cfg(tone_track), -3.0,
        )


def test_corrupt_encode_is_rejected_and_never_replaces_the_bed(
    tmp_path, tone_track, monkeypatch
):
    """The encode goes to a temp file and only replaces the bed after its
    duration validates — a truncated encode must leave NO bed behind."""
    durations = iter([30.0, 2.0])  # track probe OK, encoded probe mismatched

    monkeypatch.setattr(
        build_mod, "probe_audio_duration_seconds", lambda _p: next(durations)
    )

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"garbage")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)
    short_dir = tmp_path / "short-01"
    with pytest.raises(RuntimeError, match="corrupt"):
        build_mod.prepare_infographic_music_bed(
            short_dir, "shorts_daily_habit", _library_cfg(tone_track), 12.0
        )
    assert not (short_dir / "audio" / "infographic_bgm.m4a").exists()


def test_probe_and_encode_have_bounded_timeouts(tmp_path, tone_track, monkeypatch):
    seen = {}
    real_run = subprocess.run

    def spy_run(cmd, **kwargs):
        seen[cmd[0]] = kwargs.get("timeout")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(build_mod.subprocess, "run", spy_run)
    build_mod.prepare_infographic_music_bed(
        tmp_path / "short-01", "shorts_daily_habit", _library_cfg(tone_track), 12.0
    )
    assert seen.get("ffprobe"), "ffprobe must run with a timeout"
    assert seen.get("ffmpeg"), "ffmpeg must run with a timeout"


# --- mechanical cleanups -------------------------------------------------------

def test_switching_to_procedural_removes_stale_library_metadata(tmp_path, monkeypatch):
    short_dir = tmp_path / "short-01"
    stale = short_dir / "json" / paths.SHORT_MUSIC_SELECTION_FILE
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")

    def fake_create(short_dir, *, duration_sec, seed_key, bitrate):
        out = Path(short_dir) / "audio" / "infographic_bgm.m4a"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"m4a")
        return out

    monkeypatch.setattr("video_agent.shorts.original_bgm.create_original_bgm", fake_create)
    build_mod.prepare_infographic_music_bed(
        short_dir, "procedural_original",
        {"shorts": {"infographic": {"music_source": "procedural_original"}}}, 15.0,
    )
    assert not stale.exists()


def test_bed_honors_configured_bitrate_and_sample_rate(tmp_path, tone_track, monkeypatch):
    commands = []
    real_run = subprocess.run

    def spy_run(cmd, **kwargs):
        if cmd[0] == "ffmpeg":
            commands.append([str(c) for c in cmd])
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(build_mod.subprocess, "run", spy_run)
    cfg = _library_cfg(tone_track)
    cfg["shorts"]["infographic"]["music_bitrate"] = "160k"
    cfg["shorts"]["music"] = {"sample_rate": 48000}
    build_mod.prepare_infographic_music_bed(
        tmp_path / "short-01", "shorts_daily_habit", cfg, 12.0
    )
    cmd = commands[-1]
    assert cmd[cmd.index("-b:a") + 1] == "160k"
    assert cmd[cmd.index("-ar") + 1] == "48000"


def test_serialized_offset_is_clamped_to_legal_bounds(tmp_path, tone_track, monkeypatch):
    """Rounding for the ffmpeg argument must never push the offset past the end
    margin (round(2) can exceed max by up to 5ms)."""
    monkeypatch.setattr(
        build_mod, "deterministic_music_excerpt_offset", lambda *a, **k: 5.006
    )
    monkeypatch.setattr(build_mod, "probe_audio_duration_seconds", lambda _p: 21.004)

    def fake_run(cmd, **kwargs):
        fake_run.cmd = [str(c) for c in cmd]
        Path(cmd[-1]).write_bytes(b"m4a")
        return SimpleNamespace(stdout="")

    # encoded-duration validation needs the second probe to agree
    probes = iter([21.004, 12.0])
    monkeypatch.setattr(
        build_mod, "probe_audio_duration_seconds", lambda _p: next(probes)
    )
    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)
    build_mod.prepare_infographic_music_bed(
        tmp_path / "short-01", "shorts_daily_habit", _library_cfg(tone_track), 12.0
    )
    offset = float(fake_run.cmd[fake_run.cmd.index("-ss") + 1])
    assert offset <= 21.004 - 12.0 - 1.0 + 1e-9  # never past the end margin


# --- round 2: all-or-nothing cue, encode cleanup, margin validation -----------

@pytest.mark.parametrize("voice_sec,expected_shown", [
    (19.0, False),   # tail 1.0s
    (18.5, False),   # tail 1.5s
    (17.9, False),   # tail 2.1s
    (17.1, False),   # tail 2.9s
    (17.0, True),    # tail exactly 3.0s -> full cue fits
])
def test_cue_is_all_or_nothing_never_shortened(tmp_path, voice_sec, expected_shown):
    """Review round 2: a shortened cue cannot complete its press sequence, so
    either the FULL 3.0s fits after the narration or the cue is disabled."""
    image_fn, llm_fn, music_fn, render_fn, voice_fn, mix_fn = _stub_pipeline_fns(voice_sec)
    cfg = {"audience": {"age_range": [45, 75]}, "channel": {"name": "VP"},
           "shorts": {"infographic": {"voice": {"enabled": True, "padding_sec": 1.0,
                                                "min_duration_sec": 10.0,
                                                "max_duration_sec": 20.0}}}}
    short_dir = tmp_path / "job" / "shorts" / "short-01"
    build_mod.run_infographic_short(
        short_dir, cfg, {"topic": "vista"}, image_fn=image_fn, llm_fn=llm_fn,
        music_fn=music_fn, render_fn=render_fn, voice_fn=voice_fn, mix_fn=mix_fn,
    )
    props = json.loads((short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE).read_text())
    assert props["showEngagementCue"] is expected_shown
    assert props["engagementCueDurationSec"] == (3.0 if expected_shown else 0.0)


@pytest.mark.parametrize("failure", ["timeout", "called_process_error"])
def test_encode_failures_remove_temp_and_preserve_existing_bed(
    tmp_path, tone_track, monkeypatch, failure
):
    """TimeoutExpired / CalledProcessError / duration mismatch must all remove
    the temp encode AND leave any existing good bed untouched."""
    short_dir = tmp_path / "job" / "shorts" / "short-01"
    out_path = short_dir / "audio" / "infographic_bgm.m4a"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"existing-good-bed")

    monkeypatch.setattr(build_mod, "probe_audio_duration_seconds", lambda _p: 30.0)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"partial")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 180)
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)
    with pytest.raises((subprocess.TimeoutExpired, subprocess.CalledProcessError)):
        build_mod.prepare_infographic_music_bed(
            short_dir, "shorts_daily_habit", _library_cfg(tone_track), 12.0
        )
    assert not out_path.with_suffix(".tmp.m4a").exists()
    assert out_path.read_bytes() == b"existing-good-bed"


def test_duration_mismatch_also_preserves_existing_bed(tmp_path, tone_track, monkeypatch):
    short_dir = tmp_path / "job" / "shorts" / "short-01"
    out_path = short_dir / "audio" / "infographic_bgm.m4a"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"existing-good-bed")

    probes = iter([30.0, 2.0])  # track OK, encoded mismatched
    monkeypatch.setattr(build_mod, "probe_audio_duration_seconds", lambda _p: next(probes))

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"garbage")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(build_mod.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="corrupt"):
        build_mod.prepare_infographic_music_bed(
            short_dir, "shorts_daily_habit", _library_cfg(tone_track), 12.0
        )
    assert not out_path.with_suffix(".tmp.m4a").exists()
    assert out_path.read_bytes() == b"existing-good-bed"


@pytest.mark.parametrize("key,value", [
    ("music_excerpt_min_offset_sec", -1.0),
    ("music_excerpt_min_offset_sec", float("nan")),
    ("music_excerpt_end_margin_sec", -0.5),
    ("music_excerpt_end_margin_sec", float("inf")),
])
def test_invalid_excerpt_margins_are_rejected(tmp_path, tone_track, key, value):
    cfg = _library_cfg(tone_track)
    cfg["shorts"]["infographic"][key] = value
    with pytest.raises(RuntimeError, match=key):
        build_mod.prepare_infographic_music_bed(
            tmp_path / "job" / "shorts" / "short-01", "shorts_daily_habit", cfg, 12.0
        )
