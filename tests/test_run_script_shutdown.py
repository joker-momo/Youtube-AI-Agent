from __future__ import annotations

from pathlib import Path


def test_shutdown_cleans_project_child_processes() -> None:
    script = Path("installers/run.sh").read_text()
    assert 'pkill -f "uvicorn video_agent.web.app:app"' in script
    assert 'pkill -f "uvicorn video_agent.browser_worker.app:app"' in script
    assert 'pkill -f "video_agent.cli worker --db-path jobs/queue.db"' in script
    assert 'pkill -f "vlm_worker.py"' in script
    assert 'pkill -f "${REPO_DIR}/remotion"' in script
    assert 'rm -f "${pidfile}"' in script
