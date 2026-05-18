from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_agent.contracts import repo_root
from video_agent.utils.json_io import read_json


@dataclass
class RemotionCommands:
    video: list[str]
    thumbnail: list[str]


def _input_props_arg(render_props_path: Path) -> str:
    props = read_json(render_props_path)
    props["_render_props_path"] = str(render_props_path)
    return json.dumps(props, ensure_ascii=False)


def build_remotion_commands(render_props_path: Path, video_path: Path, thumbnail_path: Path) -> RemotionCommands:
    remotion_root = repo_root() / "remotion"
    entry = remotion_root / "src/index.ts"
    public_dir = remotion_root / "public"
    input_props = _input_props_arg(render_props_path)
    base = ["npx", "--prefix", str(remotion_root), "remotion"]
    return RemotionCommands(
        video=[
            *base,
            "render",
            str(entry),
            "ChannelVideoStandard",
            str(video_path),
            "--props",
            input_props,
            "--codec",
            "h264",
            "--public-dir",
            str(public_dir),
        ],
        thumbnail=[
            *base,
            "still",
            str(entry),
            "ThumbnailStandard",
            str(thumbnail_path),
            "--props",
            input_props,
            "--public-dir",
            str(public_dir),
        ],
    )


def render_with_remotion(render_props_path: Path, video_path: Path, thumbnail_path: Path) -> None:
    commands = build_remotion_commands(render_props_path, video_path, thumbnail_path)
    subprocess.run(commands.video, cwd=repo_root(), check=True)
    subprocess.run(commands.thumbnail, cwd=repo_root(), check=True)
