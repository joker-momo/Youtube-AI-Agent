from __future__ import annotations

import shutil
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

    def run_once(self) -> bool:
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
        try:
            for stage in self.runner.stages(job):
                if stage in completed:
                    continue
                if self.queue.is_cancel_requested(lease.job_id):
                    self.queue.acknowledge_cancel(lease.attempt_id, self.worker_id)
                    return True
                self.queue.heartbeat(
                    lease.attempt_id,
                    self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
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
                self.queue.fail_attempt(
                    lease.attempt_id,
                    self.worker_id,
                    {
                        "code": "STAGE_FAILED",
                        "message": str(exc),
                        "retryable": True,
                    },
                )
            raise
        finally:
            shutil.rmtree(attempt_work, ignore_errors=True)
