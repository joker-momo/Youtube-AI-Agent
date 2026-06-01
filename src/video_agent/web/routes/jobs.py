from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from video_agent.orchestrator.browser_client import BrowserClient
from video_agent.web.routes._legacy_mount import include_legacy_routes
from video_agent.web.routes._legacy import get_browser_client, get_jobs_root
from video_agent.web.services.video_job_creator import (
    create_job_from_full_idea,
    create_job_from_title_seed,
)

router = APIRouter()


class CreateJobFromIdeaTitleRequest(BaseModel):
    channel_id: str = "vida-plena-45"
    title_seed: str
    job_id: str | None = None
    duration_mode: str = "auto"
    target_duration_sec: int | None = None
    min_duration_sec: int = 360
    max_duration_sec: int = 1200
    run_now: bool = True
    enforce_approvals: bool = False
    notes: str | None = None
    check_duplicates: bool = True
    duplicate_policy: str = "rewrite_angle"
    max_existing_videos: int = 100


class CreateJobFromIdeaRequest(BaseModel):
    channel_id: str = "vida-plena-45"
    idea: dict
    job_id: str | None = None
    run_now: bool = True
    enforce_approvals: bool = False
    check_duplicates: bool = True
    duplicate_policy: str = "block"
    max_existing_videos: int = 100


@router.post("/jobs/from-idea-title", status_code=201)
async def post_job_from_idea_title(
    payload: CreateJobFromIdeaTitleRequest,
    jobs_root: Path = Depends(get_jobs_root),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    return await create_job_from_title_seed(
        channel_id=payload.channel_id,
        title_seed=payload.title_seed,
        job_id=payload.job_id,
        duration_mode=payload.duration_mode,
        target_duration_sec=payload.target_duration_sec,
        min_duration_sec=payload.min_duration_sec,
        max_duration_sec=payload.max_duration_sec,
        run_now=payload.run_now,
        enforce_approvals=payload.enforce_approvals,
        duplicate_policy=payload.duplicate_policy,
        check_duplicates=payload.check_duplicates,
        max_existing_videos=payload.max_existing_videos,
        notes=payload.notes,
        jobs_root=jobs_root,
        browser_client=client,
    )


@router.post("/jobs/from-idea", status_code=201)
async def post_job_from_idea(
    payload: CreateJobFromIdeaRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    return await create_job_from_full_idea(
        channel_id=payload.channel_id,
        idea=payload.idea,
        job_id=payload.job_id,
        run_now=payload.run_now,
        enforce_approvals=payload.enforce_approvals,
        duplicate_policy=payload.duplicate_policy,
        check_duplicates=payload.check_duplicates,
        max_existing_videos=payload.max_existing_videos,
        jobs_root=jobs_root,
    )


def _is_jobs_route(route: object) -> bool:
    path = getattr(route, "path", "")
    methods = set(getattr(route, "methods", []) or [])
    if path == "/jobs":
        return True
    if path == "/jobs/{job_id}" and methods:
        return True
    return path in {
        "/jobs/{job_id}/stop",
        "/jobs/{job_id}/advance",
        "/jobs/{job_id}/events",
        "/jobs/{job_id}/idea",
    } and methods


include_legacy_routes(router, _is_jobs_route)
