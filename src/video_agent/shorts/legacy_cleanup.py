"""Detect and archive legacy Shorts artifacts.

The legacy ``orchestrator/shorts_stages.py`` workflow wrote bare
``jobs/<id>/shorts/<N>/`` folders without the new manifest/source-map/status
structure. The autopilot must never co-write into a legacy folder: it detects
legacy artifacts and, when forced, archives them before creating the clean
structure.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from video_agent.shorts import paths


def _is_new_structure(shorts_root: Path) -> bool:
    return (shorts_root / paths.MANIFEST_FILE).exists() or (
        shorts_root / paths.AUTOPILOT_RUN_FILE
    ).exists()


def detect_legacy_shorts(long_job_dir: Path) -> bool:
    """True when ``shorts/`` holds artifacts but no new manifest/run marker."""
    shorts_root = paths.shorts_dir(long_job_dir)
    if not shorts_root.exists():
        return False
    if _is_new_structure(shorts_root):
        return False
    # Any content other than an existing archive dir → legacy.
    for child in shorts_root.iterdir():
        if child.name == paths.ARCHIVE_DIRNAME:
            continue
        return True
    return False


def archive_legacy_shorts(long_job_dir: Path) -> Path:
    """Move all current ``shorts/`` content (except ``archive/``) into
    ``shorts/archive/legacy-<timestamp>/``. Returns the archive dir."""
    shorts_root = paths.shorts_dir(long_job_dir)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = paths.archive_dir(long_job_dir) / f"legacy-{ts}"
    dest.mkdir(parents=True, exist_ok=True)
    if shorts_root.exists():
        for child in shorts_root.iterdir():
            if child.name == paths.ARCHIVE_DIRNAME:
                continue
            shutil.move(str(child), str(dest / child.name))
    return dest
