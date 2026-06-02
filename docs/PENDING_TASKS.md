# Pending Tasks

## Production-Ready YouTube Shorts Pack Flow

Status: pending, not implemented.

Source spec:

```text
/Users/joker/Downloads/youtube_shorts_pack_spec.md
```

Summary:

- Implement Shorts Pack as a post-production module, not part of default long-form `DEFAULT_STAGES`.
- Add separate API flow:

```text
POST /jobs/{job_id}/shorts/run-all
GET  /jobs/{job_id}/shorts
```

- Add separate Shorts state under:

```text
jobs/<job_id>/shorts/shorts_state.json
```

- Generate 3 Shorts by default from completed long-form artifacts.
- Required outputs:

```text
jobs/<job_id>/shorts/
  shorts_plan.json
  shorts_state.json
  short-01/script.json
  short-01/scenes.json
  short-01/seo.json
  short-01/render_props.json
  short-01/video.mp4
  shorts_review.html
```

- Keep long-form `/jobs/{job_id}/run-all` unchanged.
- Do not add Shorts stages to `DEFAULT_STAGES`.
- Replace current draft `src/video_agent/orchestrator/shorts_stages.py` with production flow:
  - plan
  - script
  - scenes
  - QA
  - assets
  - TTS
  - render
  - review
- Add Shorts schemas:
  - `schemas/shorts-plan.schema.json`
  - `schemas/shorts-script.schema.json`
  - `schemas/shorts-scenes.schema.json`
  - `schemas/shorts-seo.schema.json`
- Add Remotion `ChannelShortStandard` via `remotion/src/ChannelShort.tsx`.
- Render Shorts with `ChannelShortStandard`, `1080x1920`, duration 22-35 seconds.
- Do not fake `qa.verdict = PASS`; use lightweight Gemini QA.
- Add tests:
  - `tests/test_shorts_stages.py`
  - `tests/test_shorts_api.py`
  - `tests/test_remotion_short_config.py`

Implementation priority when resumed:

1. Schemas and `shorts_state.json` helper.
2. New Shorts API routes.
3. Production `shorts_stages.py`.
4. `ChannelShortStandard` and render composition support.
5. Review page and dashboard basics.
6. Full test suite.
