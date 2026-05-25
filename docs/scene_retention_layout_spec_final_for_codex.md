# Scene Retention Layout Planner — Final Codex Spec

## Purpose

Add a **content-aware scene layout system** to the YouTube AI Agent pipeline so each scene can render an appropriate overlay layout for retention, clarity, and CTA.

This spec replaces the earlier rough layout plan. The key correction is:

> ChatGPT proposes the scene layout and its display payload.  
> Python validates and safely finalizes the layout.  
> Python must **not invent unsupported overlay content**.  
> Remotion only renders the finalized scene data.

The goal is to avoid mismatches like a scene narration about walking while the overlay checklist shows food-related bullets.

---

## Current Repository Context

The current scene generation flow is:

```text
script.json
→ ChatGPT scenes stage
→ scenes.json
→ scenes QA
→ SEO
→ thumbnail_image
→ whisper_timestamps
→ render
```

The existing `scenes.json` scene objects currently include fields like:

```json
{
  "id": "scene-01",
  "duration_sec": 8,
  "narration": "...",
  "on_screen_text": "...",
  "caption": "...",
  "visual_prompt": "...",
  "motion": "slow_zoom",
  "asset_refs": {}
}
```

The project already supports video/photo backgrounds in Remotion. This spec adds **overlay layout metadata** and **content-aware validation**.

---

## Required Layout Types

Support exactly these scene layouts:

```text
hook
subtitle
checklist
warning
quote
cta
```

### Layout meaning

| Layout | Purpose | Typical use |
|---|---|---|
| `hook` | Strong opening or transition hook | First scene, section openers |
| `subtitle` | Normal readable subtitle/caption | Default layout |
| `checklist` | Practical steps or grouped takeaways | Tips, ingredients, habits, routines |
| `warning` | Common mistake, risk, what to avoid | “Error común”, “Evita…” |
| `quote` | Emotional or memorable key sentence | Reframing beliefs, motivation |
| `cta` | Final action prompt | Last scene only |

---

## Responsibility Model

Use this strict responsibility split:

```text
ChatGPT = content author
Python planner = content-aware validator + rhythm corrector
Remotion = renderer
Claude QA = reviewer
```

### ChatGPT responsibilities

ChatGPT should:

1. Write the scene narration.
2. Propose a layout for each scene.
3. Provide the layout payload required by that layout.
4. Explain briefly why the layout fits the narration via `layout_reason`.

### Python planner responsibilities

Python should:

1. Preserve valid ChatGPT-proposed layouts.
2. Validate whether the layout has enough supported payload.
3. Downgrade invalid layouts to `subtitle`.
4. Force first scene to `hook` only if safe display text exists.
5. Force final scene to `cta` only if safe CTA text exists.
6. Insert or adjust pattern breaks only when an eligible scene already has enough content.
7. Add warnings when a layout is downgraded or when a pattern break cannot be safely inserted.

### Python planner must not

Python must **never**:

1. Invent new factual overlay content not supported by the scene narration/caption/on-screen text/script CTA.
2. Create checklist bullets from scratch.
3. Turn a scene into `warning` if the narration does not express a mistake, risk, or avoidance.
4. Create emotional quotes not present in the scene content.
5. Add CTA in the middle of the video.
6. Rewrite the meaning of a scene.

---

## New Scene Fields

Add these optional fields to every scene object:

```json
{
  "layout": "subtitle",
  "layout_payload": {
    "title": "",
    "body": "",
    "bullets": [],
    "cta": ""
  },
  "layout_reason": "",
  "planner_warnings": []
}
```

### Field definitions

#### `layout`

One of:

```text
hook | subtitle | checklist | warning | quote | cta
```

#### `layout_payload`

Object containing display content for Remotion.

```json
{
  "title": "TU PLATO BASE",
  "body": "Empieza con una base simple.",
  "bullets": ["Proteína", "Verduras", "Agua"],
  "cta": "Suscríbete para más consejos después de los 45"
}
```

