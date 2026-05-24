from __future__ import annotations

from fastapi import APIRouter

from video_agent.web.routes._legacy_mount import include_legacy_routes

router = APIRouter()


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
