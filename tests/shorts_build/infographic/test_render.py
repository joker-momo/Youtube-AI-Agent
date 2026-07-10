import json
from pathlib import Path

from video_agent.shorts.infographic.render import build_infographic_render_command
from video_agent.shorts.infographic.render_props import build_infographic_render_props


def test_render_command_targets_infographic_composition_with_auto_concurrency(tmp_path):
    props = build_infographic_render_props(
        poster_ref="jobs/short-01/assets/poster.png",
        audio_ref="jobs/short-01/audio/short_narration.wav",
        duration_sec=28.0,
        music_track="shorts_sleep_stress",
        channel_name="Vida Plena 45+",
    )
    rp = tmp_path / "json" / "short_render_props.json"
    rp.parent.mkdir(parents=True)
    rp.write_text(json.dumps(props), encoding="utf-8")

    cmd = build_infographic_render_command(rp, tmp_path / "outputs" / "short.mp4")

    # Renders the InfographicShort composition.
    assert "InfographicShort" in cmd
    assert "--props" in cmd
    # Concurrency comes from "auto" -> resolved to a positive core count, NEVER a
    # hardcoded value in our code (HARD RULE).
    idx = cmd.index("--concurrency")
    assert int(cmd[idx + 1]) >= 1


def test_render_infographic_writes_live_progress_via_shared_primitive(monkeypatch, tmp_path):
    """Regression (operator report: 'render ko hien %'): render_infographic ran the
    Remotion subprocess with a bare subprocess.run that never captured stdout, so
    render_progress.json was never written -- the Renders tab progress bar sat at
    0% the whole render (Remotion prints 'Rendered n/m' the whole time; nothing
    parsed it into the file the UI polls)."""
    from video_agent.shorts.infographic import render as render_mod

    captured = {}
    short_dir = tmp_path / "job-1" / "shorts" / "short-01"
    (short_dir / "json").mkdir(parents=True, exist_ok=True)
    (short_dir / "json" / "short_render_props.json").write_text("{}", encoding="utf-8")
    video_path = short_dir / "outputs" / "short.mp4"

    def fake_run_with_progress(cmd, progress_path=None, **kwargs):
        captured["cmd"] = cmd
        captured["progress_path"] = progress_path
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"\x00")

    monkeypatch.setattr(render_mod, "_run_with_progress", fake_run_with_progress)
    monkeypatch.setattr(render_mod, "_mirror_short_assets_to_public", lambda _sd: None)
    monkeypatch.setattr(
        render_mod, "build_infographic_render_command", lambda _props, _out: ["remotion", "render"]
    )

    out = render_mod.render_infographic(short_dir, {})

    assert out == video_path
    assert captured["cmd"] == ["remotion", "render"]
    assert captured["progress_path"] == short_dir / "json" / "render_progress.json"