All fields are strings except `bullets`, which is an array of strings.

#### `layout_reason`

Short English explanation from ChatGPT explaining why this layout fits the scene.

Example:

```text
The narration gives a practical 3-part plate formula, so checklist is appropriate.
```

#### `planner_warnings`

Array of strings added by Python when it downgrades or adjusts layout.

Example:

```json
[
  "Checklist downgraded to subtitle: missing 2-4 valid bullets."
]
```

---

## Safe Data Source Rule

Every overlay text must be supported by at least one of:

```text
scene.narration
scene.caption
scene.on_screen_text
scene.layout_payload
script.cta
```

The planner may copy or shorten existing text, but must not create new claims.

Allowed safe transformations:

```text
"Empieza con un plato simple: proteína, verduras y agua."
→ title: "TU PLATO BASE"
→ bullets: ["Proteína", "Verduras", "Agua"]
```

Only allowed when the source content already contains those concepts.

Not allowed:

```text
Narration: "Caminar unos minutos después de comer puede ayudarte..."
Generated bullets: ["Proteína", "Verduras", "Agua"]
```

---

## Layout Validity Rules

### 1. `hook`

Valid when:

```text
layout_payload.title OR on_screen_text
```

exists and is 2–8 words.

If invalid:

```text
downgrade to subtitle
```

First scene should be `hook` only if safe display text exists.

### 2. `subtitle`

Always safe default.

Use:

```text
scene.caption
```

Fallback order:

```text
caption → on_screen_text → narration first sentence
```

### 3. `checklist`

Valid only when:

```text
layout_payload.bullets has 2–4 non-empty items
```

and bullets are supported by scene narration/caption.

If invalid:

```text
downgrade to subtitle
```

Do not invent bullets.

### 4. `warning`

Valid only when narration/caption expresses a mistake, risk, avoidance, or caution.

Use lightweight keyword detection for Spanish and English:

```text
error
errores
evita
evitar
no hagas
cuidado
riesgo
problema
peligro
demasiado
extremo
saltarte
culpa
ansiedad
mistake
avoid
risk
warning
danger
```

If invalid:

```text
downgrade to subtitle
```

### 5. `quote`

Valid only when:

```text
layout_payload.body OR layout_payload.title
```

is short, memorable, and supported by narration/caption.

Suggested limits:

```text
title/body max 16 words
```

If invalid:

```text
downgrade to subtitle
```

### 6. `cta`

Valid only when:

```text
scene is final scene
AND layout_payload.cta OR script.cta exists
```

If `cta` appears before the final scene:

```text
downgrade to subtitle
```

If final scene lacks CTA text:

```text
keep subtitle
add planner warning
```

---

## Desired Layout Distribution

Do not make every scene fancy. Keep most scenes calm.

Recommended distribution for a 40–55 scene video:

```text
subtitle: 60–70%
hook: 5–10%
checklist: 10–15%
warning: 5–10%
quote: 5–10%
cta: final scene only
```

Rules:

```text
scene-01 should be hook when safe
last scene should be cta when safe
do not allow more than 5 consecutive subtitle scenes if a safe pattern-break scene exists nearby
do not allow warning/checklist/quote to appear too densely
do not place CTA except final scene
```

Pattern-break layouts are:

```text
checklist
warning
quote
hook
```

---

## Implementation Plan

## 1. Update ChatGPT scenes prompt

File:

```text
src/video_agent/operator.py
```

Function:

```python
_chatgpt_scenes_prompt()
```

Add to the required scene schema:

```text
- each scene object must include:
  layout: one of ["hook", "subtitle", "checklist", "warning", "quote", "cta"]
  layout_payload: object with {title, body, bullets, cta}
  layout_reason: short English reason explaining why the layout fits the narration
```

Add layout rules:

