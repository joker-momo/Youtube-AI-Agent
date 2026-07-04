# PORTING.md — Chuyển project sang máy khác (Ubuntu)

Khuyến nghị: **Ubuntu 24.04 LTS** (hoặc 22.04). Không dùng Windows trực tiếp —
toàn bộ runner là bash/POSIX, hệ ML Python (Whisper/MeloTTS/torch) và Remotion
render đều hạng nhất trên Linux. Nếu máy đích bắt buộc Windows, chạy trong WSL2
Ubuntu và chấp nhận I/O chậm hơn (project hash/copy file rất nặng — xem bug-470).

---

## 1. Cài đặt nền (thay cho `installers/install_macos.sh`)

`install_macos.sh` dùng Homebrew — trên Ubuntu thay bằng:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv nodejs npm ffmpeg espeak-ng git lsof
# Node cần bản 22: dùng NodeSource hoặc nvm nếu apt bản cũ
# curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
```

Sau đó như trên macOS:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt   # hoặc theo installers/install_macos.sh phần pip
cd remotion && npm install && cd ..
bash tools/setup-melo-venv.sh               # venv riêng cho MeloTTS (giọng Elena)
```

> `tools/melo-venv/` là venv build tại chỗ, KHÔNG copy từ máy Mac (binary arm64
> không chạy trên x86). Chạy lại `setup-melo-venv.sh` trên máy mới.

## 2. Những chỗ macOS-specific phải sửa / lưu ý

### Bắt buộc sửa

| Chỗ | Vấn đề | Việc cần làm |
|---|---|---|
| `installers/run.sh:410` → `scripts/launch_chromium_mac.sh` | Path browser kiểu `/Applications/Brave Browser.app/...` | Viết `scripts/launch_chromium_linux.sh` (hoặc thêm nhánh OS vào script cũ) trỏ tới `/usr/bin/brave-browser` / `google-chrome` / `chromium`, giữ nguyên cờ CDP `--remote-debugging-port=9222` + `--user-data-dir=browser_profiles/default` |
| `configs/*/channel.yaml` → `tts.device` | MeloTTS mặc định `"mps"` (`src/video_agent/tts.py:289`) — MPS là Apple-only | Set `tts.device: "cpu"` (MeloTTS CPU đủ nhanh) hoặc `"cuda"` nếu có GPU NVIDIA |
| Shorts VLM judge (`src/video_agent/shorts/visual_semantic.py`, `vlm_worker.py`) | Qwen-VL chạy qua **MLX/Metal — Apple-only, không có bản Linux** | Tắt VLM judge trong config shorts hoặc thay bằng bản transformers+CUDA. Long-form KHÔNG dùng VLM (channel.yaml đã chuyển sang demographic-term gating) nên pipeline chính không ảnh hưởng |

### Tự xử lý, chỉ cần biết

| Chỗ | Ghi chú |
|---|---|
| `src/video_agent/assets/materialize.py:21` | APFS clonefile (`cp -c`) đã guard `sys.platform == "darwin"`, trên Linux tự rơi về `shutil.copy2`. Không cần sửa. (Nếu format disk đích là btrfs/xfs có reflink, có thể thêm nhánh `cp --reflink=auto` cho nhanh — tùy chọn.) |
| Whisper (`orchestrator/stages/audio.py`) | `whisper.load_model` không pin device — tự chọn CUDA nếu có, không thì CPU (`fp16=False` sẵn). Không cần sửa. |
| Remotion chrome-headless-shell | Remotion tự tải binary đúng platform vào `.remotion/` lần render đầu. Không copy thư mục `.remotion/` từ Mac sang. |
| `render.concurrency: "auto"` | **Giữ nguyên** (hard rule). Remotion tự dò số nhân máy mới. |
| Chống sleep khi render dài | Trên Mac chạy tay `caffeinate -dimsu` (bug-408: máy ngủ giữa render → `net::ERR_NETWORK_CHANGED`). Ubuntu: tắt suspend trong Settings/`systemd`, hoặc chạy `systemd-inhibit --what=sleep bash run.sh --full` |
| ffmpeg encoder | Config dùng `codec: "h264"` software (không có VideoToolbox reference trong repo) → chạy y nguyên. Có GPU NVIDIA thì cân nhắc `h264_nvenc` sau (đổi chất lượng/tốc độ — test trước khi đổi, ưu tiên chất lượng). |

### Tooling dev (tùy chọn, không chặn pipeline)

- `rtk` (token-optimized CLI proxy), `codegraph`, `code-review-graph`, OpenWolf
  hooks (`.wolf/hooks/*.js` chạy bằng node — OK trên Linux), Superpowers plugin:
  cài lại theo máy mới nếu tiếp tục dùng Claude Code/Codex ở đó.
- `.claude/settings.json` hooks giả định `node` + `git` có trong PATH — OK.

## 3. Dữ liệu cần copy sang máy mới

