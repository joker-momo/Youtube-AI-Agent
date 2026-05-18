# YouTube AI Agent MVP

Docker-first MVP for producing YouTube-ready video artifacts from a manual idea.

```text
manual_idea.json -> script -> scenes -> assets -> Remotion video -> thumbnail -> seo.json -> report.md
```

The MVP uses deterministic mock providers. It does not use Hermes, YouTube upload, OAuth, Telegram, scheduled publishing, trend research, or real LLM/TTS/image APIs.

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
