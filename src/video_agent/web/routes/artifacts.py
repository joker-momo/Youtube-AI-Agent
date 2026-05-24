from __future__ import annotations

from fastapi import APIRouter

from video_agent.web.routes._legacy_mount import include_legacy_routes

router = APIRouter()

include_legacy_routes(
    router,
    lambda route: getattr(route, "path", "") in {
        "/jobs/{job_id}/artifact",
        "/jobs/{job_id}/{path:path}",
    },
)
