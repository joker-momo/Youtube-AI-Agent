# Remotion MVP Video Agent Design

Date: 2026-05-18
Status: Ready for user review

## Goal

Build a minimal but production-shaped video agent MVP that proves this flow:

```text
manual idea -> script -> scenes -> assets -> Remotion video -> thumbnail -> title
```

The MVP must produce real local artifacts that can be manually uploaded to YouTube:

- `video.mp4`
- `thumbnail.jpg`
- `seo.json`
- `report.md`

The MVP intentionally excludes Hermes Agent, YouTube API upload, OAuth, Telegram approval, scheduled publishing, trend research, vidIQ, Google Trends, and YouTube scraping.

## Architecture Decision

Use **Approach A: Python orchestrator + Remotion app**.

Python owns pipeline orchestration, contracts, validation, QA loops, provider interfaces, logging, and job output. Remotion owns video rendering and reads only `render_props.json`.

This keeps the MVP small enough to run locally while preserving clean boundaries for a later Hermes migration. Each Python stage can later become or wrap a Hermes skill without changing the artifact contracts.

## Sample Channel

The default demo channel is `vida-plena-45`, matching the framework document.

- Name: `Vida Plena 45+`
- Language: `es-LA`
- Niche: health and wellness for adults 45+
- Audience: ages 45-75 in Spanish-speaking markets
- Tone: calm, trustworthy, practical, warm, and educational
- Compliance: avoid specific diagnosis, miracle cures, supplement promotion, and medical claims that require professional advice

The MVP uses a local sample config and Style DNA, not real OAuth, API keys, or upload credentials.

## Pipeline

The MVP pipeline is:

```text
manual_idea.json
  -> load channel config + style DNA
  -> generate script.json
  -> script QA/reflection loop
  -> generate scenes.json
  -> scene QA/reflection loop
  -> prepare local/mock assets
  -> generate thumbnail + seo.json
  -> thumbnail/title QA
  -> write render_props.json
  -> Remotion render video.mp4
  -> write report.md
```

Each stage writes structured artifacts into a per-job directory. The pipeline logs structured events to JSONL so a failed run can be inspected without reading terminal output.

## Folder Structure

```text
configs/vida-plena-45/
  channel.yaml
  style-dna.json
  brand-voice.md
  personas/
    maria.md
    carlos.md
    rosa.md
  qa-rules/
    script.yaml
    scene.yaml
    asset.yaml

inputs/
  manual_idea.json

schemas/
  channel-config.schema.json
  manual-idea.schema.json
  script.schema.json
  scenes.schema.json
  seo.schema.json
  render-props.schema.json

src/video_agent/
  cli.py
  pipeline.py
  contracts.py
  providers/
    base.py
    mock.py
  stages/
    script.py
    scene.py
    assets.py
    thumbnail.py
    render.py
  qa/
    script_qa.py
    scene_qa.py
    thumbnail_title_qa.py
  utils/
    paths.py
    json_io.py
    logging.py

remotion/
  package.json
  src/Root.tsx
  src/ChannelVideo.tsx
  src/Thumbnail.tsx
```

## Data Contracts

### `manual_idea.json`

Contains the operator-provided idea:

- topic
- angle
- target duration seconds
- key points
- optional title seed

### `script.json`

Contains the generated script:

- channel id
- job id
- hook
- sections
- narration blocks
- CTA
- QA status and iteration history

### `scenes.json`

Contains a simplified but production-shaped scene contract:

- scene id
- duration seconds
- narration
- visual type
- visual prompt
- on-screen text
- caption
- motion instruction
- asset references

The scene contract stays smaller than the full framework 13-field contract, but keeps the same direction: structured, validated, renderer-friendly scene data.

### `assets_manifest.json`

Contains paths to prepared local assets:

- placeholder narration audio or silence track
- background music or ambient audio placeholder
- generated visual placeholders
- thumbnail source material

The asset interface should look like production even when the files are locally generated.

