"""Post-render production continuity QA (spec §34).

``asset_schedule.validate_compiled_asset_schedule`` proves the *plan* is sound
BEFORE render. This module proves the *rendered MP4* actually matches that plan:
exact frame count, duration, resolution/fps, a present audio stream, and no
black/dropped frame at the scene-boundary cuts. Without it a Chromium / Remotion /
encoder failure can still finish with status ``rendered`` — the gap this closes
(``production_boundary_check_enabled`` previously had no consumer).

The verdict logic (:func:`evaluate_render_continuity`) is pure and unit-tested.
The heavy ffprobe/ffmpeg measurement (:func:`probe_render`, :func:`boundary_luma`)
is isolated and injected by the stage, and covered by the integration render test.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_agent.shorts.asset_schedule import compute_schedule_hash

QA_VERSION = 1
# Encoders round the final frame; allow a ±1 frame slop vs the schedule total.
FRAME_COUNT_TOLERANCE = 1
# Mean luma below this is a black/fade frame (matches visual_local_analysis).
BLACK_LUMA_THRESHOLD = 8.0
# avg_frame_rate may drift slightly from the integer target.
FPS_TOLERANCE = 0.5


@dataclass
class RenderProbe:
    """Measured properties of a rendered MP4."""

    frame_count: int
    duration_sec: float
    width: int
    height: int
    fps: float
    has_audio: bool
    decode_ok: bool
    error: str | None = None


def _cut_frames(schedule: dict[str, Any]) -> list[int]:
    """Frames to inspect for black/dropped content: the last frame of each scene
    and the first frame of the next (the ±1 window around every cut), plus the
    composition's first and last frame."""
    boundaries = schedule.get("scene_boundaries") or []
    total = int(schedule.get("total_duration_in_frames") or 0)
    frames: set[int] = set()
    if total > 0:
        frames.add(0)
        frames.add(total - 1)
    for b in boundaries:
        ef = int(b.get("end_frame_exclusive") or 0)
        if 0 < ef < total:
            frames.add(ef - 1)  # last frame of this scene
            frames.add(ef)  # first frame of the next scene
    return sorted(frames)


def _parse_resolution(render_props: dict[str, Any] | None) -> tuple[int, int] | None:
    res = str(((render_props or {}).get("render") or {}).get("resolution") or "")
    if "x" not in res:
        return None
    try:
        w, h = res.lower().split("x", 1)
        return int(w), int(h)
    except (ValueError, TypeError):
        return None


def evaluate_render_continuity(
    *,
    probe: RenderProbe,
    boundary_luma: dict[int, float | None],
    visual_schedule: dict[str, Any] | None,
    render_props: dict[str, Any] | None,
    fps: int,
    public_job_id: str = "",
) -> dict[str, Any]:
    """Pure verdict over already-measured render facts. Returns the
    ``render_continuity_qa.json`` document."""
    checks: dict[str, str] = {}
    errors: list[str] = []
    warnings: list[str] = []

    # --- decode ---
    if not probe.decode_ok:
        checks["decode_ok"] = "FAIL"
        errors.append(f"decode_failed:{probe.error or 'unknown'}")
    else:
        checks["decode_ok"] = "PASS"

    has_schedule = bool(visual_schedule)
    expected_frames = int((visual_schedule or {}).get("total_duration_in_frames") or 0)

    # --- frame count vs schedule ---
    if not has_schedule or expected_frames <= 0:
        checks["frame_count_matches_schedule"] = "N/A"
    elif abs(probe.frame_count - expected_frames) <= FRAME_COUNT_TOLERANCE:
        checks["frame_count_matches_schedule"] = "PASS"
    else:
        checks["frame_count_matches_schedule"] = "FAIL"
        errors.append(f"frame_count:{probe.frame_count}!=schedule:{expected_frames}")

    # --- duration vs schedule (derived; informational unless wildly off) ---
    if has_schedule and expected_frames > 0 and fps > 0:
        expected_dur = expected_frames / float(fps)
        if probe.decode_ok and abs(probe.duration_sec - expected_dur) > (2.0 / fps):
            checks["duration_matches_schedule"] = "WARN"
            warnings.append(f"duration:{probe.duration_sec:.3f}s!=expected:{expected_dur:.3f}s")
        else:
            checks["duration_matches_schedule"] = "PASS"
    else:
        checks["duration_matches_schedule"] = "N/A"

    # --- audio stream ---
    if probe.has_audio:
        checks["audio_stream_present"] = "PASS"
    else:
        checks["audio_stream_present"] = "FAIL"
        errors.append("no_audio_stream")

    # --- resolution / fps ---
    expected_res = _parse_resolution(render_props)
    if expected_res and probe.decode_ok:
        if (probe.width, probe.height) == expected_res:
            checks["resolution_correct"] = "PASS"
        else:
            checks["resolution_correct"] = "FAIL"
            errors.append(f"resolution:{probe.width}x{probe.height}!={expected_res[0]}x{expected_res[1]}")
    else:
        checks["resolution_correct"] = "N/A"
    if probe.decode_ok and fps > 0:
        if abs(probe.fps - fps) <= FPS_TOLERANCE:
            checks["fps_correct"] = "PASS"
        else:
            checks["fps_correct"] = "FAIL"
            errors.append(f"fps:{probe.fps}!={fps}")
    else:
        checks["fps_correct"] = "N/A"

    # --- black / dropped boundary frames ---
    black_boundary_frames: list[int] = []
    if not has_schedule:
        checks["no_black_boundary_frames"] = "N/A"
    else:
        for frame_no in _cut_frames(visual_schedule):
            luma = boundary_luma.get(frame_no)
            if luma is None or luma < BLACK_LUMA_THRESHOLD:
                black_boundary_frames.append(frame_no)
        if black_boundary_frames:
            checks["no_black_boundary_frames"] = "FAIL"
            errors.append(f"black_or_missing_boundary_frames:{black_boundary_frames[:8]}")
        else:
            checks["no_black_boundary_frames"] = "PASS"

    verdict = "FAIL" if errors else ("WARN" if warnings else "PASS")
    return {
        "qa_version": QA_VERSION,
        "verdict": verdict,
        "proof_mode": "production_boundary_check",
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "black_boundary_frames": black_boundary_frames,
        "rendered_frames": probe.frame_count,
        "expected_frames": expected_frames,
        "duration_sec": round(probe.duration_sec, 3),
        "width": probe.width,
        "height": probe.height,
        "fps": probe.fps,
        "has_audio": probe.has_audio,
        "schedule_hash": compute_schedule_hash(visual_schedule, public_job_id=public_job_id) if has_schedule else "",
    }


