# Coding Spec: Add Word-Synced Subtitles to Remotion Render

## Repository

- Repo: `joker-momo/Youtube-AI-Agent`
- Target branch: `main`
- Feature name: `word_synced_subtitles`
- Primary goal: render large, readable, word-synced subtitles in the Remotion video output using the existing Whisper `word_segments` pipeline.

---

## 1. Background / Current State

The project already has most of the subtitle timing pipeline implemented:

1. `whisper_timestamps` stage runs before `render` in the default stage order.
2. `run_whisper_timestamps_stage()` reads `jobs/<job_id>/assets/narration.wav`, runs Whisper with word timestamps, maps words back into scene-local timestamps, and writes `jobs/<job_id>/whisper_timestamps.json`.
3. `render_operator_job()` merges `whisper_timestamps.json` into each scene as:
   - `audio_offset_sec`
   - `word_segments`
4. `remotion/src/render-props.ts` already defines:
   ```ts
   export type WordSegment = {text: string; start: number; end: number};
   ```
   and each `Scene` already supports:
   ```ts
   audio_offset_sec?: number;
   word_segments?: WordSegment[];
   ```
5. `remotion/src/ChannelVideo.tsx` already calculates `localTimeSec`, splits words into pages, finds the active word, and finds the active page, but it does not currently render a clear subtitle/caption UI block on screen.

Therefore, this task should focus mainly on the Remotion UI layer, plus a small config/schema update and tests.

---

## 2. Desired User-Facing Behavior

When a rendered video has `scene.word_segments`, the video should display large subtitles synchronized with the narration.

For example, if the narration says:

```text
No necesitas otra dieta estricta.
```

and Whisper returns:

```json
[
  {"text": "No", "start": 0.10, "end": 0.25},
  {"text": "necesitas", "start": 0.26, "end": 0.62},
  {"text": "otra", "start": 0.63, "end": 0.82},
  {"text": "dieta", "start": 0.83, "end": 1.12},
  {"text": "estricta", "start": 1.13, "end": 1.55}
]
```

then Remotion should show a subtitle block at the bottom of the video and highlight the active word as the audio plays.

The style must be suitable for the channel `Vida Plena 45+`:

- large text
- high contrast
- readable on mobile
- calm professional wellness style
- no tiny text
- no excessive flashy effects

---

## 3. Files to Modify

### Required

1. `remotion/src/ChannelVideo.tsx`
   - Add a reusable subtitle component.
   - Render subtitles inside `SceneView` when `scene.word_segments` exists.

2. `remotion/src/render-props.ts`
   - Extend `RenderProps.render` type to include optional subtitle config.
   - Add safe defaults in `defaultRenderProps`.

3. `schemas/render-props.schema.json`
   - Add optional `render.subtitles` schema.

4. `configs/vida-plena-45/channel.yaml`
   - Add default subtitle config under `render.subtitles`.

5. Tests under `tests/`
   - Add or update tests to verify `render_props` schema accepts `render.subtitles`.
   - Add a lightweight test for Remotion props compatibility if existing test utilities allow it.

### Optional but Recommended

6. `docs/PROJECT_STATUS.md`
   - Add a short update after implementation.

---

## 4. Subtitle Config Schema

Add this optional config shape to `render`:

```yaml
render:
  subtitles:
    enabled: true
    mode: word_highlight
    words_per_page: 10
    max_lines: 2
    position: bottom
    offset_sec: 0.0
    font_size: 54
    active_scale: 1.08
    background_opacity: 0.58
```

### Field Semantics

| Field | Type | Default | Meaning |
|---|---:|---:|---|
| `enabled` | boolean | `true` | Whether subtitles are rendered when `word_segments` exist. |
| `mode` | string | `word_highlight` | Subtitle behavior. For now only implement `word_highlight`. |
| `words_per_page` | number | `10` | Number of words shown per subtitle page. |
| `max_lines` | number | `2` | Visual constraint. Do not build complex line breaking yet; use flex wrap and max width. |
| `position` | string | `bottom` | For now implement only `bottom`. |
| `offset_sec` | number | `0.0` | Fine-tuning offset. Positive value makes subtitles advance later in local time calculation; negative value makes them earlier depending on formula chosen. Use clear docs in code. |
| `font_size` | number | `54` | Subtitle font size in pixels at 1920x1080. |
| `active_scale` | number | `1.08` | Scale of active word. |
| `background_opacity` | number | `0.58` | Opacity of the dark subtitle container. |

