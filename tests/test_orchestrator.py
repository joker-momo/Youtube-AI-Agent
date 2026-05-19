from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_agent.orchestrator import (
    DEFAULT_STAGES,
    JobAlreadyExistsError,
    JobNotFoundError,
    advance,
    create_job,
    load_job,
)


def _read_events(job_dir: Path) -> list[dict]:
    events_path = job_dir / "events.jsonl"
    return [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]


def test_create_job_writes_state_and_event(tmp_path: Path):
    job_dir = tmp_path / "job-1"
    state = create_job(
        job_dir,
        job_id="job-1",
        channel_id="vida-plena-45",
        idea_path="inputs/manual_idea.json",
    )

    assert state.current_stage == DEFAULT_STAGES[0]
    assert [s.name for s in state.stages] == list(DEFAULT_STAGES)
    assert all(s.status == "pending" for s in state.stages)

    loaded = load_job(job_dir)
    assert loaded.job_id == "job-1"
    assert loaded.channel_id == "vida-plena-45"

    events = _read_events(job_dir)
    assert events[0]["event"] == "JOB_CREATED"
    assert events[0]["data"]["job_id"] == "job-1"


def test_create_job_rejects_duplicate(tmp_path: Path):
    job_dir = tmp_path / "job-1"
    create_job(job_dir, job_id="job-1", channel_id="c", idea_path="i.json")
    with pytest.raises(JobAlreadyExistsError):
        create_job(job_dir, job_id="job-1", channel_id="c", idea_path="i.json")


def test_advance_progresses_through_stages(tmp_path: Path):
    job_dir = tmp_path / "job-2"
    create_job(job_dir, job_id="job-2", channel_id="c", idea_path="i.json")

    # pending -> in_progress for current stage
    state = advance(job_dir)
    assert state.stage(DEFAULT_STAGES[0]).status == "in_progress"

    # in_progress -> completed, current advances to next pending
    state = advance(job_dir)
    assert state.stage(DEFAULT_STAGES[0]).status == "completed"
    assert state.current_stage == DEFAULT_STAGES[1]
    assert DEFAULT_STAGES[1] == "script_promote"
    assert DEFAULT_STAGES[3] == "scenes_promote"

    # walk through remaining stages
    for _ in range(len(DEFAULT_STAGES) * 2):
        state = advance(job_dir)
        if all(s.status == "completed" for s in state.stages):
            break

    assert all(s.status == "completed" for s in state.stages)
    events = [e["event"] for e in _read_events(job_dir)]
    assert events.count("STAGE_STARTED") == len(DEFAULT_STAGES)
    assert events.count("STAGE_COMPLETED") == len(DEFAULT_STAGES)
    assert events[-1] == "JOB_COMPLETED"


def test_advance_missing_job_raises(tmp_path: Path):
    with pytest.raises(JobNotFoundError):
        advance(tmp_path / "missing")
