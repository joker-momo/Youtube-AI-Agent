# 🎯 PRIME DIRECTIVE — read this FIRST, every session, before any request

**MỤC TIÊU DUY NHẤT CỦA TOÀN BỘ PROJECT:** Nâng cao chất lượng video, để hấp dẫn, thu hút người xem đúng theo nhóm đối tượng mà kênh youtube đang xây.

Mọi chỉnh sửa, tính năng hay yêu cầu đều PHẢI hướng tới mục tiêu này. Trước khi chạy bất kỳ yêu cầu nào:
1. Hỏi: "Điều này có làm cho video chất lượng hơn / hấp dẫn hơn / phù hợp hơn với nhóm đối tượng khán giả của kênh không?"
2. Nếu có → tiến hành.
3. Nếu yêu cầu hoạc chỉnh sửa đi ngược lại với mong muốn trên (ví dụ giảm chất lượng, không đúng tệp người xem để đánh đổi lấy tốc độ/chi phí) → **DỪNG LẠI, CẢNH BÁO NGAY CHO NGƯỜI DÙNG và tuyệt đối không tiến hành cho đến khi có sự xác nhận.**

This is a hard rule. Quality of the final video and fit to the target audience win over throughput, cost, and convenience whenever they conflict — unless the user explicitly overrides for that specific request.

---

# 📊 THỨ TỰ ƯU TIÊN (PRIORITY ORDER) — áp dụng khi cân nhắc mọi quyết định kỹ thuật

Khi hai mục tiêu xung đột, mục tiêu xếp trên LUÔN thắng. Mục tiêu xếp dưới chỉ được theo đuổi khi không hy sinh mục tiêu xếp trên.

1. **Chất lượng video** — cao nhất, bất khả xâm phạm. Hấp dẫn, đúng nhóm đối tượng khán giả của kênh. (Xem PRIME DIRECTIVE phía trên.)
2. **Tối ưu pipeline & thời gian chạy** — pipeline nhanh, ổn định, ra được sản phẩm. Chỉ tối ưu khi KHÔNG làm tụt chất lượng video.
3. **Chất lượng code** — phân chia module rõ ràng, chuyên nghiệp theo chuẩn ISO (ISO/IEC 25010: maintainability, readability, testability), dễ debug, dễ bảo trì lâu dài.
4. **Ưu tiên công nghệ tối ưu trên Mac (Apple Silicon M2)** — khi chọn lib / runtime / model, ưu tiên thứ chạy tối ưu native trên M2 (Metal/MPS, Core ML, Neural Engine, arm64), miễn là không vi phạm 3 ưu tiên trên.

**Quy tắc xung đột:** nếu một thay đổi cải thiện ưu tiên thấp nhưng hại ưu tiên cao hơn → **DỪNG, CẢNH BÁO, chờ xác nhận.** Không tự đánh đổi.

---

# 🔒 HARD RULES (bất khả xâm phạm — mọi agent PHẢI tuân, không ngoại lệ)

1. **KHÔNG BAO GIỜ sửa số luồng render (render concurrency) của máy Mac này.** Luôn để
   `render.concurrency: "auto"` (Remotion tự quyết theo 8 nhân). Cấm hardcode một con số,
   cấm hạ/nâng concurrency, cấm "tối ưu" nó — **với BẤT KỲ lý do gì** (kể cả khi máy chậm,
   swap, hay để tăng tốc). Chỉ được đổi khi **người dùng đồng ý rõ ràng**. Áp dụng cho
   `configs/*/channel.yaml` (`render.concurrency`) và mọi cờ concurrency truyền vào Remotion.

---

# OpenWolf

@.wolf/OPENWOLF.md

This project uses OpenWolf for context management. Read and follow .wolf/OPENWOLF.md every session. Check .wolf/cerebrum.md before generating code. Check .wolf/anatomy.md before reading files.

---

# Cross-Agent Entry Points

All coding agents working in this project must obey the same project contract:

- Codex: read root `AGENTS.md`.
- Claude: read this root `CLAUDE.md` plus `.claude/rules/*`.
- Antigravity: read `.agent/AGENTS.md` plus project-local `.agent/skills/*`.

If an agent can read more than one entrypoint, treat root `AGENTS.md` as the canonical project policy and the harness-specific file as an adapter. Do not weaken the prime directive, OpenWolf protocol, skill-routing policy, dirty worktree safety, or verification requirements in any harness-specific adapter.

---

# Agent Workflow Policy

Use Superpowers as the operating system and `agent-skills` as the toolbox.

## Precedence

When instructions conflict:

1. Direct user request and this `CLAUDE.md` win.
2. OpenWolf memory and project-specific constraints come next.
3. Superpowers workflow skills define the process.
4. Selected `agent-skills` domain checklists add focused guidance.
5. Default model behavior comes last.

Superpowers decides how the task is run. `agent-skills` supplies focused domain
checklists only when the task clearly matches a domain. Do not load the whole
agent-skills pack and do not let a generic checklist override this project's
video-quality priority, dirty-worktree safety, or verification gates.

## Default Workflow

For implementation, refactor, debugging, spec, QA, or git-workflow tasks:

- Start from the relevant Superpowers process skill.
- Check branch/worktree state before editing when the checkout is dirty.
- Keep edits small, reversible, and scoped to the request.
- Verify with targeted tests, type checks, render checks, or artifact inspection
  appropriate to the touched surface.
- Before claiming completion, cite concrete verification evidence.

## Simplicity First

- Prefer the simplest change that fully satisfies the request.
- Avoid new abstractions/frameworks unless they remove real complexity now.
- Every changed line should trace back to the user request, a failing test, or a documented project invariant.

## Agent-Skills Router

When a capability exists in both Superpowers and `agent-skills`, Superpowers wins. Do not invoke the Addy `agent-skills` twin for shadowed areas:

| Shadowed `agent-skills` area | Use instead |
| --- | --- |
| `interview-me`, `idea-refine`, `spec-driven-development` | Superpowers `brainstorming` |
| `planning-and-task-breakdown` | Superpowers `writing-plans` |
| `test-driven-development` | Superpowers `test-driven-development` |
| `debugging-and-error-recovery` | Superpowers `systematic-debugging` plus `verification-before-completion` |
| `code-review-and-quality`, `code-simplification` | Superpowers `requesting-code-review` |
| `git-workflow-and-versioning` | Superpowers `using-git-worktrees` and `finishing-a-development-branch` |
| `incremental-implementation`, `source-driven-development`, `doubt-driven-development`, `context-engineering` | Superpowers process plus OpenWolf context |

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

Routing rules:

- First choose the Superpowers process skill, then add the narrow
  `agent-skills` checklist if useful.
- If multiple checklists match, pick the smallest set that covers the risk.
- If none match, do not force one.
- If `agent-skills` is unavailable in the current harness, say so and continue
  with Superpowers plus project rules.
- For Shorts work, prefer requirement-by-requirement audits and targeted
  regression evidence over broad smoke tests.

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