```text
LAYOUT RULES:
- scene-01 should use layout="hook" with a 2-8 word Spanish title.
- final scene should use layout="cta" only if it contains a clear final action.
- Use layout="subtitle" for normal explanation scenes.
- Use layout="checklist" only when the narration contains 2-4 concrete steps/items.
- Use layout="warning" only when the narration describes a mistake, risk, or something to avoid.
- Use layout="quote" only for a short emotional or memorable sentence supported by the narration.
- Every non-subtitle layout must include enough layout_payload for rendering.
- Do not invent overlay bullets or claims that are not supported by narration/caption.
- Keep overlays short, Spanish, readable for adults 45+.
```

Add examples in the prompt.

### Example checklist scene

```json
{
  "id": "scene-08",
  "duration_sec": 6,
  "layout": "checklist",
  "layout_payload": {
    "title": "TU PLATO BASE",
    "body": "",
    "bullets": ["Proteína", "Verduras", "Agua"],
    "cta": ""
  },
  "layout_reason": "The narration gives a simple 3-part plate formula.",
  "narration": "Empieza con un plato simple: proteína, verduras y agua.",
  "on_screen_text": "TU PLATO BASE",
  "caption": "Empieza con un plato simple.",
  "visual_prompt": "A healthy balanced plate on a warm kitchen table, vegetables, protein, water glass, soft natural light, close-up",
  "motion": "slow_zoom",
  "asset_refs": {}
}
```

### Example warning scene

```json
{
  "id": "scene-12",
  "duration_sec": 6,
  "layout": "warning",
  "layout_payload": {
    "title": "ERROR COMÚN",
    "body": "Llegar con hambre extrema",
    "bullets": [],
    "cta": ""
  },
  "layout_reason": "The narration warns against a common eating mistake.",
  "narration": "Evita llegar a la cena con hambre extrema, porque eso hace más difícil elegir con calma.",
  "on_screen_text": "ERROR COMÚN",
  "caption": "Evita llegar con hambre extrema.",
  "visual_prompt": "A woman in her early fifties looking tired in the kitchen at night, warm light, realistic, medium shot",
  "motion": "pan_right",
  "asset_refs": {}
}
```

---

## 2. Add retention layout planner module

Create file:

```text
src/video_agent/retention/layout_planner.py
```

Suggested functions:

```python
from __future__ import annotations

from typing import Any

ALLOWED_LAYOUTS = {"hook", "subtitle", "checklist", "warning", "quote", "cta"}
PATTERN_BREAK_LAYOUTS = {"hook", "checklist", "warning", "quote"}

def apply_retention_layouts(
    scenes: list[dict[str, Any]],
    *,
    script: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ...
```

### Required behavior

`apply_retention_layouts()` must:

1. Normalize `layout` to lowercase.
2. Normalize `layout_payload`.
3. Validate each proposed layout.
4. Downgrade unsafe layouts to `subtitle`.
5. Add planner warnings for every downgrade.
6. Force first scene to hook only when safe.
7. Force final scene to CTA only when safe.
8. Add pattern breaks only by choosing eligible scenes that already have valid payload.
9. Never invent unsupported overlay content.

### Suggested helper functions

```python
def normalize_payload(value: Any) -> dict[str, Any]:
    ...

def add_warning(scene: dict[str, Any], warning: str) -> None:
    ...

def word_count(text: str) -> int:
    ...

def has_warning_intent(scene: dict[str, Any]) -> bool:
    ...

def has_valid_bullets(scene: dict[str, Any]) -> bool:
    ...

def has_valid_hook_text(scene: dict[str, Any]) -> bool:
    ...

def has_valid_quote_text(scene: dict[str, Any]) -> bool:
    ...

def has_valid_cta(scene: dict[str, Any], *, is_last: bool, script: dict[str, Any] | None) -> bool:
    ...

def downgrade(scene: dict[str, Any], reason: str) -> None:
    scene["layout"] = "subtitle"
    add_warning(scene, reason)
```

### Planner pseudocode

