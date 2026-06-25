"""Compile the long-form ``CompiledAssetSchedule`` (the renderer's background layer).

Pure + deterministic: no IO, no provider calls. Emits the exact schema-v2 shape
defined in ``remotion/src/render-props.ts`` (``CompiledAssetSchedule`` —
``scene_boundaries`` + ``total_duration_in_frames`` + ``end_frame_exclusive``,
``timing_source ∈ {tts_final, scene_plan}``). One ``background_media`` track per
visual span, spanning all its member scenes so a single clip runs continuously
across scene boundaries (no remount/reset).

Frame math intentionally mirrors the renderer (``ChannelVideo.tsx``:
``Math.round(duration_sec * fps)`` per scene, cumulative). We use
``floor(x + 0.5)`` to match JS ``Math.round`` (round half up), NOT Python's
banker's rounding, so compiled boundaries are frame-identical to the per-scene
fallback render. Boundaries are 0-based relative to the scene layer; the renderer
mounts the timeline inside the intro-shifted sequence, so intro/outro length does
not enter this module.

Design reference (NOT imported): ``video_agent.shorts.asset_schedule``.
"""

from __future__ import annotations

import math
from typing import Any

from video_agent.visual.spans import GRAPHIC_LAYOUTS, _layout, _scene_id

SCHEDULE_SCHEMA_VERSION = 2
_VALID_TIMING_SOURCES = {"tts_final", "scene_plan"}


def _frames(duration_sec: Any, fps: int) -> int:
    """Frames for a scene duration, matching JS ``Math.round(duration_sec*fps)``."""
    try:
        sec = float(duration_sec or 0.0)
    except (TypeError, ValueError):
        sec = 0.0
    return math.floor(sec * fps + 0.5)


def _first_asset_ref(scene: dict[str, Any], is_graphic: bool) -> str:
    """Phase-2 asset for a span: the first member scene's clip (or graphic image)."""
    if is_graphic:
        graphic = scene.get("graphic") or {}
        ref = graphic.get("image_ref")
        if ref:
            return str(ref)
    refs = scene.get("asset_refs") or {}
    return str(refs.get("background") or "")


def _prepared_background(scene: dict[str, Any]) -> str:
    return str((scene.get("asset_refs") or {}).get("background") or "")


def _shared_continuous_source(
    member_ids: list[str],
    source_clips: dict[str, dict[str, Any]],
    fps: int,
    span_frames: int,
) -> tuple[str, float] | None:
    """Return ``(path, duration_sec)`` iff every member scene was sourced from the
    SAME full clip and that clip is long enough to cover the whole span.

    Quality-safe: a span only collapses to one continuous clip when its scenes
    genuinely share footage (so playing it across the span removes an artificial
    per-scene reset). Mixed sources / too-short clips return ``None`` → the caller
    falls back to per-scene tracks rather than reusing off-topic footage.
    """
    paths: set[str] = set()
    min_dur: float | None = None
    for sid in member_ids:
        clip = source_clips.get(sid) or {}
        path = str(clip.get("path") or "")
        if not path:
            return None
        paths.add(path)
        dur = float(clip.get("duration_sec") or 0.0)
        min_dur = dur if min_dur is None else min(min_dur, dur)
    if len(paths) != 1 or min_dur is None:
        return None
    if math.floor(min_dur * fps + 0.5) < span_frames:
        return None
    return next(iter(paths)), min_dur


