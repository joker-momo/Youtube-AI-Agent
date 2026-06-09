# Youtube AI Agent Project Status

Last updated: 2026-06-09b (QA storm fix v2.2 follow-up — recurring "Scene s02 estimates 8.5s" + "idea item 4 not covered" hard blocker. Root: scene-gen crammed all 4 checklist items into s02 (overflow + dropped item-4 scene); the deterministic fit-repair was also gated behind all-hard-errors-are-fit, so a coexisting missing_item_coverage error suppressed it. Fix: (a) ungated fit-repair in short_builder (runs when ANY duration/fit hard error exists, not only when they are the sole class); (b) build_scene_repair_plan now emits anti-cram guidance on checklist scene_narration_fit ("one item per scene, short setup") + a new missing_item_coverage branch naming the uncovered item and demanding its own dedicated 1:1 scene — so the bounded regen produces a properly-paced, fully-covered Short instead of reproducing the cram. Mechanical repair intentionally cannot rescue an 18-word+visual scene; per PRIME DIRECTIVE we force a proper regen rather than ship a rushed Short. Also: recorded the project PRIME DIRECTIVE (raise video quality + audience fit, warn/stop on anything that trades it away) into CLAUDE.md + .wolf/cerebrum.md. Full suite green, +2 tests.

Previously: 2026-06-09 (QA storm fix v2.2 implemented & verified — full suite 1077 passed, 15 new in tests/test_shorts_scene_fit_repair.py. Two mechanisms stop scene-QA regeneration storms: (1) DETERMINISTIC scene_narration_fit repair in validate_scenes.py — new deterministic_scene_fit_repair() runs extend→mechanical-split→micro-condense BEFORE any LLM regen; try_mechanical_split() splits only at existing sentence boundaries (no invent/reword), copies source_scene_ids + covers_items union to both halves, rejects if any segment still overflows the layout hard cap; try_micro_condense() removes only whitelisted filler/intros and rejects if an idea label is lost; scene/graphic caps enforced (split blocked at max_count=12 or graphic cap 2); per-issue diagnostics logged (scene_id/layout/duration/hard_cap/spoken_text/word_count/est/overflow/repair_mode_attempted); wired into short_builder.py scene-repair block before regen. (2) TIERED product-score gate in qa.py — classify_product_scores() replaces the 9.0-on-every-dimension wall that hard-failed near-good Shorts: hard_block if any dim<7 / retention<7.5 / visual_specificity<7.5 (visual-first); repair if avg<8.2 or any key dim<8.0 or natural_spanish<8.0; pass_with_warning if avg≥8.2 & keys≥8.0; clean pass at publish target (avg≥8.6, keys≥8.5, natural≥9.0). Incomplete scores still hard-fail. Live-wired into normalize_gemini_scenes_qa so soft score gaps no longer leak into required_changes (the storm driver). Strict blockers unchanged: source fidelity, safety, audio-fit-impossible, invalid layout, SEO mismatch, idea-count preservation.)

Last updated: 2026-06-08 (SEO context-leak fix spec v1.2 implemented & verified — 377/377 shorts tests green, 11 new in tests/test_shorts_seo_context_leak.py. Root cause: prompts.py SEO prompt HARDCODED a "5 errores con el pan después de los 45" title rule AND a fixed hashtag set (#alimentacionsaludable/#comerpan/#nutricion/#vida45plus/#shorts) for EVERY bread/pan Short, leaking an error-list framing onto checklist/label-reading/purchase-rule Shorts (e.g. a "GIRA EL PAQUETE" checklist got titled "5 errores"). Fix: (1) replaced both hardcodes with format-aware title + topic-aware hashtag guidance; (2) extended validate_seo_idea_consistency in idea_preservation.py — new _seo_format_alignment_issues/_seo_core_action_issues/_seo_hashtag_topic_issues run BEFORE the must_preserve_count early-return guard so they fire even with no count contract; count-preservation logic moved to gated _seo_count_preservation_issues; error-title detection via _ERROR_TITLE_RE (numeric + spelled-out); (3) added SEO retry loop in short_seo_builder.build_short_seo (MAX_SEO_RETRIES=2) regenerating with cumulative _build_seo_retry_feedback via new retry_feedback param on prompts.short_seo_prompt; blocking_error still raises immediately, mismatches are severity="repairable_error". Out of scope (separate spec): artifact-14 visual polish.)

Previously: 2026-06-07e (Idea Preservation spec v1.8 + v1.9 + latest-loop fixes fully implemented & verified — 35/35 tests green in tests/test_shorts_idea_preservation.py via new module src/video_agent/shorts/idea_preservation.py. Closes ALL FIVE count-reduction vectors that silently turned "5 errores" into "2 errores": (1) validate_script_word_budget — 38s now warning, repairable only above MAX_SHORT_DURATION_SEC; (2) validate_script_checklist_point_cap — uses allowed_spoken_points_from_contract(original_count/idea_count_max) instead of fixed >4; (3) qa.py _route_validation_issue — warning severity no longer forces FAIL verdict; (4) short_builder.py scene-fit escalation feedback respects idea_contract, no hardcoded "Keep 3 points"/bread strings; (5) prompts.py short_script_prompt + gemini_script_qa_prompt count caps now conditional on idea_contract. v1.9 adds: derive_idea_contract locks count from key_points length + narration_seed enumeration (_enumeration_count), not only title number; coverage-mode classification so spoken items suppress visual_only_unreadable false positives; footage-led-aware slideshow_risk severity (has_footage_base, graphics<=2 + short_checklist<=1 + checklist_like<=2 => PASS/warning, not repairable); scene-validation fallback proceeds on soft-only issues, blocks on hard (missing/unknown coverage, unsupported layout, audio-fit, source/safety). Latest-loop fixes: mechanical CTA auto-repair (short_cta cap 1.8/2.6/2.8 via repair_scene_duration_if_possible), CTA-only specific repair plan, idea_items derived from key_points with verified source_support. Note: system python3 lacks pyyaml — run tests via .venv/bin/python.)

Previously: 2026-06-07d (Cookie cleanup now PRESERVES the ChatGPT + Gemini login session: DELETE /auth/{site}/cookies defaults to preserve_session=True — keeps auth cookies (ChatGPT NextAuth/oai-did/cf_clearance, Google SID family + __Secure-/__Host-) and drops only the non-auth bloat that causes HTTP 431 / provider errors, so recovery no longer logs the controlled browser out. preserve_session=false still does a full wipe. New _is_auth_cookie() + unit test.)

Previously: 2026-06-07c (ChatGPT scene-generation robustness: provider-error text ("Something went wrong… help.openai.com") is now detected in short_scene_builder BEFORE parse via ChatGPTProviderError + is_provider_error_text/is_valid_scene_payload — no more empty scenes / scene_count=0 creative repair from a browser failure; new chatgpt_send_with_recovery clears cookies (BrowserClient.auth_clear_cookies) + re-sends on a fresh temporary chat up to MAX_CHATGPT_PROVIDER_RETRIES=2, profile-reset fallback best-effort, wired into worker chatgpt_fn; short_builder retries provider errors on a separate budget (max_chatgpt_provider_retries) and surfaces status=needs_review/qa_verdict=PROVIDER_ERROR/failure_kind=chatgpt_provider_error instead of a fake QA failure; validate_scene_structure emits a distinct empty_scenes message for scene_count==0; short_scene_prompt_v6 hoists the layout budget to the top, replaces "Keep narration faithful" with source-faithful compression, and adds a FINAL SELF-CHECK. 7 new tests. Full suite green)

Previously: 2026-06-07b (Shorts graphic-count hard cap: short_scene_prompt_v6 now enforces MAX 2 graphics for normal Shorts — checklist/explainer is NOT graphic-led, 3 graphics only on explicit graphic-led input, bread/food-label Shorts keep a realistic supermarket/kitchen base; validate_scene_structure now returns a repairable_error for >2 graphics on a normal Short (was warning-until-4) so 3-graphic decks are caught BEFORE Gemini; new is_explicit_graphic_led() + graphic_repair_targets() keep graphic_label_callout (primer ingrediente) + graphic_comparison (fibra/azúcar/jarabes) and convert setup/recap graphic_checklist into realistic short_myth/short_tip via build_scene_repair_plan. 871 tests pass)

Previously: 2026-06-07 (Shorts QA max-regeneration fix: Gemini graphic-count false positives downgraded unless deterministic count>=4 (or ==3 and not graphic-led); structural vs product retry budgets split via max_structural_attempts / max_product_repair_attempts; deterministic pacing simplifier added — drops redundant late scene + merges tip into CTA within the layout cap, targets 7-8 scenes, never adds graphics; best-candidate fallback now hard-blocks render on safety/source/unreadable/off-topic or any product score<=5 but rescues a lone soft pacing==6; fallback unsafe-token scan no longer false-matches the word "safety" inside remediation hints. Default max_regeneration_attempts raised 2→4 in active channel configs. 857 tests pass)

Previously: 2026-06-05 (Shorts Graphic Kit MVP v7 landed: 3 motion-graphic layouts — graphic_checklist/graphic_step_list/graphic_plate_ratio — with Zod payload validation, Python pre-render validator, planner+QA prompt rules, GraphicMvpPreview composition; rendered + visually verified)

Previously: 2026-05-30 (Shorts Autopilot v5 landed: sequential Shorts derived from long videos — legacy removed, planner/QA/audio-mix/vertical-render/manifest/API/UI, auto-trigger after long Review PASS)

This file is the living project tracker. Update it whenever a meaningful system capability is added, changed, verified, or deferred so a new reader can quickly understand what the system does, what is being built now, and what remains.

## Goal

Build a Docker-first standalone YouTube production app that can take a channel and idea through:

```text
trend/data intake -> idea selection -> ChatGPT script/scenes/SEO -> Gemini QA -> assets/images -> TTS -> Remotion render -> review -> final video
```

The current v2 `operator-*` CLI flow remains functional during the transition. The approved v3 target is a standalone local FastAPI web app with WebSocket progress and a separate browser-worker service that attaches to the `browser-runtime` container over the internal Docker network. The Chromium profile is persisted under `browser_profiles/default`; CDP port 9222 is never published to host.

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
- Browser web UI access for ChatGPT Plus, Gemini, keyword scoring, and ChatGPT image generation; no LLM API client in Phase 1.
- Separate `browser-worker` container using Playwright CDP attach to the in-cluster `browser-runtime` container over the internal Docker network. CDP port 9222 is internal-only and never published to host.
- The `browser-runtime` container runs Chromium with a persisted profile mounted from `browser_profiles/default`. The user signs in to ChatGPT/Gemini/keyword scoring manually through the KasmVNC console bound to `127.0.0.1:7900`. The system must not auto-login.
- A non-default profile directory is required because Chrome blocks remote debugging on the default user-data directory (`DevTools remote debugging requires a non-default data directory`). Mounting `browser_profiles/default` satisfies that requirement and keeps the sign-ins persistent across container restarts. Browser-worker auth checks should open the target page and report `login_required` when the profile is not signed in.
- Sequential per-step flow with file-based state detection.
- Fail-soft browser handling: save trace, expose prompt path, allow user retry.
- Manual YouTube upload in Phase 1.
- Persona evaluation, Telegram, upload automation, semantic asset reuse, analytics, and scaling are deferred.

## Operating Rules

- Run project commands through Docker.
- Use ChatGPT as the primary semi-automated operator for script, scenes, SEO, and optionally image generation.
- Use Gemini as the dedicated QA reviewer for operator-produced artifacts (`ChatGPT writes → Gemini QA`). Any Gemini references in older sections are historical/legacy only; the `operator/gemini` folder name is kept as backwards-compatible storage.
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
- Kokoro local TTS enabled for production. Voice: `ef_dora` (Spanish female), `lang_code="e"`, `speed=0.95` (~145 wpm). Configured in `configs/vida-plena-45/channel.yaml`.
- First production narration: 92MB WAV, 40 scenes, Kokoro-82M from HuggingFace.

### First Production Video

- `prod-insomnio-v2`: "Insomnio despues de los 45" — all 14 stages completed 2026-05-21.
- Artifacts: `video.mp4` (5.5MB), `narration.wav` (92MB Kokoro TTS), `thumbnail.jpg` (52KB).
- Ready for manual YouTube upload.

### Semi-Automated Operator Flow

- `operator-prompts` writes ChatGPT writing prompts and Gemini QA prompts for `script`, `scenes`, and `seo`.
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
- SEO promotion validates the artifact language against `channel_config.seo.language` (Spain-first channels expect `es-ES`; legacy configs may still set `es-419`). It also blocks tag counts outside the channel rule, duplicate/empty tags, forbidden positioning such as `adultos mayores`, and placeholder social-link text. When `seo.strict_language: true`, any language mismatch is a hard error; otherwise reworkable Spanish variants emit a warning so Gemini QA can force ChatGPT to regenerate.
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
- Historical note: `run_render_stage` originally used `require_operator_qa=False` while QA stages were being ported. Current run-all uses Gemini QA before downstream SEO/thumbnail/assets/render work.
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

### Historical: V3 Phase 1 Step 11 ChatGPT + Gemini driver scaffold

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

### V3 Phase 1 Step 14 Session-per-stage + role/context briefing

Two related changes that together make the orchestrator drive ChatGPT
the way a person would, and brief the model with the channel's voice
+ rules before every task:

1. **Browser-worker session API.** Drivers were refactored into
   `open()`, `send_message()`, `close()`. New routes:
   - `POST /chatgpt/sessions` -> `{session_id}` (opens a temp chat
     tab + dismisses consent modal).
   - `POST /chatgpt/sessions/{sid}/send` -> `{raw_response}`
     (types + scrapes inside the existing tab).
   - `DELETE /chatgpt/sessions/{sid}` -> 204.
   - Same shape for Gemini. Legacy `POST /chatgpt/send` and
     `POST /gemini/send` still work for one-shot callers.
   - Worker holds session state in an in-memory `_SESSIONS` dict
     keyed by uuid4 hex. Each entry owns its own Playwright
     connection, page, and driver instance; `_close_session` tears
     all of them down.

2. **Driver scrape logic switched from "count assistant turns" to
   "wait for last-text change".** The turn-count heuristic broke when
   short answers (e.g. "OK") rendered in the temporary-chat Fast
   Answer block, which uses different selectors than the regular
   assistant turn container. Both drivers now snapshot the last
   non-empty assistant text before sending and wait for it to change.

3. **BrowserClient session helpers + `run_session(site, messages)`.**
   Opens a session, sends each message in order, returns the last
   response, and closes the session in a `finally` so partial
   failures never leak runtime tabs.

4. **Per-stage role + context briefing.** New
   `src/video_agent/orchestrator/briefing.py` builds a Spanish
   first-message that contains:
   - A stage-specific role (script / scenes / seo writer).
   - A channel summary extracted from `channel.yaml`: name,
     description, audience, niche, avoid-topics, forbidden +
     preferred positioning phrases, QA thresholds, TTS pace.
   - Absolute constraints: pinned `job_id` + `channel_id`, language,
     accents, medical-safety disclaimer, "JSON only" output rule.
   - Asks the model to reply only `OK` so it commits the briefing
     before the task arrives.
   The auto stages send `[briefing, task]` through `run_session`, so
   one stage = one temp chat = two user messages = close.

5. **Auto stages now take `session_fn: SessionFn` instead of
   `prompt_fn`.** Each stage opens a fresh temp chat (so prior-stage
   context never bleeds), sends briefing then task, then closes.

Live verified end-to-end with the user signed into ChatGPT in the
Browser Appliance:

- Session API smoke: two messages ("Responde solo OK", "Cuánto es
  2+2? Solo el número") in the same session returned "OK" and "4"
  respectively; DELETE closed the tab cleanly.
- `POST /jobs/brf-1779252992/run-all` -> 200,
  `completed: [script_promote, scenes_promote, seo_promote, render,
  review]`, **4m15s** wall (vs ~3m43s pre-briefing; the +30s is the
  briefing messages and per-stage open/close).
- Output quality jumped vs the un-briefed run:
  - SEO title and description name the channel ("Vida Plena 45+").
  - Description includes the preferred phrase "adultos 45+" and the
    medical-safety line "consulta a un profesional".
  - Tags within the channel rule (5-8), no forbidden phrases.
  - Script hook uses preferred positioning vocabulary; accents
    preserved.

Docker verification: `133 passed in 15.82s` (tests updated to feed
`run_session` and assert briefing + task arrive as two messages).

### V3 Phase 1 Step 15 Prompt-quality batch (DNA reinforcement)

Eight enhancements to `briefing.py`, all aimed at making the channel
DNA reproduce across every video without manual fixup. Persona
injection was intentionally skipped — DNA consistency first; we'll
diversify only once we have 10-20 published videos to learn from.

Briefing message (first turn of every per-stage temp chat) now also
includes:

- **Brand voice file** loaded from `channel.brand_voice_path`. If the
  channel YAML points at a markdown file, the worker injects its text
  verbatim under `# Guía de voz del canal (brand voice)`.
- **Negative tone constraints**: an explicit list of manipulative
  phrases the model must never use (`milagro`, `cura definitiva`,
  `garantizado`, `100% efectivo`, `comprobado científicamente`,
  `el mejor`, `secreto`, `fácil y rápido`, `experto número uno`).
- **Anti-hallucination rule**: if the idea lacks concrete stats /
  citations / brand names / proper nouns, the model must use
  qualitative language ("muchas personas reportan…") instead of
  inventing data.
- **Format-strict + self-correct rule**: if the first reply is not
  pure JSON, autocorrect and re-send only the JSON — no apologies,
  no commentary.

Task message (second turn) now also includes per-stage:

- **Explicit output schema** (JSON-Schema-ish shorthand) with types
  and bounds for every field. Removes "guess the type" failure mode.
- **Length contract** with exact ranges in chars / words / seconds
  derived from the idea's `target_duration_sec` and the channel's
  TTS `pace_wpm`. Locks video duration drift.
- **Sub-task decomposition checklist**: 6-7 numbered steps the model
  must follow before emitting JSON. Forces planning ("divide the
  narration into N blocks, sum their durations, only then build the
  JSON").
- **Expanded self-check** (7 items including hard `qa.verdict=
  NEEDS_REWORK` fallback if any check fails).

Live verified end-to-end with the user signed into ChatGPT,
`POST /jobs/brf2-1779253828/run-all` -> 200 in **4m18s** wall.
Every contract met by ChatGPT without manual fixup:

  Stage      Contract                                  Output
  --------------------------------------------------------------------
  script     hook 60-140 chars                          102 chars
  script     narration 110-150 words                    118 words
  script     sections 4-6                               5
  scenes     total_duration_sec 50-65                   58
  scenes     scenes count 4-6                           5
  scenes     sum(duration_sec) == total                 58 == 58
  scenes     visual_prompt in English                   all English
  scenes     ids scene-01..NN sequential                yes
  seo        title 50-70 chars                          51 chars
  seo        description 300-600 chars                  359 chars
  seo        tags 5-8                                   7
  seo        preferred phrases used when applies        "mediana edad",
                                                        "segunda juventud"
  brand      calm + respectful tone (from MD)           matched

No forbidden phrases. No invented stats. Accents preserved. The
duration contract in particular is a big win. Historically, Gemini QA
caught duration mismatches here; current QA uses Gemini.

Docker verification: `133 passed in 23.64s`.

### Historical: V3 Phase 1 Step 16 Auto QA + rework loop + DNA consistency

This historical section predates the Gemini switch and used real Gemini QA, including
self-healing rework, and verifies the channel DNA holds across two
different ideas without manual intervention.

QA rework loop:

- `auto_rework_artifact(artifact, ..., chatgpt_fn)` reads
  `operator/gemini/<artifact>_qa.json`, builds a Spanish rework
  message with the QA `issues` and `required_changes`, sends it into
  the persistent ChatGPT tab, resets `<artifact>_promote` and
  `<artifact>_qa` back to pending, and re-promotes the artifact.
- `auto_qa_with_rework(artifact, ...)` wraps the corresponding
  `auto_*_qa_stage`, catches `StageInputMissingError` from a
  NEEDS_REWORK verdict, and retries up to
  `channel.yaml -> qa_rules.thresholds.max_retry_per_qa` (default 3)
  before giving up.
- `/run-all` swaps the three `auto_*_qa_stage` calls for
  `auto_qa_with_rework`, so a failed QA self-heals via ChatGPT
  instead of halting the pipeline.

Gemini scrape composite:

- The text-diff stability wait failed in a persistent Gemini tab
  when two consecutive QA responses were byte-identical (e.g. two
  PASS verdicts with identical scores and empty issues). Driver
  kept scraping the prior response and timed out.
- Gemini scrape now returns a composite ``"[count=N]\n<text>"``
  where ``N`` is the number of balanced JSON objects in the body
  (filtered to exclude our own user-prompt-shaped objects). Any new
  response — a new JSON or a plain "OK" briefing reply — bumps
  either count or text, so the stability wait reacts. The driver
  strips the prefix before returning.
- `_wait_for_stable_response` emits one log line per poll iteration
  (with `log_tag` = ``chatgpt`` or ``gemini``) so scrape failures
  are diagnosable from `docker compose logs browser-worker` without
  rebuilding.

Live DNA consistency check against the real Browser Appliance:

- Video 1 ``composite-1779270300`` (sleep habits idea):
  - 8/8 stages PASS in 7m06s.
  - Historical Gemini QA verdicts: PASS, scores 5/5/5/5.
  - ``video.mp4`` 54.06 s (target_duration_sec=54).
- Video 2 ``dna2-1779270810`` (morning energy idea):
  - 8/8 stages PASS in 5m45s.
  - All three QA verdicts: PASS, scores 5/5/5/5.
  - ``video.mp4`` 54.06 s.
- Cross-video DNA contracts (both honored without manual fixup):
  - narration: V1=128 words, V2=124 (range 110-150).
  - scenes: V1=5, V2=5 (range 4-6).
  - ``sum(duration_sec) == total_duration_sec`` true for both.
  - SEO title: V1=55 chars, V2=54 (range 50-70).
  - SEO description: V1=414 chars, V2=327 (range 300-600).
  - tags: V1=7, V2=7 (range 5-8), language ``es-419`` both.
  - Brand voice (calm/respectful/practical) consistent across both.
  - Preferred positioning phrases used in both (``adultos 45+``,
    ``bienestar 45+``, ``Vida Plena 45+``, ``bienestar después de
    los 45``); no forbidden phrases in either.
- The channel DNA reproduces across different topics; we are ready
  to start filling a real channel without diversification (persona
  injection, A/B variants) until we have 10-20 published videos to
  learn from.

Docker verification: ``143 passed in 18.48s``.

### V3 Phase 1 Step 17 keyword scoring driver (free tier via YouTube extension overlay)

- New `src/video_agent/browser_worker/drivers/keyword.py` scrapes the
  Search Companion sidebar the keyword scoring Chrome extension injects into
  YouTube search results pages. Free tier covers keyword score,
  volume, competition, related keywords — no paid API needed.
- HTTP routes: `POST /keyword/sessions`, `POST /keyword/sessions/{sid}/score`,
  `POST /keyword/sessions/{sid}/score_batch`, `DELETE /keyword/sessions/{sid}`.
- Robust scrape: wait for digit + ``SEARCH TERM:`` match to defend
  against stale panel between queries; ``about:blank`` detour forces
  the SPA to remount the panel each query; soft-fail
  ``Not enough search data`` instead of raising.
- Live verified end-to-end with the user signed into keyword scoring free tier:
  - ``dormir mejor 50`` -> score 25, related = [dormir, musica para
    relajarse, dormirse rapido].
  - ``rutina matutina 45`` -> score 25, distinct related set.
  - ``habitos saludables despues de los 50`` -> soft-fail
    ``not_enough_search_data``.
- Driver not yet wired into the orchestrator; `seo_keyword` stage to
  follow.

### V3 Phase 1 Step 18 Long-form 20-30 min pipeline

- `channel.yaml` declares a ``content_format`` block:
  ``target_duration_sec: 1500`` (25 min), window 1200-1800 s,
  24-40 scenes, publish schedule 3/week Mon/Wed/Fri 19:00
  America/Mexico_City for the 45+ wellness audience.
- `briefing.py` per-stage contracts rewritten for the new format:
  - script: hook 80-180 chars, 10-15 sections, narration 2900-4350
    palabras (~20-30 min at 145 wpm), cta 20-250 chars.
  - scenes: total 1200-1800 s, 24-40 scenes of 30-60 s each.
  - seo: description 700-1500 chars in 3-5 paragraphs, tags 6-10
    mixing broad + long-tail.
- `manual-idea.schema.json` `target_duration_sec` max bumped to 1800.
- `inputs/long_form_idea_sleep.json` provides a 12-point 25-min idea
  for smoke testing without breaking the 54 s mock-pipeline tests.

### V3 Phase 1 Step 19 YouTube-style operations dashboard

- `GET /` serves a single-page dashboard. Backend endpoints added:
  - `GET /jobs` lists every job folder under JOBS_DIR with a
    summary view (stages_done / stages_total, current_stage,
    timestamps).
  - `GET /jobs/{id}/timeline` returns per-stage status + actual
    seconds + ETA + input/output artifact paths. Render ETA scales
    with `target_duration_sec` (~1.2x realtime).
  - `GET /jobs/{id}/artifact?path=...` streams any file inside the
    job directory (path-traversal-protected via `_resolve_inside`).
- Dashboard UI:
  - Left rail lists jobs sorted newest first with per-job progress
    bar and current_stage label.
  - Right pane shows overall percent + ETA, then a step-by-step
    timeline (numbered cards: pulsing blue for `in_progress`, green
    for `completed`, red for `failed`).
  - Each card expands to INPUT / OUTPUT artifact lists; clicking a
    file fetches and previews inline (JSON/text dump, image, or
    embedded video player).
  - Final block appears when `video.mp4` exists: full-width video
    player, thumbnail preview, title / description / tags / language
    from `seo.json` in copy-to-clipboard boxes + download link.
  - WS `/jobs/{id}/events` streamed into a live events log; each
    event also nudges a timeline refetch.

### V3 Phase 1 Step 20 QA browser flow switched to Gemini

- Gemini's free tier turned out to be too rate-limited for the 3 QA
  stages of a long-form video; switched the QA driver to Gemini
  (gemini.google.com/app), still through the Browser Appliance noVNC profile.
- New `src/video_agent/browser_worker/drivers/gemini.py` mirrors the
  ChatGPT driver shape (open / send_message / close, persistent
  session, humanized cadence, login URL detection at
  `gemini.google.com/app/login` and `/sign-in`).
- Worker dispatch + auth status routes know about a "gemini" site;
  `_open_session("gemini")` works alongside chatgpt/gemini/keyword.
- Orchestrator `/run-all` opens a persistent Gemini tab for the QA
  trio instead of Gemini.
- ``operator.py`` gained `extract_json_objects` which extracts every
  parseable JSON block from Gemini responses and tries each one
  against the artifact schema (Gemini tends to wrap JSON in
  commentary). ``promote_operator_artifact`` uses this so a noisy
  response no longer aborts the stage.
- Tests updated to cover Gemini login-URL detection.

### V3 Phase 1 Step 21 Resume `/run-all` + Gemini artifact normalisation

- `/run-all` now reads `job.json` and skips stages already
  `completed`, so the operator can interrupt and resume long-form
  runs without manual stage juggling.
- Gemini QA responses occasionally include extra `scores`-shaped
  JSON objects; the normaliser in `operator.py` picks the schema-
  valid candidate and rejects the noise.
- `tests/test_operator_workflow.py` adds coverage for multi-JSON
  Gemini output.

### V3 Phase 1 Step 22 ChatGPT per-scene image gen (Phase A + Phase B)

**Phase A** (commit `771d6eb`):

- `BrowserClient.generate_image(prompt, project_name, out_path)` wraps
  `POST /chatgpt/image` on the browser-worker.
- `generate_scene_asset(job_dir, channel_path, scene_id, image_fn)`:
  looks up `visual_prompt` in `scenes.json`, builds a brand-consistent
  image prompt with the style prefix (16:9 cinematic, soft natural
  light, no text/watermark, adultos 45+), calls `image_fn`, and patches
  `scenes.json → scenes[n].asset_refs.primary` with the relative path.
- `_find_asset_refs_primary` in `stages/assets.py` resolves
  `asset_refs.primary` and gives it higher priority than the stock/local
  directory lookup, so the render stage automatically picks up
  ChatGPT-generated images.
- HTTP route: `POST /jobs/{id}/scenes/{scene_id}/generate_asset`
  → `{scene_id, src, local_path, project_name, bytes, asset_refs_primary}`.
- `docker-compose.yml`: `browser-worker` mounts `./jobs:/app/jobs` so
  files written by the worker are visible from the host.
- Live verified: 87 s wall, 1.9 MB PNG, correct couple-in-robes scene.

**Phase B** (commit `a2ee3df`):

- `assets_chatgpt` added to `DEFAULT_STAGES` between `seo_qa` and `render`.
- `auto_assets_chatgpt_stage(job_dir, channel_path, image_fn, throttle_sec=8.0)`:
  iterates all scene IDs, calls `generate_scene_asset` per scene with an
  8 s throttle between calls; a failed scene logs `SCENE_ASSET_FAILED`
  and continues — render falls back to stock/placeholder for that scene.
- `/run-all` now runs `assets_chatgpt` between the `seo_qa` and `render`
  blocks, passing `client.generate_image`.
- `FakeBrowserClient` in tests gains a `generate_image` stub.
- 3 new unit tests: gen_all_scenes, continue_on_failure, wrong_stage.
- 4 existing render/review tests updated (`_fake_pass_assets_chatgpt`).
- 150 tests green.

### V3 Phase 1 Step 23 A/B Title + Thumbnail system

YouTube allows 3 thumbnails + 3 titles for A/B testing. Full A/B
system added:

- **Title scorer** (`src/video_agent/seo/title_scorer.py`): scores
  each `{title, thumbnail_text}` variant 0-100 (title 0-50 +
  thumbnail_text 0-50) based on CTR criteria (word count, digits,
  power words, emotion, ALL-CAPS hook).
- **`title_variants` in SEO**: `operator.py` now asks ChatGPT for
  EXACTLY 3 `{title, thumbnail_text}` variants in the SEO prompt.
  `promote_operator_artifact` scores + sorts them; top variant becomes
  `seo.title` + `seo.thumbnail_text`. `schemas/seo.schema.json` adds
  `title_variants` (not in `required` — backward compat).
- **3-thumbnail render**: `stages/render.py` `build_thumbnail_commands()`
  returns 3 Remotion `still` commands (one per variant), outputting
  `thumbnail_1.jpg`, `thumbnail_2.jpg`, `thumbnail_3.jpg`. Backward
  compat: `thumbnail.jpg` is a copy of `thumbnail_1.jpg`.
- **`thumbnail_image` DALL-E stage**: `auto_thumbnail_image_stage()`
  generates a photorealistic background (rule of thirds, emotional
  face, empty right third for text overlay, no text/watermark) via
  ChatGPT image gen. Saves to `assets/thumbnail_bg.png`, writes
  `seo.json → thumbnail_path`. `Thumbnail.tsx` uses `thumbnail_path`
  as primary bg (falls back to scene bg → gradient).
- **Webapp A/B grid**: dashboard `renderFinal()` shows 3-card AB grid;
  gold border + ⭐ badge on winner (highest score); score badge on
  each card.
- **`POST /jobs/{id}/stages/seo_keyword/auto`**: standalone route to
  run seo_keyword outside of `/run-all`.
- **`POST /run-batch`**: queue multiple `/run-all` calls back-to-back
  for overnight batch production. Soft-fails individual jobs; returns
  `{total, succeeded, failed, results}`.
- 217 tests pass (`docker compose exec app python -m pytest -q`).

### Current operational state

- **217 tests pass** (`docker compose exec app python -m pytest -q`).
- Pipeline produces 20-30 min long-form videos end-to-end with
  ChatGPT writing + Gemini QA + ChatGPT image gen + Remotion render,
  all through the Browser Appliance (host noVNC for manual login once).
- Full pipeline stage order: `idea_research → script → script_promote →
  script_qa → scenes → scenes_promote → scenes_qa → seo → seo_promote →
  seo_qa → seo_keyword → thumbnail_image → assets_chatgpt →
  whisper_timestamps → render → review`.
- `persona_eval` implementation exists but is intentionally out of default
  `/run-all`; run it manually only when needed via
  `POST /jobs/{job_id}/stages/persona_eval/run`.
- Dashboard at `http://127.0.0.1:8000/` gives step-by-step progress
  + ETA + final video / metadata / thumbnail ready to copy into
  YouTube Studio.
- Batch production: `POST /run-batch` queues multiple jobs overnight.
- A/B testing: 3 title+thumbnail variants auto-scored; winner shown
  first in dashboard + upload.
- Channel target: Vida Plena 45+ (Spanish wellness 45+), 3 videos
  per week Mon/Wed/Fri 19:00 Mexico time.

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
      -> ChatGPT/Gemini/keyword scoring/image generation browser operations
      -> browser-runtime container (Chromium + Xvfb + noVNC + CDP) over internal appliance_net
```

State must remain file-based under `jobs/<job_id>/`, including:

- `job.json`
- `events.jsonl`
- `idea.json`
- `operator/chatgpt/*_prompt.txt`
- `operator/chatgpt/*_raw.json`
- `operator/gemini/*_qa_prompt.txt` (legacy folder name; current QA provider is Gemini)
- `operator/gemini/*_qa_raw.json` (legacy folder name; current QA provider is Gemini)
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
- `operator/gemini/script_qa.json` (legacy folder name; current QA provider is Gemini)
- `operator/gemini/scenes_qa.json` (legacy folder name; current QA provider is Gemini)
- `operator/gemini/seo_qa.json` (legacy folder name; current QA provider is Gemini)
- `render_props.json`
- `visual_review.json`
- `visual_contact_sheet.jpg`
- `thumbnail.jpg`
- `video.mp4`
- `operator_review.html`

Latest full verification:

```text
docker compose run --rm video-agent pytest -q
143 passed in 18.48s
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
- Historical run: Gemini QA was `PASS` for `script`, `scenes`, and `seo`. Current QA provider is Gemini.
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
- Historical Gemini finding: Gemini QA was more reliable in a fresh chat per artifact. Current QA uses Gemini.
- Historical Gemini finding: Gemini sometimes showed `Submit` instead of a send icon; retained only as legacy driver context.
- Scene output needs stricter validation:
  - `job_id` must match the current job folder.
  - scene IDs should use the expected `scene-01` format.
  - `asset_refs` must be an object, not a list.
  - `visual_prompt` should be English for stock/image generation.
  - Spanish user-facing text must preserve accents.
  - ChatGPT must not prefill internal QA as `PASS`.
- SEO output needs stricter validation:
  - language must match `channel_config.seo.language` exactly (Vida Plena 45+ is now `es-ES`; legacy LatAm channels may still use `es-419`). Historical note: this list was authored before the Spain-first migration.
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

## Security & Reliability Hardening (2026-05-22)

Audit of `src/` produced ~20 findings; all but the per-job delete-lock are now fixed:

- **Path traversal** locked down: `_safe_job_dir` in `web/app.py` validates every `job_id` (POST/DELETE/GET/idea/stages/timeline/artifact). `timeline_helpers.resolve_inside` switched from `startswith` to `Path.is_relative_to`.
- **Browser-worker `/chatgpt/image`** now jails `out_path` under `WORKER_ASSETS_ROOT` (default `/app/jobs`) — refuses absolute paths that escape.
- **Auth handlers** (`/auth/{site}/status`, `/auth/{site}/cookies`) re-raise `HTTPException` so 404/409 from inner code paths survive.
- **Browser-worker session creation** serialized per-site via `asyncio.Lock`.
- **`save_job`** now atomic (tempfile + `os.replace`) so a crash mid-write cannot corrupt `job.json`.
- **`render._run_with_progress`** uses `Popen` as a context manager + explicit `stdout.close()`; child process always cleaned up on exception.
- **`idea_generator`** now uses `defusedxml.ElementTree` for YouTube RSS + Google Trends (XXE-safe). Dedup uses `dict.fromkeys` instead of side-effect comprehension.
- **`POST /jobs/{id}/idea`** validates payload against `schemas/manual-idea.schema.json`.
- **`assets_chatgpt` stage** skips scenes without `id` and re-raises `asyncio.CancelledError` so `/run-all` cancellation actually propagates.
- **`AssetLibrary`** uses a single timestamp per store call (fixes year/month skew at midnight UTC boundary); `_aspect_ratio` guards against zero dimensions.
- **`query_cache.set`** now records correct `results_count` for `pexels_video` (was always 0).
- **Render progress endpoint + render writes** use explicit `encoding="utf-8"`.
- **Events WebSocket loop** exits with close-code 4404 if `job.json` is deleted under it.

Remaining: `DELETE /jobs/{id}` still has a small TOCTOU window between the in-progress check and `shutil.rmtree`. Proper per-job lock requires shared lock manager — deferred.

### Round 2 (2026-05-22, after second-pass audit)

- **`channel_id` path traversal**: added `_safe_channel_id` validator; applied to `/channels/{id}/ideas` (GET), `/channels/{id}/ideas/score` (POST), `/channels/{id}/sync-videos` (GET), `/channels/{id}/ideas/generate` (POST).
- **`save_ideas`** (idea_generator) now validates `channel_id` and asserts the resolved destination stays inside `out_dir` — defends CLI/batch callers too.
- **`write_json`** (`utils/json_io.py`) is now atomic (tempfile + fsync + `os.replace`). Every artifact write (`scenes.json`, `seo.json`, QA outputs, scene asset patches) inherits crash-safety.
- **`list_saved_ideas`**: removed root `inputs/*.json` fallback that leaked other channels' ideas; scoped to `inputs/ideas/<channel_id>/` only.
- **`AssetLibrary.find_by_query`**: replaced f-string SQL `clause` with branched static SQL (kills SQLi footgun).
- **`generate_scene_asset`**: validates `scene_id` against safe-id regex before composing `assets/<scene_id>.png`.
- **`/run-batch`**: bad `job_id` from `_safe_job_dir` now lands in per-job `error` slot instead of aborting the batch.
- **`execute_run_all`**: takes a non-blocking `fcntl.flock` on `<job_dir>/.run.lock` for the full pipeline run — two concurrent `/run-all` calls on the same job get HTTP 409 instead of stomping state.
- **`UrlDownloadClient.download`**: `_assert_safe_http_url` rejects non-http(s) schemes and loopback / link-local / RFC1918 hosts (SSRF defense for stock-asset URLs).
- **`browser_client.close_session`** swallows: logged at warn instead of silent `pass`.
- Misc: `render_with_remotion` 100% write uses `encoding="utf-8"`; `ImagePromptRequest.out_path` comment aligned with new jail behaviour.

### Round 3 (2026-05-22, third-pass audit)

- **`asset_refs.primary` path traversal** (HIGH): `stages/assets.py:_find_asset_refs_primary` now refuses absolute paths and `..` segments; resolved candidate must be `is_relative_to(job_dir)`. Previously an operator-supplied primary string could leak arbitrary readable files into the publicly-served `remotion/public/jobs/<id>/assets/` mirror.
- **Telegram HTML-escape** (MEDIUM): all interpolated values inside `<code>…</code>` / `<a href>` are now run through `html.escape(..., quote=False)` so traceback strings with `<` or `&` no longer fail Telegram's HTML parser.
- **Telegram asyncio modernization** (MEDIUM): replaced `asyncio.get_event_loop()` with `asyncio.get_running_loop()` (or `asyncio.run`) in `notify_sync` and `_send_video_file` — fixes the 3.12+ deprecation and the no-loop `RuntimeError`.
- **TTS sample-rate drift** (MEDIUM): `synthesize_scene_track` now locks `sample_rate` from config and raises on any scene metadata that reports a different rate. Prevents pitched/garbled audio when two scenes report different rates.
- **`stages/thumbnail.py`**: write seo.json (with QA result) before raising on QA fail, so failed runs are inspectable on disk (parity with script/scene stages).
- **`cli.py _run_auto`**: catches `json.JSONDecodeError` on the idea file and returns 2 with a clear message instead of a bare traceback.

### Round 4 (2026-05-22, remaining lows)

- **`stages/script.py`**: retry loop breaks early when `retry_action` is anything other than `add_disclaimer` (was wasting 2 no-op iterations).
- **`stages/scene.py`**: collapsed the 3-iter loop to a single pass — no `retry_action` mutates the doc anyway.
- **`operator_validators.py`**: `Counter` replaces `list.count` in two places (O(n²) → O(n)) for duplicate scene-id / tag detection.
- **`qa/common.average_sentence_words`**: regex split on `[.!?]+` so `...` counts as a single boundary instead of three empty sentences.
- **`operator.py:813`**: `<a href={!r}>` Python-quoted attribute replaced with explicit `<a href="…">` — removes fragility around HTML attribute encoding of `&#x27;`.

False positives from audit: `extract_json_object` backslash handling (already correct — `escape` flag unconditionally resets next char).

### Round 5 (2026-05-22, final pass — all material findings cleared)

- **`pipeline._write_visual_review`**: asserts `len(asset_scenes) == len(doc_scenes)` so a dropped scene fails loudly instead of silently truncating via `zip`.
- **`stages/render.py`**: thumbnail variant loop catches `CalledProcessError` per-variant; raises only if no variant produced `thumbnail_1.jpg`. Failed variants no longer wipe a successfully rendered video.
- **`stages/visual_contact_sheet.py`**: refuses empty `scenes`; uses `scene.get("background")` so missing key returns a placeholder thumb instead of `KeyError`.
- **`assets/providers.py`**: Pixabay user URL quotes `user` via `urllib.parse.quote` — spaces / special chars no longer break the stored attribution URL.

Skipped: `qa/script_qa.py` disclaimer phrase variant list — intentional QA gate; needs product decision on which phrases to accept.

## Shorts QA Terminal Decision (2026-06-08)

Fixed the final max-regeneration failure that left the UI showing a generic
"QA failed after max regeneration attempts" message even after a successful
WARN continuation. The pipeline now separates "max attempts reached" from
"hard failure":

- `short_builder._build_short_impl` — every terminal hard-fail return (script
  retry-collapse, post-loop fallback) now sets `failure_stage` + `failure_reason`
  and writes `qa_decision_summary.json` with `decision=failed_hard_blocker`.
  Warning-only / `slideshow_risk`-only outcomes still downgrade to
  `continued_with_warn` (verdict WARN) and continue to audio/render. New helper
  `_qa_blocker_details()` extracts explicit blocker text (excludes
  `slideshow_risk` and plain `warning` severities).
- `routes/shorts_studio.py` — the drafts endpoint surfaces the structured
  decision as `d.qa_decision` (read from `qa_decision_summary.json`).
- `shorts_studio.html` — new `renderShortReviewNotice(d)` shows "Publishable
  with warnings" (green) for `continued_with_warn`/WARN, or an explicit
  hard-blocker notice listing the blockers; the unconditional generic message
  is gone.

Tests: `tests/test_shorts_build.py::test_script_hard_fail_terminal_sets_explicit_failure_and_decision`,
`tests/test_shorts_studio.py::test_shorts_studio_drafts_surfaces_qa_decision_and_failure`.
Full suite: 1048 passed.

## Recent Commits

- `fef352e fix: resume run-all and normalize Gemini artifact shapes`
- `9a5d5a9 feat: switch QA browser flow from Gemini to Gemini`
- `5846627 feat: redesign dashboard to youtube-style operations UI`
- `a9dca52 Make dashboard step-by-step with artifacts, ETA, video, metadata`
- `ba026ce Add minimal web dashboard for live job progress`
- `0c21556 Switch pipeline to 20-30 min long-form format`
- `3fe40c2 Add keyword scoring driver (free tier via YouTube Chrome extension overlay)`
- `0a181e8 Document auto QA rework loop and 2-video DNA consistency`
- `5e3f590 Add QA rework loop and fix Gemini scrape for byte-identical responses`
- `374f668 Persist Gemini QA result even on NEEDS_REWORK verdict`
- `509df24 fix: let browser worker return timeout errors`
- `71ece9c feat: add persistent auto QA pipeline`
- `e49c7b1 Enrich per-stage briefing + task prompt for DNA consistency`
- `b42c13c One temp chat per stage with role/context briefing message`
- `f1ec0ed Add POST /jobs/{id}/run-all one-shot pipeline endpoint`

## Not Yet Done

- Long-form pipeline has been smoke-tested manually but no automated test asserts the 20-30 min config end-to-end (would require ~30-40 min wall to run).
- YouTube upload is still manual (operator copies title/desc/tags from dashboard, drags video.mp4 into Studio); no Playwright auto-upload driver.
- Analytics ingestion (retention curve / CTR feedback into next prompt) is deferred.
- Persona injection, multi-channel scaling are deferred until 10-20 published videos give a baseline.
- ChatGPT image generation per scene (`assets_chatgpt`): stock/placeholder fallback still fires when image gen fails for a scene.

## Next Recommended Work

1. **First production run** — kick off 3 videos for Vida Plena 45+
   (`Mon/Wed/Fri` cadence) through `/run-all` or `/run-batch` with
   ChatGPT image gen + thumbnail_image active. Upload via YouTube
   Studio. Capture retention + CTR after 48 h.
2. **Idea batch via `/channels/{id}/ideas/generate`** — generate 10-20
   ideas at once from seed topics so the pipeline queue is always full.
3. **YouTube upload automation** — Playwright driver for YouTube Studio
   upload page: fill title/description/tags from `seo.json`, attach
   `video.mp4` + `thumbnail_1.jpg`, publish.
4. **Analytics feedback loop** — after 10-20 published videos, pull
   CTR + retention data and feed back into briefing / scoring.
