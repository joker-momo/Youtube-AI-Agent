from __future__ import annotations

import datetime
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from video_agent.assets.materialize import materialize_media
from video_agent.branding import medical_disclaimer_duration_sec
from video_agent.contracts import repo_root
from video_agent.storage.atomic import atomic_write_json, atomic_write_text
from video_agent.utils.json_io import read_json


@dataclass
class RemotionCommands:
    video: list[str]


class RemotionSubprocessError(RuntimeError):
    """Remotion failed; message includes the subprocess output tail."""


def _render_tmp_dir() -> Path:
    """Dedicated Remotion temp on the repo's (large) volume, NOT the system /tmp.

    Remotion buffers decoded frames + asset segments in os.tmpdir(); on macOS that
    is the small, OS-shared ``/var/folders`` volume, which a long 1080p render can
    fill → ENOSPC mid-render (bug 2026-07-01). The repo lives on the large data
    volume, so a sibling ``.render_tmp`` there has far more headroom.
    """
    d = repo_root() / ".render_tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _assert_render_disk_space(
    min_gb: float = 15.0, output_path: Path | None = None
) -> None:
    """Fail fast (before the long render) if any involved volume is low.

    Three volumes matter and can all differ: the Remotion buffer dir
    (.render_tmp on the data volume), the render output dir, and the OS temp
    dir that Chromium/ffmpeg still use for scratch. bug-441: a render died
    with ENOSPC at 53% even though the .render_tmp-only check had passed —
    render is not resumable, so a mid-encode ENOSPC costs the whole render.
    """
    import tempfile

    targets: dict[str, Path] = {
        "render temp": _render_tmp_dir(),
        "system temp": Path(tempfile.gettempdir()),
    }
    if output_path is not None:
        parent = output_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        targets["render output"] = parent
    for label, target in targets.items():
        # System temp only holds light scratch; the render buffers and the
        # growing video need the full headroom.
        required = 5.0 if label == "system temp" else min_gb
        free_gb = shutil.disk_usage(target).free / 1e9
        print(f"[render] preflight: {free_gb:.1f} GB free on {label} ({target})", flush=True)
        if free_gb < required:
            raise RuntimeError(
                f"Only {free_gb:.1f} GB free on the {label} volume ({target}); "
                f"need >= {required:.0f} GB. Free space (old jobs/, composed clips, "
                "demos) before rendering to avoid an ENOSPC crash mid-render."
            )


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
    """Parallel FFmpeg threads for Remotion video frame extraction.

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
    branding_duration = (
        float(branding.get("intro_sec") or 0.0)
        + medical_disclaimer_duration_sec(branding)
        + float(branding.get("outro_sec") or 0.0)
    )
    expected_duration = round(scene_sum + branding_duration, 3)
    if abs(render_duration - expected_duration) > float(tolerance_sec):
        raise ValueError(
            f"render.duration_sec {render_duration:.1f}s does not match scene sum {scene_sum:.1f}s "
            f"(tolerance {float(tolerance_sec):.1f}s)."
        )
    return render_duration


def probe_video_duration_sec(video_path: Path) -> float | None:
    """Probe the true playable duration of ``video_path`` as ``nb_frames /
    r_frame_rate`` — deliberately NOT any of ffprobe's computed "duration"
    fields (``format=duration``, ``stream=duration``, ``avg_frame_rate``).

    Root cause (bug-447/448, confirmed by isolating each segment of a real
    15-segment production render before AND after the ffmpeg concat step):
    each segment file, in isolation, is perfectly clean —
    avg_frame_rate=30/1, duration exactly nb_frames/30. Remotion's
    ``--for-seamless-aac-concatenation`` deliberately pads each segment's
    AUDIO track slightly past its video track's true length (to land on an
    AAC frame boundary for click-free joins) — by design, not a bug. But
    once ffmpeg's concat demuxer (``-c copy``) joins many such
    audio-longer-than-video segments, it corrupts the VIDEO stream's
    computed duration/avg_frame_rate across the whole file (observed:
    avg_frame_rate drifted to ~29.97 fps on the concatenated output,
    accumulating to a ~2s duration overshoot on a 29-minute video that
    exceeded the 0.3s validation tolerance and rejected an otherwise
    perfect render). Through all of this, ``nb_frames`` (exact content) and
    ``r_frame_rate`` (the DECLARED nominal rate, as opposed to the
    corrupted computed ``avg_frame_rate``) stayed correct on every single
    file checked — before concat, after concat, and on prior non-segmented
    renders alike. Computing duration from those two fields is immune to
    the concat-induced metadata corruption and identical to the old
    format=duration reading for any non-concatenated file.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_frames,r_frame_rate",
                "-of",
                "default=noprint_wrappers=1",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        values: dict[str, str] = {}
        for line in result.stdout.strip().splitlines():
            key, _, value = line.partition("=")
            values[key] = value
        nb_frames_raw = values.get("nb_frames")
        rate_raw = values.get("r_frame_rate")
        if nb_frames_raw and nb_frames_raw != "N/A" and rate_raw and rate_raw != "N/A":
            num_str, _, den_str = rate_raw.partition("/")
            rate = float(num_str) / float(den_str or 1)
            if rate > 0:
                return int(nb_frames_raw) / rate
    except (subprocess.SubprocessError, ValueError, ZeroDivisionError):
        pass
    # Fallback for streams that don't populate nb_frames/r_frame_rate
    # (rare containers/codecs) — container-level duration, best effort.
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


