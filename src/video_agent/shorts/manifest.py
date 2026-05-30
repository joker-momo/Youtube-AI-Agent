"""Atomic readers/writers for Shorts manifest, autopilot run, and per-short status."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(long_job_dir: Path, data: dict[str, Any]) -> Path:
    p = paths.manifest_path(long_job_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, data)
    return p


def read_manifest(long_job_dir: Path) -> dict[str, Any]:
    return _read(paths.manifest_path(long_job_dir))


def write_autopilot_run(long_job_dir: Path, data: dict[str, Any]) -> Path:
    p = paths.autopilot_run_path(long_job_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, data)
    return p


def read_autopilot_run(long_job_dir: Path) -> dict[str, Any]:
    return _read(paths.autopilot_run_path(long_job_dir))


def write_plan(long_job_dir: Path, data: dict[str, Any]) -> Path:
    p = paths.plan_path(long_job_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, data)
    return p


def write_short_status(long_job_dir: Path, short_id: str, data: dict[str, Any]) -> Path:
    p = paths.short_status_path(long_job_dir, short_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, data)
    return p


def read_short_status(long_job_dir: Path, short_id: str) -> dict[str, Any]:
    return _read(paths.short_status_path(long_job_dir, short_id))
