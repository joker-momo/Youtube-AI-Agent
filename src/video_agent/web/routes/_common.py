"""Shared helpers for per-resource route modules.

These functions were extracted from ``_legacy.py`` and are used by multiple
sub-modules.  ``_legacy.py`` re-exports every name from here to maintain
backward-compatible imports.
"""
from __future__ import annotations

import asyncio
import re
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel

from video_agent.orchestrator.browser_client import (
    BrowserClient,
    BrowserClientError,
    LoginRequiredFromWorker,
)
from video_agent.orchestrator.queue import JobQueue as _JobQueue

# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class CreateJobRequest(BaseModel):
    job_id: str
    channel_id: str
    idea_path: str


class RawScriptRequest(BaseModel):
    raw_response: str


class GenerateIdeasRequest(BaseModel):
    seed_topics: list[str] = []
    count: int = 10


class IdeaFromTitleRequest(BaseModel):
    title_seed: str


class ScoreIdeasRequest(BaseModel):
    ideas: list[dict]


class RunBatchRequest(BaseModel):
    job_ids: list[str]
    enforce_approvals: bool = False


# ---------------------------------------------------------------------------
# Security / path helpers
# ---------------------------------------------------------------------------

_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def get_jobs_root() -> Path:
    return Path(os.environ.get("JOBS_DIR", "/app/jobs"))


def get_channel_path() -> Path:
    return Path(
        os.environ.get(
            "CHANNEL_CONFIG",
            "/app/configs/vida-plena-45/channel.yaml",
        )
    )


def get_inputs_root() -> Path:
    return Path(os.environ.get("INPUTS_DIR", "/app/inputs"))


def _safe_channel_id(channel_id: str) -> str:
    if not channel_id or not _SAFE_CHANNEL_ID_RE.match(channel_id):
        raise HTTPException(status_code=400, detail=f"Invalid channel_id: {channel_id!r}")
    return channel_id


