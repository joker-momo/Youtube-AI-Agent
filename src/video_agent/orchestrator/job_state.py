from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from video_agent.storage.atomic import atomic_write_json

JOB_FILE = "job.json"

DEFAULT_STAGES: tuple[str, ...] = (
    "idea_research",
    "script",
    "script_promote",
    "script_qa",
    "scenes",
    "scenes_promote",
    "scenes_qa",
    # Long-form visual-span planning (report-only sidecar; never alters render).
    "visual_spans",
    "seo",
    "seo_promote",
    "seo_qa",
    # Long-form graphic scenes (checklist/warning/quote/cta) → ChatGPT images.
    "graphic_images",
    "thumbnail_image",
    # assets_chatgpt removed — default pipeline uses stock video (pexels_video provider).
    # Run assets_chatgpt manually via ▶ Run button if ChatGPT images needed.
    "whisper_timestamps",
    # Long-form compiled asset schedule (report-only sidecar; consumed by render
    # only when injected into render_props, gated by visual.span_planning.mode).
    "visual_schedule",
    # Long-form Elena presenter cue plan (report-only sidecar; same gating).
    "elena_plan",
    "render",
    # Long-form span-continuity QA on the rendered video (PASS-skips with no schedule).
    "render_continuity_qa",
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
    atomic_write_json(path, state.to_dict())
    return path


def load_job(job_dir: Path) -> JobState:
    path = job_dir / JOB_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    return JobState.from_dict(payload)