---

## 5. Implementation Details

## 5.1 Extend Remotion Types

In `remotion/src/render-props.ts`, replace:

```ts
render: {fps: number; resolution: string; duration_sec: number};
```

with something like:

```ts
export type SubtitleConfig = {
  enabled?: boolean;
  mode?: 'word_highlight';
  words_per_page?: number;
  max_lines?: number;
  position?: 'bottom';
  offset_sec?: number;
  font_size?: number;
  active_scale?: number;
  background_opacity?: number;
};

export type RenderProps = {
  // existing fields...
  render: {
    fps: number;
    resolution: string;
    duration_sec: number;
    subtitles?: SubtitleConfig;
  };
  // existing fields...
};
```

Update `defaultRenderProps`:

```ts
render: {
  fps: 30,
  resolution: '1920x1080',
  duration_sec: 54,
  subtitles: {
    enabled: true,
    mode: 'word_highlight',
    words_per_page: 10,
    max_lines: 2,
    position: 'bottom',
    offset_sec: 0,
    font_size: 54,
    active_scale: 1.08,
    background_opacity: 0.58,
  },
},
```

---

## 5.2 Add Subtitle Config Resolver

In `ChannelVideo.tsx`, add a helper near the top:

```tsx
const subtitleDefaults = {
  enabled: true,
  mode: 'word_highlight' as const,
  words_per_page: 10,
  max_lines: 2,
  position: 'bottom' as const,
  offset_sec: 0,
  font_size: 54,
  active_scale: 1.08,
  background_opacity: 0.58,
};

const resolveSubtitles = (props: RenderProps) => ({
  ...subtitleDefaults,
  ...(props.render?.subtitles ?? {}),
});
```

Pass resolved subtitle config from `ChannelVideo` into each `SceneView`.

Update `SceneView` props:

```tsx
type SubtitleRuntimeConfig = ReturnType<typeof resolveSubtitles>;

const SceneView: React.FC<{
  scene: Scene;
  totalFrames: number;
  sceneIndex: number;
  totalScenes: number;
  palette: RenderProps['style']['palette'];
  channelName: string;
  logoPath?: string | null;
  isFirst?: boolean;
  isLast?: boolean;
  subtitles: SubtitleRuntimeConfig;
}> = (...) => { ... }
```

---

## 5.3 Add CaptionBlock Component

Add this component in `ChannelVideo.tsx` above `SceneView`:

```tsx
const CaptionBlock: React.FC<{
  words: WordSegment[];
  activeWordGlobalIdx: number;
  pageStartIdx: number;
  accent: string;
  fontSize: number;
  activeScale: number;
  backgroundOpacity: number;
}> = ({
  words,
  activeWordGlobalIdx,
  pageStartIdx,
  accent,
  fontSize,
  activeScale,
  backgroundOpacity,
}) => {
  if (!words.length) return null;

  return (
    <div
      style={{
        position: 'absolute',
        left: 120,
        right: 120,
        bottom: 82,
        zIndex: 60,
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 10,
        padding: '18px 28px',
        borderRadius: 18,
        background: `rgba(0,0,0,${backgroundOpacity})`,
        boxShadow: '0 8px 28px rgba(0,0,0,0.45)',
        maxHeight: 176,
        overflow: 'hidden',
      }}
    >
      {words.map((w, i) => {
        const globalIdx = pageStartIdx + i;
        const active = globalIdx === activeWordGlobalIdx;

        return (
          <span
            key={`${w.text}-${i}-${w.start}`}
            style={{
              fontSize,
              lineHeight: 1.16,
              fontWeight: 900,
              color: active ? accent : '#FFFFFF',
              textShadow: '0 3px 8px rgba(0,0,0,0.95)',
              transform: active ? `scale(${activeScale})` : 'scale(1)',
              transition: 'transform 80ms linear, color 80ms linear',
              whiteSpace: 'pre-wrap',
            }}
          >
            {w.text}
          </span>
        );
      })}
    </div>
  );
};
```

