# Infographic Shorts — End Engagement Cue and Topic-Music Excerpts

Status: implementation specification  
Date: 2026-07-11  
Scope: static `InfographicShort` pipeline only

## Goal

Improve two weak points observed in a rendered infographic Short:

1. use the final three seconds for a clear Like + Subscribe interaction instead
   of holding the unchanged poster after narration ends;
2. retain topic-based music selection while using a reproducible pseudo-random
   excerpt from the selected track rather than starting every Short at 00:00.

The channel's existing music mapping remains authoritative. For example, a
`food` Short selects `shorts_daily_habit`, whose configured track is **Fresh
Fallen Snow — Chris Haugen**.

## Feature A — three-second end engagement cue

### Render-props contract

Add optional fields without removing the legacy `showSubscribeCue` field:

```json
{
  "showEngagementCue": true,
  "engagementCueDurationSec": 3.0
}
```

`build_infographic_render_props()` enables this cue by default for infographic
Shorts. `showSubscribeCue` remains accepted for stored render props and acts as
a legacy alias for the new end cue; it must no longer display a banner for the
whole video.

### Timeline

At any FPS:

```text
cue_frames = round(engagementCueDurationSec * fps)
cue_start  = max(0, durationInFrames - cue_frames)
```

The cue must not exist before `cue_start`. At 30 FPS and three seconds it owns
exactly the final 90 frames.

Within those three seconds:

- 0.0–0.6 s: dim the poster by no more than 18% and slide/fade the cue in;
- 0.6–1.3 s: cursor/finger presses the Like control (a short local "pop"
  sound effect fires on the exact press frame); the thumb bounces and
  changes to its active colour with visible text `ME GUSTA`;
- 1.3–2.1 s: press the red `SUSCRÍBETE` button (a short local bell "ding"
  sound effect fires on the exact press frame);
- 2.1–3.0 s: hold `✓ SUSCRITO`, lightly wiggle the bell and show the configured
  `channelName`.

Animations use Remotion frame math (`Sequence`, `spring`, `interpolate`) and
inline SVG/CSS shapes. No remote image, copyrighted animation, browser event or
runtime randomness is allowed.

### Safe-area and visual rules

- Keep the CTA panel left of the YouTube action rail and above the bottom title
  / description area. For 1080×1920, keep the panel within approximately
  `x=120..840` and `y=1050..1500`.
- Text must be Spanish and readable for adults 45+: bold geometric sans,
  high contrast, no tiny helper copy.
- The overlay may temporarily cover poster detail only during the final three
  seconds; it must not cover the headline during the rest of the Short.
- The cue must remain correct for voice-driven variable durations.
- Press sound effects (operator override 2026-07-11, superseding this spec's
  original no-SFX rule): the Like press fires `sfx/like_pop.wav` and the
  Subscribe press fires `sfx/bell_ding.wav`, both under `remotion/public/sfx/`.
  They are LOCAL, self-generated (ffmpeg sine synthesis — no third-party or
  copyrighted audio; see `remotion/public/sfx/PROVENANCE.md` for the exact
  generation commands and SHA-256 checksums), fire on the SAME shared frame
  constants as the visual presses (`sfxFrames(fps) == pressFrames(fps)`,
  asserted by executable tests), are bounded press blips (0.05–1.5 s, asserted
  via ffprobe in tests), and the final mixed audio must remain non-clipping
  (max_volume < 0 dBFS on the rendered Short). The underlying music/narration
  mix pipeline itself is unchanged.

### Component boundary

Create a focused `EndEngagementCue.tsx` component. `InfographicShort.tsx` owns
only timing and composition. Export a pure cue-start helper so timing can be
tested without rendering a browser frame.

## Feature B — deterministic random excerpt from topic-selected music

### Selection remains topic-first

`music_selector.select_music_track()` continues to choose the track key from
the Short's pillar/topic. The excerpt logic runs **after** track selection.

For `vida-plena-45`:

- `food`, `daily_habits`, `movement_light` → `shorts_daily_habit` → Fresh Fallen
  Snow;