```python
def apply_retention_layouts(scenes, *, script=None):
    if not scenes:
        return scenes

    for idx, scene in enumerate(scenes):
        scene["planner_warnings"] = list(scene.get("planner_warnings") or [])
        scene["layout_payload"] = normalize_payload(scene.get("layout_payload"))
        layout = str(scene.get("layout") or "subtitle").strip().lower()

        if layout not in ALLOWED_LAYOUTS:
            layout = "subtitle"
            add_warning(scene, "Invalid layout downgraded to subtitle.")

        scene["layout"] = layout

        is_last = idx == len(scenes) - 1

        if layout == "hook" and not has_valid_hook_text(scene):
            downgrade(scene, "Hook downgraded to subtitle: missing 2-8 word title.")

        elif layout == "checklist" and not has_valid_bullets(scene):
            downgrade(scene, "Checklist downgraded to subtitle: missing 2-4 valid bullets.")

        elif layout == "warning" and not has_warning_intent(scene):
            downgrade(scene, "Warning downgraded to subtitle: narration does not express a mistake/risk/avoidance.")

        elif layout == "quote" and not has_valid_quote_text(scene):
            downgrade(scene, "Quote downgraded to subtitle: missing short supported quote text.")

        elif layout == "cta" and not has_valid_cta(scene, is_last=is_last, script=script):
            downgrade(scene, "CTA downgraded to subtitle: CTA is allowed only on final scene and requires CTA text.")

    # Safe first-scene hook
    first = scenes[0]
    if first.get("layout") == "subtitle" and has_valid_hook_text(first):
        first["layout"] = "hook"
        add_warning(first, "Planner promoted first scene to hook using existing safe text.")

    # Safe final CTA
    last = scenes[-1]
    if last.get("layout") != "cta":
        payload = last.get("layout_payload") or {}
        if not payload.get("cta") and script and script.get("cta"):
            payload["cta"] = str(script["cta"]).strip()
            last["layout_payload"] = payload
        if has_valid_cta(last, is_last=True, script=script):
            last["layout"] = "cta"
            add_warning(last, "Planner promoted final scene to CTA using existing safe CTA text.")

    # Optional rhythm correction: only promote eligible scenes that already have valid payload.
    scenes = apply_pattern_break_rhythm(scenes)

    return scenes
```

### Pattern break rule

Implement conservatively.

If there are more than 5 consecutive `subtitle` scenes, look inside that range for a scene that already has valid checklist/warning/quote payload. If found, promote it. If not found, do nothing and add a warning to the first scene in the run:

```text
Could not insert safe pattern break: no eligible scene with valid layout payload.
```

---

## 3. Call planner from scene normalization

File:

```text
src/video_agent/operator.py
```

Function:

```python
_normalize_scenes_candidate()
```

After normalizing all scene objects, call:

```python
from video_agent.retention.layout_planner import apply_retention_layouts

parsed["scenes"] = apply_retention_layouts(
    normalized_scenes,
    script=None,
)
```

If the function has access to script context in the future, pass it. For now, `script=None` is acceptable; final CTA can still use existing scene payload.

Important: `_normalize_scenes_candidate()` must preserve new fields instead of rebuilding and dropping them.

### Required change

When normalizing a scene, start from `current = dict(scene)` and then update required fields.

Do not build a completely new object that discards unknown keys.

Example:

```python
normalized = {
    **current,
    "id": scene_id,
    "duration_sec": duration,
    "narration": narration,
    "on_screen_text": on_screen_text,
    "caption": caption,
    "visual_prompt": visual_prompt,
    "motion": motion,
    "asset_refs": asset_refs,
}
```

Then add layout fields:

```python
normalized["layout"] = str(current.get("layout") or "subtitle").strip().lower()
normalized["layout_payload"] = normalize_payload(current.get("layout_payload"))
normalized["layout_reason"] = str(current.get("layout_reason") or "").strip()
normalized["planner_warnings"] = list(current.get("planner_warnings") or [])
```

---

## 4. Update scene validator

File:

