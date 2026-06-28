"""Long-form ``visual_schedule`` pipeline stage (Phase 2).

Runs after ``whisper_timestamps`` (when scene durations are frame-accurate). Reads
``scenes.json`` + ``visual_spans.json`` and compiles
``compiled_asset_schedule.json`` (schema v2, the renderer's single source of truth
for the background layer) via the pure ``video_agent.visual.compile_asset_schedule``.

Writing the artifact is always safe: the renderer only consumes it once it is
injected into ``render_props.json`` (gated separately by the long-form
``visual.span_planning.mode``), so a report-only job keeps rendering the legacy
per-scene background. Independent of ``video_agent.shorts``.
"""

from __future__ import annotations

from pathlib import Path

from video_agent.contracts import ARTIFACT_SCENES
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    _start_stage,
    resolve_stage_fps,
)
from video_agent.storage.atomic import atomic_write_json
from video_agent.utils.json_io import read_json
from video_agent.visual import compile_asset_schedule

__all__ = ["run_visual_schedule_stage"]

_STAGE = "visual_schedule"
_OUTPUT_NAME = "compiled_asset_schedule.json"
_SPANS_NAME = "visual_spans.json"
# Optional per-span acquired clips (scene_id -> {path, duration_sec}), produced by
# the live acquisition step (video_agent.visual.acquire_span_source_clips). When
# present a span renders one continuous acquired clip; absent → Phase-2 behaviour
# (first member scene's prepared clip).
_SOURCE_CLIPS_NAME = "span_source_clips.json"


def run_visual_schedule_stage(job_dir: Path, channel_path: Path | None = None) -> Path:
    """Compile the schema-v2 asset schedule and write ``compiled_asset_schedule.json``.

    Returns the output path. Raises :class:`StageInputMissingError` if run out of
    order or if ``scenes.json`` / ``visual_spans.json`` are missing.
    """
    state = load_job(job_dir)
    if state.current_stage != _STAGE:
        raise StageInputMissingError(
            f"Cannot run {_STAGE} stage from current_stage={state.current_stage!r}"
        )

    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES, "scenes.json")
    if not scenes_path.exists():
        raise StageInputMissingError(
            f"{_STAGE}: scenes.json not found (looked at {scenes_path})"
        )
    spans_path = scenes_path.parent / _SPANS_NAME
    if not spans_path.exists():
        raise StageInputMissingError(
            f"{_STAGE}: visual_spans.json not found (looked at {spans_path}); "
            "run the visual_spans stage first"
        )

    _start_stage(job_dir, _STAGE)
    scene_doc = read_json(scenes_path) or {}
    visual_spans = read_json(spans_path) or {}
    fps = resolve_stage_fps(channel_path)

    # Use real per-span acquired clips when the live acquisition step produced them.
    source_clips_path = scenes_path.parent / _SOURCE_CLIPS_NAME
    source_clips = read_json(source_clips_path) if source_clips_path.exists() else None

    schedule = compile_asset_schedule(
        scene_doc=scene_doc,
        visual_spans=visual_spans,
        fps=fps,
        timing_source="tts_final",
        source_clips=source_clips or None,
    )

    out_path = scenes_path.parent / _OUTPUT_NAME
    atomic_write_json(out_path, schedule)
    _complete_stage(job_dir, _STAGE, out_path)
    return out_path
