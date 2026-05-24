from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from video_agent.web.routes import (
    approvals,
    artifacts,
    channels,
    config,
    jobs,
    run,
    stages,
    timeline,
    websocket,
)
from video_agent.web.routes._legacy import (
    _env_example_path,
    _env_path,
    dashboard as _dashboard,
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


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return _dashboard()


for router_module in (
    config,
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
]
