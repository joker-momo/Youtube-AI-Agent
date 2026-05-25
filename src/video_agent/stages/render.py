from __future__ import annotations

import json
import os
import re
import signal
import datetime
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from video_agent.contracts import repo_root
from video_agent.storage.atomic import atomic_write_json, atomic_write_text
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
    raw = props.get("render", {}).get("concurrency", "auto")
    cpu_count = os.cpu_count() or 1
    if isinstance(raw, str) and raw.strip().lower() in {"auto", "max", ""}:
        return max(1, cpu_count)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return max(1, cpu_count)
    if value <= 0:
        return max(1, cpu_count)
    return max(1, min(value, cpu_count))


_ALLOWED_GL_BACKENDS = {"angle", "swangle", "egl", "swiftshader", "vulkan", "angle-egl"}


def _render_video_bitrate(render_props_path: Path) -> str | None:
    props = read_json(render_props_path)
    raw = props.get("render", {}).get("video_bitrate")
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _render_gl(render_props_path: Path) -> str | None:
    props = read_json(render_props_path)
    raw = props.get("render", {}).get("gl")
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value not in _ALLOWED_GL_BACKENDS:
        return None
    return value


def build_remotion_commands(render_props_path: Path, video_path: Path, thumbnail_path: Path) -> RemotionCommands:
    remotion_root = repo_root() / "remotion"
    entry = remotion_root / "src/index.ts"
    public_dir = remotion_root / "public"
    input_props = _input_props_arg(render_props_path)
    concurrency = str(_render_concurrency(render_props_path))
    video_bitrate = _render_video_bitrate(render_props_path)
    gl_backend = _render_gl(render_props_path)
    base = ["npx", "--prefix", str(remotion_root), "remotion"]
    video_cmd = [
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
        "--hardware-acceleration",
        "if-possible",
    ]
    if video_bitrate:
        video_cmd += ["--video-bitrate", video_bitrate]
    if gl_backend:
        video_cmd += ["--gl", gl_backend]
    thumbnail_cmd = [
        *base,
        "still",
        str(entry),
        "ThumbnailStandard",
        str(thumbnail_path),
        "--props",
        input_props,
        "--public-dir",
        str(public_dir),
        "--hardware-acceleration",
        "if-possible",
    ]
    if gl_backend:
        thumbnail_cmd += ["--gl", gl_backend]
    return RemotionCommands(video=video_cmd, thumbnail=thumbnail_cmd)


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
    gl_backend = _render_gl(render_props_path)

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
        cmd = [
            *base, "still", str(entry), "ThumbnailStandard", str(out_path),
            "--props", json.dumps(props, ensure_ascii=False),
            "--public-dir", str(public_dir),
            "--hardware-acceleration", "if-possible",
        ]
        if gl_backend:
            cmd += ["--gl", gl_backend]
        cmds.append(cmd)
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
                atomic_write_text(pid_file_path, str(proc.pid), encoding="utf-8")
            except OSError:
                pass
        progress: dict = {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
        last_frame_ts = time.monotonic()
        last_frame_count = 0
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
                    if fps_m:
                        fps_value = float(fps_m.group(1))
                    else:
                        now = time.monotonic()
                        elapsed = now - last_frame_ts
                        delta_frames = frame - last_frame_count
                        if elapsed >= 0.5 and delta_frames > 0:
                            fps_value = round(delta_frames / elapsed, 1)
                            last_frame_ts = now
                            last_frame_count = frame
                        else:
                            fps_value = progress.get("fps", 0.0)
                    progress = {
                        "percent": pct,
                        "frame": frame,
                        "total_frames": total,
                        "fps": fps_value,
                        "eta": eta_m.group(1).strip() if eta_m else "",
                    }
                    try:
                        atomic_write_json(progress_path, progress, indent=0)
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


def _mark_render_stage_completed(job_dir: Path) -> None:
    """Best-effort: mark render stage completed in job.json.

    No-op when job.json missing or render stage absent. Errors swallowed —
    state bookkeeping must never invalidate a successful render artifact.
    """
    state_path = job_dir / "job.json"
    if not state_path.exists():
        return
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        stages = data.get("stages") or []
        render_stage = next((s for s in stages if s.get("name") == "render"), None)
        if render_stage is None:
            return
        if render_stage.get("status") == "completed":
            return
        render_stage["status"] = "completed"
        render_stage["completed_at"] = now
        # Advance current_stage to the next pending stage if render was current.
        if data.get("current_stage") == "render":
            pending = next(
                (s["name"] for s in stages if s.get("status") not in {"completed", "skipped"} and s.get("name") != "render"),
                None,
            )
            if pending:
                data["current_stage"] = pending
        atomic_write_json(state_path, data, indent=2)
    except Exception as exc:  # noqa: BLE001
        print(f"[render] state mark skipped: {exc}", flush=True)


def _notify_render_done(job_dir: Path) -> None:
    """Best-effort Telegram notify after a successful render.

    Silently no-ops when Telegram env vars are missing or the notifications
    module is unavailable. Errors are swallowed because notify failures must
    never invalidate a successful render.
    """
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        return
    if os.environ.get("RENDER_NOTIFY_TELEGRAM", "1") == "0":
        return
    try:
        import asyncio

        from video_agent.notifications.telegram import notify_job_done_with_files

        asyncio.run(
            notify_job_done_with_files(
                job_id=job_dir.name,
                job_dir=job_dir,
                stages_done=["render"],
                wall_seconds=None,
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[render] telegram notify skipped: {exc}", flush=True)


def render_with_remotion(
    render_props_path: Path,
    video_path: Path,
    thumbnail_path: Path,
    *,
    stop_request_path: Path | None = None,
    notify_telegram: bool = True,
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
        atomic_write_json(
            progress_path,
            {"percent": 100, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""},
            indent=0,
        )
    except OSError:
        pass

    # ------------------------------------------------------------------
    # Thumbnail step: skip if ChatGPT already generated thumbnail_1.jpg
    # (new full-composite flow via auto_thumbnail_image_stage).
    # Fall back to remotion still for legacy jobs or if file is missing.
    # ------------------------------------------------------------------
    thumb_dir = render_props_path.parent
    chatgpt_thumb = thumb_dir / "thumbnail_1.jpg"

    if chatgpt_thumb.exists():
        # New flow: thumbnails were already generated with text baked in.
        # Make sure thumbnail.jpg alias is in place for Telegram / operator UI.
        if not (thumb_dir / "thumbnail.jpg").exists():
            shutil.copy2(chatgpt_thumb, thumb_dir / "thumbnail.jpg")
    else:
        # Legacy/fallback flow: render thumbnails via Remotion still.
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

    _mark_render_stage_completed(render_props_path.parent)
    if notify_telegram:
        _notify_render_done(render_props_path.parent)
