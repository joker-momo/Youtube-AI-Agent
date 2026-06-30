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


class RemotionSubprocessError(RuntimeError):
    """Remotion failed; message includes the subprocess output tail."""


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


def _normalize_video_audio(
    video_path: Path, *, integrated_lufs: float, true_peak_dbtp: float, lra: float
) -> None:
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


def _render_offthreadvideo_threads(render_props_path: Path) -> int | None:
    """Parallel FFmpeg threads for OffthreadVideo frame extraction.

    Remotion's default is 2 — a pool shared across all render workers. With
    concurrency=auto (8 on this M2) the extraction pool starves the workers on a
    video-background-heavy composition. Bumping it lets more scene frames extract
    in parallel. This is NOT render concurrency (HARD RULE) — it only sizes the
    decode-feeder pool, never the worker count. Unset → Remotion's default.
    """
    props = read_json(render_props_path)
    raw = props.get("render", {}).get("offthreadvideo_threads")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    cpu_count = os.cpu_count() or 1
    return max(1, min(value, cpu_count))


def _render_scene_sum(props: dict) -> float:
    return round(
        sum(float(scene.get("duration_sec") or 0.0) for scene in (props.get("scenes") or [])), 3
    )


def validate_render_duration_matches_scene_sum(
    render_props_path: Path,
    *,
    tolerance_sec: float = 0.2,
) -> float:
    props = read_json(render_props_path)
    render_duration = float((props.get("render") or {}).get("duration_sec") or 0.0)
    scene_sum = _render_scene_sum(props)
    branding = props.get("branding") or {}
    branding_duration = float(branding.get("intro_sec") or 0.0) + float(
        branding.get("outro_sec") or 0.0
    )
    expected_duration = round(scene_sum + branding_duration, 3)
    if abs(render_duration - expected_duration) > float(tolerance_sec):
        raise ValueError(
            f"render.duration_sec {render_duration:.1f}s does not match scene sum {scene_sum:.1f}s "
            f"(tolerance {float(tolerance_sec):.1f}s)."
        )
    return render_duration


