from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    jobs: Path
    work: Path
    cache: Path
    logs: Path
    process_state: Path
    browser_profile: Path
    queue_db: Path
    published_titles: Path
    pid_file: Path
    lock_file: Path

    @classmethod
    def build(cls, root: Path, *, legacy_jobs_root: Path) -> RuntimePaths:
        resolved_root = root.resolve()
        legacy_root = legacy_jobs_root.resolve()
        if resolved_root == legacy_root or resolved_root.is_relative_to(legacy_root):
            raise ValueError("localized V2 runtime root must be outside the legacy jobs root")
        return cls(
            root=resolved_root,
            jobs=resolved_root / "jobs",
            work=resolved_root / "work",
            cache=resolved_root / "cache",
            logs=resolved_root / "logs",
            process_state=resolved_root / "process",
            browser_profile=resolved_root / "browser-profile",
            queue_db=resolved_root / "queue-v2.db",
            published_titles=resolved_root / "published-titles-v2.json",
            pid_file=resolved_root / "process" / "dashboard.pid",
            lock_file=resolved_root / "process" / "dashboard.lock",
        )

    def initialize(self) -> None:
        for path in (
            self.jobs,
            self.work,
            self.cache,
            self.logs,
            self.process_state,
            self.browser_profile,
        ):
            path.mkdir(parents=True, exist_ok=True)
