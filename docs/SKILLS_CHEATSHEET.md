# 📋 Skills Cheatsheet — Youtube-AI-Agent

Danh sách slash-command gõ được trong project. Trên mobile không có menu gợi ý `/`,
nên **gõ nguyên tên đầy đủ** (kèm namespace `plugin:skill`) là chạy.

- **Prompt-only** (superpowers, caveman): chạy mọi client.
- **Tool-thật** (`/code-review`, `/run`, ARS, codegraph): cần Claude Code runtime + repo.

---

## 🔧 Built-in (Claude Code core)

| Lệnh | Tác dụng |
|---|---|
| `/code-review` | review diff: bug + cleanup (low/med/high/max/ultra) |
| `/code-review ultra` | review đa-agent trên cloud |
| `/simplify` | dọn code cho gọn (chỉ quality, không tìm bug) |
| `/verify` | chạy app, xác nhận thay đổi hoạt động |
| `/run` | chạy / start / screenshot app |
| `/security-review` | soát bảo mật diff hiện tại |
| `/review` | review GitHub PR |
| `/init` | tạo CLAUDE.md |
| `/loop` | chạy lặp lệnh theo chu kỳ |
| `/schedule` | tạo cron cloud agent |
| `/claude-api` | tra cứu Claude API / model id / pricing |
| `/fewer-permission-prompts` | giảm prompt xin quyền |

## ⚡ Superpowers (quy trình làm việc)

| Lệnh | Tác dụng |
|---|---|
| `/superpowers:brainstorming` | khám phá ý tưởng trước khi code |
| `/superpowers:writing-plans` | viết plan đa-bước |
| `/superpowers:executing-plans` | chạy plan đã viết |
| `/superpowers:test-driven-development` | TDD |
| `/superpowers:systematic-debugging` | debug có hệ thống |
| `/superpowers:requesting-code-review` | xin review |
| `/superpowers:receiving-code-review` | nhận & xử lý review |
| `/superpowers:verification-before-completion` | xác minh trước khi báo xong |
| `/superpowers:using-git-worktrees` | tách worktree |
| `/superpowers:finishing-a-development-branch` | đóng nhánh |
| `/superpowers:dispatching-parallel-agents` | chạy agent song song |
| `/superpowers:subagent-driven-development` | dev bằng subagent |
| `/superpowers:writing-skills` | viết skill mới |
| `/superpowers:using-superpowers` | meta: cách dùng skill |

## 🦴 Caveman (nén token)

| Lệnh | Tác dụng |
|---|---|
| `/caveman lite\|full\|ultra` | bật chế độ caveman |
| `/caveman-help` | bảng tra nhanh |
| `/caveman-stats` | thống kê token phiên |
| `/caveman-commit` | commit message nén |
| `/caveman-review` | review PR nén |
| `/caveman-compress <file>` | nén file memory |

## 🎓 ARS — Academic Research (viết paper)

| Lệnh | Tác dụng |
|---|---|
| `/ars-full` | pipeline đầy đủ: research→write→review→revise→finalize |
| `/ars-plan` | lập kế hoạch chương từng bước (Socratic) |
| `/ars-outline` | outline chi tiết + evidence map |
| `/ars-revision` | bản revise + R&R responses |
| `/ars-revision-coach` | parse reviewer comments → roadmap |
| `/ars-abstract` | abstract song ngữ + keywords |
| `/ars-lit-review` | annotated bibliography |
| `/ars-format-convert` | chuyển LaTeX/DOCX/PDF/Markdown |
| `/ars-citation-check` | báo lỗi trích dẫn |
| `/ars-disclosure` | tuyên bố dùng AI theo venue |

## 🛠️ Agent-skills (Addy — toolbox theo domain)

Dùng theo router `.claude/rules/skill-routing.md`. Superpowers thắng khi trùng.

| Lệnh | Tín hiệu task |
|---|---|
| `/agent-skills:plan` | chia task có acceptance criteria |
| `/agent-skills:build` | implement từng bước, test, commit |
| `/agent-skills:test` | TDD / Prove-It pattern |
| `/agent-skills:review` | review 5-trục |
| `/agent-skills:ship` | checklist pre-launch |
| `/agent-skills:spec` | spec trước khi code |
| `/agent-skills:code-simplify` | giảm phức tạp |
| `/agent-skills:webperf` | audit hiệu năng web |
| `/agent-skills:frontend-ui-engineering` | UI, dashboard, Remotion |
| `/agent-skills:api-and-interface-design` | API, module boundary, schema |
| `/agent-skills:security-and-hardening` | input, auth, secrets, network |
| `/agent-skills:performance-optimization` | render time, asset, bundle |
| `/agent-skills:ci-cd-and-automation` | build/deploy/queue |
| `/agent-skills:deprecation-and-migration` | gỡ hệ thống cũ |
| `/agent-skills:documentation-and-adrs` | ADR, docs |
| `/agent-skills:observability-and-instrumentation` | log, metric, trace, QA |
| `/agent-skills:browser-testing-with-devtools` | runtime browser |

## 📄 Anthropic-skills (file / office)

| Lệnh | Tác dụng |
|---|---|
| `/anthropic-skills:docx` | tạo/đọc/sửa Word |
| `/anthropic-skills:xlsx` | tạo/đọc/sửa Excel/CSV |
| `/anthropic-skills:pptx` | tạo/đọc/sửa PowerPoint |
| `/anthropic-skills:pdf` | thao tác PDF |
| `/anthropic-skills:schedule` | task định kỳ |
| `/anthropic-skills:skill-creator` | tạo/sửa/đo skill |
| `/anthropic-skills:consolidate-memory` | dọn memory |

## ⚙️ Harness config

| Lệnh | Tác dụng |
|---|---|
| `/update-config` | sửa settings.json (quyền, hook, env) |
| `/keybindings-help` | sửa phím tắt |

---

## 📚 Deep-research / pipeline (academic-research-skills)

| Lệnh | Tác dụng |
|---|---|
| `/academic-research-skills:academic-paper` | pipeline viết paper 12-agent |
| `/academic-research-skills:academic-paper-reviewer` | mô phỏng 5 reviewer |
| `/academic-research-skills:academic-pipeline` | research → paper end-to-end |
| `/academic-research-skills:deep-research` | research-team 13-agent, 7 mode |

---

## Mẹo dùng trên mobile

1. Không có popup `/` → gõ **tên đầy đủ kèm namespace**: `/superpowers:brainstorming`.
2. Lưu file này, mở trên mobile, copy-paste lệnh hay dùng.
3. Skill prompt-only chạy mọi nơi; skill tool-thật cần phiên Claude Code có repo.
