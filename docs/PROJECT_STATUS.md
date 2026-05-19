# Youtube AI Agent Project Status

Last updated: 2026-05-20 (V3 Phase 1 Step 7 implemented and Docker-verified)

This file is the living project tracker. Update it whenever a meaningful system capability is added, changed, verified, or deferred so a new reader can quickly understand what the system does, what is being built now, and what remains.

## Goal

Build a Docker-first standalone YouTube production app that can take a channel and idea through:

```text
trend/data intake -> idea selection -> ChatGPT script/scenes/SEO -> Gemini QA -> assets/images -> TTS -> Remotion render -> review -> final video
```

The current v2 `operator-*` CLI flow remains functional during the transition. The approved v3 target is a standalone local FastAPI web app with WebSocket progress and a separate browser-worker service attached to a dedicated host Chrome profile.

Current product priority:

- Only prioritize tasks that directly complete the full end-to-end video creation flow.
- The target flow is: trend/data intake -> idea selection -> script -> scenes -> SEO -> assets/images -> TTS -> render -> QA/review -> final video.
- Defer optimization work until the complete final-video flow is reliable.
- Cache, semantic reuse, analytics, dashboards, multi-channel scaling, and other compounding improvements are valuable, but they are not priority unless they unblock the full final-video flow.

## Approved V3 Direction

Reference:

- [VIDEO_AGENT_V3_STANDALONE_HANDOFF.md](/Users/joker/Documents/Youtube-AI-Agent/docs/VIDEO_AGENT_V3_STANDALONE_HANDOFF.md)

Decisions already chosen:

- Standalone Python web app; Hermes is dropped.
- Local FastAPI UI with WebSocket realtime progress.
- Browser web UI access for ChatGPT Plus, Gemini, vidIQ, and ChatGPT image generation; no LLM API client in Phase 1.
- Separate `browser-worker` container using Playwright CDP attach to host Chrome on port `9222`.
- Dedicated host Chrome profile; user logs in manually. The system must not auto-login.
- Sequential per-step flow with file-based state detection.
- Fail-soft browser handling: save trace, expose prompt path, allow user retry.
- Manual YouTube upload in Phase 1.
- Persona evaluation, Telegram, upload automation, semantic asset reuse, analytics, and scaling are deferred.

## Operating Rules

- Run project commands through Docker.
- Use ChatGPT as the primary semi-automated operator for script, scenes, SEO, and optionally image generation.
- Use Gemini as QA for operator-produced artifacts.
- Keep generated job outputs local under `jobs/`.
- Keep the operator workflow resumable from files, not hidden browser state.
- Update this status file as the system evolves.

## Current Capabilities

### MVP Render Pipeline

- Deterministic MVP pipeline from `manual_idea.json` to `video.mp4`.
- Remotion render with `render_props.json` as the render input.
- Thumbnail, SEO JSON, report, visual review, and contact sheet artifacts.
- Dockerized tests and renders.

### Asset Flow

- Local image folder support.
- Free stock API support through Pexels and Pixabay.
- Query cache and asset library foundation are implemented.
- Visual review records provider/source mix, selected assets, fallback placeholders, and warnings.

### TTS

- `mock-local` silent placeholder TTS for fast tests.
- Kokoro local TTS option runs inside Docker.

### Semi-Automated Operator Flow

- `operator-prompts` writes ChatGPT and Gemini prompt files for `script`, `scenes`, and `seo`.
- `operator-promote` validates raw ChatGPT JSON into promoted artifacts and blocks stale or malformed artifacts before they enter the render flow.
- `operator-promote-qa` normalizes raw Gemini QA and requires `PASS`.
- `operator-render` requires promoted Gemini QA by default.
- `operator-review` writes `operator_review.html` for a single job.
- `operator-render` refreshes `operator_review.html` automatically.
- `operator-status` summarizes artifact/QA state for one job.
- `operator-next` creates the next prompt when needed and prints the next command for one job.

### Operator Validators

- `operator-promote` blocks `job_id` mismatch for script, scenes, and SEO artifacts.
- SEO artifacts now include `job_id`.
- Scene promotion blocks invalid `scene-NN` IDs, list-shaped `asset_refs`, missing `visual_prompt`, and ChatGPT-prefilled `qa.verdict=PASS`.
- SEO promotion blocks non-`es-419` language, tag count outside the channel rule, duplicate/empty tags, and forbidden channel positioning such as `adultos mayores`.
- Vida Plena 45+ channel config now declares SEO language/tag limits and positioning rules.

