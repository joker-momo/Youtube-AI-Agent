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
-> Gemini QA
-> images/assets
-> TTS
-> Remotion render
-> review page
-> final video
```

The project should only prioritize tasks that directly complete this full video flow. Optimization, analytics, semantic reuse, persona eval, scheduling, and upload automation come later.

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
      -> Playwright CDP attach
      -> host Chrome dedicated profile on port 9222
```

Browser access uses the user's logged-in ChatGPT Plus, Gemini, and vidIQ sessions through a dedicated Chrome profile. The system must not auto-login or inspect browser secrets.

## Requirements

- Docker Desktop or a running Docker daemon

No host Python or Node setup is required.

## Run

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

During the v3 transition, the existing semi-automated browser flow still works through `operator-*` commands. Place the ChatGPT/Gemini-approved artifacts in a job directory:

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

`operator-next` looks at the current job folder, creates the next prompt file when needed, and prints the exact command to run after saving the ChatGPT or Gemini raw response.

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

After Gemini reviews that artifact, promote the raw QA response:

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

This creates `jobs/<job_id>/operator_review.html` with artifact status, Gemini QA verdicts, video, thumbnail, contact sheet, and scene notes. `operator-render` also refreshes this page automatically.

## Kokoro TTS

The default channel keeps `tts.provider: "mock-local"` for fast tests and silent placeholder audio. To generate real local narration with Kokoro:

```bash
docker compose run --rm video-agent python -m video_agent.cli run \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json \
  --tts-provider kokoro \
  --tts-voice-id ef_dora \
  --tts-lang-code e \
  --tts-speed 0.92
```

Kokoro runs inside Docker. The first run may download model files from Hugging Face; setting `HF_TOKEN` is optional and only helps with Hub rate limits.

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
4. Add Chrome dedicated profile setup script for CDP port `9222`.
5. Keep the existing v2 CLI and tests green.
