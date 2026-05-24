from __future__ import annotations

from fastapi import APIRouter

from video_agent.web.routes._legacy import flatten_keyword_result_for_ui
from video_agent.web.routes._legacy_mount import include_legacy_routes

router = APIRouter()

include_legacy_routes(router, lambda route: getattr(route, "path", "").startswith("/channels"))

__all__ = ["flatten_keyword_result_for_ui", "router"]
