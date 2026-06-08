import json
import os
import subprocess
from pathlib import Path

from video_agent.stages.render import build_remotion_commands


ROOT = Path(__file__).resolve().parents[1]


def test_build_remotion_commands_include_props_and_outputs(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text("{}", encoding="utf-8")
    video_path = tmp_path / "video.mp4"
    thumbnail_path = tmp_path / "thumbnail.jpg"
    commands = build_remotion_commands(render_props, video_path, thumbnail_path)
    video_command = " ".join(commands.video)
    still_command = " ".join(commands.thumbnail)
    assert "ChannelVideoStandard" in video_command
    assert "ThumbnailStandard" in still_command
    assert str(video_path) in video_command
    assert str(thumbnail_path) in still_command
    assert str(render_props) in video_command


def _concurrency_arg(commands_video: list[str]) -> str:
    return commands_video[commands_video.index("--concurrency") + 1]


def test_video_render_defaults_to_all_cpu_cores(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text("{}", encoding="utf-8")
    commands = build_remotion_commands(render_props, tmp_path / "video.mp4", tmp_path / "thumbnail.jpg")

    assert _concurrency_arg(commands.video) == str(max(1, os.cpu_count() or 1))


def test_video_render_uses_configured_concurrency(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text('{"render": {"concurrency": 2}}', encoding="utf-8")
    commands = build_remotion_commands(render_props, tmp_path / "video.mp4", tmp_path / "thumbnail.jpg")

    assert _concurrency_arg(commands.video) == "2"


def test_video_render_auto_concurrency_uses_all_cpu_cores(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text('{"render": {"concurrency": "auto"}}', encoding="utf-8")
    commands = build_remotion_commands(render_props, tmp_path / "video.mp4", tmp_path / "thumbnail.jpg")

    assert _concurrency_arg(commands.video) == str(max(1, os.cpu_count() or 1))


def test_video_render_clamps_concurrency_to_cpu_count(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text('{"render": {"concurrency": 999}}', encoding="utf-8")
    commands = build_remotion_commands(render_props, tmp_path / "video.mp4", tmp_path / "thumbnail.jpg")

    assert _concurrency_arg(commands.video) == str(max(1, os.cpu_count() or 1))


def test_video_render_passes_video_bitrate_when_configured(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text('{"render": {"video_bitrate": "12M"}}', encoding="utf-8")
    commands = build_remotion_commands(render_props, tmp_path / "video.mp4", tmp_path / "thumbnail.jpg")

    assert "--video-bitrate" in commands.video
    assert commands.video[commands.video.index("--video-bitrate") + 1] == "12M"


def test_video_render_omits_video_bitrate_when_not_configured(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text("{}", encoding="utf-8")
    commands = build_remotion_commands(render_props, tmp_path / "video.mp4", tmp_path / "thumbnail.jpg")

    assert "--video-bitrate" not in commands.video


def test_video_render_passes_gl_backend_to_video_and_thumbnail(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text('{"render": {"gl": "angle"}}', encoding="utf-8")
    commands = build_remotion_commands(render_props, tmp_path / "video.mp4", tmp_path / "thumbnail.jpg")

    assert "--gl" in commands.video
    assert commands.video[commands.video.index("--gl") + 1] == "angle"
    assert "--gl" in commands.thumbnail
    assert commands.thumbnail[commands.thumbnail.index("--gl") + 1] == "angle"


def test_video_render_ignores_unknown_gl_backend(tmp_path):
    render_props = tmp_path / "render_props.json"
    render_props.write_text('{"render": {"gl": "nonsense"}}', encoding="utf-8")
    commands = build_remotion_commands(render_props, tmp_path / "video.mp4", tmp_path / "thumbnail.jpg")

    assert "--gl" not in commands.video
    assert "--gl" not in commands.thumbnail


def test_pre_render_duration_validation_blocks_props_scene_sum_mismatch(tmp_path):
    from video_agent.stages.render import validate_render_duration_matches_scene_sum

    render_props = tmp_path / "render_props.json"
    render_props.write_text(
        json.dumps({
            "render": {"duration_sec": 20.0},
            "scenes": [
                {"id": "s01", "duration_sec": 13.2},
                {"id": "s02", "duration_sec": 13.2},
            ],
        }),
        encoding="utf-8",
    )

    try:
        validate_render_duration_matches_scene_sum(render_props)
    except ValueError as exc:
        assert "render.duration_sec 20.0s does not match scene sum 26.4s" in str(exc)
    else:
        raise AssertionError("expected duration mismatch to fail before render")


def test_post_render_duration_validation_blocks_short_mp4_mismatch(tmp_path, monkeypatch):
    from video_agent.stages.render import validate_rendered_video_duration

    video = tmp_path / "video.mp4"
    video.write_bytes(b"mp4")

    def fake_run(cmd, check, capture_output, text):
        assert "ffprobe" in cmd[0]
        return subprocess.CompletedProcess(cmd, 0, stdout="20.000000\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        validate_rendered_video_duration(video, expected_duration_sec=26.4)
    except ValueError as exc:
        assert "MP4 duration 20.0s does not match render.duration_sec 26.4s" in str(exc)
    else:
        raise AssertionError("expected rendered MP4 duration mismatch to fail")


def test_thumbnail_uses_render_props_content_instead_of_demo_copy():
    thumbnail_source = (ROOT / "remotion/src/Thumbnail.tsx").read_text(encoding="utf-8")

    assert "DORMIR MEJOR" not in thumbnail_source
    assert "DESPUES DE LOS 45" not in thumbnail_source
    assert "props.seo.title" in thumbnail_source
    assert "props.scenes[0]" in thumbnail_source
