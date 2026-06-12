from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from video_agent.web.routes import (
    approvals,
    artifacts,
    channels,
    config,
    dashboard,
    jobs,
    run,
    shorts,
    shorts_studio,
    stages,
    timeline,
    websocket,
)
from video_agent.web.routes._legacy import (
    _env_example_path,
    _env_path,
    flatten_keyword_result_for_ui,
    get_browser_client,
    get_channel_path,
    get_inputs_root,
    get_jobs_root,
)

app = FastAPI(title="video-agent-web", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "app"}


# shorts first: its explicit GET /jobs/{id}/shorts must beat the legacy
# catch-all GET /jobs/{job_id}/{path:path} artifact route.
for router_module in (
    shorts,
    shorts_studio,
    config,
    dashboard,
    jobs,
    timeline,
    stages,
    approvals,
    channels,
    run,
    websocket,
    artifacts,
):
    app.include_router(router_module.router)

__all__ = [
    "app",
    "_env_example_path",
    "_env_path",
    "flatten_keyword_result_for_ui",
    "get_browser_client",
    "get_channel_path",
    "get_inputs_root",
    "get_jobs_root",
    "dashboard",
]
