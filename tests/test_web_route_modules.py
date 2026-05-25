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


def test_dashboard_create_job_keeps_approval_gate_enabled():
    dashboard_path = Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / "dashboard.html"
    dashboard_text = dashboard_path.read_text(encoding="utf-8")

    assert "run-all?enforce_approvals=false" not in dashboard_text
    assert "/run-all" in dashboard_text


def test_dashboard_renders_v2_keyword_metadata():
    dashboard_path = Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / "dashboard.html"
    dashboard_text = dashboard_path.read_text(encoding="utf-8")

    assert "keyword_final_score" in dashboard_text
    assert "intent_cluster" in dashboard_text
    assert "thumbnail_hook_options" in dashboard_text
    assert "audience_fit" in dashboard_text


def test_dashboard_idea_v2_badges_are_wrappable():
    dashboard_path = Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / "dashboard.html"
    dashboard_text = dashboard_path.read_text(encoding="utf-8")

    assert ".idea-score-row { display:flex; align-items:center; gap:6px; flex-wrap:wrap;" in dashboard_text
    assert "overflow-wrap:anywhere" in dashboard_text
    assert "function bucketLabel" in dashboard_text


def test_app_service_mounts_inputs_for_generated_ideas():
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    compose_text = compose_path.read_text(encoding="utf-8")

    app_block = compose_text.split("  app:", 1)[1].split("\n  worker:", 1)[0]
    assert "./inputs:/app/inputs" in app_block
