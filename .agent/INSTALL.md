# Install Official Superpowers for Antigravity

This project requires the upstream Superpowers plugin, not this legacy local profile.

## Prerequisites

- Antigravity environment installed
- Shell access
- This repository available locally

## Install

From your project root:

```bash
agy plugin install https://github.com/obra/superpowers
```

Re-run the same command to update the plugin. Do not copy or merge
`.agent/skills` as a replacement for the official plugin.

## What Gets Installed

- Official `obra/superpowers` plugin skills and session-start integration.

Runtime tracking file:

- `docs/plans/task.md` in the target project root (created at runtime by skill flow, list-only table)

## Verify Installation

From your target project root:

```bash
agy plugin list
```

Expected result: the `obra/superpowers` plugin is listed and enabled.

## Usage Notes

- Invoke skills from the installed official plugin.
- Do not invoke `.agent/skills` or `.agent/workflows`; they are legacy local adaptations.

## Update

Re-run `agy plugin install https://github.com/obra/superpowers`, then run
`agy plugin list` to confirm it remains enabled.