Notes:

- Keep this simple. Do not implement complex line-breaking in this task.
- The subtitle block should remain readable even over bright videos.
- Use `palette.accent` for the active word highlight.

---

## 5.4 Render Subtitle in `SceneView`

Inside `SceneView`, use the existing logic but make it configurable.

Current logic already has:

```tsx
const localTimeSec = frame / fps;
const segments = (scene.word_segments ?? [])...
const words = segments;
const hasWords = words.length > 0;
const wordsPerPage = 10;
const pages: WordSegment[][] = [];
// activeWordIdx, targetWordIdx, activePageIdx, displayLine
```

Update it to:

```tsx
const localTimeSec = frame / fps + subtitles.offset_sec;
const wordsPerPage = Math.max(4, Math.min(14, subtitles.words_per_page ?? 10));
```

Then after overlays but before final fade-out overlay, render:

```tsx
{subtitles.enabled && hasWords ? (
  <CaptionBlock
    words={displayLine}
    activeWordGlobalIdx={targetWordIdx}
    pageStartIdx={activePageIdx * wordsPerPage}
    accent={palette.accent}
    fontSize={subtitles.font_size}
    activeScale={subtitles.active_scale}
    backgroundOpacity={subtitles.background_opacity}
  />
) : subtitles.enabled && scene.on_screen_text ? (
  <div
    style={{
      position: 'absolute',
      left: 120,
      right: 120,
      bottom: 92,
      zIndex: 60,
      textAlign: 'center',
      fontSize: subtitles.font_size,
      lineHeight: 1.12,
      fontWeight: 900,
      color: '#FFFFFF',
      textShadow: '0 4px 14px rgba(0,0,0,0.95)',
    }}
  >
    {scene.on_screen_text}
  </div>
) : null}
```

Important placement rule:

- Place the subtitle before the final scene-to-scene fade-out overlay.
- Keep the fade-out overlay as the last child so it fades subtitles and visuals together.

---

## 5.5 Pass Subtitle Config from `ChannelVideo`

Inside `ChannelVideo`:

```tsx
export const ChannelVideo: React.FC<RenderProps> = (props) => {
  const {fps} = useVideoConfig();
  const subtitles = resolveSubtitles(props);
  // existing logic...
```

Then pass into `SceneView`:

```tsx
<SceneView
  scene={scene}
  totalFrames={totalFrames}
  sceneIndex={i}
  totalScenes={props.scenes.length}
  palette={props.style.palette}
  channelName={props.channel.name}
  logoPath={logoPath}
  isFirst={i === 0}
  isLast={i === props.scenes.length - 1}
  subtitles={subtitles}
/>
```

---

## 5.6 Add Channel Config Defaults

In `configs/vida-plena-45/channel.yaml`, add under `render`:

```yaml
subtitles:
  enabled: true
  mode: word_highlight
  words_per_page: 10
  max_lines: 2
  position: bottom
  offset_sec: 0.0
  font_size: 54
  active_scale: 1.08
  background_opacity: 0.58
```

Use existing YAML indentation/style.

---

## 5.7 Update JSON Schema

In `schemas/render-props.schema.json`, allow `render.subtitles` as an optional object.

Suggested schema fragment:

```json
"subtitles": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "enabled": {"type": "boolean"},
    "mode": {"type": "string", "enum": ["word_highlight"]},
    "words_per_page": {"type": "integer", "minimum": 4, "maximum": 14},
    "max_lines": {"type": "integer", "minimum": 1, "maximum": 3},
    "position": {"type": "string", "enum": ["bottom"]},
    "offset_sec": {"type": "number", "minimum": -1.0, "maximum": 1.0},
    "font_size": {"type": "integer", "minimum": 32, "maximum": 84},
    "active_scale": {"type": "number", "minimum": 1.0, "maximum": 1.25},
    "background_opacity": {"type": "number", "minimum": 0.0, "maximum": 0.9}
  }
}
```

Make sure `render` still validates existing props without subtitles for backward compatibility.

---

## 6. Acceptance Criteria

The implementation is complete when all of these are true:

