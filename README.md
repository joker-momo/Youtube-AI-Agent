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

## Skip Rendering

```bash
docker compose run --rm video-agent python -m video_agent.cli run \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json \
  --no-render
```
