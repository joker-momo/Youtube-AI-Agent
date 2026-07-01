"""Long-form ``render_continuity_qa`` stage (Phase 6).

After render, verifies that wherever the compiled schedule mounts ONE continuous
clip across several scenes, the rendered video does not reset / black-out at a
span-internal scene boundary (a reset shows up as a black frame). Real cuts at
track boundaries are expected and are not flagged.

``analyze_span_continuity`` is pure (operates on a per-frame luma sequence) and
unit-tested. The stage decodes only the boundary frames via ffmpeg (best-effort:
if the schedule, the rendered video, or ffmpeg is unavailable it reports PASS with
``skipped``). Reference: ``video_agent.shorts.render_continuity_qa`` (not imported).
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

from video_agent.contracts import ARTIFACT_SCENES, ARTIFACT_VIDEO
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    _start_stage,
    dag_mode,
)
from video_agent.storage.atomic import atomic_write_json
from video_agent.utils.json_io import read_json

__all__ = ["analyze_span_continuity", "run_render_continuity_qa_stage"]

_STAGE = "render_continuity_qa"
_OUTPUT_NAME = "render_continuity_qa.json"
_BLACK_THRESHOLD = 8.0  # mean luma below this == effectively black / reset


def _internal_boundaries(schedule: dict[str, Any]) -> list[int]:
    """Span-internal scene-boundary frames: a scene boundary strictly inside a
    single background track (i.e. not a track/cut boundary)."""
    total = int(schedule.get("total_duration_in_frames") or 0)
    track_ranges = [
        (int(t["from_frame"]), int(t["end_frame_exclusive"]))
        for t in schedule.get("tracks") or []
        if t.get("track_type") == "background_media"
    ]
    out: list[int] = []
    for boundary in schedule.get("scene_boundaries") or []:
        frame = int(boundary.get("end_frame_exclusive") or 0)
        if frame <= 0 or frame >= total:
            continue
        if any(tf < frame < te for tf, te in track_ranges):
            out.append(frame)
    return sorted(set(out))


def analyze_span_continuity(
    luma: Sequence[float],
    schedule: dict[str, Any],
    *,
    black_threshold: float = _BLACK_THRESHOLD,
) -> dict[str, Any]:
    """Flag span-internal boundaries where the rendered frame goes black (reset)."""
    boundaries = _internal_boundaries(schedule or {})
    flagged: list[int] = []
    for frame in boundaries:
        before = luma[frame - 1] if 0 <= frame - 1 < len(luma) else None
        at = luma[frame] if 0 <= frame < len(luma) else None
        if (before is not None and before < black_threshold) or (
            at is not None and at < black_threshold
        ):
            flagged.append(frame)
    return {
        "verdict": "FAIL" if flagged else "PASS",
        "checked_boundaries": boundaries,
        "flagged": flagged,
        "black_threshold": black_threshold,
    }


def _intro_offset_frames(job_dir: Path) -> int:
    """Frames the rendered scene layer is shifted by (long-form intro).

    ``ChannelVideo.tsx`` mounts the scene layer at ``introFrames`` while the
    schedule ``scene_boundaries`` are scene-layer 0-based, so the QA must sample
    the rendered video at ``boundary + introFrames``. Read from the already-written
    ``render_props.json`` (branding.intro_sec * render.fps, JS ``Math.round``).
    Returns 0 when render_props / branding is absent (e.g. shorts, intro disabled).
    """
    for candidate in (job_dir / "json" / "render_props.json", job_dir / "render_props.json"):
        if not candidate.exists():
            continue
        try:
            rp = read_json(candidate) or {}
        except Exception:
            return 0
        fps = float(((rp.get("render") or {}).get("fps")) or 0.0)
        intro_sec = float(((rp.get("branding") or {}).get("intro_sec")) or 0.0)
        if fps <= 0 or intro_sec <= 0:
            return 0
        return math.floor(intro_sec * fps + 0.5)
    return 0


def _sample_luma(
    video: Path, frames: list[int], total: int, *, frame_offset: int = 0
) -> list[float] | None:
    """Mean luma for the requested SCENE-LAYER frame indices (others default to a
    non-black sentinel). The rendered video is sampled at ``index + frame_offset``
    (the intro shift) but values are stored at the unshifted scene-layer index, so
    :func:`analyze_span_continuity` stays pure. Returns None when ffmpeg/PIL is
    unavailable."""
    if not frames or not shutil.which("ffmpeg"):
        return None
    try:
        from PIL import Image  # optional dep
    except Exception:
        return None
    wanted = sorted(set(frames))
    select = "+".join(f"eq(n\\,{f + frame_offset})" for f in wanted)
    tmp = video.parent / "_continuity_tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-vf", f"select='{select}'",
             "-vsync", "0", str(tmp / "%05d.png")],
            check=True, capture_output=True, timeout=300,
        )
        pngs = sorted(tmp.glob("*.png"))
        # The png -> frame mapping below is POSITIONAL: png[i] is assumed to be
        # wanted[i]. That holds only when ffmpeg emitted exactly one frame per
        # requested index (CFR render + every index in range). On any mismatch (a
        # dropped/duplicated frame on VFR input, or an out-of-range request) the
        # indices shift and every subsequent boundary would be read off the wrong
        # frame -> silent false continuity verdict. Fail safe: skip the QA rather
        # than emit a wrong verdict.
        if len(pngs) != len(wanted):
            return None
        luma = [128.0] * max(total, (max(wanted) + 1))
        for idx, png in enumerate(pngs):
            with Image.open(png) as im:
                gray = im.convert("L")
                hist = gray.histogram()
                pixels = sum(hist) or 1
                mean = sum(i * c for i, c in enumerate(hist)) / pixels
            luma[wanted[idx]] = mean
        return luma
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _find_rendered_video(job_dir: Path) -> Path | None:
    # ARTIFACT_VIDEO ("outputs/video.mp4") is where the renderer actually writes
    # the final file (same source of truth render_review.py uses). This used to
    # check only the legacy root job_dir/video.mp4 and a non-recursive top-level
    # glob, missing outputs/video.mp4 entirely — the stage silently PASS-skipped
    # a completed render because it never found the video that existed.
    candidate = _resolve_artifact(job_dir, ARTIFACT_VIDEO, "video.mp4")
    if candidate.exists():
        return candidate
    mp4s = [p for p in job_dir.glob("*.mp4") if p.is_file()]
    return mp4s[0] if mp4s else None


def _qa_schedule(job_dir: Path, sched_path: Path) -> dict | None:
    """The asset schedule the renderer actually consumed.

    Enforced renders recompile the schedule from the FINAL post-TTS scene
    durations and embed it in ``render_props.json`` under ``visual_schedule``
    (see ``pipeline._attach_enforced_visual_schedule``). The on-disk
    ``compiled_asset_schedule.json`` is the ``visual_schedule`` stage output,
    compiled *before* render-time duration sync, so its ``scene_boundaries`` are
    stale by render time. Sampling luma at stale boundaries inspects the wrong
    frames -> false continuity verdicts. Prefer the embedded schedule so QA reads
    the same boundaries the renderer drew; fall back to the on-disk artifact
    (report_only / legacy jobs / older runs without the embedded copy)."""
    for candidate in (job_dir / "json" / "render_props.json", job_dir / "render_props.json"):
        if not candidate.exists():
            continue
        try:
            rp = read_json(candidate) or {}
        except Exception:
            break
        sched = rp.get("visual_schedule")
        if isinstance(sched, dict) and sched.get("scene_boundaries"):
            return sched
        break
    if sched_path.exists():
        try:
            return read_json(sched_path) or None
        except Exception:
            return None
    return None


def run_render_continuity_qa_stage(job_dir: Path, channel_path: Path | None = None) -> Path:
    """Verify span continuity in the rendered video; write the QA artifact."""
    state = load_job(job_dir)
    if not dag_mode() and state.current_stage != _STAGE:
        raise StageInputMissingError(
            f"Cannot run {_STAGE} stage from current_stage={state.current_stage!r}"
        )

    _start_stage(job_dir, _STAGE)
    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES, "scenes.json")
    sched_path = scenes_path.parent / "compiled_asset_schedule.json"
    video = _find_rendered_video(job_dir)
    out_path = scenes_path.parent / _OUTPUT_NAME

    schedule = _qa_schedule(job_dir, sched_path)
    if schedule is None or video is None:
        result = {"verdict": "PASS", "skipped": True,
                  "reason": "no compiled schedule or rendered video"}
        atomic_write_json(out_path, result)
        _complete_stage(job_dir, _STAGE, out_path)
        return out_path

    boundaries = _internal_boundaries(schedule)
    if not boundaries:
        result = {"verdict": "PASS", "skipped": False, "checked_boundaries": [],
                  "flagged": [], "reason": "no span-internal boundaries"}
        atomic_write_json(out_path, result)
        _complete_stage(job_dir, _STAGE, out_path)
        return out_path

    frames = sorted({f for b in boundaries for f in (b - 1, b)})
    intro_offset = _intro_offset_frames(job_dir)
    luma = _sample_luma(
        video, frames, int(schedule.get("total_duration_in_frames") or 0),
        frame_offset=intro_offset,
    )
    if luma is None:
        result = {"verdict": "PASS", "skipped": True,
                  "reason": "luma sampling unavailable (ffmpeg/PIL)"}
    else:
        result = analyze_span_continuity(luma, schedule)

    atomic_write_json(out_path, result)
    _complete_stage(job_dir, _STAGE, out_path)
    return out_path