### V3 Phase 1 Step 1 Skeleton

- `app` FastAPI service at `src/video_agent/web/app.py` with `GET /health`.
- `browser-worker` FastAPI service at `src/video_agent/browser_worker/app.py` with `GET /health`.
- `docker-compose.yml` declares both services on host ports `8000` and `8001`; `host.docker.internal:host-gateway` exposes host Chrome to the browser-worker container.
- `scripts/launch-chrome-cdp.sh` launches host Chrome on port `9222` with a dedicated profile under `$HOME/.video-agent/chrome-cdp-profile`. The user logs in manually; the system never auto-logs-in.
- `requirements.txt` adds `fastapi`, `uvicorn`, `httpx`.
- Tests `tests/test_web_health.py` and `tests/test_browser_worker_health.py` cover both health routes.
- Existing v2 `operator-*` CLI flow is unchanged.

### V3 Phase 1 Step 2 Orchestrator Skeleton

- `src/video_agent/orchestrator/job_state.py` defines `JobState` and `StageStatus` dataclasses with JSON round-trip through `jobs/<job_id>/job.json`.
- Default stage list: `script -> scenes -> seo -> render -> review`.
- `src/video_agent/orchestrator/orchestrator.py` exposes `create_job`, `advance`, plus `JobAlreadyExistsError` / `JobNotFoundError` / `StageError`.
- Stage transitions: `pending -> in_progress -> completed`. Each transition appends to `events.jsonl` (`JOB_CREATED`, `STAGE_STARTED`, `STAGE_COMPLETED`, `JOB_COMPLETED`) through the shared `EventLogger`.
- Tests: `tests/test_orchestrator.py` covers create + duplicate guard, full stage walk, and missing-job error.
- Not yet wired into v2 CLI; FastAPI routes are now wired (see Step 3).

### V3 Phase 1 Step 3 FastAPI Job Routes

- `POST /jobs` creates a job folder under `JOBS_DIR` (default `/app/jobs`), writes `job.json`, and returns the initial state.
- `GET /jobs/{job_id}` returns the current `JobState` dict.
- `POST /jobs/{job_id}/advance` runs one orchestrator transition and returns the updated state.
- `GET /jobs/{job_id}/events` returns all entries from `events.jsonl` as a list.
- Errors map to HTTP: missing job -> `404`, duplicate create or stage misuse -> `409`.
- `JOBS_DIR` env var overrides the jobs root; tests override the FastAPI dependency to use `tmp_path`.
- `WS /jobs/{job_id}/events` replays existing `events.jsonl` lines and tails new entries; closes with code `4404` when the job is unknown. Polling interval is configurable via `EVENTS_POLL_SECONDS` (default `0.2s`).

### V3 Phase 1 Step 4 Browser-Worker CDP Diagnostic

- `requirements.txt` adds `playwright>=1.40` (driver-only; no bundled browser needed for CDP attach).
- `GET /chrome` on the browser-worker calls `playwright.async_api.chromium.connect_over_cdp(CHROME_CDP_URL)` and returns `{ok, cdp_url, contexts, pages}` when the host Chrome is reachable.
- Unreachable CDP endpoint returns HTTP `503` with `{cdp_url, error}` so the caller can prompt the user to run `scripts/launch-chrome-cdp.sh`.
- `CHROME_CDP_URL` defaults to `http://host.docker.internal:9222`; `extra_hosts` in `docker-compose.yml` already wires the gateway entry.
- Test `tests/test_browser_worker_chrome.py` verifies the 503 path against an unreachable port.

### V3 Phase 1 Step 5 First Real Stage (Script Prompt)

- `src/video_agent/orchestrator/stages.py` provides `run_script_stage(job_dir, channel_path)` which reads `job_dir/idea.json` + channel YAML, renders the prompt through the existing v2 helper `operator._chatgpt_script_prompt`, and writes `operator/chatgpt/script_prompt.md`.
- Stage runner marks the `script` stage `completed`, advances `current_stage` to the next pending stage (`scenes`), and appends a `STAGE_COMPLETED` event (plus `JOB_COMPLETED` when all stages are done) to `events.jsonl`.
- FastAPI now exposes:
  - `POST /jobs/{job_id}/idea` — write `idea.json` into the job folder.
  - `POST /jobs/{job_id}/stages/script/run` — execute the script stage; returns the relative output path and updated state.
