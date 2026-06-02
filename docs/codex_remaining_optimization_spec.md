# Codex Spec — Remaining Architecture Optimizations for `Youtube-AI-Agent`

This spec covers **only the remaining unfinished work** after the latest review of `main`.

Do **not** re-implement completed work unless needed for tests or compatibility.

Current status observed:

- Keyword scoring V2 is mostly implemented.
- `.env` endpoint hardening is mostly implemented.
- `/run-all` is queued through `JobQueue` and worker.
- `atomic_write_text`, `atomic_write_json`, `append_jsonl_locked`, and `file_lock` exist.
- `job.json` already uses atomic writes.
- `app.py` is still too large and still contains most route logic.
- Provider interfaces are not yet implemented.
- Docs still contain Gemini/Gemini naming drift.
- Atomic writes and queue behavior are not consistently covered by tests.

---

## 0. General rules for Codex

1. Keep this work as a **small, reviewable refactor**, not a rewrite.
2. Preserve all existing public routes, response schemas, CLI commands, and Docker Compose service names.
3. Prefer additive changes and compatibility wrappers.
4. Do not change the video production business logic unless explicitly required.
5. Run tests after each phase.
6. If a phase becomes too large, stop after the first safe slice and document the remaining work.
7. Do not add SaaS/multi-user complexity.
8. This remains a local Docker-first production appliance.

Recommended execution order:

```text
Phase 1: Refactor app.py into route modules
Phase 2: Apply atomic writes to remaining critical file writes
Phase 3: Add tests for env hardening, queue, atomic writes
Phase 4: Queue remaining long-running stage routes where safe
Phase 5: Add provider interface skeletons without behavior change
Phase 6: Clean docs/naming drift
Phase 7: Keyword scoring V2 follow-up cleanup
```

If Codex can only do one task, start with **Phase 1**.

---

## 1. Phase 1 — Refactor `src/video_agent/web/app.py` into route modules

### Problem

`src/video_agent/web/app.py` is still a god file. It contains:

- FastAPI app construction
- env config routes
- job CRUD routes
- timeline/artifact/log routes
- stage run/promote routes
- approval routes
- channel/idea routes
- `/run-all` and `/run-batch`
- websocket events
- fallback static file serving
- helper functions and Pydantic request models

This makes future Codex changes risky because small edits can accidentally break unrelated routes.

### Goal

Split `app.py` into smaller modules while preserving existing behavior.

### Required target structure

Create this structure if it does not exist:

```text
src/video_agent/web/
  app.py
  deps.py
  models.py
  routes/
    __init__.py
    config.py
    jobs.py
    timeline.py
    artifacts.py
    stages.py
    approvals.py
    channels.py
    run.py
    websocket.py
```

The exact file split may vary, but the result must keep `app.py` small.

### Target responsibility

#### `app.py`

Should only:

- create `FastAPI(title="video-agent-web", version="0.1.0")`
- include routers
- expose `GET /health`
- expose dashboard root if simple
- keep compatibility imports if tests depend on them

Suggested target:

```python
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from video_agent.web.routes import (
    config,
    jobs,
    timeline,
    artifacts,
    stages,
    approvals,
    channels,
    run,
    websocket,
)

app = FastAPI(title="video-agent-web", version="0.1.0")

@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "app"}

app.include_router(config.router)
app.include_router(jobs.router)
app.include_router(timeline.router)
app.include_router(artifacts.router)
app.include_router(stages.router)
app.include_router(approvals.router)
app.include_router(channels.router)
app.include_router(run.router)
app.include_router(websocket.router)
```

#### `deps.py`

Move shared dependencies and safe path helpers here:

```python
get_jobs_root
get_channel_path
get_inputs_root
get_browser_client
_safe_job_dir
_safe_channel_id
resolve env paths if needed
```

Do not introduce circular imports.

#### `models.py`

Move request models here:

```python
CreateJobRequest
RawScriptRequest
EnvSaveRequest
GenerateIdeasRequest
ScoreIdeasRequest
RunBatchRequest
```

#### `routes/config.py`

Move:

```text
GET /config/env
POST /config/env
POST /config/env/bootstrap
```

Also move:

```python
_env_path
_env_example_path
_env_editor_enabled
_require_env_editor
_mask_env_value
_mask_env_content
```

#### `routes/jobs.py`

Move:

