#!/usr/bin/env python3
"""File-based Codex <-> Claude Code bridge.

The bridge is intentionally boring: both agents share this workspace, so the
most reliable transport is a small JSON task ledger plus markdown inbox files.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2026-07-agent-bridge-v1"
DEFAULT_BRIDGE_DIR = Path(".agent/bridge")
VALID_AGENTS = {"codex", "claude"}
VALID_STATUS = {
    "open",
    "claimed",
    "needs-info",
    "fixed",
    "fixed-pending-codex",
    "verified",
    "wont-fix",
    "closed",
}
CLAUDE_DONE_STATUS = "fixed-pending-codex"
FINAL_STATUSES = {"verified", "wont-fix", "closed"}
DEFAULT_TIMEOUT_SEC = 300


@dataclass(frozen=True)
class BridgePaths:
    root: Path

    @property
    def bridge(self) -> Path:
        return self.root / DEFAULT_BRIDGE_DIR

    @property
    def tasks(self) -> Path:
        return self.bridge / "tasks"

    def inbox(self, agent: str) -> Path:
        return self.bridge / agent / "inbox"

    def outbox(self, agent: str) -> Path:
        return self.bridge / agent / "outbox"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(text: str, *, limit: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return (slug or "task")[:limit].strip("-") or "task"


def _ensure_dirs(paths: BridgePaths) -> None:
    for agent in sorted(VALID_AGENTS):
        paths.inbox(agent).mkdir(parents=True, exist_ok=True)
        paths.outbox(agent).mkdir(parents=True, exist_ok=True)
    paths.tasks.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _load_task(paths: BridgePaths, task_id: str) -> dict[str, Any]:
    task_path = paths.tasks / f"{task_id}.json"
    if not task_path.exists():
        raise SystemExit(f"Task not found: {task_id}")
    return json.loads(task_path.read_text(encoding="utf-8"))


def _save_task(paths: BridgePaths, task: dict[str, Any]) -> None:
    task["updated_at"] = _now()
    _atomic_write(paths.tasks / f"{task['id']}.json", json.dumps(task, ensure_ascii=False, indent=2) + "\n")


def _split_csv(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def _tail(text: str, *, max_chars: int = 8000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _markdown_task(task: dict[str, Any]) -> str:
    files = "\n".join(f"- `{path}`" for path in task.get("files", [])) or "- none"
    commands = "\n".join(f"- `{cmd}`" for cmd in task.get("commands", [])) or "- none"
    evidence = str(task.get("evidence") or "").strip() or "none"
    return f"""# Agent Bridge Task: {task["id"]}

## Assignment
- From: `{task["reporter"]}`
- To: `{task["assignee"]}`
- Severity: `{task["severity"]}`
- Status: `{task["status"]}`
- Area: `{task.get("area") or "unspecified"}`

## Title
{task["title"]}

## Summary
{task["summary"]}

## Suspect Files
{files}

## Repro / Verification Commands
{commands}

## Evidence
```text
{evidence}
```

## Requested Action
{task["requested_action"]}

## Response Protocol
After fixing or deciding, run:

```bash
rtk .venv/bin/python scripts/agent_bridge.py reply {task["id"]} --from {task["assignee"]} --status fixed --message "what changed" --evidence "tests or command output"
```

`--status fixed` from the assignee means `fixed-pending-codex`, not complete.
The reporter must verify with:

```bash
rtk .venv/bin/python scripts/agent_bridge.py verify {task["id"]} --from {task["reporter"]} --status verified --message "verified" --evidence "verification commands/artifact checks"
```

If blocked, use `--status needs-info` and explain the missing input.
"""


def _markdown_reply(task: dict[str, Any], reply: dict[str, Any]) -> str:
    evidence = str(reply.get("evidence") or "").strip() or "none"
    return f"""# Agent Bridge Reply: {task["id"]}

## From
`{reply["from"]}`

## Status
`{reply["status"]}`

## Message
{reply["message"]}

## Evidence
```text
{evidence}
```

