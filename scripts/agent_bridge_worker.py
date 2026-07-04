#!/usr/bin/env python3
"""Automation worker for the Codex <-> Claude Code file bridge.

This script is intentionally small and file-based. It does not invent another
queue; it scans `.agent/bridge/tasks/*.json`, uses per-task lock files, writes
logs, and delegates state transitions back through `scripts/agent_bridge.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.agent_bridge as bridge

DEFAULT_COMMAND_TIMEOUT_SEC = 900
DEFAULT_CLAUDE_TIMEOUT_SEC = 7200


@dataclass(frozen=True)
class WorkerPaths:
    root: Path

    @property
    def bridge(self) -> Path:
        return self.root / bridge.DEFAULT_BRIDGE_DIR

    @property
    def tasks(self) -> Path:
        return self.bridge / "tasks"

    @property
    def locks(self) -> Path:
        return self.bridge / "locks"

    @property
    def logs(self) -> Path:
        return self.bridge / "logs"


@dataclass(frozen=True)
class CommandResult:
    command: str
    returncode: int
    output: str


def _now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _tail(text: str, *, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


class TaskLock:
    def __init__(self, paths: WorkerPaths, task_id: str, mode: str) -> None:
        self.path = paths.locks / f"{task_id}.{mode}.lock"
        self.fd: int | None = None

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(self.fd, f"pid={os.getpid()} started_at={_now_slug()}\n".encode("utf-8"))
        return True

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def load_tasks(paths: WorkerPaths) -> list[dict[str, Any]]:
    if not paths.tasks.exists():
        return []
    tasks: list[dict[str, Any]] = []
    for path in sorted(paths.tasks.glob("*.json")):
        try:
            tasks.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return tasks


def pending_claude_tasks(paths: WorkerPaths) -> list[dict[str, Any]]:
    return [
        task for task in load_tasks(paths)
        if task.get("assignee") == "claude" and task.get("status") in {"open", "needs-info"}
    ]


def pending_codex_verifications(paths: WorkerPaths) -> list[dict[str, Any]]:
    return [
        task for task in load_tasks(paths)
        if task.get("reporter") == "codex" and task.get("status") == bridge.CLAUDE_DONE_STATUS
    ]


def write_log(paths: WorkerPaths, task_id: str, mode: str, text: str) -> Path:
    paths.logs.mkdir(parents=True, exist_ok=True)
    path = paths.logs / f"{task_id}-{mode}-{_now_slug()}.log"
    path.write_text(text, encoding="utf-8")
    return path


def run_commands(
    root: Path,
    commands: list[str],
    *,
    timeout_sec: int,
) -> tuple[bool, list[CommandResult]]:
    results: list[CommandResult] = []
    if not commands:
        return False, [CommandResult("(no command)", 2, "Task has no verification command.")]
    for command in commands:
        try:
            result = subprocess.run(
                command,
                cwd=root,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
            )
            output = _tail(
                (result.stdout or "")
                + ("\n" if result.stdout and result.stderr else "")
                + (result.stderr or "")
            )
            results.append(CommandResult(command, result.returncode, output))
        except subprocess.TimeoutExpired as exc:
            output = _tail(((exc.stdout or "") if isinstance(exc.stdout, str) else "") + "\n" + str(exc))
            results.append(CommandResult(command, 124, output))
        if results[-1].returncode != 0:
            return False, results
    return True, results


def format_command_evidence(results: list[CommandResult]) -> str:
    chunks = []
    for result in results:
        chunks.append(
            f"$ {result.command}\nexit={result.returncode}\n{result.output.strip() or '(no output)'}"
        )
    return "\n\n".join(chunks)


def call_bridge_verify(root: Path, task_id: str, status: str, message: str, evidence: str) -> None:
    bridge.main([
        "--root", str(root),
        "verify", task_id,
        "--from", "codex",
        "--status", status,
        "--message", message,
        "--evidence", evidence,
    ])


def verify_task_once(paths: WorkerPaths, task: dict[str, Any], *, timeout_sec: int) -> str:
    task_id = str(task["id"])
    with TaskLock(paths, task_id, "verify") as acquired:
        if not acquired:
            return "locked"
        ok, results = run_commands(paths.root, [str(cmd) for cmd in task.get("commands", [])], timeout_sec=timeout_sec)
        evidence = format_command_evidence(results)
        log_path = write_log(paths, task_id, "verify", evidence)
        if ok:
            call_bridge_verify(
                paths.root,
                task_id,
                "verified",
                "Automated Codex verifier ran the task commands successfully.",
                f"{evidence}\n\nlog={log_path.relative_to(paths.root)}",
            )
            return "verified"
        call_bridge_verify(
            paths.root,
            task_id,
            "open",
            "Automated Codex verifier found the fix is not passing.",
            f"{evidence}\n\nlog={log_path.relative_to(paths.root)}",
        )
        return "open"


def build_claude_prompt(task: dict[str, Any]) -> str:
    task_json = json.dumps(task, ensure_ascii=False, indent=2)
    return textwrap.dedent(f"""
    You are Claude Code working in the same repository as Codex.

    Fix this bridge task end to end. Follow the repository's CLAUDE.md and project rules.
    Do not mark the task complete directly. After changing code and running verification,
    reply through:

      python3 scripts/agent_bridge.py reply {task["id"]} --from claude --status fixed --message "what changed" --evidence "commands run"

    If blocked, reply with --status needs-info and explain exactly what is missing.

    Task JSON:
    ```json
    {task_json}
    ```
    """).strip()


def run_claude_for_task(
    paths: WorkerPaths,
    task: dict[str, Any],
    *,
    claude_bin: str,
    timeout_sec: int,
    permission_mode: str,
    dry_run: bool,
) -> str:
    task_id = str(task["id"])
    with TaskLock(paths, task_id, "claude") as acquired:
        if not acquired:
            return "locked"
        prompt = build_claude_prompt(task)
        if dry_run:
            log_path = write_log(paths, task_id, "claude-dry-run", prompt)
            return f"dry-run:{log_path.relative_to(paths.root)}"
        cmd = [
            claude_bin,
            "--print",
            "--effort", "medium",
            "--permission-mode", permission_mode,
            prompt,
        ]
        result = subprocess.run(
            cmd,
            cwd=paths.root,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        output = _tail((result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or ""))
        log_path = write_log(paths, task_id, "claude", f"$ {' '.join(cmd[:-1])} <prompt>\nexit={result.returncode}\n{output}")
        return f"claude-exit-{result.returncode}:{log_path.relative_to(paths.root)}"


def cmd_verify_once(args: argparse.Namespace) -> int:
    paths = WorkerPaths(args.root.resolve())
    count = 0
    for task in pending_codex_verifications(paths):
        outcome = verify_task_once(paths, task, timeout_sec=args.command_timeout)
        print(f"{task['id']}: {outcome}")
        count += 1
        if args.max_tasks and count >= args.max_tasks:
            break
    if count == 0:
        print("No fixed-pending-codex tasks to verify.")
    return 0


def cmd_claude_once(args: argparse.Namespace) -> int:
    paths = WorkerPaths(args.root.resolve())
    count = 0
    for task in pending_claude_tasks(paths):
        outcome = run_claude_for_task(
            paths,
            task,
            claude_bin=args.claude_bin,
            timeout_sec=args.claude_timeout,
            permission_mode=args.permission_mode,
            dry_run=args.dry_run,
        )
        print(f"{task['id']}: {outcome}")
        count += 1
        if args.max_tasks and count >= args.max_tasks:
            break
    if count == 0:
        print("No open Claude tasks.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    paths = WorkerPaths(args.root.resolve())
    print(f"open_for_claude={len(pending_claude_tasks(paths))}")
    print(f"pending_codex_verify={len(pending_codex_verifications(paths))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automation worker for agent_bridge tasks")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show bridge work counts")
    status.set_defaults(func=cmd_status)

    verify = sub.add_parser("verify-once", help="Verify fixed-pending-codex tasks once")
    verify.add_argument("--command-timeout", type=int, default=DEFAULT_COMMAND_TIMEOUT_SEC)
    verify.add_argument("--max-tasks", type=int, default=0)
    verify.set_defaults(func=cmd_verify_once)

    claude = sub.add_parser("claude-once", help="Run Claude Code once for open tasks")
    claude.add_argument("--claude-bin", default="claude")
    claude.add_argument("--claude-timeout", type=int, default=DEFAULT_CLAUDE_TIMEOUT_SEC)
    claude.add_argument("--permission-mode", default="acceptEdits")
    claude.add_argument("--max-tasks", type=int, default=1)
    claude.add_argument("--dry-run", action="store_true")
    claude.set_defaults(func=cmd_claude_once)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