- other topic mappings and fallback behavior remain unchanged.

The infographic channel config uses `music_source: library`.

### Reproducible pseudo-random offset

For a readable library track, calculate the excerpt start from a stable SHA-256
hash of at least:

```text
short_dir.name + selected_track_key
```

Default bounds:

```text
minimum_offset_sec = 5.0
maximum_offset_sec = track_duration_sec - required_bed_sec - end_margin_sec
end_margin_sec      = 1.0
```

Map the hash deterministically into the inclusive range
`[minimum_offset_sec, maximum_offset_sec]` and round only when serializing the
ffmpeg argument/metadata.

Consequences:

- re-rendering the same Short with the same track selects exactly the same
  excerpt;
- different Short IDs normally select different excerpts;
- a normal long track never starts at 00:00;
- changing the selected track changes the seed and therefore the excerpt;
- no use of Python's process-randomized `hash()` or unseeded `random`.

When the track cannot fit the requested bed plus margins, offset `0.0` is
allowed and the existing loop behavior remains. When ffprobe cannot read the
duration of a normal library track, fail with a clear `RuntimeError`; silently
using offset zero would violate the requested behavior and hide a broken asset.

### FFmpeg and fades

- Add input seek `-ss <offset>` before the library input.
- Preserve `-stream_loop -1`, requested output duration, volume, fade-in,
  fade-out, AAC bitrate and sample-rate behavior.
- Procedural-original music is not changed and does not use excerpt seeking.

### Audit artifact

Write `json/music_selection.json` for library infographic beds with at least:

```json
{
  "source": "library",
  "track_key": "shorts_daily_habit",
  "track_title": "Fresh Fallen Snow",
  "track_file": "assets/music/fresh_fallen_snow_chris_haugen.mp3",
  "track_duration_sec": 214.0,
  "excerpt_offset_sec": 42.37,
  "excerpt_duration_sec": 15.0,
  "seed_key": "<short name>|shorts_daily_habit",
  "selection_mode": "deterministic_random_excerpt"
}
```

The artifact is written atomically and allows QA to reproduce the chosen audio.

## Acceptance criteria

- AC1: render props enable a three-second engagement cue by default while
  retaining the legacy `showSubscribeCue` input.
- AC2: the cue is mounted only for the last `round(3 * fps)` frames.
- AC3: the visible state sequence includes `ME GUSTA`, `SUSCRÍBETE`,
  `✓ SUSCRITO`, a bell state and the configured channel name.
- AC4: the CTA implementation uses Remotion frame-based deterministic animation,
  respects safe-area constraints and has no EXTERNAL (remote/third-party/
  copyrighted) visual or audio dependency; the two local, self-generated press
  SFX under `remotion/public/sfx/` are explicitly allowed and must fire on the
  shared press-frame constants with bounded durations and non-clipping output.
- AC5: `food` resolves to Fresh Fallen Snow through the existing music library.
- AC6: excerpt offset is stable for the same Short/track, differs for tested
  different Short IDs, includes the track key in its seed and stays within the
  configured bounds.
- AC7: normal long library tracks use an ffmpeg `-ss` offset of at least five
  seconds and do not always begin at zero.
- AC8: unreadable library duration fails clearly; genuinely short tracks retain
  offset-zero loop fallback.
- AC9: `json/music_selection.json` records the complete reproducibility data.
- AC10: procedural-original generation, topic mapping, fades, audio mixing,
  variable voice duration, render concurrency `auto`, thumbnails and non-
  infographic Shorts remain unchanged.
- AC11: focused tests, all `tests/shorts_build/infographic`, relevant music tests,
  TypeScript `tsc --noEmit`, Ruff and Python compile checks pass.

## Non-goals

- No change to the poster-generation prompt or poster density.
- No change to narration wording or timing.
- No change to normal multi-scene `ShortVideo` CTA behavior.
- No hardcoded render concurrency.
- No nondeterministic choice that makes the same Short render differently.
- (Amended 2026-07-11) Local, self-generated press SFX are IN scope per the
  operator override — the excluded thing is external/copyrighted audio.

