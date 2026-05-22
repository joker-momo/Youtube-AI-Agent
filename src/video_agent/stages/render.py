from __future__ import annotations

import json
import os
import re
import signal
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


def _audio_loudness_config(render_props_path: Path) -> dict:
    props = read_json(render_props_path)
    render_cfg = props.get("render", {}) or {}
    raw = render_cfg.get("audio_loudness", {}) or {}
    enabled = bool(raw.get("enabled", True))
    try:
        integrated = float(raw.get("integrated_lufs", -14.0))
    except Exception:
        integrated = -14.0
    try:
        true_peak = float(raw.get("true_peak_dbtp", -1.0))
    except Exception:
        true_peak = -1.0
    try:
        lra = float(raw.get("lra", 11.0))
    except Exception:
        lra = 11.0
    return {
        "enabled": enabled,
        "integrated_lufs": integrated,
        "true_peak_dbtp": true_peak,
        "lra": lra,
    }


def _normalize_video_audio(video_path: Path, *, integrated_lufs: float, true_peak_dbtp: float, lra: float) -> None:
    """Normalize output audio loudness in-place (via temp file swap)."""
    tmp_path = video_path.with_name(f"{video_path.stem}.loudnorm.tmp{video_path.suffix}")
    loudnorm = f"loudnorm=I={integrated_lufs}:TP={true_peak_dbtp}:LRA={lra}"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "copy",
        "-af",
        loudnorm,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(tmp_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp_path.replace(video_path)


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
        # While rendering thumbnails, never self-reference seo.thumbnail_path
        # because that target image does not exist yet and causes 404 in Remotion.
        props["seo"] = {
            **(props.get("seo") or {}),
            "thumbnail_text": variant.get("thumbnail_text", ""),
            "thumbnail_path": "",
        }
        out_path = out_dir / f"thumbnail_{i + 1}.jpg"
        cmds.append([
            *base, "still", str(entry), "ThumbnailStandard", str(out_path),
            "--props", json.dumps(props, ensure_ascii=False),
            "--public-dir", str(public_dir),
        ])
    return cmds


def _run_with_progress(
    cmd: list[str],
    progress_path: Path | None = None,
    *,
    stop_request_path: Path | None = None,
    pid_file_path: Path | None = None,
) -> None:
    """Run a subprocess, streaming stdout and writing Remotion progress to a JSON file."""
    if stop_request_path is not None and stop_request_path.exists():
        raise RuntimeError("Stop requested by operator.")
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=repo_root(),
        start_new_session=True,
    ) as proc:
        if pid_file_path is not None:
            try:
                pid_file_path.write_text(str(proc.pid), encoding="utf-8")
            except OSError:
                pass
        progress: dict = {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if stop_request_path is not None and stop_request_path.exists():
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except OSError:
                        pass
                    raise RuntimeError("Stop requested by operator.")
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
            if pid_file_path is not None:
                try:
                    if pid_file_path.exists() and pid_file_path.read_text(encoding="utf-8").strip() == str(proc.pid):
                        pid_file_path.unlink()
                except OSError:
                    pass
        rc = proc.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)


def render_with_remotion(
    render_props_path: Path,
    video_path: Path,
    thumbnail_path: Path,
    *,
    stop_request_path: Path | None = None,
) -> None:
    commands = build_remotion_commands(render_props_path, video_path, thumbnail_path)
    progress_path = render_props_path.parent / "render_progress.json"
    render_pid_path = render_props_path.parent / ".render.pid"
    thumb_pid_path = render_props_path.parent / ".thumbnail.pid"
    _run_with_progress(
        commands.video,
        progress_path,
        stop_request_path=stop_request_path,
        pid_file_path=render_pid_path,
    )
    loudness = _audio_loudness_config(render_props_path)
    if loudness["enabled"]:
        if stop_request_path is not None and stop_request_path.exists():
            raise RuntimeError("Stop requested by operator.")
        _normalize_video_audio(
            video_path,
            integrated_lufs=loudness["integrated_lufs"],
            true_peak_dbtp=loudness["true_peak_dbtp"],
            lra=loudness["lra"],
        )
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
            _run_with_progress(
                cmd,
                stop_request_path=stop_request_path,
                pid_file_path=thumb_pid_path,
            )
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
