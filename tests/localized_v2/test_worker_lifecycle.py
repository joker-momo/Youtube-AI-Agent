from __future__ import annotations

from pathlib import Path

from video_agent.localized_v2.job_state import JobInput
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.worker import LocalizedWorker


class FakeRunner:
    def __init__(self, *, cancel_after: str | None = None):
        self.cancel_after = cancel_after
        self.calls: list[str] = []
        self.queue: LocalizedQueue | None = None
        self.job_id = ""

    def stages(self, _job: dict) -> tuple[str, ...]:
        return ("script", "audio", "render")

    def run_stage(self, _job: dict, stage: str, work_dir: Path) -> dict[str, Path]:
        self.calls.append(stage)
        artifact = work_dir / f"{stage}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f'{{"stage":"{stage}"}}', encoding="utf-8")
        if stage == self.cancel_after:
            assert self.queue is not None
            self.queue.request_cancel(self.job_id)
        return {f"{stage}.json": artifact}


def _input() -> JobInput:
    return JobInput(
        job_id="job-a",
        channel_id="healthy-life-en",
        locale="en-US",
        topic="A calm daily habit",
        channel_snapshot={"channelId": "healthy-life-en"},
        locale_snapshot={"locale": "en-US"},
    )


def _worker(tmp_path: Path, runner: FakeRunner) -> tuple[LocalizedWorker, LocalizedQueue]:
    legacy = tmp_path / "legacy-jobs"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "runtime", legacy_jobs_root=legacy)
    paths.initialize()
    queue = LocalizedQueue(paths.queue_db)
    runner.queue = queue
    runner.job_id = "job-a"
    return LocalizedWorker("worker-a", paths, queue, runner), queue


def test_worker_runs_stages_strictly_and_promotes_atomically(tmp_path: Path) -> None:
    runner = FakeRunner()
    worker, queue = _worker(tmp_path, runner)
    queue.create_job(_input())

    assert worker.run_once() is True

    assert runner.calls == ["script", "audio", "render"]
    assert queue.get_job("job-a")["status"] == "COMPLETED"
    assert queue.completed_stages("job-a") == ("script", "audio", "render")
    assert [item["name"] for item in queue.list_artifacts("job-a")] == [
        "script.json",
        "audio.json",
        "render.json",
    ]


def test_worker_cancellation_prevents_later_stages(tmp_path: Path) -> None:
    runner = FakeRunner(cancel_after="script")
    worker, queue = _worker(tmp_path, runner)
    queue.create_job(_input())

    assert worker.run_once() is True

    assert runner.calls == ["script"]
    assert queue.get_job("job-a")["status"] == "CANCELLED"
    assert queue.completed_stages("job-a") == ("script",)


def test_resume_skips_promoted_stages(tmp_path: Path) -> None:
    runner = FakeRunner(cancel_after="audio")
    worker, queue = _worker(tmp_path, runner)
    queue.create_job(_input())
    worker.run_once()
    assert queue.get_job("job-a")["status"] == "CANCELLED"

    queue.resume("job-a", allow_cancelled=True)
    runner.cancel_after = None
    runner.calls.clear()
    worker.run_once()

    assert runner.calls == ["render"]
    assert queue.get_job("job-a")["status"] == "COMPLETED"