def probe_video_duration_sec(video_path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def validate_rendered_video_duration(
    video_path: Path,
    *,
    expected_duration_sec: float,
    tolerance_sec: float = 0.3,
) -> float:
    actual = probe_video_duration_sec(video_path)
    if actual is None:
        raise ValueError(f"Could not probe MP4 duration for {video_path}.")
    if abs(float(actual) - float(expected_duration_sec)) > float(tolerance_sec):
        raise ValueError(
            f"MP4 duration {float(actual):.1f}s does not match render.duration_sec "
            f"{float(expected_duration_sec):.1f}s (tolerance {float(tolerance_sec):.1f}s)."
        )
    return float(actual)


def build_remotion_commands(render_props_path: Path, video_path: Path) -> RemotionCommands:
    remotion_root = repo_root() / "remotion"
    entry = remotion_root / "src/index.ts"
    public_dir = remotion_root / "public"
    input_props = _input_props_arg(render_props_path)
    concurrency = str(_render_concurrency(render_props_path))
    video_bitrate = _render_video_bitrate(render_props_path)
    gl_backend = _render_gl(render_props_path)
    base = ["npx", "--prefix", str(remotion_root), "remotion"]
    props = read_json(render_props_path)
    composition = props.get("render", {}).get("composition", "ChannelVideoStandard")
    video_cmd = [
        *base,
        "render",
        str(entry),
        composition,
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
        # Per-frame capture quality. Remotion's default JPEG quality (80) visibly
        # softens every frame BEFORE the h264 encode (a double-lossy generation).
        # 100 = near-lossless capture so detail/text stay crisp; the final
        # h264 12M is the only intended compression. Quality > render speed.
        "--jpeg-quality",
        "100",
    ]
    if video_bitrate:
        video_cmd += ["--video-bitrate", video_bitrate]
    if gl_backend:
        video_cmd += ["--gl", gl_backend]
    ot_threads = _render_offthreadvideo_threads(render_props_path)
    if ot_threads:
        video_cmd += ["--offthreadvideo-video-threads", str(ot_threads)]
    return RemotionCommands(video=video_cmd)


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
    output_tail: list[str] = []
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
                output_tail.append(line.rstrip())
                if len(output_tail) > 80:
                    output_tail = output_tail[-80:]
                m_frame = re.search(r"(\d+)\s*/\s*(\d+)", line)
                if m_frame and progress_path:
                    frame = int(m_frame.group(1))
                    total = int(m_frame.group(2))
                    pct = round(frame / max(total, 1) * 100, 1)
                    fps_m = re.search(r"([\d.]+)\s*fps", line, re.IGNORECASE)
                    eta_m = re.search(
                        r"time remaining:\s*([\dm\s]+\d+s)", line, re.IGNORECASE
                    ) or re.search(r"ETA\s*([\d:]+)", line, re.IGNORECASE)
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
                    if pid_file_path.exists() and pid_file_path.read_text(
                        encoding="utf-8"
                    ).strip() == str(proc.pid):
                        pid_file_path.unlink()
                except OSError:
                    pass
        rc = proc.wait()
        if rc != 0:
            tail_text = "\n".join(output_tail[-40:]).strip()
            if progress_path is not None:
                try:
                    atomic_write_text(
                        progress_path.with_name("render_subprocess_error.txt"),
                        tail_text,
                        encoding="utf-8",
                    )
                except OSError:
                    pass
            message = f"Remotion subprocess exited with code {rc}."
            if tail_text:
                message += f" Last output:\n{tail_text}"
            raise RemotionSubprocessError(message)


def _mark_render_stage_completed(job_dir: Path) -> None:
    """Best-effort: mark render stage completed in job.json AND auto-complete
    the trailing ``review`` stage so the dashboard does not stall on a stage
    that is just an HTML write.

    ``review`` is not a gating QA step in this pipeline — ``run_review_stage``
    only calls ``write_operator_review`` to render ``operator_review.html``.
    Treating it like an external approval kept jobs sitting at "Review page"
    forever after every render. We now:
      1. Mark ``render`` completed.
      2. Try to write ``operator_review.html`` immediately (failures swallowed;
         the dashboard will still link the artifact next time).
      3. Mark ``review`` completed.
      4. Advance ``current_stage`` to whatever pending stage is left
         (typically nothing → job is fully done).

    All work is best-effort; bookkeeping must never invalidate a successful
    render artifact on disk.
    """
    state_path = job_dir / "job.json"
    if not state_path.exists():
        return
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        stages = data.get("stages") or []

        def _backfill_started_at(stage: dict) -> None:
            """If ``stage`` has no ``started_at``, copy the previous stage's
            ``completed_at`` so the dashboard reports a real duration rather
            than 0 seconds when this stage was never explicitly marked
            in_progress."""
            if stage.get("started_at"):
                return
            previous_end: str | None = None
            for earlier in stages:
                if earlier.get("name") == stage.get("name"):
                    break
                if earlier.get("completed_at"):
                    previous_end = earlier["completed_at"]
            stage["started_at"] = previous_end or now

        render_stage = next((s for s in stages if s.get("name") == "render"), None)
        if render_stage is None:
            return
        if render_stage.get("status") != "completed":
            _backfill_started_at(render_stage)
            render_stage["status"] = "completed"
            render_stage["completed_at"] = now

        # Auto-complete ``review`` whenever the render finishes. The stage is
        # purely cosmetic (writes operator_review.html), so blocking the job
        # there had no value and produced the recurring "stuck at Review
        # page" support thread. Write the HTML opportunistically; ignore
        # errors so a missing artifact does not block state advance.
        review_stage = next((s for s in stages if s.get("name") == "review"), None)
        if review_stage is not None and review_stage.get("status") != "completed":
            try:
                from video_agent.operator import write_operator_review

                write_operator_review(job_dir)
            except Exception as exc:  # noqa: BLE001
                print(f"[render] auto-review HTML write skipped: {exc}", flush=True)
            _backfill_started_at(review_stage)
            review_stage["status"] = "completed"
            review_stage["completed_at"] = now

        # Advance current_stage to the next pending stage that is not one of
        # the two we just completed.
        completed_names = {"render", "review"}
        if data.get("current_stage") in completed_names:
            pending = next(
                (
                    s["name"]
                    for s in stages
                    if s.get("status") not in {"completed", "skipped"}
                    and s.get("name") not in completed_names
                ),
                None,
            )
            data["current_stage"] = pending if pending else "review"
        atomic_write_json(state_path, data, indent=2)
    except Exception as exc:  # noqa: BLE001
        print(f"[render] state mark skipped: {exc}", flush=True)


def _notify_render_done(job_dir: Path) -> None:
    """Best-effort Telegram notify after a successful render.

    Silently no-ops when Telegram env vars are missing or the notifications
    module is unavailable. Errors are swallowed because notify failures must
    never invalidate a successful render.
    """
    if os.environ.get("VIDEO_AGENT_DISABLE_TELEGRAM", "").strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }:
        return
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
    *,
    stop_request_path: Path | None = None,
    notify_telegram: bool = True,
) -> None:
    job_dir = render_props_path.parent
    if job_dir.name == "json":
        job_dir = job_dir.parent

    expected_duration_sec = validate_render_duration_matches_scene_sum(render_props_path)
    commands = build_remotion_commands(render_props_path, video_path)
    progress_path = job_dir / "json" / "render_progress.json"
    render_pid_path = job_dir / "json" / ".render.pid"
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
    validate_rendered_video_duration(video_path, expected_duration_sec=expected_duration_sec)
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
    # Thumbnail step: the ChatGPT-generated thumbnail (auto_thumbnail_image_stage)
    # is now the ONLY thumbnail source. The legacy Remotion-still fallback was
    # removed 2026-06-30 — if the ChatGPT thumbnail is missing we stop so it can be
    # regenerated, rather than silently shipping a lower-quality fallback.
    # ------------------------------------------------------------------
    thumb_dir = job_dir / "outputs"
    chatgpt_thumb = thumb_dir / "thumbnail_1.jpg"
    if not chatgpt_thumb.exists():
        raise RuntimeError(
            f"ChatGPT thumbnail missing: {chatgpt_thumb}. Generate it "
            "(auto_thumbnail_image_stage) before rendering — the Remotion thumbnail "
            "fallback has been removed."
        )
    # Keep thumbnail.jpg alias in place for Telegram / operator UI.
    if not (thumb_dir / "thumbnail.jpg").exists():
        shutil.copy2(chatgpt_thumb, thumb_dir / "thumbnail.jpg")

    _mark_render_stage_completed(job_dir)
    if notify_telegram:
        _notify_render_done(job_dir)
