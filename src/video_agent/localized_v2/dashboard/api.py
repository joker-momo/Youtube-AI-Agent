from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from video_agent.localized_v2.dashboard.schemas import (
    ChannelListResponse,
    CreateJobRequest,
    HealthResponse,
    SessionResponse,
)
from video_agent.localized_v2.dashboard.service import (
    JOB_ID_PATTERN,
    DashboardError,
    DashboardService,
)

ARTIFACT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _job_id(value: str) -> str:
    if not JOB_ID_PATTERN.fullmatch(value):
        raise DashboardError(
            422,
            "VALIDATION_ERROR",
            "Invalid localized V2 job ID.",
        )
    return value


def build_router(service: DashboardService) -> APIRouter:
    router = APIRouter(prefix="/api/v2")

    @router.get("/session", response_model=SessionResponse, response_model_by_alias=True)
    def session(request: Request) -> dict[str, str]:
        return {"csrfToken": request.app.state.csrf_token}

    @router.get("/health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return service.health()

    @router.get(
        "/channels",
        response_model=ChannelListResponse,
        response_model_by_alias=True,
    )
    def channels() -> dict[str, Any]:
        return {"data": service.list_channels()}

    @router.get("/jobs")
    def jobs(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
        status: Annotated[str | None, Query(max_length=32)] = None,
    ) -> dict[str, Any]:
        return service.list_jobs(page=page, page_size=page_size, status=status)

    @router.post("/jobs", status_code=201)
    def create_job(payload: CreateJobRequest) -> dict[str, Any]:
        return service.create_job(
            payload.channel_id,
            payload.topic,
            payload.description,
        )

    @router.get("/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        return service.get_job(_job_id(job_id))

    @router.get("/jobs/{job_id}/events")
    def events(job_id: str) -> dict[str, Any]:
        return {"data": service.list_events(_job_id(job_id))}

    @router.get("/jobs/{job_id}/artifacts")
    def artifacts(job_id: str) -> dict[str, Any]:
        return {"data": service.list_artifacts(_job_id(job_id))}

    @router.get("/jobs/{job_id}/artifacts/{name}")
    def artifact(job_id: str, name: str) -> FileResponse:
        job_id = _job_id(job_id)
        if not ARTIFACT_NAME_PATTERN.fullmatch(name):
            raise DashboardError(
                422,
                "VALIDATION_ERROR",
                "Invalid localized V2 artifact name.",
            )
        download = service.artifact_download(job_id, name)
        return FileResponse(
            download.path,
            media_type=download.media_type,
            filename=download.filename,
        )

    @router.post("/jobs/{job_id}/cancellation")
    def cancel(job_id: str) -> dict[str, Any]:
        return service.cancel(_job_id(job_id))

    @router.post("/jobs/{job_id}/retry-attempts")
    def retry(job_id: str) -> dict[str, Any]:
        return service.retry(_job_id(job_id))

    @router.post("/jobs/{job_id}/resume-attempts")
    def resume(job_id: str) -> dict[str, Any]:
        return service.resume(_job_id(job_id))

    return router
