"""Canonical storage-layout paths for the Shorts autopilot.

Single source of truth so no module hardcodes ``jobs/<id>/shorts/...`` strings.
"""
from __future__ import annotations

from pathlib import Path

SHORTS_DIRNAME = "shorts"
ARCHIVE_DIRNAME = "archive"

MANIFEST_FILE = "shorts_manifest.json"
PLAN_FILE = "shorts_plan.json"
AUTOPILOT_RUN_FILE = "autopilot_run.json"
STUDIO_RENDER_RUN_FILE = "studio_render_run.json"
SOURCE_SNAPSHOT_FILE = "source_snapshot.json"
AUTOPILOT_LOCK_FILE = ".autopilot.lock"
IDEA_GENERATION_RUN_FILE = "idea_generation_run.json"
SHORT_IDEAS_FILE = "short_ideas.json"
SELECTED_SHORT_IDEAS_FILE = "selected_short_ideas.json"
IDEA_GENERATION_LOCK_FILE = ".ideas.lock"
RENDER_SELECTED_LOCK_FILE = ".render-selected.lock"

SHORT_STATUS_FILE = "short_status.json"
SHORT_IDEA_FILE = "short_idea.json"
SHORT_SCRIPT_FILE = "short_script.json"
SHORT_SCENES_FILE = "short_scenes.json"
SHORT_SOURCE_MAP_FILE = "short_source_map.json"
SHORT_SEO_FILE = "short_seo.json"
SHORT_QA_FILE = "short_qa.json"
SHORT_SCRIPT_QA_FILE = "short_script_qa.json"
SHORT_SCENES_QA_FILE = "short_scenes_qa.json"
SHORT_RENDER_PROPS_FILE = "short_render_props.json"
SHORT_COVER_FILE = "short_cover.jpg"
SHORT_VIDEO_FILE = "short.mp4"
SHORT_REPORT_FILE = "short_report.md"
SHORT_LOCK_FILE = ".short.lock"


def shorts_dir(long_job_dir: Path) -> Path:
    return long_job_dir / SHORTS_DIRNAME


def archive_dir(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / ARCHIVE_DIRNAME


def short_dir(long_job_dir: Path, short_id: str) -> Path:
    return shorts_dir(long_job_dir) / short_id


def short_tmp_dir(long_job_dir: Path, short_id: str) -> Path:
    return short_dir(long_job_dir, short_id) / "tmp"


def short_audio_dir(long_job_dir: Path, short_id: str) -> Path:
    return short_dir(long_job_dir, short_id) / "audio"


def manifest_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / MANIFEST_FILE


def plan_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / PLAN_FILE


def autopilot_run_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / AUTOPILOT_RUN_FILE


def source_snapshot_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / SOURCE_SNAPSHOT_FILE


def autopilot_lock_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / AUTOPILOT_LOCK_FILE


def idea_generation_lock_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / IDEA_GENERATION_LOCK_FILE


def render_selected_lock_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / RENDER_SELECTED_LOCK_FILE


def short_status_path(long_job_dir: Path, short_id: str) -> Path:
    return short_dir(long_job_dir, short_id) / SHORT_STATUS_FILE


def short_lock_path(long_job_dir: Path, short_id: str) -> Path:
    return short_dir(long_job_dir, short_id) / SHORT_LOCK_FILE


def short_ideas_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / SHORT_IDEAS_FILE


def selected_short_ideas_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / SELECTED_SHORT_IDEAS_FILE


def idea_generation_run_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / IDEA_GENERATION_RUN_FILE


def studio_render_run_path(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / STUDIO_RENDER_RUN_FILE
