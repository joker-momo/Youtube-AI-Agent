# Codex Spec — Tối ưu công nghệ & kiến trúc cho `Youtube-AI-Agent`

Repo mục tiêu: `https://github.com/joker-momo/Youtube-AI-Agent`

Tài liệu này là spec thực thi cho Codex / Claude Code. Mục tiêu là tối ưu kiến trúc hiện tại mà **không rewrite toàn bộ project**, giữ nguyên khả năng chạy local Docker-first, giữ dashboard hiện tại hoạt động, và giảm rủi ro khi tiếp tục phát triển pipeline sản xuất video YouTube.

---

## 0. Bối cảnh kiến trúc hiện tại

Project hiện đi theo hướng local video-production appliance:

```text
User browser
-> FastAPI dashboard
-> orchestrator/state machine
-> stage modules
-> browser-worker
-> browser-runtime Chromium + Playwright CDP
-> ChatGPT / Claude / keyword scoring sessions
-> local artifacts under jobs/<job_id>/
-> TTS / Whisper / Remotion render
-> review page / final video
```

Các điểm cần giữ:

- Docker-first workflow.
- Local FastAPI web dashboard.
- Browser-runtime + browser-worker tách riêng.
- Persisted browser profile under `browser_profiles/default`.
- File-based job artifacts under `jobs/<job_id>/`.
- Existing v2 CLI flow vẫn phải chạy.
- Existing v3 dashboard flow không được crash.
- Manual YouTube upload vẫn là Phase 1 scope.

---

## 1. Mục tiêu tối ưu

Có 6 nhóm tối ưu chính:

1. **Refactor FastAPI `app.py`** để tránh god-file.
2. **Tách long-running stage execution khỏi HTTP request path** bằng queue/command model nhẹ.
3. **Thêm file locking + atomic writes** cho `job.json`, artifact JSON và event logs.
4. **Chuẩn hóa provider interfaces** cho LLM, keyword scorer, browser automation, asset/image, TTS, renderer.
5. **Hardening security cho `.env` / config endpoints**.
6. **Dọn documentation/naming drift** giữa ChatGPT, Claude, Gemini legacy, v2/v3.

Không làm trong scope này:

- Không triển khai Kubernetes.
- Không rewrite UI bằng React/Vue.
- Không bắt buộc thêm database server.
- Không thay browser automation bằng paid API.
- Không làm YouTube auto-upload.
- Không thay đổi business logic sản xuất video trừ khi cần để tách module.

---

## 2. Nguyên tắc triển khai bắt buộc

- Mỗi phase phải có test hoặc manual verification rõ.
- Refactor phải giữ backward compatibility.
- Không thay đổi public route nếu không cần.
- Nếu route cũ còn dùng, giữ alias hoặc wrapper.
- Không làm mất output artifact cũ.
- Không đưa secrets vào logs hoặc API response.
- Các thay đổi lớn phải có feature flag nếu có rủi ro.
- Ưu tiên small PR-style changes, không sửa 20 thứ trong một commit lớn.

---

## 3. Phase 1 — Refactor `src/video_agent/web/app.py`

### 3.1. Vấn đề

`src/video_agent/web/app.py` hiện đang gánh nhiều trách nhiệm:

- FastAPI app creation.
- Route definitions.
- Dashboard HTML inline.
- CSS/JS inline.
- Job actions.
- Stage actions.
- Idea-generation endpoints.
- Config/env endpoints.
- Artifact rendering helpers.

Điều này làm file khó đọc, khó test và dễ bị Codex sửa nhầm khi thêm feature mới.

### 3.2. Mục tiêu

Tách `app.py` thành nhiều router/module nhỏ mà không đổi hành vi.

### 3.3. File/folder cần tạo

```text
src/video_agent/web/
  app.py
  deps.py
  routers/
    __init__.py
    dashboard.py
    jobs.py
    stages.py
    ideas.py
    config.py
    artifacts.py
  static/
    dashboard.js
    dashboard.css
  templates/
    dashboard.html
```

Nếu project chưa dùng template engine, có thể giữ HTML string tạm thời trong `templates/dashboard.html` và load bằng `Path.read_text()` để tránh thêm dependency.

### 3.4. Yêu cầu chi tiết

#### `app.py`

Sau refactor, `app.py` chỉ nên làm:

```python
from fastapi import FastAPI
from video_agent.web.routers import dashboard, jobs, stages, ideas, config, artifacts

app = FastAPI(...)
app.include_router(dashboard.router)
app.include_router(jobs.router)
app.include_router(stages.router)
app.include_router(ideas.router)
app.include_router(config.router)
app.include_router(artifacts.router)
```

