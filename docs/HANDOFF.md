# Youtube AI Agent Handoff

Date: 2026-05-20
Workspace: `/Users/joker/Documents/Youtube-AI-Agent`

## Current Direction

The project direction is now **Video Agent v3: standalone local web app**.

The old v2 `operator-*` CLI flow remains functional during the transition, but it is no longer the final product shape. Do not continue Hermes work. Do not build Telegram, YouTube upload, persona eval, semantic cache, analytics, or other optimization/scaling features until the full final-video flow is reliable.

Primary architecture reference:

- [VIDEO_AGENT_V3_STANDALONE_HANDOFF.md](/Users/joker/Documents/Youtube-AI-Agent/docs/VIDEO_AGENT_V3_STANDALONE_HANDOFF.md)
- [PROJECT_STATUS.md](/Users/joker/Documents/Youtube-AI-Agent/docs/PROJECT_STATUS.md)

## Product Goal

Build a Docker-first standalone app that can produce one complete YouTube video from a channel and idea:

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

Phase 1 output:

```text
jobs/<job_id>/video.mp4
jobs/<job_id>/seo.json
jobs/<job_id>/operator_review.html
```

YouTube upload remains manual in Phase 1.

## Decisions Already Chosen

| Area | Decision |
|---|---|
| App | Standalone Python web app |
| UI | Local FastAPI web UI with WebSocket progress |
| LLM access | Browser web UI, not API |
| Browser control | Playwright CDP attach to host Chrome |
| Chrome | Dedicated host Chrome profile on port `9222` |
| Browser service | Separate `browser-worker` container |
| Flow | Sequential, file-based state detection |
| Failure mode | Fail-soft, retry, then manual prompt fallback |
| YouTube upload | Manual in Phase 1 |
| Hermes | Dropped |

## Current Implemented v2 Capabilities

- Docker-first deterministic MVP pipeline.
- Remotion render through `render_props.json`.
- Thumbnail, SEO JSON, report, visual review, and contact sheet artifacts.
- Local image folder support.
- Pexels and Pixabay stock image API support.
- Query cache and asset library foundation.
- Mock TTS and Kokoro TTS option.
- Semi-automated `operator-*` CLI workflow:
  - `operator-next`
  - `operator-prompts`
  - `operator-promote`
  - `operator-promote-qa`
  - `operator-render`
  - `operator-review`
  - `operator-status`
- Operator validators:
  - `job_id` mismatch blocking
  - scene ID and `asset_refs` validation
  - SEO `es-419`, tag count, duplicate/empty tag, and forbidden positioning checks

Latest verification:

```text
docker compose run --rm video-agent pytest -q
64 passed in 14.48s
```

## How To Run Current v2 Flow

Run tests:

```bash
docker compose run --rm video-agent pytest -q
```

Run deterministic MVP:

```bash
docker compose run --rm video-agent python -m video_agent.cli run \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json
```

Continue a semi-manual operator job:

```bash
docker compose run --rm video-agent python -m video_agent.cli operator-next \
  --channel configs/vida-plena-45/channel.yaml \
  --idea inputs/manual_idea.json \
  --job-dir jobs/<job_id>
```

Render approved operator artifacts:

```bash
docker compose run --rm video-agent python -m video_agent.cli operator-render \
  --channel configs/vida-plena-45/channel.yaml \
  --job-dir jobs/<job_id>
```

## Important Files

- `README.md`
- `docs/PROJECT_STATUS.md`
- `docs/VIDEO_AGENT_V3_STANDALONE_HANDOFF.md`
- `configs/vida-plena-45/channel.yaml`
- `src/video_agent/cli.py`
- `src/video_agent/operator.py`
- `src/video_agent/operator_validators.py`
- `src/video_agent/pipeline.py`
- `src/video_agent/assets/`
- `src/video_agent/tts/`
- `src/video_agent/stages/`
- `remotion/`

## Next Work

Start v3 Phase 1 Step 1:

1. Add a minimal FastAPI app skeleton.
2. Add a separate `browser-worker` skeleton.
3. Extend Docker Compose with `app` and `browser-worker` services.
4. Add a host Chrome profile setup script for CDP port `9222`.
5. Add health checks.
6. Keep existing v2 commands and tests working.

Do not implement browser automation details until the skeleton/health checks are in place.

## New Thread Prompt

Use this when continuing from a fresh context:

```text
Read /Users/joker/Documents/Youtube-AI-Agent/docs/HANDOFF.md, /Users/joker/Documents/Youtube-AI-Agent/docs/PROJECT_STATUS.md, and /Users/joker/Documents/Youtube-AI-Agent/docs/VIDEO_AGENT_V3_STANDALONE_HANDOFF.md. Continue with Video Agent v3 Phase 1 Step 1. Do not work on Hermes, Telegram, YouTube upload, persona eval, semantic reuse, analytics, or optimization tasks until the standalone full video flow is reliable. Keep the existing v2 CLI and tests working.
```
