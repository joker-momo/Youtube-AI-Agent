from __future__ import annotations

import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from video_agent.localized_v2.job_state import promote_artifacts
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.queue import LocalizedQueue


class JobRunner(Protocol):
    def stages(self, job: dict) -> tuple[str, ...]: ...

    def run_stage(self, job: dict, stage: str, work_dir: Path) -> dict[str, Path]: ...


class LocalizedWorker:
    def __init__(
        self,
        worker_id: str,
        paths: RuntimePaths,
        queue: LocalizedQueue,
        runner: JobRunner,
        *,
        lease_seconds: int = 30,
    ):
        self.worker_id = worker_id
        self.paths = paths
        self.queue = queue
        self.runner = runner
        self.lease_seconds = max(1, lease_seconds)

    @contextmanager
    def _maintain_lease(self, attempt_id: str) -> Iterator[None]:
        stopped = threading.Event()
        failures: list[BaseException] = []
        interval = max(0.25, min(30.0, self.lease_seconds / 3))

        def pulse() -> None:
            while not stopped.wait(interval):
                try:
                    self.queue.worker_heartbeat(self.worker_id)
                    self.queue.heartbeat(
                        attempt_id,
                        self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                except BaseException as exc:
                    failures.append(exc)
                    stopped.set()

        thread = threading.Thread(
            target=pulse,
            name=f"localized-v2-heartbeat-{self.worker_id}",
            daemon=True,
        )
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=interval + 1)
        if failures:
            raise RuntimeError("localized V2 worker heartbeat failed") from failures[0]

    def run_once(self) -> bool:
        self.queue.worker_heartbeat(self.worker_id)
        lease = self.queue.claim_next(
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if lease is None:
            return False
        job = self.queue.get_job(lease.job_id)
        if job is None:
            raise RuntimeError(f"claimed missing localized V2 job: {lease.job_id}")
        completed = set(self.queue.completed_stages(lease.job_id))
        attempt_work = self.paths.work / lease.job_id / lease.attempt_id
        attempt_work.mkdir(parents=True, exist_ok=True)
        cancelled = False
        try:
            with self._maintain_lease(lease.attempt_id):
                for stage in self.runner.stages(job):
                    if stage in completed:
                        continue
                    if self.queue.is_cancel_requested(lease.job_id):
                        cancelled = True
                        break
                    self.queue.record_stage(
                        lease.attempt_id,
                        self.worker_id,
                        stage,
                        completed=False,
                    )
                    stage_work = attempt_work / stage
                    outputs = self.runner.run_stage(job, stage, stage_work)
                    promoted = promote_artifacts(
                        self.paths,
                        lease.job_id,
                        stage,
                        outputs,
                    )
                    self.queue.register_artifacts(
                        lease.attempt_id,
                        self.worker_id,
                        stage,
                        promoted,
                    )
                    self.queue.record_stage(
                        lease.attempt_id,
                        self.worker_id,
                        stage,
                        completed=True,
                    )
                    if self.queue.is_cancel_requested(lease.job_id):
                        cancelled = True
                        break
            if cancelled:
                self.queue.acknowledge_cancel(lease.attempt_id, self.worker_id)
                return True
            self.queue.complete_attempt(
                lease.attempt_id,
                self.worker_id,
                {"artifacts": len(self.queue.list_artifacts(lease.job_id))},
            )
            return True
        except Exception as exc:
            current = self.queue.get_attempt(lease.attempt_id)
            if current and current["status"] == "RUNNING":
                failure_factory = getattr(exc, "to_failure", None)
                failure = (
                    failure_factory()
                    if callable(failure_factory)
                    else {
                        "code": "STAGE_FAILED",
                        "message": str(exc),
                        "retryable": True,
                    }
                )
                self.queue.fail_attempt(
                    lease.attempt_id,
                    self.worker_id,
                    failure,
                )
            raise
        finally:
            shutil.rmtree(attempt_work, ignore_errors=True)
