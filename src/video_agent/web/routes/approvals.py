from __future__ import annotations

from fastapi import APIRouter

from video_agent.web.routes._legacy_mount import include_legacy_routes

router = APIRouter()

include_legacy_routes(router, lambda route: "/approvals" in getattr(route, "path", ""))