# --------------------------------------------------------------------------- #
# Measurement (real ffprobe/ffmpeg) — isolated, injected by the stage.
# --------------------------------------------------------------------------- #
def _run_json(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(proc.stdout or "{}")


def _num(value: Any) -> float:
    """Coerce an ffprobe field to float, tolerating its ``"N/A"`` sentinel and
    missing/None values (ffprobe emits ``"N/A"`` for fields it can't fill — e.g.
    stream-level ``duration`` on many MP4s, where the real value is at the
    container/format level)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def probe_render(video_path: Path) -> RenderProbe:
    """Decode-accurate probe of the rendered MP4 via ffprobe (counts frames)."""
    try:
        v = _run_json(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                "-show_entries",
                "stream=nb_read_frames,width,height,avg_frame_rate,duration:format=duration",
                "-of", "json", str(video_path),
            ]
        )
    except Exception as exc:  # noqa: BLE001 - probe failure is a render failure to report
        return RenderProbe(0, 0.0, 0, 0, 0.0, False, decode_ok=False, error=f"{type(exc).__name__}")

    streams = v.get("streams") or []
    s = streams[0] if streams else {}
    try:
        frame_count = int(s.get("nb_read_frames") or 0)
    except (TypeError, ValueError):
        frame_count = 0
    # Stream duration is often "N/A" on MP4 (real value is container-level); fall
    # back to the format duration. _num tolerates "N/A"/None without raising.
    duration = _num(s.get("duration")) or _num((v.get("format") or {}).get("duration"))
    rate = str(s.get("avg_frame_rate") or "0/1")
    try:
        num, den = rate.split("/", 1)
        fps = float(num) / max(1.0, float(den))
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0

    try:
        a = _run_json(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=index", "-of", "json", str(video_path)]
        )
        has_audio = bool(a.get("streams"))
    except Exception:  # noqa: BLE001
        has_audio = False

    return RenderProbe(
        frame_count=frame_count,
        duration_sec=duration,
        width=int(_num(s.get("width"))),
        height=int(_num(s.get("height"))),
        fps=fps,
        has_audio=has_audio,
        decode_ok=frame_count > 0,
        error=None if frame_count > 0 else "no_frames_decoded",
    )


def boundary_luma(video_path: Path, frame_nos: list[int], fps: int) -> dict[int, float | None]:
    """Mean luma of each requested frame; ``None`` when the frame cannot be
    extracted (treated as a black/dropped frame by the evaluator)."""
    import numpy as np
    from PIL import Image  # local import: optional dep, matches visual_local_analysis

    out: dict[int, float | None] = {}
    if fps <= 0:
        return {f: None for f in frame_nos}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for frame_no in frame_nos:
            ts = max(0.0, frame_no / float(fps))
            jpg = tmp / f"f-{frame_no}.jpg"
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{ts:.5f}", "-i", str(video_path),
                "-frames:v", "1", str(jpg),
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if not jpg.exists() or jpg.stat().st_size == 0:
                    out[frame_no] = None
                    continue
                with Image.open(jpg) as img:
                    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
                luma = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
                out[frame_no] = float(np.mean(luma))
            except Exception:  # noqa: BLE001
                out[frame_no] = None
    return out


def build_render_continuity_qa(
    *,
    video_path: Path,
    visual_schedule: dict[str, Any] | None,
    render_props: dict[str, Any] | None,
    fps: int,
    public_job_id: str = "",
    probe_fn=probe_render,
    luma_fn=boundary_luma,
) -> dict[str, Any]:
    """Measure the rendered MP4 and evaluate continuity. Measurement functions
    are injectable for testing."""
    probe = probe_fn(Path(video_path))
    frames = _cut_frames(visual_schedule) if visual_schedule else []
    luma = luma_fn(Path(video_path), frames, int(fps)) if frames and probe.decode_ok else {}
    qa = evaluate_render_continuity(
        probe=probe,
        boundary_luma=luma,
        visual_schedule=visual_schedule,
        render_props=render_props,
        fps=int(fps),
        public_job_id=public_job_id,
    )
    qa["video_path"] = str(video_path)
    return qa