```text
GET /jobs
POST /jobs
GET /jobs/{job_id}
DELETE /jobs/{job_id}
POST /jobs/{job_id}/stop
POST /jobs/{job_id}/advance
GET /jobs/{job_id}/events
POST /jobs/{job_id}/idea
```

#### `routes/timeline.py`

Move:

```text
GET /jobs/{job_id}/timeline
GET /jobs/{job_id}/logs
```

#### `routes/artifacts.py`

Move:

```text
GET /jobs/{job_id}/artifact
GET /jobs/{job_id}/{path:path}
```

Important: route ordering must still avoid the fallback route shadowing specific routes.

#### `routes/stages.py`

Move all stage run/promote/auto routes:

```text
/stages/script/run
/stages/script/promote
/stages/scenes/run
/stages/scenes/promote
/stages/seo/run
/stages/seo/promote
/stages/whisper_timestamps/run
/stages/render/run
/stages/shorts_render/progress
/stages/render/progress
/stages/review/run
/stages/persona_eval/run
/stages/idea_research/auto
/stages/seo_keyword/auto
/stages/{stage_name}/regenerate
/stages/script/auto
/stages/scenes/auto
/stages/seo/auto
/stages/script_qa/auto
/stages/scenes_qa/auto
/stages/seo_qa/auto
/stages/thumbnail_image/auto
/scenes/{scene_id}/generate_asset
```

#### `routes/approvals.py`

Move:

```text
GET /jobs/{job_id}/approvals
POST /jobs/{job_id}/approvals/{stage_name}/confirm
POST /jobs/{job_id}/approvals/{stage_name}/clear
```

#### `routes/channels.py`

Move:

```text
GET /channels
GET /channels/{channel_id}/ideas
POST /channels/{channel_id}/ideas/score
GET /channels/{channel_id}/sync-videos
POST /channels/{channel_id}/ideas/generate
```

Also move:

```python
flatten_keyword_result_for_ui
```

But preserve import compatibility for tests that currently import:

```python
from video_agent.web.app import flatten_keyword_result_for_ui
```

Compatibility solution:

```python
# in app.py
from video_agent.web.routes.channels import flatten_keyword_result_for_ui
```

#### `routes/run.py`

Move:

```text
POST /jobs/{job_id}/run-all
POST /run-batch
```

#### `routes/websocket.py`

Move:

```text
WS /jobs/{job_id}/events
```

### Acceptance criteria

- All existing route paths continue to work.
- Existing tests continue to pass.
- `from video_agent.web.app import app` still works.
- Existing tests importing `get_browser_client`, `get_inputs_root`, or `flatten_keyword_result_for_ui` from `web.app` still pass. Re-export these in `app.py` if necessary.
- `app.py` should be reduced substantially. Target: under 200 lines if possible.
- No behavior changes in this phase.

### Suggested tests

Run:

```bash
pytest tests/test_idea_generator.py
pytest tests/test_web_health.py
pytest tests/test_orchestrator.py
```

If the repo has broader tests available, run:

```bash
pytest -q
```

---

## 2. Phase 2 — Apply atomic writes to remaining critical file writes

### Problem

The repo now has `atomic_write_text`, `atomic_write_json`, and file locks, but many file writes still use direct `Path.write_text(...)`.

Examples to check:

- `post_idea` writing `idea.json`
- `stop_job` writing stop flag
- `save_ideas` writing generated idea JSON files
- `sync_published_videos` writing `published_videos.json`
- approval writes if not already atomic
- stage artifact writes where safe
- event log appends if not already using `append_jsonl_locked`

### Goal

Use atomic writes for important JSON/text state files that can be read by dashboard/worker while being written.

### Required changes

Use:

```python
from video_agent.storage.atomic import atomic_write_text, atomic_write_json, append_jsonl_locked
```

Replace critical direct JSON writes:

```python
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
```

with:

```python
atomic_write_json(path, payload)
```

Replace critical text writes:

```python
path.write_text(text, encoding="utf-8")
```

with:

```python
atomic_write_text(path, text, encoding="utf-8")
```

### Scope rules

Do not blindly replace every `write_text` in the repo. Prioritize files that are:

- job state
- generated idea JSON
- published video cache
- approval JSON
- stage JSON artifacts
- progress JSON
- stop/request flags

Do not change large binary writes in this phase.

### Acceptance criteria

- `job.json` still uses `atomic_write_json`.
- `idea.json`, generated idea files, and published video cache use atomic writes.
- Event JSONL writes use a lock or existing logger if already safe.
- Tests pass.

