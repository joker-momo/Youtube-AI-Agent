"""Long-form ``elena_plan`` pipeline stage (Phase 5).

Runs after ``whisper_timestamps`` (frame-accurate timing). Reads scenes.json,
builds the frame-accurate Elena cue plan via the pure
``video_agent.visual.build_elena_cues`` (talking + hidden only, ≥15s spacing, no
adjacent same asset, graphic/evidence → hidden), and writes ``elena_cues.json``.

Report-only: writing the artifact never changes the render. The renderer mounts
``ChannelElenaPresenter`` only when ``elena_cues`` is injected into render_props,
which is gated separately by the long-form span-planning mode. Independent of
``video_agent.shorts``.
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
    resolve_stage_fps,
)
from video_agent.storage.atomic import atomic_write_json
from video_agent.utils.json_io import read_json, read_yaml
from video_agent.visual import build_elena_cues

__all__ = ["run_elena_plan_stage"]

_STAGE = "elena_plan"
_OUTPUT_NAME = "elena_cues.json"


def _channel_config(channel_path: Path | None) -> dict[str, Any]:
    if channel_path is None or not Path(channel_path).exists():
        return {}
    try:
        return read_yaml(Path(channel_path)) or {}
    except Exception:
        return {}


def run_elena_plan_stage(job_dir: Path, channel_path: Path | None = None) -> Path:
    """Build the Elena cue plan and write ``elena_cues.json``. Returns its path."""
    state = load_job(job_dir)
    if state.current_stage != _STAGE:
        raise StageInputMissingError(
            f"Cannot run {_STAGE} stage from current_stage={state.current_stage!r}"
        )

    scenes_path = _resolve_artifact(job_dir, ARTIFACT_SCENES, "scenes.json")
    if not scenes_path.exists():
        raise StageInputMissingError(f"{_STAGE}: scenes.json not found (looked at {scenes_path})")

    _start_stage(job_dir, _STAGE)
    scene_doc = read_json(scenes_path) or {}
    cues = build_elena_cues(
        scene_doc,
        _channel_config(channel_path),
        resolve_stage_fps(channel_path),
        job_id=state.job_id,
        mode="report_only",
    )

    out_path = scenes_path.parent / _OUTPUT_NAME
    atomic_write_json(out_path, cues)
    _complete_stage(job_dir, _STAGE, out_path)
    return out_path
