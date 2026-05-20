# Youtube AI Agent Project Status

Last updated: 2026-05-20 (One-shot /run-all: idea.json -> video.mp4 in a single POST)

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
- Dedicated CDP profile is required because Chrome blocks remote debugging on the default Chrome user-data directory (`DevTools remote debugging requires a non-default data directory`). The user's regular Chrome profile such as `CodeX` can be inspected for its directory name, but the automation path must use a separate non-default profile such as `$HOME/.video-agent/chrome-cdp-profile`. The user signs in to ChatGPT/Gemini manually inside that dedicated profile; browser-worker auth checks should open the target page and report `login_required` when the profile is not signed in.
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

### V3 Phase 1 Step 8 SEO Prompt + Promote Stages

- Default V3 stage order now includes `seo_promote` after `seo`.
- `src/video_agent/orchestrator/stages.py` exposes `run_seo_stage(job_dir, channel_path)` and `promote_seo_stage(job_dir, channel_path, raw_response)`.
- `run_seo_stage` reads `script.json`, `scenes.json`, and channel YAML; renders the prompt through the existing v2 helper `operator._chatgpt_seo_prompt`; writes `operator/chatgpt/seo_prompt.md`; emits `STAGE_COMPLETED`; and advances `current_stage` to `seo_promote`.
- `promote_seo_stage` writes raw ChatGPT output to `operator/chatgpt/seo.raw.txt`, reuses v2 `promote_operator_artifact(..., artifact="seo")`, writes `seo.json`, emits `STAGE_COMPLETED`, and advances `current_stage` to `render`.
- FastAPI exposes:
  - `POST /jobs/{job_id}/stages/seo/run`
  - `POST /jobs/{job_id}/stages/seo/promote` with body `{ "raw_response": "..." }`
- Tests were added for direct SEO prompt generation, missing-scenes guard, direct SEO promotion, stale `job_id` rejection, HTTP run, HTTP promotion, and HTTP 409 on invalid raw output.
- Docker verification passed: `docker compose run --rm video-agent pytest -q` -> `101 passed in 15.61s`.

### V3 Phase 1 Step 9 Render + Review Stages

- `src/video_agent/orchestrator/stages.py` exposes `run_render_stage(job_dir, channel_path)` and `run_review_stage(job_dir)`.
- `run_render_stage` reuses the existing operator render pipeline via `render_operator_job(OperatorRenderOptions(...))`, with `require_operator_qa=False` so V3 can complete the first end-to-end render path before Gemini QA stages are ported.
- `run_render_stage` writes the existing render artifacts (`render_props.json`, `visual_review.json`, `visual_contact_sheet.jpg`, `thumbnail.jpg`, `video.mp4`, `report.md`, and `operator_review.html` through the operator pipeline), emits `STAGE_COMPLETED`, and advances `current_stage` to `review`.
- `run_review_stage` refreshes `operator_review.html` through `write_operator_review`, emits `STAGE_COMPLETED`, and completes the V3 job.
- FastAPI exposes:
  - `POST /jobs/{job_id}/stages/render/run`
  - `POST /jobs/{job_id}/stages/review/run`
- Tests were added for direct render stage behavior, QA-gate bypass, direct review completion, HTTP render, and HTTP review.
- Docker verification passed: `docker compose run --rm video-agent pytest -q` -> `105 passed in 14.52s`.

### V3 Phase 1 Step 10 Browser Appliance (split runtime + worker)

- Host Chrome + CDP attach approach is removed. Browser is now packaged as
  the **Browser Appliance** described in [docs/BROWSER_APPLIANCE.md](BROWSER_APPLIANCE.md).
- `docker/browser-runtime/Dockerfile` builds on
  `mcr.microsoft.com/playwright:v1.49.0-jammy` and adds `xvfb`, `fluxbox`,
  `x11vnc`, `novnc`/`websockify`, `socat`, and `supervisor`.
- `supervisord` runs Xvfb (`:99`), fluxbox, x11vnc, noVNC on `127.0.0.1:7900`,
  a `socat` forwarder publishing `0.0.0.0:9222 -> 127.0.0.1:9223` (Chromium 119+
  refuses non-loopback CDP binds), and Chromium itself via
  `docker/browser-runtime/launch-chromium.sh`.
- Chromium uses `--user-data-dir=/data/profile` so manual logins persist
  in the `./browser_profiles/default/` volume mount across restarts.
