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

- Codex: read root `AGENTS.md` and invoke official `superpowers:<skill-name>` plugin skills.
- Claude: read this root `CLAUDE.md` plus `.claude/rules/*` and use the official Superpowers plugin.
- Antigravity: read `.agent/AGENTS.md` and use the `obra/superpowers` plugin.

If an agent can read more than one entrypoint, treat root `AGENTS.md` as the canonical project policy and the harness-specific file as an adapter. Do not weaken the prime directive, OpenWolf protocol, skill-routing policy, dirty worktree safety, or verification requirements in any harness-specific adapter.

## Bug Verification Gate

When Claude Code fixes a bug assigned through `.agent/bridge`, `--status fixed`
means "ready for Codex verification", not final completion. The bridge records
that state as `fixed-pending-codex`. A bug is complete only after Codex runs
`scripts/agent_bridge.py verify <task-id> --from codex --status verified` with
independent verification evidence.

If Codex reopens the task with `verify --status open` or `needs-info`, continue
from that feedback. Do not mark your own bug fix `verified` or `closed`.

---

# Agent Workflow Policy

Use Superpowers as the operating system and `agent-skills` as the toolbox.

## Canonical Superpowers Source (mandatory)

Only upstream `https://github.com/obra/superpowers` or an official marketplace
plugin is a valid Superpowers source. Claude must use
`superpowers@claude-plugins-official` or the official
`superpowers@superpowers-marketplace` distribution. Do not load, adapt, or
substitute `.agent/skills`, `.agent/workflows`, copied skill files, or an
unverified cache/mirror as Superpowers. If the official plugin is unavailable,
stop and request installation or update instead of falling back.

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

See `.claude/rules/skill-routing.md` for the full router: shadowed-skill
precedence (Superpowers wins) and the task-signal → toolbox mapping tables.
