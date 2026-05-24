from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from video_agent.orchestrator import DEFAULT_STAGES, load_job, save_job
from video_agent.orchestrator.stages import StageInputMissingError
from video_agent.storage.atomic import atomic_write_json

APPROVALS_FILE = "approvals.json"
APPROVAL_REQUIRED_STAGES: tuple[str, ...] = (
    "idea_research",
    "script_promote",
    "scenes_promote",
    "seo_promote",
    "thumbnail_image",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def approvals_path(job_dir: Path) -> Path:
    return job_dir / APPROVALS_FILE


def default_approvals() -> dict[str, bool]:
    return {name: False for name in APPROVAL_REQUIRED_STAGES}


def load_approvals(job_dir: Path) -> dict[str, bool]:
    path = approvals_path(job_dir)
    if not path.exists():
        return default_approvals()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_approvals()


def save_approvals(job_dir: Path, approvals: dict[str, bool]) -> None:
    path = approvals_path(job_dir)
    atomic_write_json(path, {k: bool(v) for k, v in approvals.items()})


def set_approval(job_dir: Path, stage_name: str, value: bool) -> None:
    approvals = load_approvals(job_dir)
    if stage_name not in approvals:
        raise ValueError(f"Unknown approval stage: {stage_name}")
    approvals[stage_name] = bool(value)
    save_approvals(job_dir, approvals)


def approval_block_for_current_stage(
    current_stage: str | None, approvals: dict[str, bool]
) -> str | None:
    if not current_stage:
        return None
    stage_order = list(DEFAULT_STAGES)
    try:
        cur_idx = stage_order.index(current_stage)
    except ValueError:
        return None
    for stage_name in APPROVAL_REQUIRED_STAGES:
        if approvals.get(stage_name):
            continue
        try:
            gate_idx = stage_order.index(stage_name)
        except ValueError:
            continue
        # If we are at/after the stage right after gate, block forward ops
        if cur_idx > gate_idx:
            return stage_name
    return None


def reset_stage_for_regen(job_dir: Path, stage_name: str) -> None:
    state = load_job(job_dir)
    names = [s.name for s in state.stages]
    if stage_name not in names:
        raise StageInputMissingError(f"Unknown stage: {stage_name}")
    stage_idx = names.index(stage_name)
    # Do not allow reset once downstream stages already completed.
    downstream_completed = any(
        s.status == "completed" for s in state.stages[stage_idx + 1 :]
    )
    if downstream_completed:
        raise StageInputMissingError(
            f"Cannot regenerate {stage_name}: downstream stages already completed."
        )
    for s in state.stages[stage_idx:]:
        s.status = "pending"
        s.started_at = None
        s.completed_at = None
        s.error = None
    state.current_stage = stage_name
    state.updated_at = now_iso()
    save_job(job_dir, state)
