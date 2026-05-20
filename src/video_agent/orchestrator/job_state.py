from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

JOB_FILE = "job.json"

DEFAULT_STAGES: tuple[str, ...] = (
    "script",
    "script_promote",
    "script_qa",
    "scenes",
    "scenes_promote",
    "scenes_qa",
    "seo",
    "seo_promote",
    "seo_qa",
    "assets_chatgpt",
    "render",
    "review",
)


@dataclass
class StageStatus:
    name: str
    status: str = "pending"  # pending | in_progress | completed | failed
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


@dataclass
class JobState:
    job_id: str
    channel_id: str
    idea_path: str
    created_at: str
    updated_at: str
    current_stage: str
    stages: list[StageStatus] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "JobState":
        stages = [StageStatus(**s) for s in payload.get("stages", [])]
        return cls(
            job_id=payload["job_id"],
            channel_id=payload["channel_id"],
            idea_path=payload["idea_path"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            current_stage=payload["current_stage"],
            stages=stages,
        )

    def stage(self, name: str) -> StageStatus:
        for entry in self.stages:
            if entry.name == name:
                return entry
        raise KeyError(f"Unknown stage: {name}")


def save_job(job_dir: Path, state: JobState) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / JOB_FILE
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_job(job_dir: Path) -> JobState:
    path = job_dir / JOB_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    return JobState.from_dict(payload)