### Suggested tests

Add tests for:

```text
test_atomic_write_json_writes_valid_json
test_atomic_write_text_replaces_existing_file
test_save_ideas_uses_safe_channel_path
test_post_idea_writes_valid_json
```

---

## 3. Phase 3 — Add missing tests for security, queue, and atomic utilities

### Problem

Keyword scoring has tests, but the new architecture-hardening pieces need dedicated tests.

### Goal

Add tests for:

- `.env` masking and write protection
- queue enqueue behavior
- worker retry/non-retry logic if easy
- atomic utility behavior

### Required tests

Create/update:

```text
tests/test_web_config_env.py
tests/test_queue.py
tests/test_storage_atomic.py
```

or put them in existing suitable test files.

### Env endpoint tests

Test cases:

```text
GET /config/env masks API keys and tokens
POST /config/env returns 403 when ENABLE_ENV_EDITOR is not true
POST /config/env/bootstrap returns 403 when editor disabled
POST /config/env works when ENABLE_ENV_EDITOR=true and ADMIN_TOKEN matches
POST /config/env returns 403 when ADMIN_TOKEN is configured but header is missing/wrong
```

Secrets to test:

```text
OPENAI_API_KEY=sk-test-secret
TELEGRAM_BOT_TOKEN=123456:secret
PASSWORD=supersecret
NORMAL_VALUE=visible
```

Expected:

```text
OPENAI_API_KEY=********cret or equivalent masked suffix
TELEGRAM_BOT_TOKEN=********xxxx
PASSWORD=********xxxx
NORMAL_VALUE=visible
```

Do not assert exact suffix if implementation differs. Assert raw secret is not present.

### Queue tests

Test cases:

```text
test_enqueue_creates_pending_job
test_enqueue_existing_job_resets_to_pending
test_get_next_job_returns_oldest_pending
test_mark_running_completed_failed
test_mark_retry_until_max_attempts
test_requeue_running_jobs
```

Use a temp SQLite file under `tmp_path`.

### Atomic tests

Test cases:

```text
test_atomic_write_text_creates_parent_and_writes
test_atomic_write_json_writes_valid_json
test_append_jsonl_locked_appends_valid_json_lines
test_file_lock_blocks_second_thread_or_times_out
```

Keep tests deterministic and fast.

### Acceptance criteria

- New tests pass locally.
- Existing tests still pass.
- Tests do not require real Docker browser runtime, ChatGPT, Gemini, or keyword scoring.

---

## 4. Phase 4 — Queue remaining long-running routes where safe

### Problem

`/jobs/{job_id}/run-all` is queued, but several individual stage routes still run heavy work directly in the request path.

Examples:

```text
/jobs/{job_id}/stages/render/run
/jobs/{job_id}/stages/whisper_timestamps/run
/jobs/{job_id}/stages/thumbnail_image/auto
/jobs/{job_id}/stages/script/auto
/jobs/{job_id}/stages/scenes/auto
/jobs/{job_id}/stages/seo/auto
/jobs/{job_id}/stages/idea_research/auto
/jobs/{job_id}/stages/seo_keyword/auto
/jobs/{job_id}/scenes/{scene_id}/generate_asset
```

### Goal

Do not attempt to queue everything at once. Add a minimal queue command model that supports at least the heaviest individual stage routes.

### Minimal design

Extend `JobQueue` to support an optional `command` and `payload`.

Current table roughly contains:

```text
job_id
status
enforce_approvals
created_at
started_at
completed_at
attempts
error
```

Add columns if missing:

```text
command TEXT DEFAULT 'run_all'
payload TEXT
```

Backward compatibility:

- Existing queue rows without command should behave as `run_all`.
- Existing `/run-all` enqueue should continue working.

### Suggested API behavior

For heavy stage route, add optional query parameter:

```text
?async=true
```

If `async=true`, route enqueues a stage command and returns quickly:

```json
{
  "job_id": "...",
  "status": "enqueued",
  "command": "stage_render"
}
```

If `async=false` or omitted, keep current behavior for now to preserve UI behavior.

Start with only these commands:

```text
stage_render
stage_whisper_timestamps
stage_thumbnail_image_auto
```

Do not queue every single ChatGPT/Gemini stage in the first pass unless easy.

### Worker changes

Update worker dispatch:

