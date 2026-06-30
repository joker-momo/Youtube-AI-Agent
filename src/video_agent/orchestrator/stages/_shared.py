from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from video_agent.contracts import EVENT_LOG
from video_agent.orchestrator.job_state import load_job, save_job
from video_agent.orchestrator.orchestrator import _now
from video_agent.runtime.providers import AUDIO_SUBPROCESS_ENV
from video_agent.utils.logging import EventLogger

__all__ = [
    "StageInputMissingError",
    "_resolve_idea_path",
    "_resolve_artifact",
    "_run_blocking_with_timeout",
    "resolve_stage_fps",
    "_start_stage",
    "_complete_stage",
    "IDEA_FILE",
    "_IDEA_FILE_LEGACY",
    "_AUDIO_SUBPROCESS_ENV",
    "SCRIPT_PROMPT_PATH",
    "SCRIPT_RAW_PATH",
    "SCENES_PROMPT_PATH",
    "SCENES_RAW_PATH",
    "SCENES_PLAN_PATH",
    "SCENES_BATCHES_DIR",
    "SCENES_QA_BATCHES_DIR",
    "SEO_PROMPT_PATH",
    "SEO_RAW_PATH",
    "SCRIPT_QA_RAW_PATH",
    "SCENES_QA_RAW_PATH",
    "SEO_QA_RAW_PATH",
    "dag_mode",
    "set_dag_mode",
    "require_stage",
]

# --- Parallel-DAG support -------------------------------------------------
# The DAG executor sets this env so (a) stage guards relax — the scheduler
# already guarantees dependency order, so the single-``current_stage`` check
# would wrongly reject concurrent lanes — and (b) state writes take a process
# lock since multiple lanes call _start_stage/_complete_stage at once.
_DAG_ENV = "PIPELINE_PARALLEL_DAG"
_STATE_LOCK = threading.Lock()


def dag_mode() -> bool:
    """True when the pipeline is running stages as a parallel DAG."""
    return os.environ.get(_DAG_ENV) == "1"


def set_dag_mode(on: bool) -> None:
    """Toggle DAG mode for the current process (set by the parallel executor)."""
    if on:
        os.environ[_DAG_ENV] = "1"
    else:
        os.environ.pop(_DAG_ENV, None)


def require_stage(state, stage_name: str) -> None:
    """Assert ``stage_name`` may run now.

    Linear mode: it must be the single ``current_stage`` (legacy invariant).
    DAG mode: no-op — the scheduler only dispatches a stage after its deps are
    ``completed``.
    """
    if dag_mode():
        return
    if state.current_stage != stage_name:
        raise StageInputMissingError(
            f"Cannot run {stage_name} stage from current_stage={state.current_stage!r}"
        )

logger = logging.getLogger("video_agent.orchestrator.stages")

IDEA_FILE = "json/idea.json"
_IDEA_FILE_LEGACY = "idea.json"

SCRIPT_PROMPT_PATH = Path("operator/chatgpt/script_prompt.md")
SCRIPT_RAW_PATH = Path("operator/chatgpt/script.raw.txt")
SCENES_PROMPT_PATH = Path("operator/chatgpt/scenes_prompt.md")
SCENES_RAW_PATH = Path("operator/chatgpt/scenes.raw.txt")
SCENES_PLAN_PATH = Path("operator/chatgpt/scenes_plan.json")
SCENES_BATCHES_DIR = Path("operator/chatgpt/scenes_batches")
SCENES_QA_BATCHES_DIR = Path("operator/gemini/scenes_qa_batches")
SEO_PROMPT_PATH = Path("operator/chatgpt/seo_prompt.md")
SEO_RAW_PATH = Path("operator/chatgpt/seo.raw.txt")
SCRIPT_QA_RAW_PATH = Path("operator/gemini/script_qa.raw.txt")
SCENES_QA_RAW_PATH = Path("operator/gemini/scenes_qa.raw.txt")
SEO_QA_RAW_PATH = Path("operator/gemini/seo_qa.raw.txt")

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


def resolve_stage_fps(channel_path: Path | None, default: int = 30) -> int:
    """Read ``render.fps`` from the channel config (best-effort, default 30).

    Used by the long-form ``visual_schedule`` stage. Never raises.
    """
    if channel_path is None:
        return default
    path = Path(channel_path)
    if not path.exists():
        return default
    try:
        from video_agent.utils.json_io import read_yaml

        cfg = read_yaml(path) or {}
        return int(((cfg.get("render") or {}).get("fps")) or default)
    except Exception:
        return default


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
    with _STATE_LOCK:
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
    with _STATE_LOCK:
        state = _apply_stage_completion(job_dir, stage_name)

    _logger = EventLogger(job_dir / EVENT_LOG)
    _logger.log(
        "STAGE_COMPLETED",
        {
            "job_id": state.job_id,
            "stage": stage_name,
            "output": str(output.relative_to(job_dir)),
        },
    )
    if not any(s.status == "pending" for s in state.stages):
        _logger.log("JOB_COMPLETED", {"job_id": state.job_id})


def _apply_stage_completion(job_dir: Path, stage_name: str):
    """Mark ``stage_name`` completed + (linear mode only) advance current_stage.

    Caller holds ``_STATE_LOCK``. In DAG mode the scheduler owns ordering, so the
    single-pointer advance is skipped (it would thrash with concurrent lanes).
    """
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
    # Advance current_stage only in linear mode. In DAG mode the scheduler owns
    # ordering, so advancing the single pointer would thrash with concurrent lanes.
    if not dag_mode():
        # Advance to the next pending stage AFTER the one that just completed.
        # Using the first pending stage anywhere would let a skipped/left-pending
        # earlier stage drag current_stage backward, wedging the next guard.
        completed_idx = next(
            (i for i, s in enumerate(state.stages) if s.name == stage_name), -1
        )
        next_pending = next(
            (s for s in state.stages[completed_idx + 1:] if s.status == "pending"), None
        )
        if next_pending is not None:
            state.current_stage = next_pending.name
        elif completed_idx + 1 < len(state.stages):
            # No PENDING stage remains but later (already-completed) stages exist
            # (a re-run). Advance FORWARD so the next stage's guard passes.
            state.current_stage = state.stages[completed_idx + 1].name
    state.updated_at = ts
    save_job(job_dir, state)
    return state