Giữ lại WebSocket route ở `app.py` hoặc tách sang `routers/events.py` nếu dễ.

#### `deps.py`

Chứa helper chung:

```python
get_jobs_dir()
get_channel_config_path()
get_browser_worker_url()
load_job(job_id)
save_job(job)
```

Nếu đã có helper tương tự ở module khác, reuse thay vì tạo mới.

#### `routers/jobs.py`

Chứa route liên quan job lifecycle:

```text
GET /jobs
POST /jobs
GET /jobs/{job_id}
POST /jobs/{job_id}/run-all
POST /jobs/{job_id}/stop
```

#### `routers/stages.py`

Chứa route stage-specific:

```text
POST /jobs/{job_id}/stages/{stage_name}/run
POST /jobs/{job_id}/stages/{stage_name}/promote
POST /jobs/{job_id}/stages/{stage_name}/auto
```

Nếu route hiện tại có path khác, giữ path cũ bằng alias hoặc preserve nguyên path.

#### `routers/ideas.py`

Chứa:

```text
POST /channels/{channel_id}/ideas/generate
GET /channels/{channel_id}/ideas
```

Áp dụng backward compatibility cho keyword result mới/cũ nếu chưa có.

#### `routers/config.py`

Chứa config/env endpoints nhưng phải hardening ở Phase 5.

#### `routers/artifacts.py`

Chứa route download/preview artifact:

```text
GET /jobs/{job_id}/artifacts/{artifact_name}
GET /jobs/{job_id}/review
```

### 3.5. Acceptance criteria

- `docker compose run --rm video-agent pytest -v` pass.
- Dashboard mở được ở `http://localhost:8000`.
- Các route cũ vẫn hoạt động.
- Không mất WebSocket progress.
- Không thay đổi format `jobs/<job_id>/`.
- `app.py` giảm xuống còn app creation + include routers + minimal glue.

---

## 4. Phase 2 — Tách long-running stage execution khỏi HTTP request path

### 4.1. Vấn đề

Các route kiểu `run-all`, `stage/run`, `render/run`, `auto` có thể chạy tác vụ dài trực tiếp trong HTTP request. Điều này dễ gây:

- Request timeout.
- UI bị treo.
- Khó cancel/resume.
- Race condition khi người dùng bấm nhiều lần.
- Khó scale worker.

### 4.2. Mục tiêu

FastAPI app chỉ đóng vai trò control-plane:

```text
HTTP request
-> validate command
-> enqueue command
-> return 202 Accepted
```

Worker đóng vai trò execution-plane:

```text
poll queue
-> acquire job lock
-> run stage
-> write events/job state
-> release lock
```

### 4.3. Cách triển khai tối thiểu

Nếu project đã có `worker` service và `jobs/queue.db`, reuse nó. Không thêm Redis/Celery trong scope này.

Tạo module:

```text
src/video_agent/queue/
  __init__.py
  models.py
  sqlite_queue.py
```

Hoặc nếu đã có queue module, chỉ mở rộng.

### 4.4. Queue command schema

```json
{
  "id": "cmd_...",
  "job_id": "job_...",
  "command_type": "run_all|run_stage|promote_stage|stop_job",
  "stage_name": "script|scenes|seo|assets|tts|render|null",
  "payload": {},
  "status": "queued|running|succeeded|failed|cancelled",
  "created_at": "ISO-8601",
  "started_at": null,
  "finished_at": null,
  "error": null
}
```

### 4.5. HTTP behavior

Routes should return:

```json
{
  "accepted": true,
  "command_id": "cmd_...",
  "job_id": "job_...",
  "status": "queued"
}
```

HTTP status: `202`.

### 4.6. Worker behavior

Worker loop:

```python
while True:
    command = queue.claim_next()
    if not command:
        sleep(...)
        continue

    with job_lock(command.job_id):
        run_command(command)
        write_event(...)
        update_job_state(...)
```

### 4.7. Backward compatibility

If current UI expects immediate JSON with final result, return accepted JSON and rely on WebSocket/polling. If needed, keep old synchronous route behind env flag:

```text
ENABLE_SYNC_STAGE_ROUTES=true
```

Default should be async/queued once stable.

### 4.8. Acceptance criteria

- `POST /jobs/{job_id}/run-all` returns quickly with `202`.
- Worker picks up command and runs job.
- Dashboard progress still updates.
- Stop job works through queue or stop flag.
- Running same stage twice concurrently is blocked by job lock.
- Existing CLI still works without queue.

---

## 5. Phase 3 — File locking + atomic writes

### 5.1. Vấn đề

