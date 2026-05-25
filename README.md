# YouTube AI Agent

Docker-first YouTube video production system.

Current state:

- v2 CLI pipeline is working and remains supported during transition.
- v3 direction is approved: standalone local FastAPI web app with WebSocket progress and a separate browser-worker service.
- Hermes is dropped.
- Phase 1 still ends with manual YouTube upload.

Primary docs:

- `docs/HANDOFF.md`
- `docs/PROJECT_STATUS.md`
- `docs/VIDEO_AGENT_V3_STANDALONE_HANDOFF.md`

## Target V3 Flow

```text
trend/data intake
-> idea selection
-> ChatGPT script/scenes/SEO
-> Claude QA
-> images/assets
-> TTS
-> Remotion render
-> review page
-> final video
```

The project should only prioritize tasks that directly complete this full video flow. Optimization, analytics, semantic reuse, persona eval, scheduling, and upload automation come later.

Note: `persona_eval` code exists and can run manually via stage route, but it is intentionally excluded from default `/run-all` flow for now.

## Current v2 CLI Flow

```text
manual_idea.json -> script -> scenes -> assets -> Remotion video -> thumbnail -> seo.json -> report.md
```

The current deterministic CLI flow uses mock/local providers for fast verification, plus optional stock assets and Kokoro TTS. It does not yet provide the v3 web app or browser-worker.

## V3 Architecture Direction

```text
User browser
  -> app container
      -> FastAPI UI
      -> WebSocket progress
      -> orchestrator/state machine
      -> stage modules
      -> existing render/assets/TTS code
  -> browser-worker container
      -> Playwright CDP attach over the internal Docker network
      -> browser-runtime container (Chromium + persisted profile)
```

Browser access uses the user's logged-in ChatGPT Plus, Claude, and vidIQ sessions through a persisted Chromium profile mounted into the `browser-runtime` container at `browser_profiles/default`. The system must not auto-login or inspect browser secrets. CDP port 9222 is not published to the host; KasmVNC is bound to `127.0.0.1:7900` for manual sign-ins only.

## Requirements

- Docker Desktop or a running Docker daemon

No host Python or Node setup is required.

## Fresh Setup On A New Machine

If you are setting up the project on a new/fresh machine, we provide **one-click automated installers** that automatically install Docker and all other system prerequisites, configure credentials, build the containers, and launch the entire suite in the background.

### 🍎 macOS & 🐧 Linux

To install everything on macOS or Linux, simply navigate to the repository directory and run:

```bash
bash install.sh
```

- **macOS**: Automatically installs Homebrew, git, Docker Desktop, starts the Docker daemon, and boots the project.
- **Ubuntu / Debian**: Automatically installs prerequisites, registers Docker keys/repositories, installs Docker Engine + Compose plugin, configures groups for running without `sudo`, and boots the project.
- **CentOS / RHEL / Rocky Linux**: Automatically configures repository lists, installs Docker Engine + Compose plugin, and launches all services.
- **WSL (Windows Subsystem for Linux)**: If you prefer running via WSL Ubuntu, run: `bash installers/install_ubuntu.sh`.

### windows 🪟 Windows (Native PowerShell)

Open PowerShell as **Administrator**, navigate to the repository folder, and run:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; .\install.ps1
```

- **Windows Native**: Automatically installs Git and Docker Desktop (via `winget`), starts the Docker application daemon, handles environment bootstrap, builds all required containers, and launches the services.

Once the installer finishes, the dashboard will be live!

### Manual Setup (Alternative):
If you prefer to configure everything manually:
1. Ensure you have Docker Desktop or a running Docker daemon.
2. Bootstrap environment file:
   - On macOS/Linux: `cp .env.example .env`
   - On Windows: `Copy-Item .env.example .env`
3. Build and launch:
   ```bash
   docker compose build
   ```

---

## Run

To start the dashboard and background services, run:

### macOS / Linux:
```bash
bash run.sh
```

### Windows (Native PowerShell):
```powershell
.\run.ps1
```

This runner automatically verifies that Docker is running (and launches it if not), configures environments, and boots all necessary background processes.

Open in your browser:
- **Web Dashboard**: `http://localhost:8000` (To manage jobs, generate ideas, and trigger renders)
- **Browser Runtime (VNC)**: `http://localhost:7900` (Manual sign-ins for ChatGPT, Claude, vidIQ)

