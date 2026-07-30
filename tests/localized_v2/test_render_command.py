from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from video_agent.localized_v2.render import (
    LocalizedRenderError,
    build_render_command,
    render_localized_video,
)
from video_agent.localized_v2.render_props import compile_render_props

from .test_render_props import _inputs

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTION_ROOT = REPO_ROOT / "remotion"
SCHEMA_ROOT = REPO_ROOT / "schemas"


def test_render_command_uses_only_v2_entrypoint_and_automatic_concurrency(
    tmp_path: Path,
) -> None:
    props = tmp_path / "render-props.json"
    props.write_text("{}", encoding="utf-8")
    output = tmp_path / "final.mp4"

    command = build_render_command(
        remotion_root=REMOTION_ROOT,
        artifacts_root=tmp_path,
        props_path=props,
        output_path=output,
    )

    assert "src/localized-v2/index.ts" in command
    assert "LocalizedV2ChannelVideo" in command
    assert "src/index.ts" not in command
    assert "ChannelVideoStandard" not in command
    assert "--concurrency" not in command
    assert command[command.index("--public-dir") + 1] == str(tmp_path.resolve())


def test_render_validates_props_before_process_and_requires_output(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    payload = compile_render_props(**inputs)
    props_path = tmp_path / "render-props.json"
    props_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    output = tmp_path / "rendered.mp4"

    def successful_runner(*_args, **_kwargs):
        output.write_bytes(b"rendered")
        return subprocess.CompletedProcess([], 0, "", "")

    assert (
        render_localized_video(
            remotion_root=REMOTION_ROOT,
            artifacts_root=tmp_path,
            schema_root=SCHEMA_ROOT,
            props_path=props_path,
            output_path=output,
            runner=successful_runner,
        )
        == output
    )

    output.unlink()
    with pytest.raises(LocalizedRenderError) as error:
        render_localized_video(
            remotion_root=REMOTION_ROOT,
            artifacts_root=tmp_path,
            schema_root=SCHEMA_ROOT,
            props_path=props_path,
            output_path=output,
            runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
                [], 1, "", "failed"
            ),
        )
    assert error.value.code == "REMOTION_RENDER_FAILED"


def test_render_rejects_external_or_traversing_media_paths(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    payload = compile_render_props(**inputs)
    props_path = tmp_path / "render-props.json"
    output = tmp_path / "rendered.mp4"
    called = False

    def forbidden_runner(*_args, **_kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0, "", "")

    for hostile in ("https://evil.example/video.mp4", "../legacy/intro.mp4"):
        payload["branding"]["intro_video_path"] = hostile
        props_path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(LocalizedRenderError) as error:
            render_localized_video(
                remotion_root=REMOTION_ROOT,
                artifacts_root=tmp_path,
                schema_root=SCHEMA_ROOT,
                props_path=props_path,
                output_path=output,
                runner=forbidden_runner,
            )
        assert error.value.code == "INVALID_RENDER_PROPS"
    assert called is False