- `launch-chromium.sh` removes stale `SingletonLock`/`SingletonCookie`/
  `SingletonSocket` files so a hard kill does not block the next start.
- `docker-compose.yml` adds an internal bridge network `appliance_net`;
  CDP port 9222 is reachable only on that network and never published to
  the host. `app`, `browser-worker`, and `browser-runtime` share the
  network; `app`/`browser-worker` HTTP ports bind to `127.0.0.1` only.
- `browser-worker` now defaults `CHROME_CDP_URL=http://browser-runtime:9222`
  and drops the old `host.docker.internal` plumbing.
- Worker fetches `/json/version` with a forced `Host: localhost` header
  (Chromium rejects other Host headers as DNS rebinding) and rewrites
  the advertised `webSocketDebuggerUrl` host to match `CHROME_CDP_URL`
  so Playwright actually connects to the runtime instead of the worker's
  own loopback.
- `GET /chrome` was replaced by `GET /runtime`, which returns
  `{ok, cdp_url, contexts, pages}`. `tests/test_browser_worker_chrome.py`
  was rewritten accordingly.
- `scripts/launch-chrome-cdp.sh` is deleted.
- Live smoke test: `curl http://127.0.0.1:8001/runtime` returns
  `{"ok":true,"cdp_url":"http://browser-runtime:9222","contexts":1,"pages":1}`
  with the Browser Appliance up; noVNC `GET /vnc.html` returns HTTP 200.
- Docker verification passed: `docker compose run --rm video-agent pytest -q`
  -> `109 passed in 14.21s`.

### V3 Phase 1 Step 11 ChatGPT + Gemini driver scaffold

- New package `src/video_agent/browser_worker/drivers/` with:
  - `base.py`: `BrowserDriverError`, `LoginRequiredError`, debug
    screenshot helper (`save_trace_screenshot`), `normalise_response_text`.
  - `chatgpt.py`: `ChatGPTDriver.send(prompt)` opens a temporary chat at
    `chatgpt.com/?model=gpt-4o&temporary-chat=true`, types into the
    contenteditable composer, clicks send, waits for the stop button to
    disappear and the assistant turn to grow, then scrapes
    `[data-message-author-role='assistant'] .markdown` text. Multiple
    selector fallbacks because ChatGPT's UI churns frequently.
  - `gemini.py`: same shape for `gemini.google.com/app` using
    `rich-textarea` and `model-response`/`message-content` selectors.
- Drivers never attempt to log in; signed-out profiles raise
  `LoginRequiredError` with a debug screenshot. Operator signs in via
  noVNC (`http://127.0.0.1:7900/vnc.html`).
- Browser-worker exposes new HTTP routes:
  - `POST /chatgpt/send` body `{prompt, response_timeout_ms?}` returns
    `{site, raw_response}`.
  - `POST /gemini/send` same contract.
- Errors map to HTTP: login required -> `409 {login_required: true,
  screenshot}`; selector/response failure -> `502 {error, screenshot}`.
- Debug screenshots are written under `BROWSER_TRACE_DIR`
  (default `/data/trace`); mount this as a volume to inspect failures.
- New tests `tests/test_browser_drivers.py` cover pure helpers
  (`normalise_response_text`, login URL detectors, error subclassing).
  End-to-end driver behaviour is verified manually via the runtime
  container — selectors will need updates as ChatGPT/Gemini UI changes.
- Live smoke (with Browser Appliance up): empty prompt returns
  `502 {"detail":{"error":"Empty prompt","screenshot":""}}` from
  `POST /chatgpt/send`, proving the route is wired and validation works.
- Docker verification passed: `docker compose run --rm video-agent pytest -q`
  -> `116 passed in 14.19s`.

### V3 Phase 1 Step 11b ChatGPT + Gemini drivers verified end-to-end

