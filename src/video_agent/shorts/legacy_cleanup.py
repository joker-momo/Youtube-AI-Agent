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


_NEW_STRUCTURE_FILES = {
    paths.MANIFEST_FILE,
    paths.AUTOPILOT_RUN_FILE,
    paths.PLAN_FILE,
    paths.SOURCE_SNAPSHOT_FILE,
    paths.AUTOPILOT_LOCK_FILE,
}


def detect_legacy_shorts(long_job_dir: Path) -> bool:
    """True when ``shorts/`` holds artifacts but no new manifest/run marker."""
    shorts_root = paths.shorts_dir(long_job_dir)
    if not shorts_root.exists():
        return False
    if _is_new_structure(shorts_root):
        return False
    # Any content other than archive/ or new-structure infra → legacy. A bare
    # short-XX/ directory without manifest/autopilot_run is also legacy per the
    # migration rule.
    for child in shorts_root.iterdir():
        name = child.name
        if name == paths.ARCHIVE_DIRNAME:
            continue
        if name in _NEW_STRUCTURE_FILES:
            continue
        if child.is_dir() and name.startswith("short-"):
            has_new_short_marker = any(
                (child / marker).exists()
                for marker in (
                    paths.SHORT_STATUS_FILE,
                    paths.SHORT_SOURCE_MAP_FILE,
                    paths.SHORT_QA_FILE,
                )
            )
            if has_new_short_marker:
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


def archive_short_dir(long_job_dir: Path, short_id: str) -> Path:
    """Move one existing ``shorts/short-XX`` directory into archive.

    Used by the "Regenerate one Short" API so a targeted rebuild does not
    disturb other rendered Shorts or the top-level manifest/history.
    """
    src = paths.short_dir(long_job_dir, short_id)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = paths.archive_dir(long_job_dir) / f"{short_id}-{ts}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(dest))
    return dest