def _safe_job_dir(jobs_root: Path, job_id: str) -> Path:
    """Validate ``job_id`` and return the resolved job directory inside ``jobs_root``.

    Rejects any id that contains path separators, parent refs, NUL, or that
    resolves outside ``jobs_root``. Raises HTTPException(400) on violation.
    """
    if not job_id or not _SAFE_JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail=f"Invalid job_id: {job_id!r}")
    candidate = (jobs_root / job_id).resolve()
    try:
        candidate.relative_to(jobs_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid job_id: {job_id!r}") from exc
    return candidate


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------


def _enqueue_stage_command(
    *,
    job_id: str,
    jobs_root: Path,
    command: str,
    payload: dict[str, Any] | None = None,
) -> dict:
    queue = _JobQueue(jobs_root / "queue.db")
    queue.enqueue(job_id, enforce_approvals=False, command=command, payload=payload)
    return {"job_id": job_id, "status": "enqueued", "command": command}


def _queue_status(jobs_root: Path, job_id: str, job_dir: Path | None = None) -> str | None:
    from video_agent.web.run_all_pipeline import is_run_locked

    try:
        if job_dir is not None and is_run_locked(job_dir):
            return "running"
        queue = _JobQueue(jobs_root / "queue.db")
        row = queue.get_job(job_id)
        if row and row.get("status") == "running" and queue.is_running_stale(job_id):
            return "stale"
        return str(row.get("status")) if row and row.get("status") else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Browser client helpers
# ---------------------------------------------------------------------------


def get_browser_client() -> BrowserClient:
    """FastAPI dependency: returns the BrowserClient used by auto stages.

    Tests override this with a fake to avoid hitting the real
    ``browser-worker`` container.
    """
    return BrowserClient()


def _handle_browser_client_error(exc: BrowserClientError) -> HTTPException:
    if isinstance(exc, LoginRequiredFromWorker):
        return HTTPException(
            status_code=409,
            detail={
                "error": str(exc),
                "browser_worker_detail": exc.detail,
                "login_required": True,
            },
        )
    return HTTPException(
        status_code=502,
        detail={
            "error": str(exc),
            "browser_worker_status": exc.status_code,
            "browser_worker_detail": exc.detail,
        },
    )


def _one_shot_with_briefing(
    client: BrowserClient,
    site: str,
    kind: str,
    channel_path: Path,
    job_dir: Path,
):
    """Wrap ``client.run_session`` so the one-shot tab receives the
    initial briefing as its first message and the task as the second.

    Used by the per-stage ``/stages/X/auto`` routes which create a
    fresh tab per request (no shared context with previous calls).
    The /run-all flow uses ``open_persistent_session`` instead so the
    briefing is sent only once for the whole pipeline.
    """
    from video_agent.orchestrator.briefing import build_initial_briefing
    from video_agent.orchestrator.orchestrator import StageError  # noqa: F401 (re-exported)
    from video_agent.utils.json_io import read_yaml as _read_yaml
    from video_agent.orchestrator import load_job

    channel_config = _read_yaml(channel_path)
    state = load_job(job_dir)
    briefing = build_initial_briefing(
        channel_config,
        kind=kind,
        job_id=state.job_id,
        channel_id=state.channel_id,
    )

    async def fn(msgs):
        return await client.run_session(site, [briefing] + list(msgs))

    return fn


# ---------------------------------------------------------------------------
# Idea generator helpers (pure functions, not routes)
# ---------------------------------------------------------------------------


def flatten_keyword_result_for_ui(top_keywords):
    if isinstance(top_keywords, list):
        return top_keywords
    if isinstance(top_keywords, dict):
        return (
            top_keywords.get("top_opportunity_keywords", [])
            + top_keywords.get("long_tail_test_keywords", [])
            + top_keywords.get("rejected_keywords", [])
        )
    return []


def keyword_result_summary(keyword_result) -> dict:
    if isinstance(keyword_result, dict):
        top = keyword_result.get("top_opportunity_keywords", [])
        long_tail = keyword_result.get("long_tail_test_keywords", [])
        rejected = keyword_result.get("rejected_keywords", [])
        all_scored = keyword_result.get("all_scored_keywords", [])
        metadata = keyword_result.get("metadata", {})
        return {
            "total_scanned": len(all_scored) or (len(top) + len(long_tail) + len(rejected)),
            "total_top_opportunity": len(top),
            "total_long_tail": len(long_tail),
            "total_rejected": len(rejected),
            "target_language": metadata.get("target_language", "spanish"),
            "target_audience": metadata.get("target_audience", "people_45_plus"),
            "keyword_scoring_version": metadata.get("version", "legacy"),
            "serp_inspection": metadata.get("serp_inspection", "unknown"),
        }
    if isinstance(keyword_result, list):
        return {
            "total_scanned": len(keyword_result),
            "total_top_opportunity": len(keyword_result),
            "total_long_tail": 0,
            "total_rejected": 0,
            "target_language": "unknown",
            "target_audience": "unknown",
            "keyword_scoring_version": "legacy",
            "serp_inspection": "unknown",
        }
    return {
        "total_scanned": 0,
        "total_top_opportunity": 0,
        "total_long_tail": 0,
        "total_rejected": 0,
        "target_language": "unknown",
        "target_audience": "unknown",
        "keyword_scoring_version": "unknown",
        "serp_inspection": "unknown",
    }


def keyword_score_payload(kw_data: dict, target_kw: str, summary: dict, match_source: str) -> dict:
    return {
        "target_keyword": kw_data.get("keyword", target_kw),
        "keyword_source_score": kw_data.get("keyword_source_score", kw_data.get("score")),
        "keyword_final_score": kw_data.get("final_score", kw_data.get("score")),
        "intent_cluster": kw_data.get("intent_cluster"),
        "audience_fit": kw_data.get("audience_fit"),
        "intent_strength": kw_data.get("intent_strength"),
        "content_fit": kw_data.get("content_fit"),
        "language_fit": kw_data.get("language_fit"),
        "serp_opportunity": kw_data.get("serp_opportunity"),
        "bucket": kw_data.get("bucket"),
        "recommended_angle": kw_data.get("recommended_angle"),
        "thumbnail_hook_options": kw_data.get("thumbnail_hook_options", []),
        "keyword_notes": kw_data.get("notes", []),
        "keyword_rejection_reasons": kw_data.get("rejection_reasons", []),
        "keyword_scoring_version": summary.get("keyword_scoring_version"),
        "keyword_match_source": match_source,
    }


def _keyword_is_safe_for_ui(keyword: str, channel_config: dict) -> bool:
    from video_agent.orchestrator.idea_generator import (
        detect_language_fit,
        merge_keyword_channel_config,
        score_content_fit,
    )

    text = str(keyword or "").strip()
    if not text:
        return False
    scoring_cfg = merge_keyword_channel_config(channel_config)
    language_fit, _notes = detect_language_fit(text, scoring_cfg["target_language"])
    if language_fit < 80:
        return False
    return score_content_fit(text, scoring_cfg) >= 60


def _filter_keyword_result_for_ui(keyword_result, channel_config: dict):
    def clean_item(item: dict) -> dict | None:
        if not _keyword_is_safe_for_ui(item.get("keyword", ""), channel_config):
            return None
        cleaned = dict(item)
        if isinstance(cleaned.get("related"), list):
            related = []
            for raw in cleaned["related"]:
                if isinstance(raw, str):
                    kw = raw
                    value = raw
                elif isinstance(raw, dict):
                    kw = raw.get("keyword") or raw.get("term") or ""
                    value = dict(raw)
                else:
                    continue
                if _keyword_is_safe_for_ui(kw, channel_config):
                    related.append(value)
            cleaned["related"] = related
        return cleaned

    if isinstance(keyword_result, list):
        filtered = []
        for item in keyword_result:
            if isinstance(item, dict):
                cleaned = clean_item(item)
                if cleaned is not None:
                    filtered.append(cleaned)
        return filtered
    if isinstance(keyword_result, dict):
        filtered = dict(keyword_result)
        for key in (
            "top_opportunity_keywords",
            "long_tail_test_keywords",
            "rejected_keywords",
            "all_scored_keywords",
        ):
            cleaned_items = []
            for item in keyword_result.get(key) or []:
                if isinstance(item, dict):
                    cleaned = clean_item(item)
                    if cleaned is not None:
                        cleaned_items.append(cleaned)
            filtered[key] = cleaned_items
        return filtered
    return keyword_result