- Fixed `auth_status` route to route through `_resolve_browser_ws` (it
  was still calling `connect_over_cdp(cdp_url)` directly, which 500'd
  because Chromium's `/json/version` rejects non-`Host: localhost`).
- `ChatGPTDriver` now dismisses ChatGPT's "No model training"
  temporary-chat consent dialog before clicking the composer (the
  modal's backdrop intercepts pointer events otherwise). It clicks the
  common confirmation buttons (`Continue`, `Got it`, `Okay`, `OK`,
  `I understand`, dialog close), then falls back to pressing Escape,
  retrying up to three times.
- Driver response wait/scrape now uses a multi-selector cascade and
  drops the previous `> 32 chars` length threshold (it broke "PONG"-
  style short answers). ChatGPT cascade tries
  `[data-message-author-role='assistant']`,
  `[data-testid='conversation-turn-content']`, and
  `article[data-message-author-role='assistant']`. Gemini cascade
  tries `.model-response-text`, `message-content .markdown`,
  `message-content`, `model-response .markdown`, `model-response`,
  and `.markdown.markdown-main-panel`.
- `browser-worker` `_drive` catches any uncaught Playwright exception
  (`TimeoutError`, navigation failure, ...) and converts it into a
  structured `HTTP 502 {error, screenshot}` instead of bare 500.
- Compose mounts `./browser_trace:/data/trace` for `browser-worker`
  with `BROWSER_TRACE_DIR=/data/trace`, and `.gitignore` excludes
  `browser_trace/`. Debug screenshots taken by drivers are inspectable
  from the host immediately.
- Live verification with the user logged in via noVNC:
  - `GET /auth/chatgpt/status` -> `{"logged_in": true, ...}`
  - `GET /auth/gemini/status` -> `{"logged_in": true, ...}`
  - `POST /chatgpt/send {"prompt":"Reply with exactly: PONG"}` ->
    `{"site":"chatgpt","raw_response":"pong"}`
  - `POST /chatgpt/send` with `Return one JSON object {greeting, language}`
    -> `{"raw_response":"{\"greeting\":\"Hola\",\"language\":\"Spanish\"}"}`
  - `POST /gemini/send {"prompt":"Reply with exactly: PONG"}` ->
    `{"site":"gemini","raw_response":"PONG"}`
  - `POST /gemini/send` JSON test ->
    `{"raw_response":"{\"ok\": true, \"language\": \"Spanish\"}"}`
- Docker verification: `116 passed in 15.01s`.

### V3 Phase 1 Step 11c Gemini temporary chat

- `GeminiDriver.send` now calls `_enter_temporary_chat(page)` before
  the composer click. The helper tries to click Gemini's "Temporary
  chat" toggle (`button[aria-label*='Temporary' i]` and variants) and
  silently falls back to clicking "New chat" if the toggle is hidden
  by a rollout/locale variation.
- Live verified: after toggle the page shows the Gemini banner
  *"Temporary chats don't appear in recent chats and aren't used to
  improve Google AI. Stored for 72 hours for safety."* and a fresh
  `Welcome, stranger` view. `POST /gemini/send {"prompt":"Reply with
  exactly: PONG"}` still returns `{"site":"gemini","raw_response":"PONG"}`.
- Matches the existing ChatGPT driver behaviour which uses
  `chatgpt.com/?model=gpt-4o&temporary-chat=true`. Both drivers now
  avoid polluting the operator's permanent chat history.
- Docker verification: `116 passed in 14.69s`.

### V3 Phase 1 Step 12 Auto pipeline (orchestrator -> browser-worker)

- New module `src/video_agent/orchestrator/browser_client.py` exposes
  `BrowserClient`, `BrowserClientError`, and `LoginRequiredFromWorker`.
  The client wraps `POST {site}/send` on the browser-worker and reads
  `BROWSER_WORKER_URL` (default `http://browser-worker:8001`).
- `src/video_agent/orchestrator/stages.py` adds `PromptFn` type alias
  plus three async helpers:
  - `auto_script_stage(job_dir, channel_path, prompt_fn)`
  - `auto_scenes_stage(job_dir, channel_path, prompt_fn)`
  - `auto_seo_stage(job_dir, channel_path, prompt_fn)`
  
  Each runs the prompt stage if needed, fetches the model response via
  `prompt_fn`, then promotes through the existing v2 validators. The
  helpers prepend an "ABSOLUTE CONSTRAINT" line to the prompt that
  injects the real `job_id` and `channel_id`, because ChatGPT was
  otherwise inventing a different job_id that the promoter rejects
  with `job_id mismatch`.
- FastAPI exposes:
  - `POST /jobs/{job_id}/stages/script/auto`
  - `POST /jobs/{job_id}/stages/scenes/auto`
  - `POST /jobs/{job_id}/stages/seo/auto`
  
  Errors map to HTTP: stage misuse / empty worker response -> `409`,
  worker login required -> `409 {login_required: true}`, worker
  selector failure or other 5xx -> `502 {browser_worker_status,
  browser_worker_detail}`.
- New `tests/test_auto_stages.py` (12 tests) covers happy path for all
  three stages, skip-runner-when-already-promote, empty response,
  wrong-stage guard, HTTP success, HTTP 409 login-required, HTTP 502
  worker error, HTTP 404 unknown job, and BrowserClient base-URL
  defaults/override.
- Live end-to-end verification with the user signed in via noVNC and
  the Browser Appliance up:
  - `POST /jobs/auto-1779250210/stages/script/auto` ->
    `current_stage: scenes`, `output: script.json`
  - `POST .../scenes/auto` -> `current_stage: seo`, `output: scenes.json`
  - `POST .../seo/auto` -> `current_stage: render`, `output: seo.json`
  - Final state: `script`, `script_promote`, `scenes`, `scenes_promote`,
    `seo`, `seo_promote` all `completed`; `render` and `review`
    pending.
  - Real Spanish artifacts written: `jobs/auto-1779250210/script.json`,
    `scenes.json`, `seo.json` with the correct `job_id`, valid
    `es-419` SEO, and channel-appropriate hooks.
  - Zero copy-paste between ChatGPT/Gemini and the orchestrator.
- Docker verification: `docker compose run --rm video-agent pytest -q`
  -> `128 passed in 21.89s`.

### V3 Phase 1 Step 12b Driver humanization

- New `src/video_agent/browser_worker/drivers/humanize.py` with
  `human_pause(page, min_ms, max_ms)` and `human_type(page, text)`.
- ChatGPT and Gemini drivers now insert randomised pauses between
  navigation, modal dismiss, composer focus, typing, and send-click —
  no more burst-style instant-typing that screams "bot".
- `human_type` is hybrid:
  - Short text (< `BROWSER_HUMAN_PASTE_THRESHOLD` chars, default 200):
    per-character `keyboard.type(ch, delay=random(35..110ms))` with
    occasional 200-900 ms "thinking" pauses (~1 per 25 chars). This
    matches the cadence of a short manually-typed reply.
  - Long text: instant `keyboard.insert_text(text)` followed by a
    1500-3500 ms "reading what I just pasted" pause. Pasting is what
    a human does for multi-KB prompts too, and per-char typing of a
    2 KB prompt would block for minutes.
- All thresholds tunable via env without rebuild: `BROWSER_HUMAN_*`
  variables documented inline (`TYPING_MIN_MS`, `TYPING_MAX_MS`,
  `PAUSE_MIN_MS`, `PAUSE_MAX_MS`, `THINK_*`, `PASTE_THRESHOLD`,
  `PASTE_PAUSE_*`).
- Live verified:
  - Short prompt (`Reply with exactly: PONG`) round trip: ~11.7 s
    instead of instant — cadence visible in noVNC.
  - Full `script` auto stage with the real ~1.5 KB v2 prompt:
    ~15 s total (paste + review pause + ChatGPT generate).
- 2 new tests in `tests/test_browser_drivers.py` for env override and
  default sanity.
- Docker verification: `130 passed in 15.63s`.

### V3 Phase 1 Step 12c Humanization pass 2 (clicks, tabs, read)

- `humanize.py` now exports `human_click(locator)` and
  `estimate_read_pause_ms(text)` alongside the existing pause/type
  helpers. `human_click` hovers (with a 80-240 ms hover pause), then
  clicks, then pauses 250-700 ms — a real pointer cadence.
- ChatGPT and Gemini drivers no longer call any raw `.click()`:
  composer click, send-button click, modal-dismiss buttons, and the
  Gemini temporary-chat toggle / new-chat fallback all go through
  `human_click`. Every previous `wait_for_timeout(<fixed>)` is now
  `human_pause(...)` with a randomised window.
- After a successful scrape both drivers pause for an
  `estimate_read_pause_ms(text)`-derived interval (~300 wpm, clamped
  0.8-4 s) so the tab isn't closed the same millisecond the response
  finishes streaming.
- `_drive` in `browser_worker/app.py` adds a 300-900 ms beat after
  `context.new_page()` and a 400-1100 ms beat before `page.close()`
  so opening and closing a job tab no longer looks like an instant
  `Ctrl+T` -> `Ctrl+W` script. `auth_status` got the same treatment
  around its diagnostic navigation.
- Live verified: short "PONG" round trip is ~16.4 s vs the previous
  ~11.7 s; the extra ~5 s is the hover/click/read/close cadence and
  is visible in noVNC as a person driving the page.
- Docker verification: `130 passed in 14.45s` (no new tests; the
  humanization changes only affect timing).

### V3 Phase 1 Step 12d End-to-end video produced via V3 pipeline

- Continued job `auto-1779250210` (where the auto script/scenes/seo
  trio left current_stage at `render`) through the manual `render`
  and `review` routes:
  - `POST /jobs/auto-1779250210/stages/render/run`
    -> `current_stage: review`, `output: video.mp4`, 2m27s wall.
  - `POST /jobs/auto-1779250210/stages/review/run`
    -> `output: operator_review.html`. All 8 stages completed,
    `JOB_COMPLETED` emitted in `events.jsonl`.
- Final artifacts in `jobs/auto-1779250210/`:
  - `video.mp4`: H.264 1920x1080 @ 30 fps, AAC audio, 54.06 s,
    22 MB. Matches the idea's `target_duration_sec: 54`.
  - `thumbnail.jpg`: 47 KB.
  - `report.md`: 631 B.
  - `operator_review.html`: 6.5 KB.
  - `visual_review.json`: 8.4 KB.
- This is the first video produced by the V3 pipeline with zero
  manual copy-paste between ChatGPT and the orchestrator. ChatGPT
  was driven through the Browser Appliance with humanized cadence.
- Render and review routes already existed (Step 9); only auto run
  + auto promote needed the BrowserClient. They are not in the
  `/stages/.../auto` set yet because rendering does not call the
  browser-worker.

### V3 Phase 1 Step 13 One-shot /run-all endpoint

- New route `POST /jobs/{job_id}/run-all` chains the full pipeline:
  `auto_script_stage -> auto_scenes_stage -> auto_seo_stage ->
  run_render_stage -> run_review_stage`. Returns
  `{"completed": [...], "state": JobState}` on success.
- On partial failure the route returns HTTP 409 (stage misuse / empty
  worker response) or 502 (browser-worker error) with the
  completed-so-far list, `stopped_at: current_stage`, and the full
  `state` in `detail` so the caller can resume by hitting the
  per-stage route that failed.
- 3 new tests in `tests/test_auto_stages.py`:
  - happy-path with all 5 stages (render/review stubbed),
  - worker error mid-flight (HTTP 502, `completed: []`,
    `stopped_at: script_promote`),
  - unknown job (HTTP 404).
- Live verified end-to-end against the real Browser Appliance with
  the user signed into ChatGPT:
  - `POST /jobs/runall-1779251655/run-all` -> 200,
    `completed: [script_promote, scenes_promote, seo_promote, render,
    review]`, `current_stage: review`.
  - Wall clock: **3m43s** for the entire pipeline from `idea.json`
    to `video.mp4` in a single HTTP call, zero copy-paste.
  - Output: `video.mp4` (19 MB, 54.06 s, matches the idea's
    `target_duration_sec`), `thumbnail.jpg` (51 KB), `report.md`,
    `operator_review.html`, `visual_review.json`.
- Docker verification: `133 passed in 22.09s`.

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
      -> browser-runtime container (Chromium + Xvfb + noVNC + CDP) over internal appliance_net
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
116 passed in 14.19s
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

- V3 FastAPI app runs the `script`, `scenes`, `seo`, `render`, and `review` stages end-to-end from promoted artifacts.
- Browser-worker has health and CDP diagnostic routes, but no ChatGPT/Gemini/vidIQ/image-generation drivers yet.
- ChatGPT/Gemini/vidIQ browser automation is not yet packaged into a service.
- WebSocket progress UI and `events.jsonl` replay are not implemented yet.
- Trend/data intake and idea selection are not implemented yet.
- ChatGPT image generation is not integrated as a first-class pipeline asset source.
- Video-level QA checklist is still human review through `operator_review.html`.
- YouTube upload, scheduling, persona eval, semantic reuse, analytics, and multi-job scaling are deferred.

## Next Recommended Work

V3 Phase 1 Steps 1-9 complete (health + orchestrator + job HTTP/WS + CDP diagnostic + script/scenes/SEO prompt+promote stages + render/review stages; 105/105 tests green). Next:

1. Commit Step 9.
2. Browser-worker driver work (ChatGPT/Gemini/vidIQ Playwright flows) tracked separately under Step 10.
3. Smoke-test the host Chrome path with `scripts/launch-chrome-cdp.sh` + `GET /chrome` before depending on the browser-worker.
4. Add Gemini QA stages once the render path is usable through the web app.
