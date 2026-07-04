from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import agent_bridge, agent_bridge_worker


def _bridge(root: Path, *args: str) -> int:
    return agent_bridge.main(["--root", str(root), *args])


def _worker(root: Path, *args: str) -> int:
    return agent_bridge_worker.main(["--root", str(root), *args])


def _task(root: Path, task_id: str) -> dict:
    return json.loads((root / ".agent/bridge/tasks" / f"{task_id}.json").read_text(encoding="utf-8"))


def _create_task(root: Path, *, command: str) -> str:
    _bridge(
        root,
        "report-bug",
        "--title",
        "Worker task",
        "--summary",
        "A worker should process this.",
        "--command",
        command,
    )
    return next((root / ".agent/bridge/tasks").glob("*.json")).stem


def test_status_counts_open_and_pending_verify_tasks(tmp_path: Path, capsys) -> None:
    task_id = _create_task(tmp_path, command=f"{sys.executable} -c 'print(\"ok\")'")
    _bridge(
        tmp_path,
        "reply",
        task_id,
        "--from",
        "claude",
        "--status",
        "fixed",
        "--message",
        "Fixed.",
        "--evidence",
        "pytest passed",
    )

    rc = _worker(tmp_path, "status")

    assert rc == 0
    out = capsys.readouterr().out
    assert "open_for_claude=0" in out
    assert "pending_codex_verify=1" in out


def test_verify_once_marks_passing_task_verified(tmp_path: Path) -> None:
    task_id = _create_task(tmp_path, command=f"{sys.executable} -c 'print(\"ok\")'")
    _bridge(
        tmp_path,
        "reply",
        task_id,
        "--from",
        "claude",
        "--status",
        "fixed",
        "--message",
        "Fixed.",
        "--evidence",
        "pytest passed",
    )

    rc = _worker(tmp_path, "verify-once")

    assert rc == 0
    task = _task(tmp_path, task_id)
    assert task["status"] == "verified"
    assert task["verified_by"] == "codex"
    assert (tmp_path / ".agent/bridge/logs").exists()


def test_verify_once_reopens_failing_task(tmp_path: Path) -> None:
    task_id = _create_task(tmp_path, command=f"{sys.executable} -c 'import sys; sys.exit(3)'")
    _bridge(
        tmp_path,
        "reply",
        task_id,
        "--from",
        "claude",
        "--status",
        "fixed",
        "--message",
        "Fixed.",
        "--evidence",
        "pytest passed",
    )

    rc = _worker(tmp_path, "verify-once")

    assert rc == 0
    task = _task(tmp_path, task_id)
    assert task["status"] == "open"
    assert task["awaiting_fix_by"] == "claude"
    assert "Automated Codex verifier found" in task["replies"][-1]["message"]


def test_claude_once_dry_run_writes_prompt_log(tmp_path: Path) -> None:
    task_id = _create_task(tmp_path, command=f"{sys.executable} -c 'print(\"ok\")'")

    rc = _worker(tmp_path, "claude-once", "--dry-run")

    assert rc == 0
    logs = sorted((tmp_path / ".agent/bridge/logs").glob(f"{task_id}-claude-dry-run-*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "Task JSON" in text
    assert f"agent_bridge.py reply {task_id}" in text