```text
src/video_agent/operator_validators.py
```

Add:

```python
ALLOWED_LAYOUTS = {"hook", "subtitle", "checklist", "warning", "quote", "cta"}
```

Add a lightweight validation function:

```python
def _validate_scene_layout(scene: dict[str, Any], scene_label: str) -> ValidationResult:
    result = ValidationResult()
    layout = str(scene.get("layout") or "subtitle").lower()

    if layout not in ALLOWED_LAYOUTS:
        result.errors.append(
            f"Scene {scene_label}: invalid layout {layout!r}. "
            f"Allowed: {sorted(ALLOWED_LAYOUTS)}"
        )

    payload = scene.get("layout_payload")
    if payload is not None and not isinstance(payload, dict):
        result.errors.append(f"Scene {scene_label}: layout_payload must be an object.")

    if layout == "checklist":
        bullets = (payload or {}).get("bullets") if isinstance(payload, dict) else None
        if not isinstance(bullets, list) or len([b for b in bullets if str(b).strip()]) < 2:
            result.warnings.append(
                f"Scene {scene_label}: checklist layout should have 2-4 bullets."
            )

    if layout == "cta":
        cta = (payload or {}).get("cta") if isinstance(payload, dict) else ""
        if not str(cta or "").strip():
            result.warnings.append(
                f"Scene {scene_label}: CTA layout should include layout_payload.cta."
            )

    return result
```

Call it inside `_validate_scenes()` for each scene:

```python
result.merge(_validate_scene_layout(scene, scene_id or f"index {index}"))
```

Do not block promotion for minor distribution problems. Use warnings unless the layout value or payload type is invalid.

---

## 5. Update Remotion types

File:

```text
remotion/src/render-props.ts
```

Add:

```ts
export type SceneLayout =
  | 'hook'
  | 'subtitle'
  | 'checklist'
  | 'warning'
  | 'quote'
  | 'cta';

export type LayoutPayload = {
  title?: string;
  body?: string;
  bullets?: string[];
  cta?: string;
};
```

Update `Scene`:

```ts
export type Scene = {
  id: string;
  duration_sec: number;
  narration: string;
  visual_type: string;
  visual_prompt: string;
  on_screen_text: string;
  caption: string;
  motion: string;
  asset_refs: {background: string};
  audio_offset_sec?: number;
  word_segments?: WordSegment[];

  layout?: SceneLayout;
  layout_payload?: LayoutPayload;
  layout_reason?: string;
  planner_warnings?: string[];
};
```

Update `defaultRenderProps.scenes[0]` to include safe defaults:

```ts
layout: 'subtitle',
layout_payload: {title: '', body: '', bullets: [], cta: ''},
layout_reason: '',
planner_warnings: [],
```

---

## 6. Render overlays in Remotion

File:

```text
remotion/src/ChannelVideo.tsx
```

Keep the existing video/photo background logic.

Add overlay components:

```tsx
HookOverlay
SubtitleOverlay
ChecklistOverlay
WarningOverlay
QuoteOverlay
CtaOverlay
SceneRetentionOverlay
```

### SceneRetentionOverlay

```tsx
const SceneRetentionOverlay: React.FC<{
  scene: Scene;
  palette: RenderProps['style']['palette'];
}> = ({scene, palette}) => {
  const layout = scene.layout ?? 'subtitle';
  const payload = scene.layout_payload ?? {};
  const title = payload.title || scene.on_screen_text || '';
  const body = payload.body || scene.caption || '';
  const bullets = Array.isArray(payload.bullets) ? payload.bullets : [];
  const cta = payload.cta || '';

  if (layout === 'hook') {
    return <HookOverlay text={title} accent={palette.accent} />;
  }

  if (layout === 'checklist') {
    return <ChecklistOverlay title={title} bullets={bullets} accent={palette.accent} />;
  }

  if (layout === 'warning') {
    return <WarningOverlay title={title || 'ERROR COMÚN'} body={body} accent={palette.accent} />;
  }

  if (layout === 'quote') {
    return <QuoteOverlay text={body || title} accent={palette.accent} />;
  }

  if (layout === 'cta') {
    return <CtaOverlay title={title} cta={cta} accent={palette.accent} />;
  }

  return <SubtitleOverlay text={scene.caption || scene.on_screen_text || ''} />;
};
```

