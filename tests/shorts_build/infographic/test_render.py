import json

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