```python
command = job.get("command") or "run_all"
if command == "run_all":
    execute_run_all(...)
elif command == "stage_render":
    run_render_stage(...)
elif command == "stage_whisper_timestamps":
    run_whisper_timestamps_stage(...)
elif command == "stage_thumbnail_image_auto":
    auto_thumbnail_image_stage(...)
else:
    mark_failed(job_id, f"Unknown queue command: {command}")
```

### Acceptance criteria

- `/run-all` still queues as before.
- At least render and whisper timestamp stages can be enqueued asynchronously.
- Worker can dispatch queued commands safely.
- Tests cover enqueue/dispatch behavior without real browser calls.
- Direct synchronous route behavior is preserved unless `async=true` is used.

---

## 5. Phase 5 — Add provider interface skeletons without behavior change

### Problem

`src/video_agent/providers` currently contains concrete provider code but lacks explicit interfaces for the major external systems.

### Goal

Add lightweight protocols/interfaces to reduce coupling and prepare for future replacement of browser automation/API providers.

### Required new file

Create:

```text
src/video_agent/providers/interfaces.py
```

### Required protocols

Use `typing.Protocol`.

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

class LLMProvider(Protocol):
    async def generate_text(self, messages: list[str], *, site: str = "chatgpt") -> str: ...

class KeywordScorer(Protocol):
    async def score_keywords(self, keywords: list[str]) -> list[dict[str, Any]]: ...

class ImageProvider(Protocol):
    async def generate_image(self, prompt: str, *, output_path: Path | None = None) -> dict[str, Any]: ...

class TTSProvider(Protocol):
    def synthesize(self, text: str, output_path: Path, **kwargs: Any) -> dict[str, Any]: ...

class Renderer(Protocol):
    def render(self, job_dir: Path, channel_path: Path, **kwargs: Any) -> Path | None: ...
```

### Adapter for BrowserClient

Create:

```text
src/video_agent/providers/browser_client_adapter.py
```

Suggested:

```python
class BrowserClientLLMProvider:
    def __init__(self, client: BrowserClient):
        self.client = client

    async def generate_text(self, messages: list[str], *, site: str = "chatgpt") -> str:
        return await self.client.run_session(site, messages)

class BrowserClientKeywordScorer:
    def __init__(self, client: BrowserClient):
        self.client = client

    async def score_keywords(self, keywords: list[str]) -> list[dict]:
        return await self.client.run_keyword_scores(keywords)

class BrowserClientImageProvider:
    def __init__(self, client: BrowserClient):
        self.client = client

    async def generate_image(self, prompt: str, *, output_path: Path | None = None) -> dict:
        return await self.client.generate_image(prompt, output_path=output_path)
