from __future__ import annotations

import os
import shutil
from pathlib import Path


def prepare_public_job_dir(root_dir: Path, job_id: str) -> Path:
    root = root_dir / "remotion" / "public" / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    job_dir = root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _prune_public_jobs(root, keep=_public_jobs_keep(), current=job_dir)
    return job_dir


def _public_jobs_keep() -> int:
    raw = os.environ.get("PUBLIC_JOBS_KEEP", "5")
    try:
        return max(1, int(raw))
    except ValueError:
        return 5


def _prune_public_jobs(root: Path, *, keep: int, current: Path) -> None:
    dirs = [path for path in root.iterdir() if path.is_dir()]
    dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    kept = 0
    for path in dirs:
        if path == current:
            kept += 1
            continue
        if kept < keep:
            kept += 1
            continue
        shutil.rmtree(path, ignore_errors=True)