In KasmVNC, sign in manually to the sites used by the pipeline. The profile is persisted in `browser_profiles/default`, so you will not need to login repeatedly.

Optional quick verification:

```bash
docker compose run --rm video-agent pytest -v
```

```bash
docker compose run --rm video-agent
```

Outputs are written under `jobs/<job_id>/` on the host:

- `video.mp4`
- `thumbnail.jpg`
- `seo.json`
- `report.md`
- `script.json`
- `scenes.json`
- `assets_manifest.json`
- `render_props.json`
- `events.jsonl`

## Test

```bash
docker compose run --rm video-agent pytest -v
```

## Environment

Copy `.env.example` to `.env` when using stock photo APIs:

```bash
cp .env.example .env
```

Set:

```text
PEXELS_API_KEY=...
PIXABAY_API_KEY=...
```

Docker Compose passes those variables into the container. Do not commit `.env`.

## Skip Rendering

```bash
docker compose run --rm video-agent python -m video_agent.cli run \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json \
  --no-render
```

## Operator-Approved Content

During the v3 transition, the existing semi-automated browser flow still works through `operator-*` commands. Place the ChatGPT/Claude-approved artifacts in a job directory:

```text
jobs/<job_id>/script.json
jobs/<job_id>/scenes.json
jobs/<job_id>/seo.json
```

Generate copy/paste prompts for each stage:

```bash
docker compose run --rm video-agent python -m video_agent.cli operator-next \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json \
  --job-dir jobs/<job_id>
```

`operator-next` looks at the current job folder, creates the next prompt file when needed, and prints the exact command to run after saving the ChatGPT or Claude raw response.

```bash
docker compose run --rm video-agent python -m video_agent.cli operator-prompts \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json \
  --job-dir jobs/<job_id> \
  --stage script
```

After ChatGPT returns a raw response, promote it into a validated artifact:

```bash
docker compose run --rm video-agent python -m video_agent.cli operator-promote \
  --job-dir jobs/<job_id> \
  --artifact script \
  --raw-file jobs/<job_id>/operator/chatgpt/script.raw.txt \
  --channel configs/vida-plena-45/channel.yaml
```

After Claude reviews that artifact, promote the raw QA response (the path keeps `operator/gemini` for backward compatibility):

```bash
docker compose run --rm video-agent python -m video_agent.cli operator-promote-qa \
  --job-dir jobs/<job_id> \
  --artifact script \
  --raw-file jobs/<job_id>/operator/gemini/script_qa.raw.txt
```

Repeat with `--stage scenes` / `--artifact scenes`, then `--stage seo` / `--artifact seo`.
`operator-render` requires promoted `script_qa.json`, `scenes_qa.json`, and `seo_qa.json` with `verdict: "PASS"` by default. Use `--skip-operator-qa` only for legacy jobs or debugging.

Then render that approved content through the same Docker pipeline:

```bash
docker compose run --rm video-agent python -m video_agent.cli operator-render \
  --channel configs/vida-plena-45/channel.yaml \
  --job-dir jobs/<job_id>
```

Use `--no-render` to validate JSON, prepare assets, create `render_props.json`, write the visual review, and refresh `operator_review.html` without rendering the MP4.

Write a local review page for a completed or in-progress operator job:

```bash
docker compose run --rm video-agent python -m video_agent.cli operator-review \
  --job-dir jobs/<job_id>
```

This creates `jobs/<job_id>/operator_review.html` with artifact status, QA verdicts, video, thumbnail, contact sheet, and scene notes. `operator-render` also refreshes this page automatically.

## Kokoro TTS (Local In Docker)

The current channel config uses local Kokoro TTS in Docker. The image now
attempts to warm up Whisper tiny + Kokoro model cache at build time so first
pipeline runs are less likely to stall on model download.

You can still override voice/speed at runtime:

