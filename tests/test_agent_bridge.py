from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import agent_bridge


def _run(root: Path, *args: str) -> int:
    return agent_bridge.main(["--root", str(root), *args])


def test_report_bug_creates_task_and_claude_inbox(tmp_path: Path) -> None:
    rc = _run(
        tmp_path,
        "report-bug",
        "--from",
        "codex",
        "--to",
        "claude",
        "--severity",
        "P1",
        "--title",
        "Renderer contract failed",
        "--summary",
        "A generated card layout did not render as a card.",
        "--area",
        "remotion",
        "--file",
        "remotion/src/ChannelVideo.tsx",
        "--command",
        "cd remotion && npx tsc --noEmit",
        "--evidence",
        "line 123",
    )

    assert rc == 0
    tasks = sorted((tmp_path / ".agent/bridge/tasks").glob("*.json"))
    assert len(tasks) == 1
    task = json.loads(tasks[0].read_text(encoding="utf-8"))
    assert task["schema_version"] == agent_bridge.SCHEMA_VERSION
    assert task["reporter"] == "codex"
    assert task["assignee"] == "claude"
    assert task["status"] == "open"
    assert task["files"] == ["remotion/src/ChannelVideo.tsx"]

    inbox = tmp_path / task["inbox_file"]
    assert inbox.exists()
    text = inbox.read_text(encoding="utf-8")
    assert "Renderer contract failed" in text
    assert "agent_bridge.py reply" in text


def test_reply_updates_task_and_writes_codex_inbox(tmp_path: Path) -> None:
    _run(
        tmp_path,
        "report-bug",
        "--title",
        "Audit failed",
        "--summary",
        "A test failed.",
    )
    task_id = next((tmp_path / ".agent/bridge/tasks").glob("*.json")).stem

    rc = _run(
        tmp_path,
        "reply",
        task_id,
        "--from",
        "claude",
        "--status",
        "fixed",
        "--message",
        "Fixed root cause.",
        "--evidence",
        "pytest passed",
    )

    assert rc == 0
    task = json.loads((tmp_path / ".agent/bridge/tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
    assert task["status"] == "fixed"
    assert task["replies"][0]["from"] == "claude"
    replies = sorted((tmp_path / ".agent/bridge/codex/inbox").glob("*.md"))
    assert len(replies) == 1
    assert "pytest passed" in replies[0].read_text(encoding="utf-8")


def test_audit_creates_claude_task_on_failure(tmp_path: Path) -> None:
    rc = _run(
        tmp_path,
        "audit",
        "--name",
        "unit-smoke",
        "--command",
        f"{sys.executable} -c 'import sys; print(\"boom\"); sys.exit(7)'",
        "--to",
        "claude",
    )

    assert rc == 0
    task_path = next((tmp_path / ".agent/bridge/tasks").glob("*.json"))
    task = json.loads(task_path.read_text(encoding="utf-8"))
    assert task["title"] == "unit-smoke failed"
    assert "exit code 7" in task["summary"]
    assert "boom" in task["evidence"]


def test_audit_pass_does_not_create_task(tmp_path: Path) -> None:
    rc = _run(
        tmp_path,
        "audit",
        "--name",
        "unit-smoke",
        "--command",
        f"{sys.executable} -c 'print(\"ok\")'",
    )

    assert rc == 0
    assert not (tmp_path / ".agent/bridge/tasks").exists()
