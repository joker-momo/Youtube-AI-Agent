"""Codex bridge 20260704-005810: job.json current_stage remained stale
(mid-pipeline, e.g. render_continuity_qa) after a DAG-mode run_all completed
every stage.

In DAG mode _apply_stage_completion deliberately skips the linear
current_stage advance (it would thrash with concurrent lanes), but nothing
else maintained the pointer either -- so a completed job kept whatever value
the last reset/failure wrote, confusing the dashboard/timeline into treating
a finished job as mid-flight. The fix parks current_stage on the FINAL stage
exactly once, when the last remaining stage completes.
"""
from __future__ import annotations

import json
from pathlib import Path

from video_agent.orchestrator import load_job, save_job
from video_agent.orchestrator.orchestrator import create_job
from video_agent.orchestrator.stages._shared import _apply_stage_completion, set_dag_mode


STAGES = ["script", "render", "render_continuity_qa", "review"]


def _make_job(tmp_path: Path) -> Path:
    job_dir = tmp_path / "job"
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=STAGES)
    return job_dir


def _set_statuses(job_dir: Path, statuses: dict[str, str], current_stage: str) -> None:
    state = load_job(job_dir)
    for s in state.stages:
        if s.name in statuses:
            s.status = statuses[s.name]
    state.current_stage = current_stage
    save_job(job_dir, state)


def test_dag_final_completion_parks_current_stage_on_last_stage(tmp_path, monkeypatch):
    job_dir = _make_job(tmp_path)
    # Reproduce the production shape: everything done except review, and a
    # stale mid-pipeline pointer left behind by an earlier reset.
    _set_statuses(
        job_dir,
        {"script": "completed", "render": "completed", "render_continuity_qa": "completed"},
        current_stage="render_continuity_qa",
    )
    set_dag_mode(True)
    try:
        _apply_stage_completion(job_dir, "review")
    finally:
        set_dag_mode(False)

    state = load_job(job_dir)
    assert all(s.status == "completed" for s in state.stages)
    assert state.current_stage == "review"


def test_dag_mid_pipeline_completion_does_not_touch_current_stage(tmp_path):
    """The concurrent-lane thrash guard must survive: completing a stage while
    OTHER stages are still pending must leave current_stage alone in DAG mode."""
    job_dir = _make_job(tmp_path)
    _set_statuses(
        job_dir,
        {"script": "completed"},
        current_stage="render",
    )
    set_dag_mode(True)
    try:
        _apply_stage_completion(job_dir, "render")
    finally:
        set_dag_mode(False)

    state = load_job(job_dir)
    assert state.current_stage == "render"  # untouched -- review/continuity still pending


def test_dag_finalization_counts_skipped_stages_as_done(tmp_path):
    job_dir = _make_job(tmp_path)
    _set_statuses(
        job_dir,
        {"script": "completed", "render": "completed", "render_continuity_qa": "skipped"},
        current_stage="render",
    )
    set_dag_mode(True)
    try:
        _apply_stage_completion(job_dir, "review")
    finally:
        set_dag_mode(False)

    state = load_job(job_dir)
    assert state.current_stage == "review"
