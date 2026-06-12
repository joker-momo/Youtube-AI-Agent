from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
from typing import Callable

from video_agent.contracts import EVENT_LOG, repo_root
from video_agent.orchestrator.job_state import load_job, save_job
from video_agent.orchestrator.orchestrator import _now
from video_agent.runtime.providers import AUDIO_SUBPROCESS_ENV
from video_agent.utils.logging import EventLogger

__all__ = [
    "StageInputMissingError",
    "_resolve_idea_path",
    "_resolve_artifact",
    "_run_blocking_with_timeout",
    "_start_stage",
    "_complete_stage",
    "IDEA_FILE",
    "_IDEA_FILE_LEGACY",
    "_AUDIO_SUBPROCESS_ENV",
]

logger = logging.getLogger("video_agent.orchestrator.stages")

IDEA_FILE = "json/idea.json"
_IDEA_FILE_LEGACY = "idea.json"

_AUDIO_SUBPROCESS_ENV = AUDIO_SUBPROCESS_ENV


class StageInputMissingError(Exception):
    pass


def _resolve_idea_path(job_dir: Path) -> Path:
    """Resolve idea.json with fallback for legacy (root) layout."""
    return _resolve_artifact(job_dir, IDEA_FILE, _IDEA_FILE_LEGACY)


def _resolve_artifact(job_dir: Path, new_rel: str, legacy_rel: str | None = None) -> Path:
    """Resolve a job artifact with fallback for legacy (root) layout.

    Checks the new subdirectory path first (e.g. ``json/script.json``).
    If not found, falls back to the legacy root-level path (``script.json``).
    """
    new_path = job_dir / new_rel
    if new_path.exists():
        return new_path
    if legacy_rel is None:
        # Derive legacy name from basename of new path
        legacy_rel = Path(new_rel).name
    legacy_path = job_dir / legacy_rel
    if legacy_path.exists():
        return legacy_path
    # Neither exists. Fall back to new path only if json/ exists, else legacy.
    if (job_dir / "json").exists():
        return new_path
    return legacy_path


def _run_blocking_with_timeout(
    label: str,
    timeout_sec: int,
    fn: Callable,
    *args,
    **kwargs,
):
    """Run blocking work in a helper thread with a hard timeout."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError as exc:
            raise RuntimeError(
                f"{label} timed out after {timeout_sec}s. "
                "Please restart worker and resume this stage."
            ) from exc


def _start_stage(job_dir: Path, stage_name: str) -> None:
    """Mark ``stage_name`` as in_progress with a real ``started_at`` timestamp.

    The pipeline used to transition stages directly from ``pending`` to
    ``completed`` inside ``_complete_stage`` with both timestamps set to
    ``now``, which reported every stage as taking 0 seconds. Stage runners
    that wrap real work (script generation, scene shards, render, etc.)
    should call ``_start_stage`` at the top of their work so the dashboard
    shows the real elapsed time once the stage finishes.

    Idempotent: only sets ``started_at`` if it has not been set yet, so a
    re-run after a transient failure does not overwrite the original
    timestamp.
    """
    state = load_job(job_dir)
    stage = state.stage(stage_name)
    ts = _now()
    if stage.status not in {"completed", "skipped"} and stage.started_at is None:
        stage.started_at = ts
    if stage.status == "pending":
        stage.status = "in_progress"
    state.updated_at = ts
    save_job(job_dir, state)


def _complete_stage(job_dir: Path, stage_name: str, output: Path) -> None:
    state = load_job(job_dir)
    ts = _now()
    stage = state.stage(stage_name)
    if stage.started_at is None:
        # Stage was never explicitly started. Best-effort guess: use the
        # most recent completed_at of any earlier stage in the list. The
        # pipeline runs stages sequentially, so the previous stage's end
        # is a reasonable proxy for this stage's start. Falls back to
        # ``ts`` (duration = 0) only when no earlier stage carries a
        # timestamp at all (e.g. very first stage in a brand-new job).
        previous_end: str | None = None
        for earlier in state.stages:
            if earlier.name == stage_name:
                break
            if earlier.completed_at:
                previous_end = earlier.completed_at
        stage.started_at = previous_end or ts
    stage.status = "completed"
    stage.completed_at = ts
    next_pending = next((s for s in state.stages if s.status == "pending"), None)
    if next_pending is not None:
        state.current_stage = next_pending.name
    state.updated_at = ts
    save_job(job_dir, state)

    _logger = EventLogger(job_dir / EVENT_LOG)
    _logger.log(
        "STAGE_COMPLETED",
        {
            "job_id": state.job_id,
            "stage": stage_name,
            "output": str(output.relative_to(job_dir)),
        },
    )
    if next_pending is None:
        _logger.log("JOB_COMPLETED", {"job_id": state.job_id})