- `CHANNEL_CONFIG` env var configures the channel YAML path inside the container (default `/app/configs/vida-plena-45/channel.yaml`); tests override the FastAPI dependency.
- v2 `operator-prompts` CLI remains unchanged and shares the prompt helper.
- Tests: `tests/test_script_stage.py` covers the runner happy path, missing-idea guard, HTTP idea upload, HTTP run, missing-idea HTTP 409, and unknown-job 404.

### V3 Phase 1 Step 6 Script Promote Stage

- Default V3 stage order now includes `script_promote` after `script`.
- `run_script_stage` now completes `script` and advances `current_stage` to `script_promote`, not directly to `scenes`.
- `src/video_agent/orchestrator/stages.py` exposes `promote_script_stage(job_dir, channel_path, raw_response)`.
- `promote_script_stage` writes raw ChatGPT output to `operator/chatgpt/script.raw.txt`, reuses v2 `promote_operator_artifact(..., artifact="script")`, writes `script.json`, emits `STAGE_COMPLETED`, and advances `current_stage` to `scenes`.
- FastAPI exposes `POST /jobs/{job_id}/stages/script/promote` with body `{ "raw_response": "..." }`.
- Tests were added for direct stage promotion, stale `job_id` rejection, HTTP promotion, and HTTP 409 on invalid raw output.
- Docker verification passed: `docker compose run --rm video-agent pytest -q` -> `83 passed in 16.89s`.

### V3 Phase 1 Step 7 Scenes Prompt + Promote Stages

- Default V3 stage order now includes `scenes_promote` after `scenes`.
- `src/video_agent/orchestrator/stages.py` exposes `run_scenes_stage(job_dir, channel_path)` and `promote_scenes_stage(job_dir, channel_path, raw_response)`.
- `run_scenes_stage` reads `script.json` + channel YAML, renders the prompt through the existing v2 helper `operator._chatgpt_scenes_prompt`, writes `operator/chatgpt/scenes_prompt.md`, emits `STAGE_COMPLETED`, and advances `current_stage` to `scenes_promote`.
- `promote_scenes_stage` writes raw ChatGPT output to `operator/chatgpt/scenes.raw.txt`, reuses v2 `promote_operator_artifact(..., artifact="scenes")`, writes `scenes.json`, emits `STAGE_COMPLETED`, and advances `current_stage` to `seo`.
- FastAPI exposes:
  - `POST /jobs/{job_id}/stages/scenes/run`
  - `POST /jobs/{job_id}/stages/scenes/promote` with body `{ "raw_response": "..." }`
- Tests were added for direct scenes prompt generation, missing-script guard, direct scenes promotion, stale `job_id` rejection, HTTP run, HTTP promotion, and HTTP 409 on invalid raw output.
- Docker verification passed: `docker compose run --rm video-agent pytest -q` -> `94 passed in 13.68s`.

## Target V3 Architecture

```text
User browser
  -> app container
      -> FastAPI routes
      -> WebSocket progress
      -> orchestrator/state machine
      -> stage modules
      -> validators
      -> existing assets/TTS/render code
  -> browser-worker container
      -> Playwright drivers
      -> ChatGPT/Gemini/vidIQ/image generation browser operations
      -> host Chrome dedicated profile via CDP
```

State must remain file-based under `jobs/<job_id>/`, including:

- `job.json`
- `events.jsonl`
- `idea.json`
- `operator/chatgpt/*_prompt.txt`
- `operator/chatgpt/*_raw.json`
- `operator/gemini/*_qa_prompt.txt`
- `operator/gemini/*_qa_raw.json`
- `script.json`
- `scenes.json`
- `seo.json`
- `assets/`
- `browser_trace/`
- `render_props.json`
- `thumbnail.jpg`
- `video.mp4`
- `operator_review.html`
- `report.md`

## Verified Demo Job

Demo job:

```text
jobs/web-demo-chatgpt-image-script-qa-20260519
```

Verified artifacts:

