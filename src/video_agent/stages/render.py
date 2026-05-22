from __future__ import annotations

import json
import re
import shutil
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


def build_thumbnail_commands(render_props_path: Path, out_dir: Path) -> list[list[str]]:
    """Build Remotion still commands for each title_variant (up to 3).

    Each command overrides seo.thumbnail_text with the variant's value.
    Falls back to a single command using props as-is if no variants present.
    Outputs: thumbnail_1.jpg, thumbnail_2.jpg, thumbnail_3.jpg
    """
    remotion_root = repo_root() / "remotion"
    entry = remotion_root / "src/index.ts"
    public_dir = remotion_root / "public"
    base = ["npx", "--prefix", str(remotion_root), "remotion"]
    props_base = read_json(render_props_path)
    props_base["_render_props_path"] = str(render_props_path)

    variants = (props_base.get("seo") or {}).get("title_variants") or []
    if not variants:
        # Fallback: single thumbnail using props as-is
        variants = [{"thumbnail_text": (props_base.get("seo") or {}).get("thumbnail_text", "")}]

    cmds = []
    for i, variant in enumerate(variants[:3]):
        props = json.loads(json.dumps(props_base))  # deep copy
        props["seo"] = {**(props.get("seo") or {}), "thumbnail_text": variant.get("thumbnail_text", "")}
        out_path = out_dir / f"thumbnail_{i + 1}.jpg"
        cmds.append([
            *base, "still", str(entry), "ThumbnailStandard", str(out_path),
            "--props", json.dumps(props, ensure_ascii=False),
            "--public-dir", str(public_dir),
        ])
    return cmds


def _run_with_progress(cmd: list[str], progress_path: Path | None = None) -> None:
    """Run a subprocess, streaming stdout and writing Remotion progress to a JSON file."""
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=repo_root(),
    ) as proc:
        progress: dict = {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                print(line, end="", flush=True)
                m_frame = re.search(r"(\d+)\s*/\s*(\d+)", line)
                if m_frame and progress_path:
                    frame = int(m_frame.group(1))
                    total = int(m_frame.group(2))
                    pct = round(frame / max(total, 1) * 100, 1)
                    fps_m = re.search(r"([\d.]+)\s*fps", line, re.IGNORECASE)
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
                        progress_path.write_text(json.dumps(progress), encoding="utf-8")
                    except OSError:
                        pass
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
        rc = proc.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)


def render_with_remotion(render_props_path: Path, video_path: Path, thumbnail_path: Path) -> None:
    commands = build_remotion_commands(render_props_path, video_path, thumbnail_path)
    progress_path = render_props_path.parent / "render_progress.json"
    _run_with_progress(commands.video, progress_path)
    # Mark 100% on completion.
    try:
        progress_path.write_text(
            json.dumps({"percent": 100, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}),
            encoding="utf-8",
        )
    except OSError:
        pass
    thumb_dir = render_props_path.parent
    thumb_cmds = build_thumbnail_commands(render_props_path, thumb_dir)
    thumb_errors: list[str] = []
    for i, cmd in enumerate(thumb_cmds, start=1):
        try:
            subprocess.run(cmd, cwd=repo_root(), check=True)
        except subprocess.CalledProcessError as exc:
            # One bad variant should not invalidate the rendered video.
            thumb_errors.append(f"variant {i}: {exc}")
    # Keep thumbnail.jpg as alias of thumbnail_1.jpg for backward compat
    t1 = thumb_dir / "thumbnail_1.jpg"
    if t1.exists():
        shutil.copy2(t1, thumb_dir / "thumbnail.jpg")
    elif thumb_errors:
        raise RuntimeError(
            "All thumbnail variants failed: " + "; ".join(thumb_errors)
        )
