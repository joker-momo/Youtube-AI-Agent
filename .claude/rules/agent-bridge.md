---
description: Codex-Claude file bridge protocol
globs: **/*
---

# Agent Bridge

This workspace may contain Codex-created handoffs for Claude Code under
`.agent/bridge/claude/inbox/`.

At the start of a Claude Code task, after reading `CLAUDE.md` and OpenWolf:

1. Select, announce, and load the relevant Superpowers process skill before
   tool work. For bug handoffs, this is normally
   `Using superpowers:systematic-debugging`; before claiming the bug is fixed,
   also use `superpowers:verification-before-completion`.
2. Run `rtk .venv/bin/python scripts/agent_bridge.py list --agent claude`.
3. If there is an open task relevant to the user's current request, read it with
   `rtk .venv/bin/python scripts/agent_bridge.py show <task-id>` and inspect the
   markdown task in `.agent/bridge/claude/inbox/`.
4. Fix the root cause using the normal project workflow and targeted
   verification.
5. Reply to Codex with:

```bash
rtk .venv/bin/python scripts/agent_bridge.py reply <task-id> \
  --from claude \
  --status fixed \
  --message "what changed and why" \
  --evidence "commands/tests/artifact checks"
```

Important: `--status fixed` from Claude means "ready for Codex verification",
not "complete." The bridge records it as `fixed-pending-codex`. A bug is only
complete after Codex verifies the evidence and runs:

```bash
rtk .venv/bin/python scripts/agent_bridge.py verify <task-id> \
  --from codex \
  --status verified \
  --message "verified" \
  --evidence "verification commands/artifact checks"
```

If Codex verification fails, Codex reopens it with `verify --status open` or
`verify --status needs-info` and Claude must continue from that feedback. Claude
must not use `verified` or `closed` for its own fixes.

Use `--status needs-info` when the task is blocked by missing user input or an
external service. Do not mark a task `fixed` without verification evidence.

The bridge is a coordination layer only. Root `AGENTS.md`, `CLAUDE.md`,
OpenWolf, dirty-worktree safety, and the render-concurrency hard rule remain
authoritative.