```

Adjust method signatures to match the actual `BrowserClient` implementation.

### Export

Update:

```text
src/video_agent/providers/__init__.py
```

Export protocols and adapters.

### Scope limitation

Do not refactor the entire orchestrator to use providers yet.

Only add interfaces and a small adapter, plus tests/import checks.

### Acceptance criteria

- New protocols import cleanly.
- Existing code still works.
- BrowserClient adapter wraps existing methods without changing behavior.
- Tests do not require real browser worker.

---

## 6. Phase 6 — Clean docs/naming drift: Gemini vs Gemini

### Problem

The current README describes the V3 target flow as:

```text
ChatGPT script/scenes/SEO -> Gemini QA
```

But `docs/PROJECT_STATUS.md` still contains old references to Gemini QA in several sections.

### Goal

Make docs consistent with the current direction.

### Required changes

Update docs to use:

```text
ChatGPT = writing / generation
Gemini = QA
Gemini = legacy/deferred/optional, only mention where historically relevant
```

Files to review:

```text
README.md
docs/PROJECT_STATUS.md
docs/HANDOFF.md
docs/VIDEO_AGENT_V3_STANDALONE_HANDOFF.md
GEMINI.md
```

### Rules

- Do not erase useful historical notes if they explain old behavior.
- If a section is historical, mark it explicitly as historical.
- Current operating rules should not tell the user to use Gemini if the code now uses Gemini QA.
- Keep phase/status language consistent with the current repo.

### Acceptance criteria

- README and PROJECT_STATUS agree on current target flow.
- No current instruction says Gemini is the primary QA provider unless code still uses Gemini there.
- Historical Gemini references are clearly labeled as historical/legacy.

---

## 7. Phase 7 — Keyword scoring V2 follow-up cleanup

### Problem

Keyword scoring V2 is mostly implemented, but there are cleanup items:

1. Top module docstring still describes old `score DESC` behavior.
2. SERP inspection is still skipped with fixed `serp_opportunity=50`.
3. Some direct writes in `sync_published_videos` and `save_ideas` should use atomic writes.
4. Tests cover V2 helpers, but not every integration/fallback path.

### Goal

Make V2 implementation internally consistent and better documented.

### Required changes

#### 7.1 Update docstring

Replace old docstring language:

```text
Merge all scored keywords, sort by score DESC
```

with:

```text
Merge all scored keywords, enrich with V2 scoring signals, assign buckets, then select top opportunity + long-tail candidates.
```

#### 7.2 Keep SERP inspection disabled by default

Do not implement full Playwright SERP scrape in this pass unless small.

Instead:

- Rename note to `serp_inspection_disabled` or keep `serp_inspection_skipped` consistently.
- Ensure metadata says `enable_serp_inspection=false`.
- Add TODO comment explaining SERP inspection is intentionally deferred.

#### 7.3 Atomic writes

Update:

```text
sync_published_videos -> atomic_write_json
save_ideas -> atomic_write_json
```

#### 7.4 More integration tests

Add tests:

```text
test_discover_top_keywords_v2_returns_bucketed_dict
test_discover_top_keywords_v2_rejects_portuguese_even_with_high_keyword_score
test_generate_ideas_with_metadata_returns_v2_keywords_when_keyword_available
test_select_keywords_for_prompt_uses_top_opportunity_then_long_tail
```

### Acceptance criteria

- Docstring no longer lies about `score DESC` being the main selection logic.
- V2 output remains backward compatible with UI.
- Portuguese high-score keyword is rejected or not selected for prompt.
- Long-tail Spanish keyword can be selected when no enough top opportunity exists.

---

## 8. What not to do in this spec

Do not implement:

- Full SaaS auth/multi-user system.
- Kubernetes/cloud deployment.
- YouTube upload automation.
- Full provider migration away from browser automation.
- Real paid keyword scoring API integration.
- Full semantic embeddings.
- Large UI redesign.
- Database migration beyond minimal SQLite queue columns.

---

## 9. Suggested Codex goals

Use **one goal at a time**. Do not paste this whole spec into the goal box if Codex rejects long goals. Put this file into `docs/` and reference it.

### Goal A — Refactor app.py only

```text
Implement Phase 1 from docs/codex_remaining_optimization_spec.md.

Split src/video_agent/web/app.py into smaller route modules while preserving all existing routes, response formats, and tests. Do not change business logic. Re-export compatibility helpers from app.py if tests import them.
```

### Goal B — Atomic writes + tests

```text
Implement Phases 2 and 3 from docs/codex_remaining_optimization_spec.md.

Apply atomic writes to remaining critical JSON/text file writes and add tests for env hardening, queue behavior, and atomic utilities. Preserve existing behavior.
```

### Goal C — Queue heavy stage routes

```text
Implement Phase 4 from docs/codex_remaining_optimization_spec.md.

Extend JobQueue with command/payload support and add async enqueue support for render, whisper_timestamps, and thumbnail_image auto routes. Keep synchronous behavior by default.
```

### Goal D — Provider interfaces

```text
Implement Phase 5 from docs/codex_remaining_optimization_spec.md.

Add provider Protocol interfaces and BrowserClient adapters without changing orchestrator behavior.
```

### Goal E — Docs cleanup + keyword cleanup

```text
Implement Phases 6 and 7 from docs/codex_remaining_optimization_spec.md.

Clean Gemini/Gemini docs drift, update keyword scoring V2 docstrings, keep SERP inspection disabled by default, and add V2 integration tests.
```

---

## 10. Final acceptance checklist

The optimization work is complete when:

```text
[ ] app.py is small and route modules own their domains.
[ ] All existing endpoints still work.
[ ] /run-all queue behavior still works.
[ ] Render/Whisper/thumbnail heavy routes can be queued asynchronously.
[ ] Atomic writes are used for critical state/artifact JSON files.
[ ] Env editor tests prove secrets are masked and writes are protected.
[ ] Queue tests cover retry/requeue behavior.
[ ] Provider interfaces exist and import cleanly.
[ ] Docs no longer confuse Gemini and Gemini in current instructions.
[ ] Keyword scoring V2 docs and tests match current behavior.
[ ] pytest -q passes.
```

