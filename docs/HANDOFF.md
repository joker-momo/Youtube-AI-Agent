# Youtube AI Agent MVP Handoff

Date: 2026-05-18
Workspace: `/Users/joker/Documents/Youtube-AI-Agent`

## Current State

The MVP is implemented and Docker-first.

It proves this flow:

```text
manual_idea.json -> script -> scenes -> assets -> Remotion video -> thumbnail -> seo.json -> report.md
```

The current demo channel is `vida-plena-45`, matching the framework docs. Providers are mock/local and deterministic. Hermes, YouTube upload, OAuth, Telegram approval, scheduling, trend research, and real LLM/TTS/image APIs are intentionally out of scope.

## How To Run

Build and run through Docker only:

```bash
docker compose build
docker compose run --rm video-agent
```

Run tests:

```bash
docker compose run --rm video-agent pytest -v
```

Shortcut scripts:

```bash
scripts/run_mvp.sh
scripts/test_mvp.sh
```

## Verified Output

Successful rendered job:

```text
jobs/20260518-095238-vida-plena-45-habitos-nocturnos-para-dormir-mejor-despues-de-l/
```

Key files:

- `video.mp4`
- `thumbnail.jpg`
- `seo.json`
- `report.md`
- `script.json`
- `scenes.json`
- `assets_manifest.json`
- `render_props.json`
- `events.jsonl`

Verification already performed:

- Docker image builds.
- Docker test suite passes: 14 tests.
- Remotion compositions are available: `ChannelVideoStandard`, `ThumbnailStandard`.
- Full Docker run produced `video.mp4` and `thumbnail.jpg`.
- `ffprobe` confirmed video is `1920x1080`, `54.0s`.

## Important Commits

- `f449383` Add Remotion MVP video agent design
- `8b45485` Add Remotion MVP implementation plan
- `e2948b7` feat: add MVP config and schemas
- `4fe8aeb` feat: add MVP IO and validation utilities
- `7c4d869` feat: add mock providers and deterministic QA
- `1977283` feat: generate structured MVP job artifacts
- `706868a` feat: add MVP pipeline CLI
- `2c8186b` feat: add Dockerized Remotion MVP renderer

## Main Files

- `Dockerfile`
- `docker-compose.yml`
- `README.md`
- `inputs/manual_idea.json`
- `configs/vida-plena-45/channel.yaml`
- `configs/vida-plena-45/style-dna.json`
- `src/video_agent/pipeline.py`
- `src/video_agent/cli.py`
- `src/video_agent/providers/mock.py`
- `src/video_agent/stages/render.py`
- `remotion/src/Root.tsx`
- `remotion/src/ChannelVideo.tsx`
- `remotion/src/Thumbnail.tsx`

## Notes

- The host editable Python install was removed. Host `jobs/`, failed temporary jobs, `.pytest_cache`, and `src/youtube_ai_agent.egg-info` were cleaned up.
- Host Python packages like `pytest`, `jsonschema`, and `PyYAML` were not removed to avoid breaking other projects.
- `jobs/` is gitignored, so rendered outputs stay local.
- `remotion/public/jobs/` is gitignored; the pipeline copies renderable assets there inside the container so Remotion can serve them through its public directory.

## Recommended Next Work

1. Add a real provider interface implementation behind the existing mock provider shape.
2. Improve visual richness of Remotion scenes while keeping `render_props.json` as the only render input.
3. Add real TTS provider and replace silent narration audio.
4. Expand QA checks for health/wellness safety.
5. Add optional persona evaluation after render.
6. Only later: Hermes skill migration and YouTube upload automation.

## Prompt For A New Thread

Use this if continuing in a new context:

```text
Read /Users/joker/Documents/Youtube-AI-Agent/docs/HANDOFF.md and continue from the Docker-first Remotion MVP state. Do not reintroduce host Python/Node setup; keep the project Docker-first.
```
