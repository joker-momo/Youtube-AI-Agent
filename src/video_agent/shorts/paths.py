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

SHORT_STATUS_FILE = "short_status.json"     # root — job state marker
SHORT_IDEA_FILE = "short_idea.json"
SHORT_SCRIPT_FILE = "short_script.json"
SHORT_SCENES_FILE = "short_scenes.json"
SHORT_SOURCE_MAP_FILE = "short_source_map.json"
SHORT_SEO_FILE = "short_seo.json"
SHORT_QA_FILE = "short_qa.json"
SHORT_SCRIPT_QA_FILE = "short_script_qa.json"
SHORT_SCENES_QA_FILE = "short_scenes_qa.json"
SHORT_SCENE_STRUCTURE_FILE = "short_scene_structure.json"
SHORT_SCENE_REPAIR_FILE = "short_scene_repair.json"
SHORT_RETENTION_PLAN_FILE = "retention_plan.json"
SHORT_HUMANIZATION_FILE = "spoken_humanization.json"
SHORT_VISUAL_RHYTHM_FILE = "visual_rhythm_plan.json"
SHORT_ANTI_AI_REVIEW_FILE = "anti_ai_review.json"
SHORT_PERFORMANCE_MEMORY_FILE = "performance_memory.json"
SHORT_AUDIO_SYNC_SUMMARY_FILE = "audio_sync_summary.json"
SHORT_CALL_BUDGET_SUMMARY_FILE = "call_budget_summary.json"
SHORT_FAILURE_REPORT_FILE = "short_failure_report.json"
SHORT_QA_DECISION_SUMMARY_FILE = "qa_decision_summary.json"
SHORT_RENDER_PROPS_FILE = "short_render_props.json"
SHORT_LLM_HISTORY_FILE = "llm_history.jsonl"  # all ChatGPT + Gemini prompts/responses
SHORT_COVER_FILE = "short_cover.jpg"
SHORT_THUMBNAIL_FILE = "thumbnail.jpg"
SHORT_VIDEO_FILE = "short.mp4"
SHORT_REPORT_FILE = "short_report.md"
SHORT_LOCK_FILE = ".short.lock"

# Sub-directory names (mirrors long-form job layout)
SHORT_JSON_SUBDIR = "json"
SHORT_OUTPUTS_SUBDIR = "outputs"


def shorts_dir(long_job_dir: Path) -> Path:
    return long_job_dir / SHORTS_DIRNAME


def archive_dir(long_job_dir: Path) -> Path:
    return shorts_dir(long_job_dir) / ARCHIVE_DIRNAME


def short_dir(long_job_dir: Path, short_id: str) -> Path:
    return shorts_dir(long_job_dir) / short_id


def short_tmp_dir(long_job_dir: Path, short_id: str) -> Path:
    return short_dir(long_job_dir, short_id) / "tmp"


def short_json_dir(long_job_dir: Path, short_id: str) -> Path:
    """json/ subdirectory inside a short folder (data/config files)."""
    return short_dir(long_job_dir, short_id) / SHORT_JSON_SUBDIR


def short_outputs_dir(long_job_dir: Path, short_id: str) -> Path:
    """outputs/ subdirectory inside a short folder (deliverable files)."""
    return short_dir(long_job_dir, short_id) / SHORT_OUTPUTS_SUBDIR


def resolve_short_json(sd: Path, filename: str) -> Path:
    """Read-path helper: prefer sd/json/filename, fall back to sd/filename."""
    preferred = sd / SHORT_JSON_SUBDIR / filename
    return preferred if preferred.exists() else sd / filename


def resolve_short_output(sd: Path, filename: str) -> Path:
    """Read-path helper: prefer sd/outputs/filename, fall back to sd/filename."""
    preferred = sd / SHORT_OUTPUTS_SUBDIR / filename
    return preferred if preferred.exists() else sd / filename


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


def slugify(text: str) -> str:
    """Normalize and slugify a title or text to a clean URL-friendly string of max 20 chars."""
    import unicodedata
    import re
    if not text:
        return "short"
    normalized = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    slug = normalized.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s-]+', '-', slug).strip('-')
    return slug[:20].strip('-') or "short"
