"""Regression guard for ``_complete_stage`` resume pointer (I2).

Bug: ``current_stage`` was set to the FIRST pending stage anywhere in the list.
A skipped/left-pending earlier stage would therefore drag ``current_stage``
backward when a later stage completed, and the next stage's
``current_stage != _STAGE`` guard would wedge the pipeline. The pointer must
advance to the next pending stage AFTER the one that just completed.
"""

from __future__ import annotations

from video_agent.orchestrator.job_state import JobState, StageStatus, load_job, save_job
from video_agent.orchestrator.stages._shared import _complete_stage


def _job(tmp_path, stages):
    state = JobState(
        job_id="job-1",
        channel_id="ch",
        idea_path="idea.json",
        created_at="t0",
        updated_at="t0",
        current_stage=stages[0][0],
        stages=[StageStatus(name=n, status=s) for n, s in stages],
    )
    save_job(tmp_path, state)
    return state


def test_pointer_advances_past_completed_not_to_earlier_pending(tmp_path):
    # "a" was skipped (still pending); "b" is what we just completed. The next
    # stage must be "c", NOT the earlier-pending "a".
    _job(tmp_path, [("a", "pending"), ("b", "in_progress"), ("c", "pending")])
    _complete_stage(tmp_path, "b", tmp_path / "out.json")
    assert load_job(tmp_path).current_stage == "c"


def test_normal_sequential_advance(tmp_path):
    _job(tmp_path, [("a", "completed"), ("b", "in_progress"), ("c", "pending")])
    _complete_stage(tmp_path, "b", tmp_path / "out.json")
    assert load_job(tmp_path).current_stage == "c"


def test_pointer_advances_forward_when_next_stage_already_completed(tmp_path):
    # Re-run case (HTTP-409 finalize bug): render_continuity_qa just completed, but
    # the only later stage `review` is still "completed" from a prior pass (e.g. a
    # re-render). current_stage must advance FORWARD to `review` so its
    # ``current_stage == "review"`` guard passes (review re-runs and the job
    # finalizes) instead of wedging on render_continuity_qa → "Cannot run review
    # from current_stage='render_continuity_qa'" → 409 → job failed unfinalized.
    _job(
        tmp_path,
        [
            ("render", "completed"),
            ("render_continuity_qa", "in_progress"),
            ("review", "completed"),
        ],
    )
    _complete_stage(tmp_path, "render_continuity_qa", tmp_path / "out.json")
    assert load_job(tmp_path).current_stage == "review"