def probe_audio_duration_sec(video_path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        out = result.stdout.strip()
        if out and out != "N/A":
            return float(out)
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


def validate_rendered_video_duration(
    video_path: Path,
    *,
    expected_duration_sec: float,
    tolerance_sec: float = 0.3,
) -> float:
    """Validate BOTH streams of the delivered MP4 against the timeline.

    Video via exact frame math (nb_frames / r_frame_rate); audio via its own
    stream duration. bug-454: validating only frame count let a file ship
    whose audio track was ~2s longer than the video (accumulated per-segment
    AAC padding from raw concat) — audible as progressively-late narration.
    A correct artifact (single-piece audio muxed over concatenated muted
    video) satisfies both checks; a desynced one fails the audio check."""
    actual = probe_video_duration_sec(video_path)
    if actual is None:
        raise ValueError(f"Could not probe MP4 duration for {video_path}.")
    if abs(float(actual) - float(expected_duration_sec)) > float(tolerance_sec):
        raise ValueError(
            f"MP4 duration {float(actual):.1f}s does not match render.duration_sec "
            f"{float(expected_duration_sec):.1f}s (tolerance {float(tolerance_sec):.1f}s)."
        )
    audio_actual = probe_audio_duration_sec(video_path)
    if audio_actual is not None and abs(audio_actual - float(expected_duration_sec)) > float(
        tolerance_sec
    ):
        raise ValueError(
            f"MP4 AUDIO duration {audio_actual:.1f}s does not match "
            f"render.duration_sec {float(expected_duration_sec):.1f}s "
            f"(tolerance {float(tolerance_sec):.1f}s) — audio/video desync."
        )
    return float(actual)


def build_remotion_commands(
    render_props_path: Path,
    video_path: Path,
    *,
    frame_range: tuple[int, int] | None = None,
    muted: bool = False,
) -> RemotionCommands:
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
    if frame_range is not None:
        start, end = frame_range
        video_cmd += ["--frames", f"{start}-{end}"]
    if muted:
        # Segmented renders produce video-only chunks; audio is rendered ONCE
        # for the full composition and muxed in after concat. Per-segment
        # audio (even with --for-seamless-aac-concatenation) accumulated AAC
        # padding at every raw-concat boundary — bug-454: narration drifted
        # progressively late, ~1.9s behind the burned-in subtitles by the end
        # of a 15-segment render. Remotion's seamless flag only works with
        # Remotion's own offset-trimming combiner, not a plain ffmpeg concat.
        video_cmd += ["--muted"]
    return RemotionCommands(video=video_cmd)


def build_remotion_audio_command(render_props_path: Path, audio_path: Path) -> list[str]:
    """Full-composition audio-only render (WAV: sample-exact, no AAC padding).

    One continuous audio track for the whole timeline — muxed over the
    concatenated muted video segments, so there is no per-segment audio to
    misalign (bug-454)."""
    remotion_root = repo_root() / "remotion"
    entry = remotion_root / "src/index.ts"
    public_dir = remotion_root / "public"
    props = read_json(render_props_path)
    composition = props.get("render", {}).get("composition", "ChannelVideoStandard")
    concurrency = str(_render_concurrency(render_props_path))
    return [
        "npx",
        "--prefix",
        str(remotion_root),
        "remotion",
        "render",
        str(entry),
        composition,
        str(audio_path),
        "--props",
        _input_props_arg(render_props_path),
        "--codec",
        "wav",
        "--public-dir",
        str(public_dir),
        "--concurrency",
        concurrency,
    ]


def _blank_progress(phase: str) -> dict:
    return {
        "phase": phase,
        "percent": 0,
        "frame": 0,
        "rendered_frame": 0,
        "encoded_frame": 0,
        "total_frames": 0,
        "fps": 0.0,
        "eta": "",
        "attempt_started_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


def _update_progress_from_line(progress: dict, line: str) -> bool:
    """Fold one Remotion output line into ``progress``. Returns True if changed.

    Remotion interleaves TWO counters — ``Rendered n/m`` (frames produced)
    and ``Encoded n/m`` (encoder catching up). The old generic ``n/m`` regex
    overwrote a single ``frame`` field with whichever counter printed last,
    so the dashboard seesawed (e.g. 17433 → 885) and looked like a restart.
    Track them separately and expose an explicit ``phase``.
    """
    changed = False
    if re.search(r"\bbundling\b|\bbundled\b", line, re.IGNORECASE):
        if progress.get("phase") in (None, "", "preparing"):
            progress["phase"] = "bundling"
            changed = True
    m = re.search(r"\bRendered\s+(\d+)\s*/\s*(\d+)", line, re.IGNORECASE)
    if m:
        progress["rendered_frame"] = int(m.group(1))
        progress["total_frames"] = int(m.group(2))
        if progress.get("phase") != "encoding":
            progress["phase"] = "rendering"
        changed = True
    m = re.search(r"\bEncoded\s+(\d+)\s*/\s*(\d+)", line, re.IGNORECASE)
    if m:
        progress["encoded_frame"] = int(m.group(1))
        progress["total_frames"] = int(m.group(2))
        progress["phase"] = "encoding"
        changed = True
    if not changed:
        return False
    total = max(int(progress.get("total_frames") or 0), 1)
    # Completion percent follows the encoder once it starts (the true output);
    # before that, rendered frames are the best signal. Legacy ``frame`` keeps
    # feeding older dashboard readers with the same number percent uses.
    lead_frame = progress.get("encoded_frame") or progress.get("rendered_frame") or 0
    progress["frame"] = lead_frame
    progress["percent"] = round(lead_frame / total * 100, 1)
    eta_m = re.search(
        r"time remaining:\s*([\dhms\s]+?s)\b", line, re.IGNORECASE
    ) or re.search(r"ETA\s*([\d:]+)", line, re.IGNORECASE)
    if eta_m:
        progress["eta"] = eta_m.group(1).strip()
    return True


def _run_with_progress(
    cmd: list[str],
    progress_path: Path | None = None,
    *,
    stop_request_path: Path | None = None,
    pid_file_path: Path | None = None,
    progress_overrides: dict | None = None,
) -> None:
    """Run a subprocess, streaming stdout and writing Remotion progress to a JSON file.

    ``progress_overrides`` is merged into the progress dict once at start (e.g.
    segment_index/segment_total for a segmented render) and stays present for
    every subsequent write since ``_update_progress_from_line`` only sets its
    own known keys, never replaces the dict.
    """
    if stop_request_path is not None and stop_request_path.exists():
        raise RuntimeError("Stop requested by operator.")
    output_tail: list[str] = []
    # Redirect Remotion's temp (decoded-frame / asset buffers) onto the repo's large
    # volume instead of the small, macOS-shared system /var/folders — a long 1080p
    # render otherwise fills the system volume and dies with ENOSPC mid-render.
    render_env = {**os.environ, "TMPDIR": str(_render_tmp_dir())}
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=repo_root(),
        start_new_session=True,
        env=render_env,
    ) as proc:
        if pid_file_path is not None:
            try:
                atomic_write_text(pid_file_path, str(proc.pid), encoding="utf-8")
            except OSError:
                pass
        progress: dict = _blank_progress("preparing")
        if progress_overrides:
            progress.update(progress_overrides)
        if progress_path is not None:
            # Overwrite any leftovers from a previous attempt immediately so
            # a stale frame count never masquerades as live progress while
            # Remotion is still bundling (bug-441 follow-up).
            try:
                atomic_write_json(progress_path, progress, indent=0)
            except OSError:
                pass
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
                if progress_path and _update_progress_from_line(progress, line):
                    fps_m = re.search(r"([\d.]+)\s*fps", line, re.IGNORECASE)
                    if fps_m:
                        progress["fps"] = float(fps_m.group(1))
                    else:
                        now = time.monotonic()
                        frame = int(progress.get("frame") or 0)
                        elapsed = now - last_frame_ts
                        delta_frames = frame - last_frame_count
                        if elapsed >= 0.5 and delta_frames > 0:
                            progress["fps"] = round(delta_frames / elapsed, 1)
                            last_frame_ts = now
                            last_frame_count = frame
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
        now = datetime.datetime.now(datetime.UTC).isoformat()
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


# ---------------------------------------------------------------------------
# Segmented render: split a long video into frame-range chunks so a crash
# (ENOSPC, network change, etc.) mid-render only loses the current segment
# instead of the whole render. bug-441 lost 90 minutes to a crash at 53%.
# ---------------------------------------------------------------------------

DEFAULT_SEGMENT_SECONDS = 120.0


def _segment_seconds(render_props_path: Path) -> float:
    props = read_json(render_props_path)
    raw = (props.get("render", {}) or {}).get("segment_seconds")
    try:
        value = float(raw) if raw is not None else DEFAULT_SEGMENT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_SEGMENT_SECONDS
    return value if value > 0 else DEFAULT_SEGMENT_SECONDS


def _segmented_render_enabled(render_props_path: Path) -> bool:
    props = read_json(render_props_path)
    # Default ON: segmented rendering is strictly safer for long videos and
    # degrades to a single "segment" for anything shorter than segment_seconds,
    # so it is a no-op for Shorts / short long-form renders.
    return bool((props.get("render", {}) or {}).get("segmented", True))


def _total_render_frames_from_props(props: dict) -> int:
    render_cfg = props.get("render", {}) or {}
    frames = render_cfg.get("duration_in_frames")
    if isinstance(frames, int) and frames > 0:
        return frames
    fps = float(render_cfg.get("fps") or 30)
    duration = float(render_cfg.get("duration_sec") or 0)
    return max(1, round(fps * duration))


def _total_render_frames(render_props_path: Path) -> int:
    return _total_render_frames_from_props(read_json(render_props_path))


def _segment_plan(total_frames: int, segment_frames: int) -> list[tuple[int, int]]:
    """Inclusive [start, end] 0-indexed frame ranges covering ``total_frames``."""
    if segment_frames <= 0:
        segment_frames = total_frames
    plan: list[tuple[int, int]] = []
    start = 0
    while start < total_frames:
        end = min(start + segment_frames, total_frames) - 1
        plan.append((start, end))
        start = end + 1
    return plan


def _segments_dir(job_dir: Path) -> Path:
    d = job_dir / "outputs" / ".render_segments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _segment_path(job_dir: Path, index: int) -> Path:
    return _segments_dir(job_dir) / f"seg_{index:04d}.mp4"


def _segment_is_valid(
    path: Path, *, start: int, end: int, fps: float, tolerance_sec: float = 0.15
) -> bool:
    """A segment is trustworthy for reuse iff its probed duration matches the
    planned frame range. A process killed mid-write leaves a short/partial mp4
    that fails this check and gets re-rendered."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    expected = (end - start + 1) / fps
    actual = probe_video_duration_sec(path)
    if actual is None:
        return False
    return abs(actual - expected) <= tolerance_sec


def _render_tmp_children() -> set[str]:
    try:
        return {c.name for c in _render_tmp_dir().iterdir()}
    except OSError:
        return set()


def _clear_render_tmp_entries(names: set[str]) -> None:
    """Delete only the named top-level entries under ``.render_tmp``.

    bug (found investigating a 404 mid-segment-2 render for narration.wav
    inside a bundle dir under ``.render_tmp``): the previous version wiped
    the ENTIRE ``.render_tmp`` tree right after each segment's
    ``npx remotion render`` process exited. Node's dev/bundler server does
    not necessarily finish tearing down synchronously with the parent CLI
    process — a lingering async cleanup (or a bundle-output directory the
    NEXT segment's process is still mid-setup on) can still be touching
    that tree. Deleting everything unconditionally raced with that.

    Callers now only pass the names snapshotted *before* the segment that
    just finished started — i.e. leftovers from an EARLIER segment that has
    had a full extra segment's worth of time to finish any async cleanup.
    Whatever the just-finished segment itself created is deliberately left
    alone for one more cycle."""
    d = _render_tmp_dir()
    for name in names:
        child = d / name
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            elif child.exists():
                child.unlink(missing_ok=True)
        except OSError:
            pass


def _concat_segments(segment_paths: list[Path], output_path: Path) -> None:
    """Losslessly join VIDEO-ONLY segment mp4s (stream copy, no re-encode)
    via the ffmpeg concat demuxer. Segments are rendered ``--muted``; video
    PTS runs continuously across keyframe-aligned ``--frames`` chunks, and
    with no per-segment audio there is nothing left to drift (bug-454)."""
    list_path = output_path.with_name(f"{output_path.stem}.concat.txt")
    lines = [f"file '{p.resolve().as_posix()}'" for p in segment_paths]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_path),
                "-c", "copy",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RemotionSubprocessError(
                f"ffmpeg concat of {len(segment_paths)} segments failed (code "
                f"{result.returncode}): {result.stderr[-2000:]}"
            )
    finally:
        list_path.unlink(missing_ok=True)


def _can_use_fast_audio_track(render_props: dict) -> bool:
    """Whether the audio graph is simple enough to assemble directly with
    ffmpeg (no Chromium at all).

    ChannelVideo.tsx's ONLY audio sources are: intro.mp4's own embedded
    track, the narration <Audio>, and outro.mp4's own embedded track — no
    background-music mixing today. If music is ever configured
    (``audio.music`` non-null), that assumption no longer holds and this
    MUST fall back to a real Remotion render — hardcoding a second, richer
    audio graph here would silently drift from whatever the composition
    actually plays."""
    return not (render_props.get("audio") or {}).get("music")


def _build_fast_audio_track(render_props_path: Path, audio_path: Path) -> None:
    """Assemble the full-composition audio track directly with ffmpeg — no
    Chromium. A Remotion audio-only render still evaluates the whole
    timeline frame-by-frame just to place these same pieces (~14fps
    observed, the same order as real pixel rendering, for a job that
    should take seconds).

    Four pieces, concatenated in order, exactly matching what
    ChannelVideo.tsx actually composes (verified by reading the source,
    not assumed):
      1. intro.mp4's OWN embedded audio (real content — a branding
         jingle/music bed, confirmed via ffprobe; ChannelVideo's intro
         <MediaVideo> has no ``muted`` prop, unlike scene backgrounds).
      2. silence for the medical disclaimer, when enabled.
      3. narration.wav, trimmed/padded to exactly the content span's
         duration (content starts at frame 0 of narration in every
         case, so no intro-offset shift is needed here at all).
      4. outro.mp4's OWN embedded audio, same as intro.

    Every duration used is read fresh from render_props — intro_sec/
    outro_sec are themselves probed from the real intro/outro video files
    upstream (_probe_duration_sec), never hardcoded here. Swapping either
    branding video for a different length changes this automatically."""
    props = read_json(render_props_path)
    branding = props.get("branding") or {}
    fps = float((props.get("render", {}) or {}).get("fps") or 30)
    intro_sec = float(branding.get("intro_sec") or 0.0)
    disclaimer_sec = medical_disclaimer_duration_sec(branding)
    outro_sec = float(branding.get("outro_sec") or 0.0)
    intro_frames = math.floor(intro_sec * fps + 0.5)
    disclaimer_frames = math.floor(disclaimer_sec * fps + 0.5)
    outro_frames = math.floor(outro_sec * fps + 0.5)
    total_frames = _total_render_frames_from_props(props)
    content_frames = max(
        0,
        total_frames - intro_frames - disclaimer_frames - outro_frames,
    )
    content_sec = content_frames / fps

    narration_rel = (props.get("audio") or {}).get("narration")
    if not narration_rel:
        raise RemotionSubprocessError("render_props.audio.narration is missing.")
    narration_path = repo_root() / narration_rel
    if not narration_path.exists():
        raise RemotionSubprocessError(f"Narration audio not found: {narration_path}")

    public_dir = repo_root() / "remotion" / "public"
    inputs: list[Path] = []
    if intro_frames > 0 and branding.get("intro_video_path"):
        intro_path = public_dir / branding["intro_video_path"]
        if intro_path.exists() and _has_audio_stream(intro_path):
            inputs.append(intro_path)
    inputs.append(narration_path)
    if outro_frames > 0 and branding.get("outro_video_path"):
        outro_path = public_dir / branding["outro_video_path"]
        if outro_path.exists() and _has_audio_stream(outro_path):
            inputs.append(outro_path)

    narration_index = inputs.index(narration_path)
    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    # Only the narration piece needs shaping (pad/trim to the content span's
    # exact duration); intro/outro play their own audio as-is.
    filters = []
    labels = []
    for i in range(len(inputs)):
        if i == narration_index:
            if disclaimer_frames > 0:
                filters.append(
                    "anullsrc=r=48000:cl=stereo,"
                    f"atrim=0:{disclaimer_frames / fps:.6f}[adisclaimer]"
                )
                labels.append("[adisclaimer]")
            filters.append(f"[{i}:a]apad,atrim=0:{content_sec:.6f}[a{i}]")
        else:
            filters.append(f"[{i}:a]anull[a{i}]")
        labels.append(f"[a{i}]")
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]")
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-c:a", "pcm_s16le",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RemotionSubprocessError(
            f"ffmpeg fast audio-track assembly failed (code {result.returncode}): "
            f"{result.stderr[-2000:]}"
        )


def _has_audio_stream(video_path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(video_path),
        ],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def _write_render_progress(progress_path: Path, phase: str, **fields) -> None:
    payload = _blank_progress(phase)
    payload.update(fields)
    try:
        atomic_write_json(progress_path, payload, indent=0)
    except OSError:
        pass


def _audio_track_is_valid(audio_path: Path, expected_duration_sec: float) -> bool:
    """Reusable full-composition audio track from a previous attempt."""
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        return False
    actual = probe_audio_duration_sec(audio_path)
    if actual is None:
        return False
    return abs(actual - expected_duration_sec) <= 0.15


def _mux_video_audio(video_only_path: Path, audio_path: Path, output_path: Path) -> None:
    """Mux the concatenated video-only stream with the single full-length
    audio track. Video is stream-copied (zero quality loss); audio encodes to
    AAC here and gets loudness-normalized by the existing loudnorm step."""
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video_only_path),
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RemotionSubprocessError(
            f"ffmpeg mux of video+audio failed (code {result.returncode}): "
            f"{result.stderr[-2000:]}"
        )


def _is_public_asset_404(exc: RemotionSubprocessError) -> bool:
    """Return whether Remotion failed while fetching a job-scoped public asset."""
    detail = str(exc).lower()
    return "404" in detail and ("/public/jobs/" in detail or "public/jobs/" in detail)


def _iter_public_job_refs(value) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            refs.update(_iter_public_job_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_iter_public_job_refs(child))
    elif isinstance(value, str):
        normalized = value.lstrip("/")
        if normalized.startswith("jobs/"):
            refs.add(normalized)
    return refs


def _touch_public_job_dir(job_dir: Path) -> None:
    public_job_dir = repo_root() / "remotion" / "public" / "jobs" / job_dir.name
    if public_job_dir.exists():
        public_job_dir.touch()


def _restore_public_job_assets(job_dir: Path, render_props_path: Path) -> int:
    """Re-materialize job files referenced by render props after public pruning."""
    prefix = Path("jobs") / job_dir.name
    public_root = repo_root() / "remotion" / "public"
    restored = 0
    for raw_ref in sorted(_iter_public_job_refs(read_json(render_props_path))):
        ref = Path(raw_ref)
        try:
            relative = ref.relative_to(prefix)
        except ValueError:
            continue
        source = job_dir / relative
        if not source.is_file():
            continue
        materialize_media(source, public_root / ref)
        restored += 1
    _touch_public_job_dir(job_dir)
    return restored


def _render_segments(
    render_props_path: Path,
    video_path: Path,
    *,
    stop_request_path: Path | None,
    progress_path: Path,
    render_pid_path: Path,
) -> list[Path]:
    job_dir = render_props_path.parent
    if job_dir.name == "json":
        job_dir = job_dir.parent

    props = read_json(render_props_path)
    fps = float((props.get("render", {}) or {}).get("fps") or 30)
    total_frames = _total_render_frames(render_props_path)
    segment_frames = max(1, round(_segment_seconds(render_props_path) * fps))
    plan = _segment_plan(total_frames, segment_frames)
    segment_total = len(plan)
    expected_duration_sec = total_frames / fps

    # Full-composition audio track, built ONCE (WAV: sample-exact, no AAC
    # padding). Segments render --muted; this single track gets muxed over
    # the concatenated video, so no per-segment audio exists to drift
    # (bug-454: raw-concat of per-segment AAC accumulated padding at every
    # boundary — narration ended ~1.9s late on a 15-segment render).
    audio_path = _segments_dir(job_dir) / "audio.wav"
    if not _audio_track_is_valid(audio_path, expected_duration_sec):
        _assert_render_disk_space(output_path=video_path)
        _write_render_progress(
            progress_path,
            "rendering_audio",
            segment_total=segment_total,
            segments_done=0,
        )
        if _can_use_fast_audio_track(props):
            # Narration + intro/outro's own embedded audio only (no mixed
            # background music): assemble directly with ffmpeg — no
            # Chromium. A Remotion audio-only render still evaluates the
            # whole timeline frame-by-frame just to place these same
            # pieces (~14fps observed, same order as real pixel rendering,
            # for a job that should take seconds).
            _build_fast_audio_track(render_props_path, audio_path)
        else:
            _run_with_progress(
                build_remotion_audio_command(render_props_path, audio_path),
                progress_path,
                stop_request_path=stop_request_path,
                pid_file_path=render_pid_path,
                progress_overrides={
                    "segment_index": 0,
                    "segment_total": segment_total,
                    "segments_done": 0,
                    "note": "audio track",
                },
            )
        if not _audio_track_is_valid(audio_path, expected_duration_sec):
            actual = probe_audio_duration_sec(audio_path)
            raise RemotionSubprocessError(
                f"Full-composition audio track failed verification: got "
                f"{actual}s, expected ~{expected_duration_sec:.1f}s."
            )

    segment_paths: list[Path] = []
    # .render_tmp cleanup is deferred by one full segment cycle: prev_tmp_before
    # is the snapshot taken before the PREVIOUS segment ran, so by the time we
    # delete it (after the CURRENT segment has also fully finished) an entire
    # extra segment's wall-clock time has passed since anything in it was
    # created — ample margin for Node's bundler/dev-server to finish any async
    # teardown, never racing with what a still-settling process just made.
    prev_tmp_before: set[str] | None = None
    for index, (start, end) in enumerate(plan, start=1):
        # Public-job pruning uses directory mtime. Keep this active long render
        # newer than completed jobs created concurrently by Shorts batches.
        _touch_public_job_dir(job_dir)
        if stop_request_path is not None and stop_request_path.exists():
            raise RuntimeError("Stop requested by operator.")
        seg_path = _segment_path(job_dir, index)
        if _segment_is_valid(seg_path, start=start, end=end, fps=fps):
            segment_paths.append(seg_path)
            _write_render_progress(
                progress_path,
                "rendering",
                segment_index=index,
                segment_total=segment_total,
                segments_done=index,
                note="reused from previous attempt",
            )
            _assert_render_disk_space(output_path=video_path)
            continue
        _assert_render_disk_space(output_path=video_path)
        tmp_before = _render_tmp_children()
        commands = build_remotion_commands(
            render_props_path,
            seg_path,
            frame_range=(start, end),
            muted=True,
        )
        for seg_attempt in range(2):
            try:
                _run_with_progress(
                    commands.video,
                    progress_path,
                    stop_request_path=stop_request_path,
                    pid_file_path=render_pid_path,
                    progress_overrides={
                        "segment_index": index,
                        "segment_total": segment_total,
                        "segments_done": index - 1,
                    },
                )
                break
            except RemotionSubprocessError as exc:
                # The bundle's public dir is a SYMLINK to remotion/public
                # (Remotion CLI: symlinkPublicDir for ephemeral bundles), and
                # assets are fetched lazily DURING the render — so anything
                # that touches remotion/public/jobs/<job>/ mid-render (a
                # concurrent asset re-sync, another agent session) 404s the
                # segment at a random frame (bug-451: narration.wav 404 at
                # frame 1408). The asset is usually back by the time we
                # retry; one retry converts a lost render into a lost couple
                # of minutes.
                if seg_attempt == 0 and _is_public_asset_404(exc):
                    restored = _restore_public_job_assets(job_dir, render_props_path)
                    print(
                        f"[render] segment {index}: asset 404 mid-render "
                        f"(public job pruned or mutated); restored {restored} "
                        "referenced files — retrying once",
                        flush=True,
                    )
                    continue
                raise
        if not _segment_is_valid(seg_path, start=start, end=end, fps=fps):
            expected_sec = (end - start + 1) / fps
            raise RemotionSubprocessError(
                f"Segment {index}/{segment_total} ({seg_path.name}) failed "
                f"verification after render (expected ~{expected_sec:.1f}s)."
            )
        segment_paths.append(seg_path)
        # User-requested: reclaim disk without waiting for the whole render to
        # finish — but only entries at least one full segment cycle old.
        if prev_tmp_before is not None:
            _clear_render_tmp_entries(prev_tmp_before)
        prev_tmp_before = tmp_before

    if prev_tmp_before is not None:
        _clear_render_tmp_entries(prev_tmp_before)

    _write_render_progress(
        progress_path,
        "concatenating",
        segment_total=segment_total,
        segments_done=segment_total,
    )
    _assert_render_disk_space(output_path=video_path)
    video_only_path = _segments_dir(job_dir) / "video_only.mp4"
    try:
        _concat_segments(segment_paths, video_only_path)
        _mux_video_audio(video_only_path, audio_path, video_path)
    finally:
        video_only_path.unlink(missing_ok=True)
    # Segments + audio track are NOT deleted here — the caller only deletes
    # them once the muxed output has passed final duration validation.
    # Deleting right after concat (the original design) destroyed the only
    # resumable checkpoints in exactly the case that matters: when the final
    # artifact turns out to be wrong and needs re-diagnosis or re-assembly.
    return [*segment_paths, audio_path]


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
    progress_path = job_dir / "json" / "render_progress.json"
    render_pid_path = job_dir / "json" / ".render.pid"

    # Resume fast-path: a previous attempt may have produced a fully correct
    # video.mp4 (already loudness-normalized) that only failed the OLD,
    # buggy duration check (bug: format=duration drifts after concat even
    # though the real frame content is exact — see probe_video_duration_sec).
    # Re-validating avoids re-rendering everything for a file that was
    # already correct. Uses the FULL validation (video frames + audio stream)
    # so an artifact with desynced audio (bug-454) is never reused.
    if video_path.exists():
        try:
            validate_rendered_video_duration(
                video_path, expected_duration_sec=expected_duration_sec
            )
        except ValueError:
            pass
        else:
            _write_render_progress(progress_path, "done", percent=100)
            _finish_render_artifact(job_dir, video_path, notify_telegram=notify_telegram)
            return

    _assert_render_disk_space(output_path=video_path)  # fail fast, not ENOSPC mid-render

    fps = float((read_json(render_props_path).get("render", {}) or {}).get("fps") or 30)
    total_frames = _total_render_frames(render_props_path)
    segment_frames = max(1, round(_segment_seconds(render_props_path) * fps))
    # A video shorter than one segment renders on the plain single-shot path
    # unchanged — segmentation only kicks in where it can actually help.
    use_segments = _segmented_render_enabled(render_props_path) and total_frames > segment_frames

    segment_paths: list[Path] | None = None
    if use_segments:
        segment_paths = _render_segments(
            render_props_path,
            video_path,
            stop_request_path=stop_request_path,
            progress_path=progress_path,
            render_pid_path=render_pid_path,
        )
    else:
        commands = build_remotion_commands(render_props_path, video_path)
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
        try:
            atomic_write_json(
                progress_path,
                dict(_blank_progress("normalizing_audio"), percent=99),
                indent=0,
            )
        except OSError:
            pass
        _normalize_video_audio(
            video_path,
            integrated_lufs=loudness["integrated_lufs"],
            true_peak_dbtp=loudness["true_peak_dbtp"],
            lra=loudness["lra"],
        )
    validate_rendered_video_duration(video_path, expected_duration_sec=expected_duration_sec)
    # Validation passed: segments are no longer needed for resume/diagnosis.
    # Deleting only NOW (not right after concat) means a validation failure
    # leaves the checkpoints intact for re-diagnosis or a cheap re-concat.
    if segment_paths is not None:
        for p in segment_paths:
            p.unlink(missing_ok=True)
    _write_render_progress(progress_path, "done", percent=100)
    _finish_render_artifact(job_dir, video_path, notify_telegram=notify_telegram)


def _finish_render_artifact(job_dir: Path, video_path: Path, *, notify_telegram: bool) -> None:
    """Shared tail for both the fast-path (reused existing video.mp4) and the
    freshly-rendered path: verify the ChatGPT thumbnail, mark stages
    completed, notify."""
    # ------------------------------------------------------------------
    # Thumbnail step: the ChatGPT-generated thumbnail (auto_thumbnail_image_stage)
    # is now the ONLY thumbnail source. The legacy Remotion-still fallback was
    # removed 2026-06-30 — if the ChatGPT thumbnail is missing we stop so it can be
    # regenerated, rather than silently shipping a lower-quality fallback.
    # ------------------------------------------------------------------
    thumb_dir = job_dir / "outputs"
    chatgpt_thumb = thumb_dir / "thumbnail_1.jpg"
    # Shorts have NO cover deliverable: the Shorts pipeline intentionally removed
    # its thumbnail stage, and YouTube Shorts use an auto frame, not a custom
    # thumbnail. The mandatory-thumbnail gate is a long-form requirement (the
    # legacy Remotion still-fallback was removed 2026-06-30); requiring it for a
    # Short blocks a fully-rendered video (bug-479). Detect a Short the same way
    # _is_short_job_dir does (a short_dir is jobs/<job>/shorts/<short_id>).
    is_short = (
        job_dir.parent.name == "shorts"
        or (job_dir / "json" / "short_render_props.json").exists()
        or (job_dir / "short_render_props.json").exists()
    )
    if not is_short and not chatgpt_thumb.exists():
        raise RuntimeError(
            f"ChatGPT thumbnail missing: {chatgpt_thumb}. Generate it "
            "(auto_thumbnail_image_stage) before rendering — the Remotion thumbnail "
            "fallback has been removed."
        )
    # Keep thumbnail.jpg alias in place for Telegram / operator UI (when one exists).
    if chatgpt_thumb.exists() and not (thumb_dir / "thumbnail.jpg").exists():
        shutil.copy2(chatgpt_thumb, thumb_dir / "thumbnail.jpg")

    _mark_render_stage_completed(job_dir)
    if notify_telegram:
        _notify_render_done(job_dir)
