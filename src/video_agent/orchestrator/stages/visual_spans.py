"""Long-form ``visual_spans`` pipeline stage (Phase 1, report-only).

Runs after ``scenes_qa``. Reads ``json/scenes.json``, groups contiguous editorial
scenes into visual spans via the pure ``video_agent.visual`` core, and writes
``json/visual_spans.json``. It never touches assets or render artifacts: Phase 1
is a planning sidecar only. The mode is forced to ``report_only`` here regardless
of channel config, so grouping cannot affect the render path before the Gate-1
manual quality review. Independent of ``video_agent.shorts``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.contracts import ARTIFACT_SCENES
from video_agent.orchestrator.job_state import load_job
from video_agent.orchestrator.stages._shared import (
    StageInputMissingError,
    _complete_stage,
    _resolve_artifact,
    _start_stage,
)
from video_agent.storage.atomic import atomic_write_json
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.visual import assign_span_ids_to_scenes, build_visual_spans

__all__ = ["run_visual_spans_stage"]

_STAGE = "visual_spans"
_OUTPUT_NAME = "visual_spans.json"


def _load_channel_config(channel_path: Path | None) -> dict[str, Any]:
    """Read the channel config for ``visual.span_planning`` knobs (best-effort)."""
    if channel_path is None:
        return {}
    path = Path(channel_path)
    if not path.exists():
        return {}
    try:
        return read_yaml(path) or {}
    except Exception:
        return {}


def run_visual_spans_stage(job_dir: Path, channel_path: Path | None = None) -> Path:
    """Group scenes into visual spans and write ``json/visual_spans.json``.

    Report-only: forces ``mode="report_only"`` so Phase 1 never alters assets or
    render. Returns the output path. Raises :class:`StageInputMissingError` if the
    stage is run out of order or ``scenes.json`` is absent.
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

    _start_stage(job_dir, _STAGE)
    scene_doc = read_json(scenes_path) or {}
    channel_config = _load_channel_config(channel_path)

    visual_spans = build_visual_spans(
        scene_doc,
        channel_config,
        mode="report_only",
        job_id=state.job_id,
    )

    # Write next to scenes.json so we don't flip a legacy (root-layout) job into
    # the json/ layout mid-pipeline (which would change downstream output paths).
    out_path = scenes_path.parent / _OUTPUT_NAME
    atomic_write_json(out_path, visual_spans)

    # Reflect the authoritative grouping back onto scenes.json by adding only the
    # visual_span_id field (all other scene fields are left untouched), so later
    # stages and the renderer can resolve a scene's span without re-grouping.
    assign_span_ids_to_scenes(scene_doc, visual_spans)
    atomic_write_json(scenes_path, scene_doc)

    _complete_stage(job_dir, _STAGE, out_path)
    return out_path