Project dùng file-based state under `jobs/<job_id>/`. Đây là đúng cho local appliance, nhưng cần bảo vệ khỏi race/corruption.

Rủi ro:

- Hai route/worker ghi `job.json` cùng lúc.
- Ghi JSON giữa chừng bị crash.
- `events.jsonl` bị interleave.
- Artifact được đọc khi đang ghi.

### 5.2. Mục tiêu

Tạo utilities chuẩn cho atomic write và lock.

### 5.3. File cần tạo

```text
src/video_agent/storage/
  __init__.py
  atomic.py
  locks.py
```

### 5.4. API đề xuất

```python
from pathlib import Path
from contextlib import contextmanager

@contextmanager
def file_lock(lock_path: Path, timeout_sec: float = 30.0):
    ...

def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    ...

def atomic_write_json(path: Path, data: dict, indent: int = 2) -> None:
    ...

def append_jsonl_locked(path: Path, event: dict) -> None:
    ...
```

Implementation detail:

```text
write temp file in same directory
flush
fsync
rename temp -> target
```

Lock file pattern:

```text
jobs/<job_id>/.job.lock
```

### 5.5. Apply to files

Use atomic writes for:

```text
job.json
script.json
scenes.json
seo.json
research.json
assets_manifest.json
render_props.json
*_qa.json
```

Use locked append for:

```text
events.jsonl
```

### 5.6. Acceptance criteria

- Unit test atomic write creates valid JSON.
- Simulated exception during write does not corrupt old file.
- Concurrent append to `events.jsonl` does not lose events.
- Existing readers still work.

---

## 6. Phase 4 — Provider interfaces

### 6.1. Vấn đề

Browser automation hiện là lợi thế lớn, nhưng cũng là điểm brittle. Nếu orchestrator phụ thuộc trực tiếp vào ChatGPT/Claude/keyword scoring browser workflow, sau này khó đổi sang API/provider khác.

### 6.2. Mục tiêu

Tạo interface/adapter layer. Không cần rewrite provider hiện có; chỉ bọc chúng sau interface.

### 6.3. File/folder đề xuất

```text
src/video_agent/providers/
  __init__.py
  llm.py
  keyword.py
  image.py
  tts.py
  renderer.py
  browser_llm.py
  browser_keyword.py
```

### 6.4. Interfaces

#### LLM Provider

```python
from typing import Protocol, Any

class LLMProvider(Protocol):
    async def generate_text(self, prompt: str, *, timeout_sec: int | None = None) -> str: ...
    async def generate_json(self, prompt: str, *, schema: dict | None = None, timeout_sec: int | None = None) -> dict[str, Any]: ...
```

#### Keyword Scorer

```python
class KeywordScorer(Protocol):
    async def score_keywords(self, keywords: list[str]) -> list[dict]: ...
```

#### Image Provider

```python
class ImageProvider(Protocol):
    async def generate_image(self, prompt: str, output_path: str, *, aspect_ratio: str = "16:9") -> dict: ...
```

#### TTS Provider

```python
class TTSProvider(Protocol):
    def synthesize(self, text: str, output_path: str, *, voice_id: str, lang_code: str) -> dict: ...
```

#### Renderer

```python
class VideoRenderer(Protocol):
    def render(self, render_props_path: str, output_path: str) -> dict: ...
```

### 6.5. Adapter requirement

Existing browser-worker based ChatGPT/Claude/keyword scoring should become adapter implementations:

```text
BrowserChatGPTProvider
BrowserClaudeProvider
BrowserKeywordScorer
```

Do not remove old code immediately. Wrap/reuse it.

### 6.6. Acceptance criteria

- Orchestrator can call `LLMProvider` instead of raw browser logic for new paths.
- Existing browser flow still works.
- Adding future OpenAI/Anthropic API provider would not require changing orchestrator stage logic.
- Unit tests can mock provider interfaces without launching browser.

---

## 7. Phase 5 — Security hardening for config/env endpoints

### 7.1. Vấn đề

If web app is ever exposed beyond localhost, endpoints that read/write `.env` or return config can leak secrets.

### 7.2. Mục tiêu

Make config endpoints safe by default.

### 7.3. Required env flags

```text
ENABLE_ENV_EDITOR=false
ADMIN_TOKEN=
```

Behavior:

- If `ENABLE_ENV_EDITOR` is not true, write endpoints must return `403`.
- Read endpoint must mask secrets by default.
- If write endpoint enabled, require `ADMIN_TOKEN` header.

Header:

```text
X-Admin-Token: <token>
```

### 7.4. Masking rules

Mask keys containing:

```text
KEY
TOKEN
SECRET
PASSWORD
COOKIE
SESSION
API
```