| Thư mục/file | Bắt buộc? | Ghi chú |
|---|---|---|
| Repo (git clone / copy) | ✅ | |
| `.env` | ✅ | API keys (PEXELS_API_KEY, Telegram bot...). KHÔNG commit. |
| `browser_profiles/default/` | ✅ | **Session ChatGPT đã đăng nhập** — copy nguyên thư mục, không thì phải login lại. Copy khi browser ĐÃ ĐÓNG. |
| `asset_library/` (~15GB) | Nên | Không copy thì pipeline tự tải lại từ Pexels (chậm + tốn quota). `metadata.db` đi kèm file — copy cả cặp, lệch là `is_file_valid` loại hết. |
| `configs/` | ✅ (trong repo) | style-dna.json, channel.yaml |
| `caches/query_cache.db` | Không cần | Tự build lại, TTL 24h |
| `jobs/` | Tùy | Chỉ cần nếu muốn resume/tham khảo job cũ. `jobs/queue.db` nên để máy mới tự tạo (row cũ trỏ path cũ). |
| `tools/melo-venv/`, `.venv/`, `node_modules/`, `.remotion/` | ❌ KHÔNG copy | Build lại tại chỗ (binary theo arch/OS) |
| `assets/music/`, font, logo brand (`remotion/public/...`) | ✅ (trong repo) | Kiểm tra font Montserrat/Manrope có trên máy mới: `sudo apt install fonts-montserrat` hoặc cài từ Google Fonts — **thiếu font là card/subtitle render sai mặt chữ, dễ bị bỏ sót** |

## 4. Thứ tự dựng máy mới

1. Cài nền (mục 1) + clone repo + copy `.env`, `browser_profiles/`, `asset_library/`.
2. Cài font Montserrat + Manrope, xác nhận `fc-list | grep -i montserrat`.
3. Sửa 3 mục "Bắt buộc sửa" (mục 2).
4. `bash run.sh --full` → check `http://127.0.0.1:8000` (dashboard), `:8001/health`
   (browser-worker), `:9222/json/version` (Chromium CDP).
5. Mở Chromium profile, xác nhận ChatGPT còn đăng nhập (browser-worker cần nó
   để sinh ảnh graphic/thumbnail).
6. Chạy smoke test không render: 
   `PYTHONPATH=src .venv/bin/python -m pytest tests/test_cli.py -q` (gọi Pexels +
   MeloTTS thật — pass là core pipeline sống).
7. Chạy 1 job thật ngắn trước khi giao job dài.

## 5. Skills & rules — cái gì đi theo git, cái gì phải cài lại

Skill/rule đến từ **hai nguồn**. Chỉ nguồn A đi theo `git pull`.

### A. Trong repo — tự có sau `git pull` (đã track)

| Đường dẫn | Là gì |
|---|---|
| `.claude/rules/*` | Rule vận hành Claude Code đọc mỗi phiên (openwolf, skill-routing, agent-bridge) |
| `.claude/skills/*` | 4 skill project-local của Claude Code (debug-issue, explore-codebase, refactor-safely, review-changes) |
| `.agent/AGENTS.md`, `.agent/INSTALL.md` | Policy gốc + hướng dẫn setup Antigravity |
| `.agent/skills/*`, `.agent/workflows/*`, `.agent/agents/*` | Toolbox Antigravity (brainstorming, systematic-debugging, TDD, review…) |
| `.wolf/OPENWOLF.md`, `anatomy.md`, `cerebrum.md` | Protocol OpenWolf + file-map + learnings |

### B. Marketplace plugins — KHÔNG trong repo, phải cài lại per máy

Superpowers, caveman, v.v. nằm ở `~/.claude/plugins/` (per-user), git không mang
theo. **`skill-routing.md` BẮT BUỘC superpowers** — thiếu là hỏng quy trình. Trên
Ubuntu, trong Claude Code chạy `/plugin` → add marketplace → install từng cái, hoặc
theo danh sách (bản đang dùng trên máy Mac tính đến 2026-07-04):

| Plugin | Marketplace (GitHub) | Version | Vai trò |
|---|---|---|---|
| `superpowers` | `anthropics/claude-plugins-official` | 5.1.0 | **Bắt buộc** — workflow OS (brainstorming, TDD, systematic-debugging, code-review…) mà skill-routing.md dựa vào |
| `agent-skills` | `addyosmani/agent-skills` | — | Toolbox checklist (frontend, security, performance, CI/CD…) |
| `caveman` | `JuliusBrussee/caveman` | — | Chế độ trả lời terse (đang bật) |
| `headroom` | `chopratejas/headroom` | 0.22.3 | Nén context |
| `academic-research-skills` | `Imbad0202/academic-research-skills` (git url) | 3.9.3 | ARS (nghiên cứu học thuật) — tùy chọn, không liên quan pipeline video |

> Chỉ **superpowers** là load-bearing cho quy trình sửa code (skill-routing bắt
> buộc). Số còn lại là tiện ích — cài nếu muốn parity, bỏ qua vẫn chạy pipeline được.

### C. MCP servers (tùy chọn dev tooling, cũng per-máy)

CLAUDE.md/agent nhắc `codegraph`, `code-review-graph`, `rtk`, `serena`… — đây là
MCP server / CLI cài riêng, không trong repo. Không có chúng thì agent tự rơi về
Grep/Read/git thường (chậm hơn, vẫn hoạt động). Cài lại nếu tiếp tục dev nặng trên
Ubuntu; không cần cho việc chỉ chạy pipeline.

## 6. Bẫy đã biết (từ buglog)

- **Disk chậm/USB**: bug-470 — asset validation từng quét SHA256 cả library. Đã
  fix (cache + score-first), nhưng máy mới nên để `asset_library/` trên SSD nội bộ.
- **Máy ngủ giữa render**: bug-408 — render 1-2.5h chết giữa chừng vì suspend.
  Tắt suspend trước khi giao render dài.
- **HF tokenizers deadlock sau fork**: `run.sh` đã export `TOKENIZERS_PARALLELISM=false`
  (xem comment dòng ~280) — giữ nguyên khi chỉnh run.sh cho Linux.
- **Browser-worker ghi file muộn sau khi client timeout**: đã có late-recovery
  sweep (bug-473) — không cần làm gì, nhưng đừng "dọn" file PNG lạ khi stage
  graphic đang chạy.
