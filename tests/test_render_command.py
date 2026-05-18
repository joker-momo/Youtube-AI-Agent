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


def test_thumbnail_uses_render_props_content_instead_of_demo_copy():
    thumbnail_source = (ROOT / "remotion/src/Thumbnail.tsx").read_text(encoding="utf-8")

    assert "DORMIR MEJOR" not in thumbnail_source
    assert "DESPUES DE LOS 45" not in thumbnail_source
    assert "props.seo.title" in thumbnail_source
    assert "props.scenes[0]" in thumbnail_source
