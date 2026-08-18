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

All five channel templates are disabled by default. A normal disabled template
has no voice and no canary evidence, so it cannot accidentally enter the
dashboard. Exactly one locale may temporarily use `qualification: true`; that
template remains `enabled: false`, must declare a qualified voice, and appears
in the dashboard as `CANARY` while its canary status is still `PENDING`.

## Rollout order

Enable one locale at a time, in this exact order:

1. `en-US`
2. `fr-FR`
3. `pt-BR`
4. `ko-KR`
5. `ja-JP`

A locale cannot be enabled or placed under qualification until every earlier
locale is enabled and approved. More than one qualification locale is rejected.
Do not perform parallel first canaries.

## Required channel decisions

Before a canary, replace the pending values only for the locale being qualified:

- final channel ID and channel name;
- locale-specific intro, disclaimer, and outro clips;
- qualified TTS provider, language code, voice ID, and speed;
- final brand assets and visual direction.

Do not reuse a Vida Plena identity, Spanish copy, subscription ID, browser
session, media path, published-title registry, or runtime directory.

## Runtime capability manifest

The production dashboard loads approved channels from the V2 registries at
startup. It never hardcodes channel objects. The qualification dashboard uses
the same registries and additionally loads the single explicitly qualified
channel. If every channel is disabled and none is under qualification, the
dashboard starts with an empty channel list and does not require a capability
manifest.

Before starting qualification for the first channel, create the runtime-root
`capabilities.yaml` with only capabilities qualified on this machine. The
macOS V2 launcher copies the selected locale manifest into that location; for
the English qualification it uses `configs/localized-v2/capabilities-en-us.yaml`:

```yaml
schemaVersion: localized-capabilities-v2/v1
voices:
  - provider: kokoro
    language: a
    voiceId: <qualified-voice-id>
fonts:
  - Manrope
brandClips:
  - brand/en-US/intro.mp4
  - brand/en-US/disclaimer.mp4
  - brand/en-US/outro.mp4
```

Store the referenced clips under `runtime/localized-v2/media/`. Dashboard
startup checks the exact local TTS backend, verifies each font with `fc-match`,
and probes every clip with `ffprobe`. Missing, duplicate, malformed, symlinked,
out-of-root, or unavailable capabilities stop startup. A manifest declaration
does not override a failed runtime probe.

## Dedicated browser worker identity

The structured-content adapter accepts only the V2 endpoint configured in
`configs/localized-v2/runtime.yaml`. Before sending any prompt it calls the
endpoint's `/health` route and requires this identity:

```json
{
  "service": "localized-v2-browser-worker",
  "sessionNamespace": "localized-v2:<channel-id>",
  "profileRoot": "<absolute V2 browser-profile path>"
}
```

The values must match the worker configuration exactly. The ordinary legacy
browser-worker health response is deliberately incompatible, so pointing V2 at
the current production worker fails before content generation. Start the V2
browser worker with its own process, CDP instance, browser profile, and session
namespace; a different port alone is not sufficient isolation.

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
6. Set `qualification: false`; an approved production channel must not keep the
   temporary qualification slot.
7. Set `enabled: true`.
8. Validate the complete channel matrix; the rollout dependency gate must pass.
9. Restart only the Localized V2 dashboard and confirm exactly the approved
   channels are listed.

Schema validation, evidence validation, or rollout validation failure is a hard
stop. Never bypass it by inserting a channel directly into the dashboard
service.

## Current readiness

The isolated English pipeline is technically qualified end to end, but it is
not production-approved:

- `healthy-life-en` (`en-US`) is the only `qualification: true` channel and is
  visible in the V2 dashboard as `CANARY`;
- the real dashboard job
  `7-gentle-morning-habits-that-may-support-healthy-en-us-20260801-171539-eede6e8b`
  completed every stage and produced a 1920x1080 H.264/AAC MP4;
- final duration is `371.882667` seconds and final SHA-256 is
  `36863c1eb851f99e10a72c1bd33ef027fcd35ddf708552b3ebad5af7d9047e74`;
- the technical test gate is 207 localized-V2 tests plus Ruff, compileall,
  TypeScript, shell syntax, real dashboard browser checks, and repeat-launch
  worker-lifecycle verification;
- no subtitles, captions, word alignment, background music, legacy queue,
  legacy browser profile, or legacy job tree are used.

The English channel intentionally remains `enabled: false`, with canary status
and checks still `PENDING`, until a qualified human reviewer records the five
required evidence files and explicitly approves language, localization,
medical wording, SEO, thumbnail, visuals, and audience fit. The other four
locale templates remain disabled and are not runnable.
