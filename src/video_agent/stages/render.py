from __future__ import annotations

import json
import re
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


def _render_concurrency(render_props_path: Path) -> int:
    props = read_json(render_props_path)
    concurrency = props.get("render", {}).get("concurrency", 1)
    return max(1, int(concurrency))


def build_remotion_commands(render_props_path: Path, video_path: Path, thumbnail_path: Path) -> RemotionCommands:
    remotion_root = repo_root() / "remotion"
    entry = remotion_root / "src/index.ts"
    public_dir = remotion_root / "public"
    input_props = _input_props_arg(render_props_path)
    concurrency = str(_render_concurrency(render_props_path))
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
            "--concurrency",
            concurrency,
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


def _run_with_progress(cmd: list[str], progress_path: Path | None = None) -> None:
    """Run a subprocess, streaming stdout and writing Remotion progress to a JSON file."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=repo_root(),
    )
    progress: dict = {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
    for line in proc.stdout:  # type: ignore[union-attr]
        print(line, end="", flush=True)
        # Remotion progress line: "Rendered 18800/44400, time remaining: 5m 22s"
        # Also catches generic "N/M" variants with optional fps/ETA.
        m_frame = re.search(r"(\d+)\s*/\s*(\d+)", line)
        if m_frame and progress_path:
            frame = int(m_frame.group(1))
            total = int(m_frame.group(2))
            pct = round(frame / max(total, 1) * 100, 1)
            fps_m = re.search(r"([\d.]+)\s*fps", line, re.IGNORECASE)
            # "time remaining: 5m 22s" or "ETA 0:05:22"
            eta_m = re.search(r"time remaining:\s*([\dm\s]+\d+s)", line, re.IGNORECASE) \
                 or re.search(r"ETA\s*([\d:]+)", line, re.IGNORECASE)
            progress = {
                "percent": pct,
                "frame": frame,
                "total_frames": total,
                "fps": float(fps_m.group(1)) if fps_m else progress.get("fps", 0.0),
                "eta": eta_m.group(1).strip() if eta_m else "",
            }
            try:
                progress_path.write_text(json.dumps(progress))
            except OSError:
                pass
    proc.wait()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def render_with_remotion(render_props_path: Path, video_path: Path, thumbnail_path: Path) -> None:
    commands = build_remotion_commands(render_props_path, video_path, thumbnail_path)
    progress_path = render_props_path.parent / "render_progress.json"
    _run_with_progress(commands.video, progress_path)
    # Mark 100% on completion.
    try:
        progress_path.write_text(json.dumps({"percent": 100, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}))
    except OSError:
        pass
    subprocess.run(commands.thumbnail, cwd=repo_root(), check=True)
