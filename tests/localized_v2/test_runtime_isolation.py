from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from video_agent.localized_v2.contracts import CapabilityFailure, PreflightResult
from video_agent.localized_v2.job_state import JobInput
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.runtime import LocalizedRuntime, PreflightRejected


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(job_id: str = "job-a") -> JobInput:
    return JobInput(
        job_id=job_id,
        channel_id="healthy-life-en",
        locale="en-US",
        topic="A calm daily habit",
        channel_snapshot={"channelId": "healthy-life-en", "locale": "en-US"},
        locale_snapshot={"locale": "en-US"},
    )


def test_runtime_paths_reject_legacy_root_and_descendants(tmp_path: Path) -> None:
    legacy = tmp_path / "jobs"
    legacy.mkdir()

    with pytest.raises(ValueError, match="legacy"):
        RuntimePaths.build(legacy, legacy_jobs_root=legacy)
    with pytest.raises(ValueError, match="legacy"):
        RuntimePaths.build(legacy / "localized", legacy_jobs_root=legacy)


def test_v2_job_writes_only_inside_v2_root(tmp_path: Path) -> None:
    legacy = tmp_path / "jobs"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "localized-v2-runtime", legacy_jobs_root=legacy)
    runtime = LocalizedRuntime(paths, LocalizedQueue(paths.queue_db))

    snapshot = runtime.submit(_request(), PreflightResult())

    assert snapshot["jobId"] == "job-a"
    assert (paths.jobs / "job-a" / "input.json").is_file()
    assert json.loads((paths.jobs / "job-a" / "input.json").read_text())["locale"] == "en-US"
    assert list(legacy.iterdir()) == []


def test_matching_legacy_job_id_cannot_collide(tmp_path: Path) -> None:
    legacy = tmp_path / "jobs"
    legacy_job = legacy / "job-a"
    legacy_job.mkdir(parents=True)
    (legacy_job / "sentinel.txt").write_text("legacy", encoding="utf-8")
    paths = RuntimePaths.build(tmp_path / "localized-v2-runtime", legacy_jobs_root=legacy)
    runtime = LocalizedRuntime(paths, LocalizedQueue(paths.queue_db))

    runtime.submit(_request("job-a"), PreflightResult())

    assert (legacy_job / "sentinel.txt").read_text(encoding="utf-8") == "legacy"
    assert (paths.jobs / "job-a" / "input.json").is_file()


def test_failed_preflight_creates_no_v2_state(tmp_path: Path) -> None:
    legacy = tmp_path / "jobs"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "localized-v2-runtime", legacy_jobs_root=legacy)
    queue = LocalizedQueue(paths.queue_db)
    runtime = LocalizedRuntime(paths, queue)
    failure = CapabilityFailure(
        locale="en-US",
        capability="voice",
        provider="kokoro",
        code="VOICE_UNAVAILABLE",
        remediation="register a voice",
    )

    with pytest.raises(PreflightRejected) as error:
        runtime.submit(_request(), PreflightResult((failure,)))

    assert error.value.failures[0].code == "VOICE_UNAVAILABLE"
    assert queue.get_job("job-a") is None
    assert not (paths.jobs / "job-a").exists()


def test_runtime_never_mutates_or_imports_legacy_queue(tmp_path: Path) -> None:
    legacy = tmp_path / "jobs"
    legacy.mkdir()
    legacy_db = legacy / "queue.db"
    legacy_db.write_bytes(b"legacy queue sentinel")
    before = _sha256(legacy_db)
    paths = RuntimePaths.build(tmp_path / "localized-v2-runtime", legacy_jobs_root=legacy)
    runtime = LocalizedRuntime(paths, LocalizedQueue(paths.queue_db))

    runtime.submit(_request(), PreflightResult())

    assert _sha256(legacy_db) == before
    assert "video_agent.orchestrator.queue" not in sys.modules
    assert "video_agent.orchestrator.worker" not in sys.modules


def test_credentials_are_rejected_before_persistence(tmp_path: Path) -> None:
    legacy = tmp_path / "jobs"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "localized-v2-runtime", legacy_jobs_root=legacy)
    runtime = LocalizedRuntime(paths, LocalizedQueue(paths.queue_db))
    request = _request()
    request.channel_snapshot["apiToken"] = "do-not-store"

    with pytest.raises(ValueError, match="secret"):
        runtime.submit(request, PreflightResult())

    assert not paths.jobs.exists()
    assert not paths.queue_db.exists()
