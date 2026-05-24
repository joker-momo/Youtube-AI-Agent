from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.job_cleanup import CleanupOptions, collect_cleanup_candidates, cleanup_jobs


def _write_job(path, *, status: str = "completed") -> None:
    path.mkdir(parents=True, exist_ok=True)
    stages = [{"name": "render", "status": status}]
    payload = {
        "job_id": path.name,
        "channel_id": "vida-plena-45",
        "idea_path": "idea.json",
        "created_at": "2026-05-24T00:00:00Z",
        "updated_at": "2026-05-24T00:00:00Z",
        "current_stage": "review",
        "stages": stages,
    }
    (path / "job.json").write_text(json.dumps(payload), encoding="utf-8")


def test_cleanup_dry_run_does_not_delete_files(tmp_path):
    job = tmp_path / "jobs" / "failed-job"
    _write_job(job, status="failed")
    media = job / "video.mp4"
    media.write_bytes(b"x" * 10)

    result = cleanup_jobs(
        CleanupOptions(jobs_dir=tmp_path / "jobs", apply=False, include_failed_media=True)
    )

    assert result.bytes_reclaimable == 10
    assert result.bytes_deleted == 0
    assert media.exists()


def test_cleanup_apply_deletes_failed_job_media_when_explicit(tmp_path):
    job = tmp_path / "jobs" / "failed-job"
    _write_job(job, status="failed")
    media = job / "assets" / "scene-01.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"x" * 12)

    result = cleanup_jobs(
        CleanupOptions(jobs_dir=tmp_path / "jobs", apply=True, include_failed_media=True)
    )

    assert result.bytes_deleted == 12
    assert not media.exists()


def test_cleanup_preserves_newest_successful_job_media(tmp_path):
    old_job = tmp_path / "jobs" / "20260101-old"
    new_job = tmp_path / "jobs" / "20260201-new"
    _write_job(old_job, status="completed")
    _write_job(new_job, status="completed")
    old_media = old_job / "video.mp4"
    new_media = new_job / "video.mp4"
    old_media.write_bytes(b"x" * 5)
    new_media.write_bytes(b"x" * 7)

    candidates = collect_cleanup_candidates(
        CleanupOptions(jobs_dir=tmp_path / "jobs", include_success_media=True, keep_successful=1)
    )

    assert old_media in [item.path for item in candidates]
    assert new_media not in [item.path for item in candidates]


def test_cleanup_removes_stale_shards_only_when_canonical_exists(tmp_path):
    job = tmp_path / "jobs" / "job-a"
    _write_job(job, status="completed")
    shard = job / "operator" / "chatgpt" / "scenes_batches" / "scenes_batch_01.json"
    shard.parent.mkdir(parents=True)
    shard.write_text("{}", encoding="utf-8")

    assert collect_cleanup_candidates(
        CleanupOptions(jobs_dir=tmp_path / "jobs", include_shards=True)
    ) == []

    (job / "scenes.json").write_text("{}", encoding="utf-8")
    candidates = collect_cleanup_candidates(
        CleanupOptions(jobs_dir=tmp_path / "jobs", include_shards=True)
    )

    assert [item.path for item in candidates] == [shard]


def test_cleanup_can_include_orphan_job_media_when_explicit(tmp_path):
    job = tmp_path / "jobs" / "orphan-job"
    job.mkdir(parents=True)
    media = job / "video.mp4"
    media.write_bytes(b"x" * 9)

    assert collect_cleanup_candidates(CleanupOptions(jobs_dir=tmp_path / "jobs")) == []

    candidates = collect_cleanup_candidates(
        CleanupOptions(jobs_dir=tmp_path / "jobs", include_orphan_media=True)
    )

    assert [item.path for item in candidates] == [media]
