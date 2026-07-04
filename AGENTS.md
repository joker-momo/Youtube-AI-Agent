@/Users/joker/.codex/RTK.md

# Project Operating Rules

Read this file before acting in this repository. These instructions apply to
`/Users/joker/Documents/Youtube-AI-Agent`.

## Prime Directive

The single goal of this project is to raise final video quality so videos are
more engaging and better matched to the channel's target audience.

Before running any request, ask whether it improves or preserves video quality,
audience fit, or production reliability. If a request trades those away for
speed, cost, convenience, or throughput, stop and warn the user before acting.

Priority order when goals conflict:

1. Video quality and audience fit.
2. Pipeline stability and runtime efficiency, without lowering video quality.
3. Code quality, maintainability, readability, and testability.
4. Technology choices optimized for Apple Silicon M2, when compatible with the
   higher priorities.

## Hard Rules (inviolable -- every agent, no exceptions)

1. NEVER change the Mac's render concurrency (thread count). Always leave
   `render.concurrency: "auto"` (Remotion decides for the 8-core M2). Do not
   hardcode a number, do not lower or raise it, do not "optimize" it -- for ANY
   reason (slow machine, swap, or speed-ups included). Change ONLY with the
   user's explicit consent. Applies to `configs/*/channel.yaml`
   (`render.concurrency`) and any concurrency flag passed to Remotion.

## Reasoning Tier Handshake

The Codex UI Reasoning setting is assumed to be Medium by default.

For every user task, start the response with:

`Reasoning tier: Low/Medium/High/Max -- reason.`

Then follow this control flow:

- If the task only needs Low or Medium reasoning, proceed immediately in the
  same turn.
- If the task needs High or Max reasoning, stop after the recommendation and ask
  the user to flip the Codex UI Reasoning setting to that tier.
- Do not execute High/Max work until the user confirms they changed the UI.
- The purpose is to keep light tasks one-turn and cheap, while heavy tasks cost
  exactly one UI flip plus one execution turn.

## OpenWolf

This project uses OpenWolf for context management.

- Read and follow `.wolf/OPENWOLF.md` every session.
- Check `.wolf/anatomy.md` before reading project files.
- Check `.wolf/cerebrum.md` before generating code.

## Cross-Agent Entry Points

All coding agents working in this project must obey the same project contract:

- Codex: read this root `AGENTS.md`.
- Claude: read root `CLAUDE.md` plus `.claude/rules/*`.
- Antigravity: read `.agent/AGENTS.md` plus project-local `.agent/skills/*`.

If an agent can read more than one entrypoint, treat root `AGENTS.md` as the
canonical project policy and the harness-specific file as an adapter. Do not
weaken the prime directive, OpenWolf protocol, skill-routing policy, dirty
worktree safety, or verification requirements in any harness-specific adapter.

## Bug Verification Gate

When a bug is handed from Codex to Claude Code via `.agent/bridge`, Claude's
`fixed` reply is not final completion. The bridge records Claude fixes as
`fixed-pending-codex`; the bug counts as complete only after Codex independently
verifies the evidence/artifacts/tests and runs:

```bash
rtk .venv/bin/python scripts/agent_bridge.py verify <task-id> \
  --from codex --status verified \
  --message "verified" --evidence "verification commands/artifact checks"
```

If verification fails, Codex reopens the task with `verify --status open` or
`verify --status needs-info`. Do not treat `fixed-pending-codex` as done.

## Skill Orchestration Policy

Use Superpowers as the operating system and `agent-skills` as the toolbox.

### Instruction Priority

When instructions conflict, use this order:

1. Direct user request and this `AGENTS.md`.
2. OpenWolf project memory and project-specific constraints.
3. Superpowers workflow skills.
4. Selected `agent-skills` domain checklist.
5. Default model behavior.

Superpowers decides the process. `agent-skills` adds focused domain expertise.
Never let a broad `agent-skills` checklist override project quality priorities,
dirty-worktree safety, or Superpowers verification gates.

### Superpowers Default Workflow

For implementation, refactor, debugging, spec, QA, or git-workflow tasks:

- Start from the relevant Superpowers process skill.
- Use worktrees or branch checks when the checkout is dirty or the task is large.
- Prefer small, reversible steps.
- Verify with targeted tests, type checks, render checks, or artifact inspection
  appropriate to the touched surface.
- Before claiming completion, use verification evidence rather than confidence.

### Simplicity First

- Prefer the simplest change that fully satisfies the request.
- Avoid new abstractions/frameworks unless they remove real complexity now.
- Every changed line should trace back to the user request, a failing test, or a documented project invariant.

### Agent-Skills Toolbox Router

Use exactly one or a small number of `agent-skills` checklists only when the task
matches a concrete domain below. Do not load the whole pack.

When a capability exists in both Superpowers and `agent-skills`, Superpowers
wins. Do not invoke the Addy `agent-skills` twin for these shadowed areas:

| Shadowed `agent-skills` area | Use instead |
| --- | --- |
| `interview-me`, `idea-refine`, `spec-driven-development` | Superpowers `brainstorming` |
| `planning-and-task-breakdown` | Superpowers `writing-plans` |
| `test-driven-development` | Superpowers `test-driven-development` |
| `debugging-and-error-recovery` | Superpowers `systematic-debugging` plus `verification-before-completion` |
| `code-review-and-quality`, `code-simplification` | Superpowers `requesting-code-review` |
| `git-workflow-and-versioning` | Superpowers `using-git-worktrees` and `finishing-a-development-branch` |
| `incremental-implementation`, `source-driven-development`, `doubt-driven-development`, `context-engineering` | Superpowers process plus OpenWolf context |

| Task signal | Agent-skill to use |
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

### Routing Rules

- First choose the Superpowers process skill, then select any matching
  `agent-skills` checklist as supporting guidance.
- If multiple `agent-skills` match, pick the narrowest one that covers the risk.
- If no domain-specific checklist matches, do not force one.
- If `agent-skills` is not installed or not available in the current harness,
  say so briefly and continue with Superpowers plus project rules.
- For this repo's Shorts pipeline, prefer requirement-by-requirement audits and
  targeted regression evidence over broad smoke tests.
- For dirty worktrees, inspect branch/worktree state before editing and avoid
  mixing unrelated user changes into the task.

## CodeGraph

This project has a CodeGraph MCP server (`codegraph_*` tools) configured.
CodeGraph is a tree-sitter-parsed knowledge graph of every symbol, edge, and
file. Reads are sub-millisecond and return structural information grep cannot.

Use codegraph for structural questions:

| Question | Tool |
| --- | --- |
| Where is X defined? / Find symbol named X | `codegraph_search` |
| What calls function Y? | `codegraph_callers` |
| What does Y call? | `codegraph_callees` |
| What would break if I changed Z? | `codegraph_impact` |
| Show me Y's signature/source/docstring | `codegraph_node` |
| Give me focused context for a task/area | `codegraph_context` |
| Survey an unfamiliar module/topic | `codegraph_explore` |
| What files exist under path/ | `codegraph_files` |
| Is the index healthy? | `codegraph_status` |

Use native search only for literal text queries, comments, log messages, or when
you already know the exact file to read.

If `.codegraph/` does not exist or the MCP server says "not initialized", ask
the user whether to run `codegraph init -i`.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
