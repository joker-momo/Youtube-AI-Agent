# Youtube AI Agent Project Status

Last updated: 2026-05-20

This file is the living project tracker. Update it whenever a meaningful system capability is added, changed, verified, or deferred so a new reader can quickly understand what the system does, what is being built now, and what remains.

## Goal

Build a Docker-first semi-automated YouTube production system that can take one video idea through:

```text
idea -> ChatGPT script/scenes/SEO -> Gemini QA -> assets/TTS -> Remotion render -> human review -> final video
```

The current focus is one high-quality end-to-end video flow before expanding to queues, multiple channels, or upload automation.

Current product priority:

- Only prioritize tasks that directly complete the full end-to-end video creation flow.
- The target flow is: trend/data intake -> idea selection -> script -> scenes -> SEO -> assets/images -> TTS -> render -> QA/review -> final video.
- Defer optimization work until the complete final-video flow is reliable.
- Cache, semantic reuse, analytics, dashboards, multi-channel scaling, and other compounding improvements are valuable, but they are not priority unless they unblock the full final-video flow.

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
64 passed in 14.48s
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

- `818c08b Update project status after fresh operator run`
- `e9b5ab8 Add operator next-step guide`
- `1f0161f Add operator job status command`
- `a7f188f Refresh operator review after render`
- `0d17b53 Add operator job review page`
- `6c75dfc Add operator QA gate`
- `545effa feat: add operator content workflow`

## Not Yet Done

- Browser automation is not fully packaged into the one-video flow; ChatGPT/Gemini browser steps are still semi-manual.
- ChatGPT image generation skill exists as a separate browser skill, but is not yet integrated as a first-class pipeline asset source.
- Gemini QA is artifact-level only; video-level QA is still human review through `operator_review.html`.
- No YouTube upload, scheduling, or channel publishing automation yet.
- No multi-job queue/index yet; intentionally deferred until one-video flow is solid.
- No trend research loop yet.
- No semantic asset reuse layer yet.

## Next Recommended Work

1. Add a video-level QA checklist in `operator_review.html`.
2. Package the browser handoff rules for ChatGPT/Gemini into the operator guide.
3. Integrate ChatGPT image folder selection into the operator flow when the one-video content flow is stable.
4. Add the missing front-of-pipeline step for trend/data intake and idea selection.
5. Add Spanish accent checks as a follow-up validator pass if missing accents continue to appear in real runs.