```bash
docker compose run --rm video-agent python -m video_agent.cli run \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json \
  --tts-provider kokoro \
  --tts-voice-id ef_dora \
  --tts-lang-code e \
  --tts-speed 0.92
```

Kokoro runs inside Docker. If model warmup is skipped during build (network
restricted), runtime will download on demand and keep cache under `./caches/`.

## Resume After Restart (No Need To Re-run From Start)

Yes, you can restart and continue from the stuck stage:

1. If a run appears stuck, click `Stop Job` (optional but recommended).
2. Restart services:

```bash
docker compose restart app worker browser-worker browser-runtime
```

3. In webapp, run the same job again (`Run All`).

The pipeline resumes from the first non-completed stage (for example
`whisper_timestamps`) and keeps completed outputs from earlier stages.

### Anti-stuck timeout for Whisper/TTS

`whisper_timestamps` now has hard timeouts so it fails with a clear reason
instead of hanging forever:

- `WHISPER_SYNTH_TIMEOUT_SEC` (default `900`)
- `WHISPER_MODEL_LOAD_TIMEOUT_SEC` (default `300`)
- `WHISPER_TRANSCRIBE_TIMEOUT_SEC` (default `1800`)

Set them in `.env` when needed.

## Batch Runs And Audit

Run several ideas in sequence and write a visual QA audit:

```bash
docker compose run --rm video-agent python -m video_agent.cli batch \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/batch_idea_sleep_habits.json \
  --idea inputs/batch_idea_balanced_breakfast.json \
  --idea inputs/batch_idea_daily_walk.json \
  --idea inputs/batch_idea_hydration.json \
  --audit-path jobs/latest_batch_audit.md
```

Use `--no-render` for a fast artifact-only batch. To audit existing jobs:

```bash
docker compose run --rm video-agent python -m video_agent.cli audit \
  --job jobs/<job_id_1> \
  --job jobs/<job_id_2>
```

## Browser Driver Speed Profile

The ChatGPT / Claude / Gemini browser drivers run in **balanced mode** by default. The visible cadence still looks like a real user typing carefully — real keystrokes, hover-before-click, occasional "thinking" pauses, no instant insert-text dumps — but the wait-for-response layer is rewritten to detect stream-end via an in-page `MutationObserver` instead of polling `page.evaluate` every 250-500 ms. The result is roughly **50× fewer CDP round-trips per turn** and stream-end detection within ~100 ms of the final mutation instead of one full poll interval.

Three modes, controlled by `BROWSER_HUMAN_MODE`:

| Env var | `balanced` (default) | `fast` | `human` |
|---|---|---|---|
| `BROWSER_HUMAN_PAUSE_MIN_MS` / `MAX_MS` | 200 / 700 | 100 / 400 | 400 / 1400 |
| `BROWSER_HUMAN_TYPING_MIN_MS` / `MAX_MS` | 25 / 75 | 18 / 55 | 35 / 110 |
| `BROWSER_HUMAN_THINK_PROB` | 0.03 | 0.02 | 0.04 |
| `BROWSER_HUMAN_PASTE_THRESHOLD` (chars) | 150 | 100 | 200 |
| `BROWSER_HUMAN_PASTE_PAUSE_MIN_MS` / `MAX_MS` | 700 / 1800 | 400 / 1200 | 1500 / 3500 |
| `BROWSER_HUMAN_POST_READ_PAUSE` | `0` | `0` | `1` |
| `BROWSER_HUMAN_STABLE_MS` | 600 | 400 | 1500 |
| `BROWSER_HUMAN_STABLE_POLL_MS` | 150 | 120 | 300 |

Notes:

- **`balanced`** is the production default. Visible cadence (typing speed, pre-send pauses, paste review) stays in the human-typist range; only the Python⇄Chrome detection layer is accelerated. Use this when ChatGPT / Claude tabs are unattended automation tabs.
- **`fast`** trims the visible cadence too. Pick this when latency matters more than the look (e.g. batch backfill of dozens of jobs overnight).
- **`human`** restores the original slow, conspicuously-human cadence. Use this for trust-building sessions, live demos, or recovery from a rate-limit warning.

How the technical detection works (`_wait_for_stable_response` in `src/video_agent/browser_worker/drivers/chatgpt.py`):