## Task
{task["title"]}
"""


def _append_reply(
    paths: BridgePaths,
    task: dict[str, Any],
    reply: dict[str, Any],
    *,
    recipient: str,
) -> Path:
    task.setdefault("replies", []).append(reply)
    _save_task(paths, task)
    reply_path = paths.inbox(recipient) / f"{task['id']}-reply-{_slug(reply['from'])}.md"
    _atomic_write(reply_path, _markdown_reply(task, reply))
    return reply_path


def create_task(
    paths: BridgePaths,
    *,
    reporter: str,
    assignee: str,
    severity: str,
    title: str,
    summary: str,
    area: str,
    files: list[str],
    commands: list[str],
    evidence: str,
    requested_action: str,
) -> dict[str, Any]:
    if reporter not in VALID_AGENTS:
        raise SystemExit(f"Invalid reporter: {reporter}")
    if assignee not in VALID_AGENTS:
        raise SystemExit(f"Invalid assignee: {assignee}")
    _ensure_dirs(paths)
    task_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{_slug(title)}"
    task = {
        "schema_version": SCHEMA_VERSION,
        "id": task_id,
        "created_at": _now(),
        "updated_at": _now(),
        "reporter": reporter,
        "assignee": assignee,
        "severity": severity,
        "status": "open",
        "title": title.strip(),
        "summary": summary.strip(),
        "area": area.strip(),
        "files": files,
        "commands": commands,
        "evidence": evidence.strip(),
        "requested_action": requested_action.strip(),
        "replies": [],
    }
    inbox_path = paths.inbox(assignee) / f"{task_id}.md"
    task["inbox_file"] = str(inbox_path.relative_to(paths.root))
    _save_task(paths, task)
    _atomic_write(inbox_path, _markdown_task(task))
    return task


def cmd_init(args: argparse.Namespace) -> int:
    paths = BridgePaths(args.root)
    _ensure_dirs(paths)
    readme = paths.bridge / "README.md"
    if not readme.exists():
        _atomic_write(
            readme,
            "# Agent Bridge Runtime\n\n"
            "Generated inbox/outbox and task ledger for Codex <-> Claude Code handoffs.\n"
            "Use `scripts/agent_bridge.py` rather than editing task JSON by hand.\n",
        )
    print(f"Bridge ready: {paths.bridge}")
    return 0


def cmd_report_bug(args: argparse.Namespace) -> int:
    task = create_task(
        BridgePaths(args.root),
        reporter=args.from_agent,
        assignee=args.to,
        severity=args.severity,
        title=args.title,
        summary=args.summary,
        area=args.area or "",
        files=_split_csv(args.file),
        commands=args.command or [],
        evidence=args.evidence or "",
        requested_action=args.requested_action,
    )
    print(f"Created {task['id']} -> {task['inbox_file']}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    paths = BridgePaths(args.root)
    cmd = args.command
    result = subprocess.run(
        cmd,
        cwd=args.root,
        shell=True,
        text=True,
        capture_output=True,
        timeout=args.timeout,
        check=False,
    )
    output = _tail((result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or ""))
    if result.returncode == 0:
        print(f"PASS {args.name}: {cmd}")
        return 0
    task = create_task(
        paths,
        reporter=args.from_agent,
        assignee=args.to,
        severity=args.severity,
        title=f"{args.name} failed",
        summary=f"Automated audit `{args.name}` failed with exit code {result.returncode}.",
        area=args.area or "automated-audit",
        files=_split_csv(args.file),
        commands=[cmd],
        evidence=output,
        requested_action=args.requested_action,
    )
    print(f"FAIL {args.name}: created {task['id']} -> {task['inbox_file']}")
    return result.returncode if args.propagate else 0


def cmd_list(args: argparse.Namespace) -> int:
    paths = BridgePaths(args.root)
    _ensure_dirs(paths)
    statuses = set(args.status or ["open", "claimed", "needs-info"])
    rows: list[dict[str, Any]] = []
    for path in sorted(paths.tasks.glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        if args.agent and task.get("assignee") != args.agent:
            continue
        if task.get("status") not in statuses:
            continue
        rows.append(task)
    if not rows:
        print("No matching bridge tasks.")
        return 0
    for task in rows:
        print(f"{task['id']} [{task['severity']}] {task['status']} -> {task['assignee']}: {task['title']}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    task = _load_task(BridgePaths(args.root), args.task_id)
    print(json.dumps(task, ensure_ascii=False, indent=2))
    return 0


def cmd_reply(args: argparse.Namespace) -> int:
    paths = BridgePaths(args.root)
    if args.from_agent not in VALID_AGENTS:
        raise SystemExit(f"Invalid from agent: {args.from_agent}")
    if args.status not in VALID_STATUS:
        raise SystemExit(f"Invalid status: {args.status}")
    task = _load_task(paths, args.task_id)
    if args.from_agent == task.get("assignee") and args.status in {"verified", "closed"}:
        raise SystemExit(
            "Only the reporting agent may verify/close a task. "
            "Assignee fixes must use --status fixed, which becomes fixed-pending-codex."
        )
    if args.from_agent not in {task.get("assignee"), task.get("reporter")}:
        raise SystemExit(
            f"{args.from_agent} is neither reporter nor assignee for task {task['id']}"
        )
    task_status = args.status
    if args.from_agent == task.get("assignee") and args.status == "fixed":
        # Claude's "fixed" means "ready for Codex verification", not final done.
        # Final completion requires `agent_bridge.py verify ... --status verified`
        # from the reporter/Codex side.
        task_status = CLAUDE_DONE_STATUS
        task["awaiting_verification_by"] = task.get("reporter")
        task["fix_reported_at"] = _now()
    reply = {
        "from": args.from_agent,
        "status": task_status,
        "message": args.message.strip(),
        "evidence": (args.evidence or "").strip(),
        "created_at": _now(),
    }
    task["status"] = task_status
    recipient = task["reporter"] if args.from_agent == task.get("assignee") else task["assignee"]
    reply_path = _append_reply(paths, task, reply, recipient=recipient)
    print(f"Reply written -> {reply_path.relative_to(paths.root)}")
    if task_status == CLAUDE_DONE_STATUS:
        print(
            "Status is fixed-pending-codex. This bug is not complete until "
            "Codex verifies it with `agent_bridge.py verify ... --status verified`."
        )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    paths = BridgePaths(args.root)
    if args.from_agent not in VALID_AGENTS:
        raise SystemExit(f"Invalid from agent: {args.from_agent}")
    if args.status not in {"verified", "open", "needs-info"}:
        raise SystemExit("Verification status must be one of: verified, open, needs-info")
    task = _load_task(paths, args.task_id)
    if args.from_agent != task.get("reporter"):
        raise SystemExit(
            f"Only reporter {task.get('reporter')} may verify task {task['id']}."
        )
    if task.get("status") not in {CLAUDE_DONE_STATUS, "fixed", "needs-info", "open"}:
        raise SystemExit(
            f"Task {task['id']} is {task.get('status')}; expected fixed-pending-codex "
            "before verification."
        )
    reply = {
        "from": args.from_agent,
        "status": args.status,
        "message": args.message.strip(),
        "evidence": (args.evidence or "").strip(),
        "created_at": _now(),
    }
    task["status"] = args.status
    if args.status == "verified":
        task["verified_by"] = args.from_agent
        task["verified_at"] = _now()
        task.pop("awaiting_verification_by", None)
    else:
        task["awaiting_fix_by"] = task.get("assignee")
    reply_path = _append_reply(paths, task, reply, recipient=task["assignee"])
    print(f"Verification written -> {reply_path.relative_to(paths.root)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex <-> Claude Code file bridge")
    parser.add_argument("--root", type=Path, default=_repo_root(), help="Workspace root")
    sub = parser.add_subparsers(dest="command_name", required=True)

    init = sub.add_parser("init", help="Create bridge directories")
    init.set_defaults(func=cmd_init)

    report = sub.add_parser("report-bug", help="Create a task for another agent")
    report.add_argument("--from", dest="from_agent", default="codex", choices=sorted(VALID_AGENTS))
    report.add_argument("--to", default="claude", choices=sorted(VALID_AGENTS))
    report.add_argument("--severity", default="P2")
    report.add_argument("--title", required=True)
    report.add_argument("--summary", required=True)
    report.add_argument("--area", default="")
    report.add_argument("--file", action="append")
    report.add_argument("--command", action="append")
    report.add_argument("--evidence", default="")
    report.add_argument("--requested-action", default="Find the root cause, fix it, and reply with verification evidence.")
    report.set_defaults(func=cmd_report_bug)

    audit = sub.add_parser("audit", help="Run a command and create a handoff if it fails")
    audit.add_argument("--from", dest="from_agent", default="codex", choices=sorted(VALID_AGENTS))
    audit.add_argument("--to", default="claude", choices=sorted(VALID_AGENTS))
    audit.add_argument("--severity", default="P1")
    audit.add_argument("--name", required=True)
    audit.add_argument("--command", required=True)
    audit.add_argument("--area", default="")
    audit.add_argument("--file", action="append")
    audit.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
    audit.add_argument("--propagate", action="store_true", help="Exit with the failing command's status")
    audit.add_argument("--requested-action", default="Fix the failure, run the command again, and reply with evidence.")
    audit.set_defaults(func=cmd_audit)

    list_cmd = sub.add_parser("list", help="List bridge tasks")
    list_cmd.add_argument("--agent", choices=sorted(VALID_AGENTS))
    list_cmd.add_argument("--status", action="append", choices=sorted(VALID_STATUS))
    list_cmd.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="Show one task JSON")
    show.add_argument("task_id")
    show.set_defaults(func=cmd_show)

    reply = sub.add_parser("reply", help="Reply to a task")
    reply.add_argument("task_id")
    reply.add_argument("--from", dest="from_agent", required=True, choices=sorted(VALID_AGENTS))
    reply.add_argument("--status", required=True, choices=sorted(VALID_STATUS))
    reply.add_argument("--message", required=True)
    reply.add_argument("--evidence", default="")
    reply.set_defaults(func=cmd_reply)

    verify = sub.add_parser("verify", help="Reporter verifies an assignee fix")
    verify.add_argument("task_id")
    verify.add_argument("--from", dest="from_agent", required=True, choices=sorted(VALID_AGENTS))
    verify.add_argument("--status", required=True, choices=["verified", "open", "needs-info"])
    verify.add_argument("--message", required=True)
    verify.add_argument("--evidence", default="")
    verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    if args.command_name == "audit":
        # Print a shell-escaped command in task evidence when callers pass a list-like string.
        args.command = args.command if isinstance(args.command, str) else shlex.join(args.command)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
