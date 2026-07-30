from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from .dashboard_support import DASHBOARD_BASE_URL, make_dashboard

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_v2_app_imports_no_legacy_dashboard_queue_or_worker() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys;"
                "import video_agent.localized_v2.dashboard.app;"
                "blocked=('video_agent.web','video_agent.orchestrator.queue',"
                "'video_agent.orchestrator.worker');"
                "print(json.dumps([name for name in blocked if name in sys.modules]))"
            ),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == []


def test_v2_routes_are_not_mounted_on_current_dashboard_surface(tmp_path: Path) -> None:
    context = make_dashboard(tmp_path)
    routes = {route.path for route in context.app.routes}

    assert "/api/v2/jobs" in routes
    assert "/api/jobs" not in routes
    assert "/shorts-studio" not in routes
    assert context.paths.queue_db.name == "queue-v2.db"


def test_v2_dashboard_lists_only_v2_jobs(tmp_path: Path) -> None:
    context = make_dashboard(tmp_path)
    legacy_job = tmp_path / "legacy-jobs" / "same-id"
    legacy_job.mkdir()
    (legacy_job / "job.json").write_text('{"job_id":"same-id"}', encoding="utf-8")
    client = TestClient(context.app, base_url=DASHBOARD_BASE_URL)
    csrf = client.get("/api/v2/session").json()["csrfToken"]

    created = client.post(
        "/api/v2/jobs",
        headers={"Origin": DASHBOARD_BASE_URL, "X-CSRF-Token": csrf},
        json={"channelId": "healthy-life-en", "topic": "Only V2"},
    ).json()
    jobs = client.get("/api/v2/jobs").json()["data"]

    assert [job["jobId"] for job in jobs] == [created["jobId"]]
    assert (legacy_job / "job.json").is_file()