- `script.json`
- `scenes.json`
- `seo.json`
- `operator/gemini/script_qa.json`
- `operator/gemini/scenes_qa.json`
- `operator/gemini/seo_qa.json`
- `render_props.json`
- `visual_review.json`
- `visual_contact_sheet.jpg`
- `thumbnail.jpg`
- `video.mp4`
- `operator_review.html`

Latest full verification:

```text
docker compose run --rm video-agent pytest -q
94 passed in 13.68s
```

## Fresh Operator Run

Fresh job from an empty folder:

```text
jobs/fresh-operator-flow-20260519-195952
```

Status:

- `operator-next` reached `review-video`.
- `operator-status` returned `Overall: READY`.
- `script`, `scenes`, and `seo` artifacts are present.
- Gemini QA is `PASS` for `script`, `scenes`, and `seo`.
- Rendered artifacts are present:
  - `video.mp4`
  - `thumbnail.jpg`
  - `operator_review.html`
  - `report.md`
  - `seo.json`

Important findings from this fresh run:

- ChatGPT project tabs can reuse stale conversation state, so each job/artifact should use a clearly isolated chat or enforce artifact/job ID matching before promotion.
- ChatGPT project prompt paste can appear as an attached prompt tile; the operator must click `Send prompt` before waiting for output.
- Existing ChatGPT tabs can have clipboard/paste issues; a fresh project tab fixed the prompt input.
- Gemini QA is more reliable in a fresh chat per artifact. Reusing a Gemini tab can mix old and new responses.
- Gemini sometimes shows `Submit` instead of a send icon; the browser flow must handle both.
- Scene output needs stricter validation:
  - `job_id` must match the current job folder.
  - scene IDs should use the expected `scene-01` format.
  - `asset_refs` must be an object, not a list.
  - `visual_prompt` should be English for stock/image generation.
  - Spanish user-facing text must preserve accents.
  - ChatGPT must not prefill internal QA as `PASS`.
- SEO output needs stricter validation:
  - language should be `es-419` for Latin American Spanish.
  - Spanish accents must be preserved.
  - tags should stay focused, around 5-8 high-relevance tags.
  - avoid positioning Vida Plena 45+ as `adultos mayores`.

## How To Continue One Video

Run this repeatedly to see the next step:

```bash
docker compose run --rm video-agent python -m video_agent.cli operator-next \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json \
  --job-dir jobs/<job_id>
```

The command will either:

- create the next ChatGPT prompt,
- create the next Gemini QA prompt,
- point to a raw response that should be promoted,
- tell you to run `operator-render`,
- or tell you to open the review page.

## Recent Commits

- `6dcbea8 Add operator artifact validators`
- `818c08b Update project status after fresh operator run`
- `e9b5ab8 Add operator next-step guide`
- `1f0161f Add operator job status command`
- `a7f188f Refresh operator review after render`
- `0d17b53 Add operator job review page`
- `6c75dfc Add operator QA gate`
- `545effa feat: add operator content workflow`

## Not Yet Done

- V3 FastAPI app runs the `script` and `scenes` stages end-to-end; `seo`, `render`, `review` stages still advance through stubs.
- Browser-worker has health and CDP diagnostic routes, but no ChatGPT/Gemini/vidIQ/image-generation drivers yet.
- ChatGPT/Gemini/vidIQ browser automation is not yet packaged into a service.
- WebSocket progress UI and `events.jsonl` replay are not implemented yet.
- Trend/data intake and idea selection are not implemented yet.
- ChatGPT image generation is not integrated as a first-class pipeline asset source.
- Video-level QA checklist is still human review through `operator_review.html`.
- YouTube upload, scheduling, persona eval, semantic reuse, analytics, and multi-job scaling are deferred.

## Next Recommended Work

V3 Phase 1 Steps 1-7 complete (health + orchestrator + job HTTP/WS + CDP diagnostic + script/scenes prompt+promote stages; 94/94 tests green). Next:

1. Commit Step 7.
2. V3 Phase 1 Step 8 — port `seo` stage similarly.
3. V3 Phase 1 Step 9 — wire `render` and `review` into the orchestrator; reuse existing render pipeline.
4. Browser-worker driver work (ChatGPT/Gemini/vidIQ Playwright flows) tracked separately under Step 10.
5. Smoke-test the host Chrome path with `scripts/launch-chrome-cdp.sh` + `GET /chrome` before depending on the browser-worker.
