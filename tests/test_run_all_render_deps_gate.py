"""Regression for bug-461/462: render used to run even when a cross-lane
dependency (e.g. whisper_timestamps) silently failed inside the parallel DAG,
or when it was marked "completed" without ever producing usable output.

DagScheduler catches per-stage exceptions so an independent lane keeps going,
but its result dict was discarded by the caller -- a failed stage stayed
job.json-"pending" forever with no error recorded, and render/render_continuity_qa
/review (which run OUTSIDE DagScheduler, gated only on "not yet completed")
had no way to notice. This produced a shipped video with outputs/video.mp4
fully "completed" but zero whisper word_segments -- static, non-synced
captions in the final product.

_assert_stage_deps_satisfied is the fix: it re-checks dag.STAGE_DEPS against
the CURRENT job.json state right before dispatching a stage that has
cross-lane dependencies. Codex's verification of the first pass caught a
follow-up gap: checking stage *status* alone doesn't prove the artifact a
"completed" stage was supposed to produce actually exists with real content
-- a synthetic job with every dep marked "completed" but no real whisper
artifact still passed. _whisper_timestamps_artifact_invalid_reason closes
that gap by inspecting whisper_timestamps.json's actual content.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_agent.orchestrator.job_state import JobState, StageStatus, save_job
from video_agent.orchestrator.stages._shared import StageInputMissingError
from video_agent.web.run_all_pipeline import _assert_stage_deps_satisfied

_ALL_STAGES = (
    "seo", "graphic_images", "thumbnail_image", "whisper_timestamps",
    "visual_spans", "visual_schedule", "render", "render_continuity_qa", "review",
)


def _make_job(tmp_path: Path, stage_statuses: dict[str, str], errors: dict[str, str] | None = None) -> Path:
    job_dir = tmp_path / "job-1"
    stages = [
        StageStatus(name=name, status=stage_statuses.get(name, "completed"),
                    error=(errors or {}).get(name))
        for name in _ALL_STAGES
    ]
    state = JobState(
        job_id="job-1", channel_id="vida-plena-45", idea_path="json/idea.json",
        created_at="2026-07-03T00:00:00+00:00", updated_at="2026-07-03T00:00:00+00:00",
        current_stage="render", stages=stages,
    )
    save_job(job_dir, state)
    return job_dir


def _write_whisper_artifact(job_dir: Path, *, scenes: list[dict] | None) -> None:
    json_dir = job_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    payload = {"scenes": scenes} if scenes is not None else {}
    (json_dir / "whisper_timestamps.json").write_text(json.dumps(payload), encoding="utf-8")


def _valid_whisper_scenes() -> list[dict]:
    return [
        {"scene_id": "scene-01", "audio_offset_sec": 0.0,
         "word_segments": [{"text": "hola", "start": 0.0, "end": 0.4}]},
        {"scene_id": "scene-02", "audio_offset_sec": 5.0, "word_segments": []},
    ]


def test_render_blocked_when_whisper_timestamps_never_completed(tmp_path):
    job_dir = _make_job(
        tmp_path,
        {"whisper_timestamps": "pending"},  # exactly the bug-461 symptom: never marked failed either
    )

    with pytest.raises(StageInputMissingError, match="whisper_timestamps"):
        _assert_stage_deps_satisfied(job_dir, "render")


def test_render_blocked_when_whisper_timestamps_marked_failed(tmp_path):
    job_dir = _make_job(
        tmp_path,
        {"whisper_timestamps": "failed"},
        {"whisper_timestamps": "Narration synthesis timed out after 900s."},
    )

    with pytest.raises(StageInputMissingError, match="Narration synthesis timed out"):
        _assert_stage_deps_satisfied(job_dir, "render")


def test_render_proceeds_when_all_deps_completed_with_real_whisper_artifact(tmp_path):
    job_dir = _make_job(tmp_path, {})  # everything defaults to "completed"
    _write_whisper_artifact(job_dir, scenes=_valid_whisper_scenes())

    _assert_stage_deps_satisfied(job_dir, "render")  # must not raise


def test_render_blocked_when_any_single_dep_incomplete(tmp_path):
    for dep in ("seo", "graphic_images", "thumbnail_image", "visual_schedule", "visual_spans"):
        job_dir = _make_job(tmp_path / dep, {dep: "in_progress"})
        with pytest.raises(StageInputMissingError, match=dep):
            _assert_stage_deps_satisfied(job_dir, "render")


def test_render_blocked_when_whisper_completed_but_artifact_missing(tmp_path):
    """Codex's exact verification repro: every dep (including
    whisper_timestamps) marked 'completed', but no whisper_timestamps.json on
    disk at all."""
    job_dir = _make_job(tmp_path, {})  # no artifact written

    with pytest.raises(StageInputMissingError, match="whisper_timestamps.json is missing"):
        _assert_stage_deps_satisfied(job_dir, "render")


def test_render_blocked_when_whisper_completed_but_no_scenes(tmp_path):
    job_dir = _make_job(tmp_path, {})
    _write_whisper_artifact(job_dir, scenes=[])

    with pytest.raises(StageInputMissingError, match="no scenes"):
        _assert_stage_deps_satisfied(job_dir, "render")


def test_render_blocked_when_whisper_completed_but_all_word_segments_empty(tmp_path):
    """The exact real-world shipped defect: whisper_timestamps.json exists
    and has scene entries, but every scene's word_segments list is empty."""
    job_dir = _make_job(tmp_path, {})
    _write_whisper_artifact(
        job_dir,
        scenes=[
            {"scene_id": "scene-01", "audio_offset_sec": 0.0, "word_segments": []},
            {"scene_id": "scene-02", "audio_offset_sec": 5.0, "word_segments": []},
        ],
    )

    with pytest.raises(StageInputMissingError, match="empty word_segments"):
        _assert_stage_deps_satisfied(job_dir, "render")