def compile_asset_schedule(
    *,
    scene_doc: dict[str, Any],
    visual_spans: dict[str, Any],
    fps: int,
    timing_source: str = "tts_final",
    source_clips: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile a schema-v2 ``CompiledAssetSchedule`` from scenes + visual spans.

    ``scene_doc`` supplies per-scene ``duration_sec`` (frame-accurate after TTS)
    and ``asset_refs.background``. ``visual_spans`` is the document from
    :func:`video_agent.visual.build_visual_spans`. Each ``graphic_image`` span
    becomes one image track.

    Background tracks for ``continuous_clip`` spans:

    * ``source_clips`` is ``None`` (Phase-2 default) → one track per span using the
      first member scene's prepared clip.
    * ``source_clips`` provided (``scene_id -> {path, duration_sec}`` of the full
      original asset) → if all members share one long-enough source clip, emit a
      single continuous track over the whole span; otherwise fail closed to one
      track per member scene (legacy per-scene background, no off-topic reuse).

    The track set always tiles the scene timeline exactly.
    """
    scenes = list((scene_doc or {}).get("scenes") or [])
    fps = int(fps)
    timing = timing_source if timing_source in _VALID_TIMING_SOURCES else "tts_final"

    boundaries: list[dict[str, Any]] = []
    boundary_by_id: dict[str, dict[str, Any]] = {}
    scene_by_id: dict[str, dict[str, Any]] = {}
    cursor = 0
    for index, scene in enumerate(scenes):
        sid = _scene_id(scene, index)
        dur = _frames(scene.get("duration_sec"), fps)
        boundary = {
            "scene_id": sid,
            "from_frame": cursor,
            "duration_in_frames": dur,
            "end_frame_exclusive": cursor + dur,
            "is_graphic": _layout(scene) in GRAPHIC_LAYOUTS,
        }
        boundaries.append(boundary)
        boundary_by_id[sid] = boundary
        scene_by_id[sid] = scene
        cursor += dur
    total_frames = cursor

    tracks: list[dict[str, Any]] = []
    track_seq = 0

    def _emit(
        *, span_id: str, scene_ids: list[str], asset_ref: str,
        from_frame: int, end_frame: int, is_graphic: bool,
        source_duration_sec: float | None = None,
    ) -> None:
        nonlocal track_seq
        track_seq += 1
        track: dict[str, Any] = {
            "track_id": f"vt{track_seq:02d}",
            "track_type": "background_media",
            "visual_span_id": span_id,
            "scene_ids": scene_ids,
            "asset_ref": asset_ref,
            "render_media_kind": "image" if is_graphic else "video",
            "source_media_kind": "native_image" if is_graphic else "native_video",
            "from_frame": from_frame,
            "duration_in_frames": end_frame - from_frame,
            "end_frame_exclusive": end_frame,
            "trim_before_in_frames": 0,
            "trim_timebase_fps": fps,
            "playback_rate": 1.0,
            "loop_policy": "forbid",
        }
        if source_duration_sec is not None:
            track["source_duration_sec"] = source_duration_sec
        tracks.append(track)

    for k, span in enumerate(list((visual_spans or {}).get("spans") or [])):
        member_ids = [s for s in (span.get("scene_ids") or []) if s in boundary_by_id]
        if not member_ids:
            continue
        span_id = str(span.get("id") or f"vs{k + 1:02d}")
        first = boundary_by_id[member_ids[0]]
        last = boundary_by_id[member_ids[-1]]
        from_frame = int(first["from_frame"])
        end_frame = int(last["end_frame_exclusive"])

        if span.get("planned_mode") == "graphic_image":
            _emit(
                span_id=span_id, scene_ids=member_ids,
                asset_ref=_first_asset_ref(scene_by_id[member_ids[0]], True),
                from_frame=from_frame, end_frame=end_frame, is_graphic=True,
            )
            continue

        if source_clips is not None:
            shared = _shared_continuous_source(member_ids, source_clips, fps, end_frame - from_frame)
            if shared is not None:
                path, dur = shared
                _emit(
                    span_id=span_id, scene_ids=member_ids, asset_ref=path,
                    from_frame=from_frame, end_frame=end_frame, is_graphic=False,
                    source_duration_sec=dur,
                )
            else:
                # Fail closed: one track per member scene (legacy per-scene clip).
                for sid in member_ids:
                    b = boundary_by_id[sid]
                    _emit(
                        span_id=span_id, scene_ids=[sid],
                        asset_ref=_prepared_background(scene_by_id[sid]),
                        from_frame=int(b["from_frame"]), end_frame=int(b["end_frame_exclusive"]),
                        is_graphic=False,
                    )
            continue

        # Phase-2 default: single track over the span, first member's prepared clip.
        _emit(
            span_id=span_id, scene_ids=member_ids,
            asset_ref=_prepared_background(scene_by_id[member_ids[0]]),
            from_frame=from_frame, end_frame=end_frame, is_graphic=False,
        )

    return {
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "fps": fps,
        "timing_source": timing,
        "total_duration_in_frames": total_frames,
        "scene_boundaries": boundaries,
        "tracks": tracks,
    }
