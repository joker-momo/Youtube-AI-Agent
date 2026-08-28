---
description: Skill routing — Superpowers is the OS, agent-skills is the toolbox
globs: **/*
---

# Skill Routing

All coding agents in this project share one policy:

- Codex reads root `AGENTS.md` and invokes official `superpowers:<skill-name>` plugin skills.
- Claude reads root `CLAUDE.md` plus `.claude/rules/*` and uses the official Superpowers plugin.
- Antigravity reads `.agent/AGENTS.md` and uses the `obra/superpowers` plugin.

Root `AGENTS.md` is the canonical project policy. Harness-specific files are
adapters and must not weaken the prime directive, OpenWolf protocol, dirty
worktree safety, or verification requirements.

## Canonical Superpowers Source (mandatory)

The only valid source is upstream `https://github.com/obra/superpowers` or an
official marketplace plugin. Claude must use `superpowers@claude-plugins-official`
or `superpowers@superpowers-marketplace`; do not load `.agent/skills`,
`.agent/workflows`, copied skill files, or an unverified cache/mirror as a
Superpowers fallback. If the official plugin is unavailable, stop and request
installation or update.

## Layers

- **Superpowers = operating system.** It governs how work happens.
- **Addy Osmani agent-skills = toolbox.** Pull one narrow checklist only when a
  task needs a non-overlapping domain capability.
- Do not load the whole agent-skills pack.
- Do not use `using-agent-skills` as an auto-driver; consult it manually only if
  a toolbox choice is unclear.

## Mandatory Skill Use

Claude must make skill use visible. Before any debug, fix, review,
implementation, refactor, QA, or git-workflow work:

1. State the selected process skill in chat, using this exact style:
   `Using superpowers:<skill-name>`.
2. Load/read that Superpowers skill before tool work. Use Claude's native skill
   mechanism when available; otherwise read the plugin skill file from the
   installed Superpowers cache.
3. Follow the skill's process in order, then add the narrow `agent-skills`
   checklist only when the task matches a non-shadowed toolbox domain below.

Common mappings:

| Task signal | Required Superpowers process |
| --- | --- |
| Bug, runtime error, failed render, failed test | `superpowers:systematic-debugging` |
| Before claiming a bug is fixed | `superpowers:verification-before-completion` |
| Code review or audit | `superpowers:requesting-code-review` |
| Receiving review feedback | `superpowers:receiving-code-review` |
| Multi-step implementation from an approved plan | `superpowers:executing-plans` or `superpowers:subagent-driven-development` |
| Planning work from requirements | `superpowers:writing-plans` |
| Starting isolated branch/worktree work | `superpowers:using-git-worktrees` |

If Claude cannot load a required official Superpowers skill, it must say so
explicitly and stop to request plugin installation or update; it must not fall
back to a local adaptation or generic workflow.

## Shadowed Skills

When a capability exists in both stacks, Superpowers wins:

| Shadowed `agent-skills` area | Use instead |
| --- | --- |
| `interview-me`, `idea-refine`, `spec-driven-development` | Superpowers `brainstorming` |
| `planning-and-task-breakdown` | Superpowers `writing-plans` |
| `test-driven-development` | Superpowers `test-driven-development` |
| `debugging-and-error-recovery` | Superpowers `systematic-debugging` plus `verification-before-completion` |
| `code-review-and-quality`, `code-simplification` | Superpowers `requesting-code-review` |
| `git-workflow-and-versioning` | Superpowers `using-git-worktrees` and `finishing-a-development-branch` |
| `incremental-implementation`, `source-driven-development`, `doubt-driven-development`, `context-engineering` | Superpowers process plus OpenWolf context |

## Toolbox Skills

Invoke these Addy `agent-skills` on demand only:

| Task signal | Agent-skill |
| --- | --- |
| UI, dashboard, Remotion visual surface | `frontend-ui-engineering` |
| Public API, module boundary, schema, contract | `api-and-interface-design` |
| Browser runtime behavior | `browser-testing-with-devtools` |
| User input, paths, secrets, auth, network fetch | `security-and-hardening` |
| Runtime speed, render time, asset search, bundle size | `performance-optimization` |
| Build/deploy/queue automation | `ci-cd-and-automation` |
| Removing old systems or migrating contracts | `deprecation-and-migration` |
| Architecture records or user-facing docs | `documentation-and-adrs` |
| Logging, metrics, traces, QA summaries | `observability-and-instrumentation` |
| Launch or production rollout | `shipping-and-launch` |

If `agent-skills` is unavailable in the current harness, say so and continue
with Superpowers plus project rules.
