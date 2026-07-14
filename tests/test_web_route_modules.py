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


def test_dashboard_compact_pipeline_uses_animated_flow_with_full_labels():
    dashboard_path = Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / "dashboard.html"
    dashboard_text = dashboard_path.read_text(encoding="utf-8")

    assert "@keyframes stageFlow" in dashboard_text
    assert "step-flow-dot" in dashboard_text
    assert "stage-flow-link" in dashboard_text
    assert "title=\"${escapeHtml(stageTitle)}\"" in dashboard_text


def test_dashboard_filters_blocked_keyword_related_keywords_from_insights():
    dashboard_path = Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / "dashboard.html"
    dashboard_text = dashboard_path.read_text(encoding="utf-8")

    assert "function isBlockedKeywordLabel" in dashboard_text
    assert "best camera settings" in dashboard_text
    assert ".filter(r => !isBlockedKeywordLabel" in dashboard_text


def test_dashboard_adds_idea_from_title_and_full_idea_job_actions():
    dashboard_path = Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / "dashboard.html"
    dashboard_text = dashboard_path.read_text(encoding="utf-8")

    assert "Create video from idea" in dashboard_text
    # Title input now produces an enriched idea (Idea Generator card), not a job.
    assert "ideas/from-title" in dashboard_text
    assert "/jobs/from-idea-title" not in dashboard_text
    # The video is still created via the per-idea Run Job action.
    assert "/jobs/from-idea" in dashboard_text
    assert "Run Job" in dashboard_text
    assert "id=\"itj-status\"" in dashboard_text
    assert "function isActuallyRunning" in dashboard_text
    assert "click Run Job on its card to make the video" in dashboard_text


def test_shorts_studio_includes_panel_and_api_hooks():
    shorts_studio_path = Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / "shorts_studio.html"
    shorts_studio_text = shorts_studio_path.read_text(encoding="utf-8")

    assert "Shorts Studio" in shorts_studio_text
    assert "id=\"shorts-page\"" in shorts_studio_text
    assert "fetch('/shorts-studio/state')" in shorts_studio_text
    assert "/shorts-studio/jobs/" in shorts_studio_text
    assert "const ideasStillLoading = container.textContent.includes('Loading ideas')" in shorts_studio_text
    assert "ideasJson === LAST_IDEAS_JSON_BY_JOB[jobId] && !ideasStillLoading" in shorts_studio_text


def test_dashboard_has_link_to_shorts_studio():
    dashboard_path = Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / "dashboard.html"
    dashboard_text = dashboard_path.read_text(encoding="utf-8")

    assert "href=\"/shorts-studio\"" in dashboard_text


def _read_html(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "src" / "video_agent" / "web" / name).read_text(
        encoding="utf-8"
    )


def test_dashboard_create_from_idea_requires_title_and_description():
    """The Create-video-from-idea modal must collect BOTH inputs and submit both
    to the required endpoint, with a client-side description-required guard."""
    html = _read_html("dashboard.html")
    assert 'id="itj-description"' in html
    assert '<label for="itj-description">Description</label>' in html
    assert "const description = document.getElementById('itj-description').value.trim()" in html
    assert "Enter a description first." in html
    assert "JSON.stringify({title_seed: titleSeed, description})" in html
    # Existing title contract preserved.
    assert "const titleSeed = document.getElementById('itj-title').value.trim()" in html
    assert "/ideas/from-title" in html


def test_shorts_studio_from_title_caller_submits_description():
    """The Shorts Studio caller of the same endpoint must also send description
    with a blank guard, so the strengthened API can't silently break it."""
    html = _read_html("shorts_studio.html")
    assert "document.getElementById('itj-description')" in html
    assert "Enter a description first." in html
    assert "JSON.stringify({title_seed: titleSeed, description})" in html
    assert "/ideas/from-title" in html


def test_both_from_title_clients_send_required_description_no_fabricated_default():
    """Both static clients reference the endpoint and submit the operator's own
    description — neither substitutes a fabricated fallback description."""
    for name in ("dashboard.html", "shorts_studio.html"):
        html = _read_html(name)
        assert "/ideas/from-title" in html
        assert "title_seed: titleSeed, description" in html
        # No hard-coded default description slipped into the payload.
        assert "description: '" not in html
        assert 'description: "' not in html