1. Before sending, the driver snapshots the previous assistant turn text.
2. After clicking send + waiting for the Stop button to hide, it calls `page.wait_for_function` with a predicate that:
   - Lazily installs a `MutationObserver` on the latest `data-message-author-role="assistant"` node.
   - Records `lastMutationTs = Date.now()` on every childList / subtree / characterData change.
   - Returns `true` only when `Date.now() - lastMutationTs >= STABLE_MS` **and** the scraped text differs from the prior snapshot.
3. Playwright polls the predicate inside the page every `STABLE_POLL_MS` (no CDP round-trip while the predicate is false). When it returns `true`, the Python side does one final scrape and returns the text.

Per-turn wins versus the old "evaluate every 500 ms" loop:

- ~50 fewer CDP messages for a 30-second response.
- Detection latency drops from one full poll interval (250-500 ms) to ~100 ms after the last mutation.
- The page-side observer is event-driven, so the Chrome CPU cost is essentially zero between mutations — unlike the old loop which fired a full `querySelectorAll` walk every poll.

A legacy poll-evaluate implementation lives next to the new one as `_wait_for_stable_response_legacy` for emergency fallback only.

## Opening Retention Policy

YouTube viewers decide whether to keep watching within the first 5–15 seconds. To protect that window we run with **no logo intro and no outro by default**:

- `branding.enable_intro_outro` is `false` in `configs/vida-plena-45/channel.yaml`. The pipeline (`src/video_agent/pipeline.py::_prepare_branding`) reads this flag and clamps `intro_sec` / `outro_sec` to `0`, skipping the Remotion intro and outro sequences entirely. The first frame the viewer sees is `scene-01` narration.
- `branding.show_channel_name_overlay` is also `false` by default. The "VIDA PLENA 45+" label that used to sit in the top-left of every scene is hidden so the opening frame is uncluttered. Flip the flag to `true` when a one-off cut needs the channel-name overlay visible.
- The ChatGPT script prompt (`_chatgpt_script_prompt`) carries a top-priority `OPENING RETENTION RULES (FIRST 30 SECONDS)` block. It bans meta-introductions (`En este video`, `Hoy`, `Bienvenidos`, `Hola`, channel-name openers) and forces one of four punchy openers: specific pain symptom, contradiction / pattern interrupt, concrete number + promise, or vivid micro-scene. Section 1 must deliver the first concrete payoff in the first ~30 seconds.
- The ChatGPT scenes prompt (`_chatgpt_scenes_prompt`) tells the model that `scene-01` is the first frame the viewer sees, caps scene-01 duration to 8–12 s, and demands that scenes 01–03 visuals show the pain/situation directly rather than a logo card or wide establishing shot.

To re-enable the logo intro/outro for a one-off ceremonial cut, set:

```yaml
branding:
  enable_intro_outro: true
  intro_sec: 2.5
  outro_sec: 2.5
  show_channel_name_overlay: true
```

## Visual Assets

The default channel uses `visuals.strategy: "auto"`:

1. Use local ChatGPT/manual images from `source_dir` when files like `scene-01.png` exist.
2. Otherwise search all configured stock providers, rank candidates globally, then choose the best asset.
3. Otherwise fall back to generated placeholder images.

Downloaded stock photos are stored in `asset_library/`, and query responses are cached in `caches/`.

## Local Scene Images

The asset stage can use local image files before falling back to generated placeholders.
Set this in a channel config:

```yaml
visuals:
  strategy: "local_directory"
  source_dir: "inputs/image-library"
  scene_count_target: 5
```

Name files by scene id, for example `scene-01.jpg`, `scene-02.png`, or `scene-03.webp`.
The pipeline copies them into the job assets folder and Remotion renders those copied images.

## Next Development Step

The next code work should be v3 Phase 1 Step 1:

1. Add minimal FastAPI `app` service.
2. Add minimal `browser-worker` service.
3. Extend Docker Compose.
4. Run `browser-runtime` with a persisted Chromium profile under `browser_profiles/default` and KasmVNC on `127.0.0.1:7900` for manual sign-ins.
5. Keep the existing v2 CLI and tests green.
