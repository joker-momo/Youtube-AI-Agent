# Codex-Claude Agent Bridge

This repo uses a file-based bridge so Codex can report bugs to Claude Code and
Claude Code can reply with fixes and verification evidence.

## Runtime Location

- Task ledger: `.agent/bridge/tasks/*.json`
- Claude inbox: `.agent/bridge/claude/inbox/*.md`
- Codex inbox: `.agent/bridge/codex/inbox/*.md`

Use the CLI instead of editing task JSON by hand:

```bash
rtk .venv/bin/python scripts/agent_bridge.py init
```

## Codex: Report A Bug To Claude

```bash
rtk .venv/bin/python scripts/agent_bridge.py report-bug \
  --from codex \
  --to claude \
  --severity P1 \
  --title "Graphic subtitle still overlaps card" \
  --summary "Renderer still hard-codes subtitle overlay on non-CTA graphic cards." \
  --area remotion \
  --file remotion/src/ChannelVideo.tsx \
  --command "cd remotion && npx tsc --noEmit" \
  --evidence "Source lines and observed artifact details." \
  --requested-action "Fix the renderer contract and reply with verification evidence."
```

## Codex: Monitor A Command

`audit` runs a command. If it passes, no task is created. If it fails, the tail
of stdout/stderr is written into Claude's inbox.

```bash
rtk .venv/bin/python scripts/agent_bridge.py audit \
  --name "long-card-contract-tests" \
  --command ".venv/bin/python -m pytest tests/test_long_visual_spans.py tests/test_graphic_images_stage.py -q" \
  --to claude \
  --severity P1 \
  --area long-form-cards
```

Use `--propagate` when this command is part of a shell/CI gate and should return
the original failing exit status.

## Claude: Pick Up Work

```bash
rtk .venv/bin/python scripts/agent_bridge.py list --agent claude
rtk .venv/bin/python scripts/agent_bridge.py show <task-id>
```

Claude should:

1. Read the markdown task from `.agent/bridge/claude/inbox/`.
2. Follow root `CLAUDE.md`, `.claude/rules/*`, and OpenWolf.
3. Fix the root cause with focused verification.
4. Reply to Codex:

```bash
rtk .venv/bin/python scripts/agent_bridge.py reply <task-id> \
  --from claude \
  --status fixed \
  --message "Changed X; root cause was Y." \
  --evidence ".venv/bin/python -m pytest ... -q passed"
```

## Codex: Read Claude Replies

```bash
rtk .venv/bin/python scripts/agent_bridge.py list --agent codex --status fixed
```

Reply markdown appears in `.agent/bridge/codex/inbox/`.

## Contract

- Keep task reports concise and evidence-heavy.
- Include repro commands whenever possible.
- The fixing agent must not mark `fixed` without verification evidence.
- Do not use the bridge to bypass user approval for risky actions.
- OpenWolf remains authoritative for durable project learning and bug history.
