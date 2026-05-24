from __future__ import annotations

import importlib
from pathlib import Path


def test_web_app_is_small_and_routes_are_split():
    app_path = Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / "app.py"
    app_text = app_path.read_text(encoding="utf-8")

    assert len(app_text.splitlines()) < 220
    assert "include_router(_legacy.router)" not in app_text

    for module_name in [
        "video_agent.web.deps",
        "video_agent.web.models",
        "video_agent.web.routes.config",
        "video_agent.web.routes.jobs",
        "video_agent.web.routes.timeline",
        "video_agent.web.routes.artifacts",
        "video_agent.web.routes.stages",
        "video_agent.web.routes.approvals",
        "video_agent.web.routes.channels",
        "video_agent.web.routes.run",
        "video_agent.web.routes.websocket",
    ]:
        module = importlib.import_module(module_name)
        if ".routes." in module_name:
            assert hasattr(module, "router")