Call it inside `SceneView`, above fade overlay and after visual background/gradients:

```tsx
<SceneRetentionOverlay scene={scene} palette={palette} />
```

Important: scene-to-scene fade overlay must remain last/topmost.

### Styling requirements

For adults 45+:

```text
large font
high contrast
max 2 lines where possible
avoid tiny text
avoid too much motion
premium calm wellness style
```

Use `palette.accent` for highlights.

---

## 7. Interaction with word-level subtitle feature

This spec does not replace word-level subtitles.

When word-level subtitles are implemented:

```text
layout="subtitle"
```

should use the word-sync subtitle component.

Other layouts (`hook`, `checklist`, `warning`, `quote`, `cta`) may suppress word-sync subtitles to avoid visual clutter.

Suggested behavior:

```text
subtitle layout → render word-sync captions if word_segments exist
non-subtitle layout → render layout overlay only
```

---

## 8. Tests

Add tests for the planner.

Suggested file:

```text
tests/test_retention_layout_planner.py
```

### Required test cases

1. Valid checklist is preserved.
2. Checklist without bullets downgrades to subtitle.
3. Warning without warning intent downgrades to subtitle.
4. CTA before final scene downgrades to subtitle.
5. Final CTA is preserved when CTA text exists.
6. First scene is promoted to hook only when safe hook text exists.
7. Invalid layout value downgrades to subtitle.
8. Planner does not invent bullets.
9. Pattern break insertion only promotes eligible scenes with valid payload.
10. `planner_warnings` are added for downgrades.

Example test:

```python
def test_checklist_without_bullets_downgrades():
    scenes = [
        {
            "id": "scene-01",
            "layout": "checklist",
            "narration": "Empieza con un plato simple.",
            "caption": "Empieza con un plato simple.",
            "on_screen_text": "TU PLATO BASE",
            "layout_payload": {"title": "TU PLATO BASE", "bullets": []},
        }
    ]

    out = apply_retention_layouts(scenes)

    assert out[0]["layout"] == "subtitle"
    assert out[0]["layout_payload"]["bullets"] == []
    assert out[0]["planner_warnings"]
```

---

## 9. Acceptance Criteria

The implementation is complete when:

1. ChatGPT scenes prompt requests layout + layout_payload + layout_reason.
2. `scenes.json` preserves layout fields after promotion.
3. Python planner downgrades unsafe layouts instead of forcing them.
4. Python planner never invents unsupported overlay content.
5. Remotion renders different overlays based on `scene.layout`.
6. Invalid or incomplete layouts gracefully fall back to subtitle.
7. Final scene can render CTA overlay.
8. Tests cover planner safety behavior.
9. Existing videos without layout fields still render successfully using subtitle fallback.
10. Existing background video/photo rendering remains unchanged.

---

## Non-goals

Do not implement these in this task:

```text
full word-by-word subtitle karaoke
new stock-video provider
AI visual generation changes
thumbnail A/B logic changes
YouTube upload automation
database migration
```

---

## Final Architecture Summary

```text
ChatGPT scenes prompt
→ outputs scene narration + layout + layout_payload + layout_reason

Python _normalize_scenes_candidate
→ preserves new fields
→ calls apply_retention_layouts()

Python layout planner
→ validates content/layout alignment
→ downgrades unsafe layouts
→ keeps rhythm without inventing claims

Claude QA
→ reviews final scenes artifact

Remotion ChannelVideo
→ renders overlay based on scene.layout

Render output
→ video has retention-friendly hook/checklist/warning/quote/CTA overlays
→ no mismatch between narration and on-screen layout
```
