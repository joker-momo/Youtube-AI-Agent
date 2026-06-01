# Video Agent v3 Standalone App Handoff

Date: 2026-05-20
Status: Architecture approved, ready for Phase 1

This is the current product direction for the project. It supersedes the older Hermes-oriented and CLI-first direction.

## TL;DR

Refactor the current v2 semi-manual `operator-*` CLI flow into a standalone local Python web app for producing complete YouTube videos.

Target input:

```text
channel + idea from local web UI
```

Target output:

```text
jobs/<job_id>/video.mp4
jobs/<job_id>/seo.json
jobs/<job_id>/operator_review.html
```

Phase 1 is manual YouTube upload. Do not build YouTube upload, Telegram, Hermes, persona eval, or LLM API clients yet.

## Fixed Decisions

| Area | Decision |
|---|---|
| App shape | Standalone Python web app |
| UI | Local FastAPI web UI with WebSocket progress |
| Hermes | Dropped |
| LLM access | Browser web UI only: ChatGPT Plus and Claude |
| Browser control | Playwright CDP attach to the `browser-runtime` container over the internal Docker network |
| Chrome | Persisted Chromium profile mounted into `browser-runtime` at `browser_profiles/default` |
| Browser container | Separate `browser-worker` service plus the headed `browser-runtime` container (KasmVNC on `127.0.0.1:7900` for manual sign-ins) |
| Flow | Sequential step-by-step execution |
| Failure mode | Fail-soft, retry twice, then manual prompt fallback |
| Tab strategy | Reuse one tab per AI, but use new chat per artifact |
| YouTube upload | Manual in Phase 1 |
| Persona eval | Phase 2 |
| Optimization work | Defer until the final-video flow is reliable |

## Target Flow

```text
trend/data intake
-> idea selection
-> ChatGPT script
-> Claude script QA
-> ChatGPT scenes
-> Claude scenes QA
-> ChatGPT SEO
-> Claude SEO QA
-> images/assets
-> TTS
-> Remotion render
-> review page
-> final video
```

## Architecture

```text
User browser
  -> FastAPI app container
      -> orchestrator/state machine
      -> stage modules
      -> validators
      -> existing assets/TTS/render code
  -> browser-worker container
      -> Playwright CDP over the internal Docker network
  -> browser-runtime container
      -> Chromium with persisted profile (browser_profiles/default)
      -> KasmVNC bound to 127.0.0.1:7900 for manual sign-ins
```

The app owns orchestration and state. The browser worker owns all ChatGPT, Claude, keyword scoring, and ChatGPT image-generation browser actions.

## Services

### `app`

Future FastAPI service:

- Home page with channel selector and idea input
- Job detail page with realtime stepper
- WebSocket progress stream
- Job list
- Retry/manual-fallback actions
- Reads and writes job state under `jobs/<job_id>/`

### `browser-worker`

Future FastAPI service:

- `POST /chatgpt/run`
- `POST /claude/run`
- `POST /keyword/scrape`
- `POST /chatgpt/images`
- `GET /health`

It attaches to the in-cluster `browser-runtime` container through:

```text
http://browser-runtime:9222
```

CDP port 9222 is internal to the Docker network and never published to host. The browser profile is persisted under `browser_profiles/default`.

## State Model

State must be file-based so a job can recover after page reload, process restart, or manual intervention.

Expected job layout:

```text
jobs/<job_id>/
├── job.json
├── events.jsonl
├── idea.json
├── operator/
│   ├── chatgpt/
│   │   ├── script_prompt.txt
│   │   ├── script_raw.json
│   │   ├── scenes_prompt.txt
│   │   ├── scenes_raw.json
│   │   ├── seo_prompt.txt
│   │   ├── seo_raw.json
│   │   └── image_prompts/
│   └── gemini/  # legacy folder name; stores Claude QA artifacts
│       ├── script_qa_prompt.txt
│       ├── script_qa_raw.json
│       ├── script_qa.json
│       ├── scenes_qa_raw.json
│       └── seo_qa_raw.json
├── script.json
├── scenes.json
├── seo.json
├── assets/
├── browser_trace/
├── render_props.json
├── thumbnail.jpg
├── video.mp4
├── operator_review.html
└── report.md
```

## Phase 1 Build Plan

1. Skeleton and Docker
   - Add `app` and `browser-worker` services.
   - Add Chrome dedicated profile setup script.
   - Add health endpoint.
   - Keep all existing tests passing.

2. Browser worker MVP
   - CDP attach to the `browser-runtime` container over the internal Docker network.
   - ChatGPT driver: new chat, send prompt, wait complete, extract JSON/text/images.
   - Claude driver with send/submit handling.
   - Centralized selectors.
   - Trace screenshots and request metadata.

3. Stage modules
   - Refactor current operator prompt/promote/QA logic into script, scenes, and SEO stages.
   - Keep `operator-*` CLI working during transition.
   - Reuse existing validators.

4. Orchestrator and state machine
   - Detect next step from files.
   - Run stages sequentially.
   - Retry QA loops up to three iterations.
   - Emit progress events into `events.jsonl` and WebSocket.

5. Web app MVP
   - Home page.
   - Job detail page.
   - Realtime stepper.
   - Retry/manual-fallback UI.
   - Link to final video, SEO JSON, and review page.

6. Integration test
   - Run one full video through the web UI.
   - Fix only full-flow blockers.

## Keep From v2

- Remotion render path and `render_props.json` flow
- Existing asset pipeline, Pexels/Pixabay providers, query cache, and asset library
- Kokoro/mock TTS
- `jobs/` artifact pattern
- Channel config structure, extended as needed
- `operator-*` commands during transition
- Existing test suite
- Docker-first operation

## Defer

- Hermes
- Telegram
- YouTube upload automation
- Persona evaluation
- Semantic asset reuse
- Analytics dashboards
- Multi-job queue and scaling
- LLM API clients

These are useful later, but they are not Phase 1 unless they directly unblock one complete final-video flow.

## Browser Failure Rules

When browser automation fails:

- Save screenshots and metadata under `browser_trace/`.
- Emit a progress failure event.
- Show the prompt path in the web UI.
- Let the user fix login/CAPTCHA/session manually.
- Provide a Retry action.

Do not auto-login. Do not inspect cookies, passwords, browser storage, or session files.

## Acceptance Criteria For Phase 1

- User opens `http://localhost:8000`.
- User selects channel, enters idea, and starts a job.
- Pipeline runs automatically to render.
- UI shows realtime progress.
- Browser failures surface a prompt path and Retry button.
- Validators block malformed or stale artifacts.
- Output includes `video.mp4`, `seo.json`, and `operator_review.html`.
- Existing v2 CLI still works during the transition.
- Docker test suite passes.

## Next Implementation Step

Start Phase 1 Step 1: skeleton and Docker.

Before coding, inspect the current codebase to answer:

- Exact artifact field names for script, scenes, and SEO
- Current `operator-promote` behavior
- Current Remotion render entrypoints
- Current TTS wrapper entrypoints
- Current asset cache/library entrypoints

Then add only the smallest skeleton needed for the local web app and browser-worker health check while keeping the current test suite green.