1. If `scene.word_segments` exists and `render.subtitles.enabled = true`, subtitles appear in the rendered video.
2. Active words are highlighted according to the current frame/time.
3. Subtitles are readable at 1920x1080 and mobile-safe.
4. If `word_segments` are missing but `scene.on_screen_text` exists, the scene can still show the fallback text.
5. If `render.subtitles.enabled = false`, no subtitles should be rendered.
6. Existing render jobs without `render.subtitles` should still render successfully.
7. `render_props.schema.json` accepts the new subtitle config.
8. `docker compose run --rm video-agent pytest -q` passes.
9. A short manual render with `whisper_timestamps.json` produces visible word-synced captions.

---

## 7. Suggested Tests

Add tests only where the repo already has similar patterns. Keep tests pragmatic.

### Test 1: Render props schema accepts subtitles

Create or update a fixture with:

```json
"render": {
  "fps": 30,
  "resolution": "1920x1080",
  "duration_sec": 12,
  "subtitles": {
    "enabled": true,
    "mode": "word_highlight",
    "words_per_page": 10,
    "max_lines": 2,
    "position": "bottom",
    "offset_sec": 0,
    "font_size": 54,
    "active_scale": 1.08,
    "background_opacity": 0.58
  }
}
```

Assert schema validation passes.

### Test 2: Backward compatibility

Render props without `render.subtitles` should still validate and render command building should still work.

### Test 3: Manual smoke render

If existing tests already invoke Remotion, add a minimal test video with:

```json
"word_segments": [
  {"text": "No", "start": 0.0, "end": 0.2},
  {"text": "necesitas", "start": 0.2, "end": 0.7},
  {"text": "otra", "start": 0.7, "end": 1.0},
  {"text": "dieta", "start": 1.0, "end": 1.4}
]
```

If rendering video in tests is too heavy, do not add this as an automated test. Instead document a manual verification command.

---

## 8. Manual Verification Steps

After implementation:

```bash
docker compose run --rm video-agent pytest -q
```

Then run a real job through:

```text
script -> script_promote -> script_qa -> scenes -> scenes_promote -> scenes_qa -> seo -> seo_promote -> seo_qa -> seo_vidiq -> thumbnail_image -> whisper_timestamps -> render -> review
```

Check:

1. `jobs/<job_id>/whisper_timestamps.json` exists.
2. `jobs/<job_id>/render_props.json` contains `word_segments` inside scenes.
3. `jobs/<job_id>/video.mp4` shows subtitles at the bottom.
4. Highlighted word roughly matches the narration.
5. If subtitles are slightly early/late, adjust:

```yaml
render:
  subtitles:
    offset_sec: 0.05
```

or:

```yaml
render:
  subtitles:
    offset_sec: -0.05
```

---

## 9. Important Guardrails

- Do not change the existing Whisper stage unless absolutely necessary.
- Do not rewrite the render pipeline.
- Do not replace Remotion.
- Do not add a new subtitle dependency unless needed.
- Keep subtitle rendering data-driven from `word_segments`.
- Preserve backward compatibility with old jobs that do not have `word_segments` or `render.subtitles`.
- Keep the fade-out overlay as the last visual layer.
- Keep all text readable and high contrast.

---

## 10. Expected Final Code Shape

At the end, the render path should look like:

```text
narration.wav
→ whisper_timestamps.json
→ scene.word_segments
→ render_props.json
→ Remotion ChannelVideo
→ CaptionBlock highlights current word
→ video.mp4
```

The feature should be implemented primarily in:

```text
remotion/src/ChannelVideo.tsx
remotion/src/render-props.ts
schemas/render-props.schema.json
configs/vida-plena-45/channel.yaml
```

---

## 11. Optional Future Improvements — Do Not Implement Now

These are deliberately out of scope for this task:

- karaoke syllable-level highlighting
- automatic line breaking by pixel measurement
- multiple subtitle themes
- subtitle export to `.srt`
- burnt-in subtitles for Shorts-specific vertical render
- subtitle language translation
- per-scene layout detection such as `layout: checklist`, `layout: warning`, etc.

Focus only on word-synced subtitles for the current 16:9 video render.
