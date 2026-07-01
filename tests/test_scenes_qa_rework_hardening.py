"""Regression coverage for the scenes-QA rework hardening (bridge P1s).

Covers:
- bug-423: rework embeds the source artifact and rejects error-object / empty
  responses BEFORE promoting (so a MISSING_SOURCE_ARTIFACT reply never overwrites
  the last-good raw and death-spirals the retry loop).
- bug-421: a run_all failure is persisted to job.json (stage=failed + error)
  instead of leaving the stage 'pending' forever.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from video_agent.contracts import repo_root
from video_agent.orchestrator import mark_stage_failed
from video_agent.orchestrator.job_state import (
    DEFAULT_STAGES,
    JobState,
    StageStatus,
    load_job,
    save_job,
)
from video_agent.orchestrator.stages.qa import (
    StageInputMissingError,
    _load_current_artifact_json,
    _rework_response_is_usable,
    auto_rework_artifact,
)


@pytest.fixture
def channel_path() -> Path:
    return repo_root() / "configs/vida-plena-45/channel.yaml"


def _make_job(tmp_path: Path, *, current_stage: str = "scenes_qa") -> Path:
    job_dir = tmp_path / "job-test"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "operator" / "gemini").mkdir(parents=True)
    state = JobState(
        job_id="job-test",
        channel_id="vida-plena-45",
        idea_path="idea.json",
        created_at="2026-07-01T00:00:00+00:00",
        updated_at="2026-07-01T00:00:00+00:00",
        current_stage=current_stage,
        stages=[StageStatus(name=n) for n in DEFAULT_STAGES],
    )
    save_job(job_dir, state)
    return job_dir


# ---------------------------------------------------------------------------
# bug-423: reject error-object / empty rework responses
# ---------------------------------------------------------------------------


def test_rework_response_is_usable_rejects_error_object():
    raw = '{"artifact":"scenes","job_id":"MISSING_SOURCE_ARTIFACT","scenes":[],"error":{"code":"MISSING_SOURCE_ARTIFACT"}}'
    assert _rework_response_is_usable("scenes", raw) is False


def test_rework_response_is_usable_rejects_empty_scenes():
    assert _rework_response_is_usable("scenes", '{"scenes":[]}') is False


def test_rework_response_is_usable_rejects_garbage():
    assert _rework_response_is_usable("scenes", "not json at all") is False


def test_rework_response_is_usable_accepts_real_scenes():
    assert _rework_response_is_usable("scenes", '{"scenes":[{"id":"s1"}]}') is True


def test_rework_response_is_usable_non_scenes_accepts_any_object():
    # script/seo have no scenes[] gate — any non-error object is usable.
    assert _rework_response_is_usable("script", '{"title":"x"}') is True
    assert _rework_response_is_usable("seo", '{"error":{"code":"X"}}') is False


# ---------------------------------------------------------------------------
# bug-423: embed the last-good source artifact
# ---------------------------------------------------------------------------


def test_load_current_artifact_prefers_json_dir(tmp_path: Path):
    job_dir = _make_job(tmp_path)
    (job_dir / "json" / "scenes.json").write_text(
        json.dumps({"job_id": "job-test", "scenes": [{"id": "scene-01"}]}),
        encoding="utf-8",
    )
    loaded = _load_current_artifact_json(job_dir, "scenes")
    assert loaded is not None
    assert loaded["scenes"][0]["id"] == "scene-01"


def test_load_current_artifact_missing_returns_none(tmp_path: Path):
    job_dir = _make_job(tmp_path)
    assert _load_current_artifact_json(job_dir, "scenes") is None


def test_rework_embeds_source_and_does_not_overwrite_on_error(
    tmp_path: Path, channel_path: Path
):
    job_dir = _make_job(tmp_path)
    last_good = {"job_id": "job-test", "channel_id": "vida-plena-45", "scenes": [{"id": "scene-01", "narration": "hola"}]}
    scenes_json = job_dir / "json" / "scenes.json"
    scenes_json.write_text(json.dumps(last_good), encoding="utf-8")
    (job_dir / "operator" / "gemini" / "scenes_qa.json").write_text(
        json.dumps({"verdict": "NEEDS_REWORK", "issues": ["fix x"], "required_changes": ["do y"]}),
        encoding="utf-8",
    )

    captured: list[str] = []

    async def fake_chatgpt(messages):
        captured.append("\n".join(messages))
        # Simulate the aged-tab failure: model returns an error object.
        return '{"artifact":"scenes","job_id":"MISSING_SOURCE_ARTIFACT","scenes":[],"error":{"code":"MISSING_SOURCE_ARTIFACT"}}'

    with pytest.raises(StageInputMissingError):
        asyncio.run(
            auto_rework_artifact("scenes", job_dir, channel_path, fake_chatgpt)
        )

    # The rework message embedded the last-good artifact (so a healthy tab could fix it).
    assert "scene-01" in captured[0]
    # The error response did NOT overwrite the last-good promoted artifact.
    assert json.loads(scenes_json.read_text(encoding="utf-8")) == last_good


# ---------------------------------------------------------------------------
# bug-421: persist stage failure to job.json
# ---------------------------------------------------------------------------


def test_mark_stage_failed_persists_status_and_error(tmp_path: Path):
    job_dir = _make_job(tmp_path, current_stage="scenes_promote")
    mark_stage_failed(job_dir, "scenes_promote", "boom: scenes schema empty")
    reloaded = load_job(job_dir)
    entry = reloaded.stage("scenes_promote")
    assert entry.status == "failed"
    assert "boom" in (entry.error or "")
    assert entry.completed_at is not None
    assert reloaded.current_stage == "scenes_promote"


def test_mark_stage_failed_truncates_long_error(tmp_path: Path):
    job_dir = _make_job(tmp_path, current_stage="scenes_qa")
    mark_stage_failed(job_dir, "scenes_qa", "x" * 5000)
    entry = load_job(job_dir).stage("scenes_qa")
    assert entry.error is not None and len(entry.error) <= 2000