Example response:

```json
{
  "PEXELS_API_KEY": "********abcd",
  "PIXABAY_API_KEY": "********wxyz",
  "TZ": "Asia/Ho_Chi_Minh"
}
```

### 7.5. Acceptance criteria

- `.env` write endpoints disabled by default.
- Raw secrets are not returned from GET config endpoint.
- Admin token required when editor enabled.
- Existing local bootstrap flow still has clear error message if disabled.

---

## 8. Phase 6 — Documentation and naming cleanup

### 8.1. Vấn đề

Docs and paths may still contain legacy references such as `gemini` for QA even though current flow uses Claude QA. This confuses Codex and future maintainers.

### 8.2. Mục tiêu

Clarify provider naming without breaking legacy file paths.

### 8.3. Required docs updates

Update:

```text
README.md
docs/HANDOFF.md
docs/PROJECT_STATUS.md
docs/VIDEO_AGENT_V3_STANDALONE_HANDOFF.md
```

Add a section:

```md
## Provider naming

- Writer: ChatGPT browser session.
- QA reviewer: Claude browser session.
- Some legacy paths may still use `gemini` in folder names for backward compatibility.
- Do not rename legacy job artifact paths unless migration is implemented.
```

### 8.4. Acceptance criteria

- Docs no longer imply Gemini is active QA provider unless marked legacy.
- README explains v2 vs v3 clearly.
- Codex can read docs and know the current default flow.

---

## 9. Phase 7 — Docker optimization roadmap

This phase is optional and should not block the previous phases.

### 9.1. Current concern

Single image appears to carry many responsibilities:

- FastAPI app.
- CLI.
- Worker.
- TTS/Whisper/Torch.
- Remotion/Node.
- Playwright-related code.

This is acceptable for MVP but makes build heavy and cache invalidation expensive.

### 9.2. Target split

Later split into:

```text
app image:
  FastAPI + orchestrator + lightweight deps

worker image:
  TTS + Whisper + Remotion + asset/render deps

browser-worker image:
  FastAPI worker + Playwright client deps

browser-runtime image:
  Chromium + KasmVNC + persistent profile
```

### 9.3. Do not implement unless asked

For now, only document this as roadmap. Do not split Dockerfile in this pass unless explicitly requested.

---

## 10. Testing plan

### 10.1. Unit tests

Add tests for:

```text
storage atomic write
storage lock
config masking
queue enqueue/claim/complete
provider interface mocks
flatten keyword result if touching ideas endpoint
```

### 10.2. Integration/manual tests

Run:

```bash
docker compose build
docker compose up app worker browser-worker browser-runtime
```

Manual verify:

```text
1. Dashboard opens at http://localhost:8000
2. Existing jobs list loads
3. Create/generate idea still works
4. Run one lightweight stage
5. Stop job works
6. Review page/artifacts still load
7. Browser-worker health route still works
8. No raw secrets visible in config UI/API
```

### 10.3. Regression tests

Run:

```bash
docker compose run --rm video-agent pytest -v
```

If full tests are slow, at minimum run:

```bash
docker compose run --rm video-agent pytest tests/test_idea_generator.py -v
docker compose run --rm video-agent pytest tests/ -k "web or job or storage or queue" -v
```

---

## 11. Recommended implementation order for Codex

Implement in this exact order:

```text
1. Add storage atomic write + lock utilities with tests.
2. Refactor app.py into routers without changing behavior.
3. Add env/config endpoint hardening.
4. Add queue command abstraction if not already robust.
5. Move long-running route execution to queue where safe.
6. Add provider interfaces and adapt existing browser providers lightly.
7. Update docs/naming drift.
8. Leave Docker image split as documented roadmap only.
```

Reason: storage safety should come before queue-based execution because queue increases concurrent writes.

---

## 12. Definition of Done

This optimization pass is complete when:

- Dashboard still works.
- v2 CLI still works.
- Existing tests pass.
- New storage/config/queue tests pass.
- `app.py` is no longer a god-file.
- Long-running stages can be queued or have a clear migration path.
- Job file writes are atomic.
- Event writes are locked or safe.
- Config secrets are masked by default.
- Provider interfaces exist for future API/browser provider swaps.
- Docs clearly describe current v3 flow and legacy naming.

---

## 13. Notes for Codex

- Prefer minimal diffs.
- Do not rename public artifact paths unless a migration is included.
- Do not remove existing CLI commands.
- Do not remove browser-worker or browser-runtime.
- Do not expose CDP port to host.
- Do not add cloud dependencies.
- Do not require host Python or host Node.
- Preserve Docker-first workflow.
