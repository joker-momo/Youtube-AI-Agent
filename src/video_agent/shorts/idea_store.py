from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_short_ideas(long_job_dir: Path, ideas_doc: dict) -> None:
    path = paths.short_ideas_path(long_job_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, ideas_doc)


def read_short_ideas(long_job_dir: Path) -> dict[str, Any]:
    return _read(paths.short_ideas_path(long_job_dir))


def write_selected_ideas(long_job_dir: Path, selected: dict) -> None:
    path = paths.selected_short_ideas_path(long_job_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, selected)


def read_selected_ideas(long_job_dir: Path) -> dict[str, Any]:
    return _read(paths.selected_short_ideas_path(long_job_dir))


def write_idea_generation_run(long_job_dir: Path, run: dict) -> None:
    path = paths.idea_generation_run_path(long_job_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, run)


def read_idea_generation_run(long_job_dir: Path) -> dict[str, Any]:
    return _read(paths.idea_generation_run_path(long_job_dir))


def write_studio_render_run(long_job_dir: Path, run: dict) -> None:
    path = paths.studio_render_run_path(long_job_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, run)


def read_studio_render_run(long_job_dir: Path) -> dict[str, Any]:
    return _read(paths.studio_render_run_path(long_job_dir))
