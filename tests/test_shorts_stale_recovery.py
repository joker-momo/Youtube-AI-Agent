"""Orphaned-Short recovery: a synchronous build that dies mid-stage leaves
short_status.json stuck at status="generating" forever. These tests cover the
stale-detection + force-terminal transitions that release the UI."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from video_agent.shorts import paths, status as shorts_status
from video_agent.shorts.manifest import write_manifest, write_short_status


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _write_status(job: Path, short_id: str, *, status: str, age_seconds: float) -> dict:
    ts = _iso(_now() - datetime.timedelta(seconds=age_seconds))
    doc = {
        "short_id": short_id,
        "source_long_job_id": job.name,
        "status": status,
        "rendered": False,
        "qa_verdict": "PENDING",
        "stages": [],
        "created_at": ts,
        "updated_at": ts,
        "heartbeat_at": ts,
    }
    write_short_status(job, short_id, doc)
    return doc


def _job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    paths.shorts_dir(job).mkdir(parents=True, exist_ok=True)
    return job


# -- recover_stale_short ----------------------------------------------------

def test_orphaned_generating_no_owner_stale_recovers_to_terminal(tmp_path: Path):
    job = _job(tmp_path)
    short_id = "short-01"
    _write_status(job, short_id, status="generating", age_seconds=600)
    doc = shorts_status.read_short_status_or_empty(job, short_id)

    recovered = shorts_status.recover_stale_short(
        job, short_id, doc, owner_alive=False, threshold=300,
    )

    assert recovered is not None
    assert recovered["status"] == "failed"
    assert recovered["stop_requested"] is True
    assert recovered.get("recovered_from_stale") is True
    # persisted to disk, not just returned
    on_disk = json.loads(paths.short_status_path(job, short_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "failed"
    assert on_disk["stop_requested"] is True


def test_generating_with_live_owner_is_not_recovered(tmp_path: Path):
    job = _job(tmp_path)
    short_id = "short-01"
    _write_status(job, short_id, status="generating", age_seconds=600)
    doc = shorts_status.read_short_status_or_empty(job, short_id)

    recovered = shorts_status.recover_stale_short(
        job, short_id, doc, owner_alive=True, threshold=300,
    )

    assert recovered is None
    on_disk = json.loads(paths.short_status_path(job, short_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "generating"


def test_generating_with_fresh_heartbeat_is_not_recovered(tmp_path: Path):
    job = _job(tmp_path)
    short_id = "short-01"
    _write_status(job, short_id, status="generating", age_seconds=5)
    doc = shorts_status.read_short_status_or_empty(job, short_id)

    recovered = shorts_status.recover_stale_short(
        job, short_id, doc, owner_alive=False, threshold=300,
    )

    assert recovered is None


def test_terminal_status_is_never_recovered(tmp_path: Path):
    job = _job(tmp_path)
    short_id = "short-01"
    _write_status(job, short_id, status="rendered", age_seconds=999)
    doc = shorts_status.read_short_status_or_empty(job, short_id)

    recovered = shorts_status.recover_stale_short(
        job, short_id, doc, owner_alive=False, threshold=300,
    )

    assert recovered is None


# -- force_terminate_orphaned_shorts (Stop path) ----------------------------

def test_stop_force_terminates_orphan_immediately_when_no_owner(tmp_path: Path):
    job = _job(tmp_path)
    # Even a freshly-written generating short is force-terminated on explicit
    # Stop when no live process owns the build (require_stale=False).
    _write_status(job, "short-01", status="generating", age_seconds=2)
    paths.short_status_path(job, "short-02")  # touch path resolution
    _write_status(job, "short-02", status="rendered", age_seconds=2)

    recovered = shorts_status.force_terminate_orphaned_shorts(job, owner_alive=False)

    assert recovered == ["short-01"]
    on_disk = json.loads(paths.short_status_path(job, "short-01").read_text(encoding="utf-8"))
    assert on_disk["status"] == "failed"
    assert on_disk["stop_requested"] is True
    # terminal short untouched
    other = json.loads(paths.short_status_path(job, "short-02").read_text(encoding="utf-8"))
    assert other["status"] == "rendered"


def test_stop_does_not_terminate_when_owner_alive(tmp_path: Path):
    job = _job(tmp_path)
    _write_status(job, "short-01", status="generating", age_seconds=2)

    recovered = shorts_status.force_terminate_orphaned_shorts(job, owner_alive=True)

    assert recovered == []
    on_disk = json.loads(paths.short_status_path(job, "short-01").read_text(encoding="utf-8"))
    assert on_disk["status"] == "generating"


# -- summarize_shorts integration -------------------------------------------

def test_summarize_counts_recovered_orphan_as_failed(tmp_path: Path):
    job = _job(tmp_path)
    short_id = "short-01"
    _write_status(job, short_id, status="generating", age_seconds=600)
    write_manifest(
        job,
        {
            "source_long_job_id": job.name,
            "status": "rendering",
            "mode": "synthesis_ideas",
            "shorts": [{"short_id": short_id, "status": "generating", "rendered": False}],
        },
    )

    summary = shorts_status.summarize_shorts(job, owner_alive=False)

    assert summary["counts"]["failed"] == 1
    # and the on-disk short status was transitioned (UI releases it)
    on_disk = json.loads(paths.short_status_path(job, short_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "failed"


def test_summarize_ignores_stale_autopilot_lock_file(tmp_path: Path):
    job = _job(tmp_path)
    short_id = "short-01"
    paths.autopilot_lock_path(job).write_text("stale\n", encoding="utf-8")
    _write_status(job, short_id, status="generating", age_seconds=600)
    write_manifest(
        job,
        {
            "source_long_job_id": job.name,
            "status": "rendering",
            "mode": "synthesis_ideas",
            "shorts": [{"short_id": short_id, "status": "generating", "rendered": False}],
        },
    )

    summary = shorts_status.summarize_shorts(job)

    assert summary["running"] is False
    assert summary["state"] != "running"
    assert summary["counts"]["failed"] == 1
