# Localized V2 canary runbook

## Scope and isolation

Localized V2 is a separate long-form pipeline and a separate operator dashboard.
Do not submit or inspect a V2 canary through the legacy dashboard, legacy CLI,
legacy queue, legacy browser profile, or `jobs/` tree. Shorts are not supported.

The V2 contract is voice-only:

- subtitles are disabled;
- captions and word-level alignment are not generated;
- background music is absent;
- intro, disclaimer, and outro are independent video clips;
- `render.concurrency` remains `auto`.

All five channel templates are disabled by default. A disabled template has no
voice and no canary evidence, so it cannot accidentally enter the dashboard.

## Rollout order

Enable one locale at a time, in this exact order:

1. `en-US`
2. `fr-FR`
3. `pt-BR`
4. `ko-KR`
5. `ja-JP`

A locale cannot be enabled until every earlier locale is enabled and approved.
Do not perform parallel first canaries.

## Required channel decisions

Before a canary, replace the pending values only for the locale being qualified:

- final channel ID and channel name;
- locale-specific intro, disclaimer, and outro clips;
- qualified TTS provider, language code, voice ID, and speed;
- final brand assets and visual direction.

Do not reuse a Vida Plena identity, Spanish copy, subscription ID, browser
session, media path, published-title registry, or runtime directory.

## Canary evidence gate

Create five evidence files under the dedicated Localized V2 evidence root.
Evidence paths in `channel.yaml` must be relative, must remain inside that root,
must exist, and must not be symlinks.

### 1. Audio

- Generate the complete narration with the selected locale voice.
- Listen to the opening, a middle segment, medical terminology, numbers, and the
  ending.
- Confirm natural pronunciation, pacing, respectful audience address, and no
  Spanish leakage.
- Record provider, voice ID, speed, sample artifact hashes, and reviewer result.

### 2. Font and glyphs

- Render native accented or script-specific probes from the locale pack.
- Confirm every required codepoint renders without tofu, fallback mismatch, or
  clipped text.
- Verify Manrope for Latin locales, Noto Sans KR for Korean, and Noto Sans JP
  for Japanese.
- Record rendered probe paths, font family, and reviewer result.

### 3. Render

- Run the canary through the Localized V2 dashboard only.
- Confirm intro, disclaimer, content, and outro order.
- Confirm graphic scenes retain their moving video background.
- Confirm the MP4 contains a video stream and narration audio, with no subtitle
  stream and no music.
- Record render props hash, final MP4 hash, ffprobe output, and representative
  frame paths.

### 4. Human review

- A native or professionally qualified reviewer must approve language,
  localization, medical soft-claim wording, SEO, thumbnail copy, visuals, and
  audience fit.
- Verify claims use informational wording such as the locale pack examples and
  never diagnose, prescribe, promise a cure, or guarantee results.
- Record reviewer identity, date, checklist result, and explicit approval.

### 5. Dashboard lifecycle

- Create the canary from the V2 dashboard.
- Observe queue, running stages, terminal status, events, artifacts, retry,
  resume, and cancellation behavior in the V2 dashboard.
- Refresh the page and verify the job state is preserved.
- Confirm the same job never appears in the legacy dashboard or legacy queue.
- Record API responses/screenshots and the terminal job artifact manifest.

## Enabling a channel

Only after all five checks pass:

1. Store the five evidence files under the V2 evidence root.
2. Set every canary check to `PASS`.
3. Set canary status to `APPROVED`.
4. Add the five safe relative evidence paths.
5. Set the qualified voice object.
6. Set `enabled: true`.
7. Validate the complete channel matrix; the rollout dependency gate must pass.
8. Restart only the Localized V2 dashboard and confirm exactly the approved
   channels are listed.

Schema validation, evidence validation, or rollout validation failure is a hard
stop. Never bypass it by inserting a channel directly into the dashboard
service.

## Current readiness

The locale contracts and deterministic five-locale pipeline matrix are ready.
No real channel is approved yet: final channel identities, brand clips, voices,
native human reviews, and real dashboard canary artifacts have not been
provided. Therefore all five templates intentionally remain disabled and the
V2 dashboard exposes zero production channels.