### `render_props.json`

The only input Remotion needs:

- channel metadata
- style DNA
- output settings
- scenes
- captions
- asset paths
- audio paths

Remotion must not read pipeline internals or infer content from other files.

### `seo.json`

Contains metadata for manual upload:

- title
- description
- tags
- language
- AI disclosure flag
- thumbnail path

The title is required and must pass thumbnail/title QA.

### `report.md`

Contains a human-readable summary:

- job id
- channel
- source idea
- stage statuses
- QA retries
- output paths
- render settings
- any warnings

## QA And Reflection Loops

MVP QA is deterministic and local. It uses the same critic-shaped output style as the future LLM QA agents:

- `verdict`: `PASS`, `REVISE`, or `FAIL`
- scores
- issues
- retry action
- iteration number

Max retries per QA gate is 3. Retries are targeted. For example, a weak hook only rewrites the hook, not the entire script.

Initial deterministic checks:

- script hook is concise
- average sentence length stays readable
- health content avoids direct diagnosis and miracle-cure language
- scene durations sum to the requested duration range
- every scene has narration, caption, visual prompt, and motion instruction
- title exists and is upload-ready
- thumbnail text is short enough to be readable
- render props pass schema validation

## Providers

MVP providers are mock/local and deterministic by default.

Interfaces should allow later replacement with real providers:

- LLM provider for script, scenes, title, and SEO
- TTS provider for narration audio
- image or stock provider for visuals
- music provider for background audio

No API key is required for the MVP run.

## Remotion Rendering

Remotion is the primary renderer.

Requirements:

- Composition: `ChannelVideoStandard`
- Default resolution: 1920x1080
- Default FPS: 30
- Default duration: 45-60 seconds with 4-6 scenes
- Input: `render_props.json`
- Output: `video.mp4`
- Visuals: scene-specific backgrounds or placeholders
- Motion: subtle pan, zoom, slide, or fade per scene
- Text: on-screen scene text and captions
- Audio: local placeholder audio or silence track with production-shaped paths

`ffmpeg` may be used only as a supporting tool for audio or asset preparation. It must not be the main video renderer.

Thumbnail generation can be done by a Remotion still render or a local image step, as long as the output is `thumbnail.jpg` and it follows the same style DNA.

## CLI

Primary command:

```bash
python3 -m video_agent.cli run --channel configs/vida-plena-45/channel.yaml --idea inputs/manual_idea.json
```

The command should create a timestamped or slugged job directory under `jobs/` and write all artifacts there.

## Verification

Implementation is complete when the MVP can:

1. Run the CLI command with no external API credentials.
2. Validate all JSON artifacts against schemas.
3. Produce `script.json`, `scenes.json`, `assets_manifest.json`, `render_props.json`, `seo.json`, and `report.md`.
4. Invoke Remotion via Node subprocess.
5. Produce non-empty `video.mp4` and `thumbnail.jpg`.
6. Confirm video resolution and approximate duration when `ffprobe` is available.
7. Keep upload automation out of scope.

## Non-Goals

The MVP does not include:

- Hermes Agent runtime
- YouTube API upload
- OAuth
- Telegram approval
- schedule publish
- trend research
- vidIQ integration
- Google Trends integration
- YouTube scraping
- real LLM/TTS/image/stock APIs
- persona evaluation
- dashboard UI

## Risks And Mitigations

- Remotion dependency setup can be slow or fail without installed Node dependencies. Mitigation: keep the Remotion app minimal and document the exact install/render command.
- Health niche can accidentally imply medical advice. Mitigation: deterministic QA blocks diagnosis, miracle cures, supplement promotion, and direct treatment instructions.
- Placeholder assets can make the video feel too static. Mitigation: use scene-specific backgrounds, text overlays, captions, and subtle motion in Remotion.
- Future provider replacement can become messy if mock providers leak into stage code. Mitigation: keep provider interfaces separate from stages.
