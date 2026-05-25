# SPEC: Implement Production-Ready YouTube Shorts Pack Flow

## 0. Context

Repo hiện tại đã có long-form pipeline khá hoàn chỉnh:

```text
idea_research
→ script
→ script_qa
→ scenes
→ scenes_qa
→ seo
→ seo_qa
→ seo_vidiq
→ thumbnail_image
→ whisper_timestamps
→ render
→ review
```

Hiện tại `DEFAULT_STAGES` chưa có Shorts stages. Trong `run_all_pipeline.py` đã có import/nhánh gọi các hàm Shorts như:

```python
auto_shorts_script_stage
auto_shorts_scenes_stage
auto_shorts_tts_stage
auto_shorts_render_stage
```

Nhưng vì các stage này không nằm trong state mặc định của job, job mới sẽ không chạy Shorts mặc định.

Repo cũng đã có:

```text
src/video_agent/orchestrator/shorts_stages.py
```

Nhưng implementation hiện tại mới là bản nháp:

- Prompt Shorts còn sơ sài.
- SEO Shorts đang dummy.
- Có bypass QA bằng `qa.verdict = PASS`.
- Chưa có Remotion composition riêng cho Shorts.
- Remotion hiện chỉ có `ChannelVideoStandard` và `ThumbnailStandard`, chưa có `ChannelShortStandard`.

---

## 1. Goal

Implement một production-ready **Shorts Pack flow** để tạo 3–5 YouTube Shorts từ một long-form video job đã hoàn thành.

Shorts flow phải là **post-production module**, không phải một phần bắt buộc của default long-form `/run-all`.

Kiến trúc đúng:

```text
/jobs/{job_id}/run-all
→ produces long-form video

/jobs/{job_id}/shorts/run-all
→ reads long-form artifacts
→ produces Shorts Pack
```

Không ép Shorts vào default long-form `DEFAULT_STAGES`.

---

## 2. Non-goals

Không triển khai các phần sau trong task này:

```text
- YouTube Studio auto-upload
- YouTube analytics ingestion
- automatic scheduling
- Shorts A/B testing
- changing the long-form pipeline contracts
- replacing existing long-form Remotion renderer
```

Task này chỉ triển khai Shorts generation và Shorts review.

---

## 3. Desired User Flow

Sau khi long-form job hoàn thành, user có thể bấm:

```text
Create Shorts Pack
```

hoặc gọi API:

```http
POST /jobs/{job_id}/shorts/run-all
```

System tạo output:

```text
jobs/<job_id>/shorts/
  shorts_plan.json
  shorts_state.json
  short-01/
    script.json
    scenes.json
    seo.json
    assets/
    render_props.json
    video.mp4
    report.md
  short-02/
    ...
  short-03/
    ...
  shorts_review.html
```

Default output count:

```text
3 Shorts per long-form video
```

Configurable trong channel config:

```yaml
shorts:
  enabled: true
  count: 3
  duration_sec_min: 22
  duration_sec_max: 35
  resolution: "1080x1920"
  fps: 30
  related_video_strategy: "source_long_form"
```

---

## 4. Shorts Content Formula

Mỗi Short phải theo editorial formula:

```text
0–3s: specific pain after 45
3–7s: common misunderstanding
7–18s: simple explanation
18–28s: one practical step
28–35s: calm ending + soft CTA to watch full video
```

Với channel `Vida Plena 45+`, Shorts style phải là:

```text
Spanish es-419
warm
calm
practical
not fear-based
not medical-claim-heavy
no miracle language
no exaggerated health claims
easy to read on mobile
```

Mỗi Short chỉ tập trung vào **một micro-topic**.

Không tốt:

```text
Summary of the entire long video
```

Tốt:

```text
One pain point from one section of the long video
```

Ví dụ:

```text
Long video:
Cómo comer mejor sin dietas después de los 45

Short 1:
¿Comes sano pero sigues cansada?

Short 2:
No necesitas comer menos después de los 45

Short 3:
La cena que puede arruinar tu sueño
```

---

## 5. Required New/Updated Files

### 5.1 New schemas

Create:

```text
schemas/shorts-plan.schema.json
schemas/shorts-script.schema.json
schemas/shorts-scenes.schema.json
schemas/shorts-seo.schema.json
```

---

### 5.2 `shorts-plan.schema.json`

Expected shape:

```json
{
  "source_job_id": "string",
  "source_title": "string",
  "channel_id": "string",
  "shorts": [
    {
      "short_id": "short-01",
      "angle": "pain_hook | myth_busting | practical_tip | calm_reminder",
      "source_section_title": "string",
      "micro_topic": "string",
      "hook": "string",
      "reason": "string"
    }
  ]
}
```

Rules:

```text
- shorts length: 3–5
- short_id format: short-01, short-02, ...
- each micro_topic must be distinct
- no duplicate hooks
```

---

### 5.3 `shorts-script.schema.json`

Expected shape:

```json
{
  "source_job_id": "string",
  "channel_id": "string",
  "short_id": "string",
  "title": "string",
  "hook": "string",
  "duration_target_sec": 30,
  "narration": "string",
  "beats": [
    {
      "start_sec": 0,
      "end_sec": 3,
      "type": "pain",
      "on_screen_text": "string",
      "narration": "string"
    }
  ],
  "cta": "string",
  "related_video": {
    "source_job_id": "string",
    "title": "string",
    "url": ""
  },
  "qa": {
    "verdict": "PENDING_CLAUDE_QA"
  }
}
```

Rules:

```text
- duration_target_sec: 22–35
- hook: <= 12 words
- narration: 55–95 words
- beats: exactly 5 beats
- on_screen_text: max 6 words each
- qa.verdict must be PENDING_CLAUDE_QA, never PASS from writer
```

---

### 5.4 `shorts-scenes.schema.json`

Expected shape:

```json
{
  "source_job_id": "string",
  "channel_id": "string",
  "short_id": "string",
  "orientation": "portrait",
  "total_duration_sec": 30,
  "scenes": [
    {
      "id": "scene-01",
      "duration_sec": 3,
      "layout": "hook_center",
      "narration": "string",
      "on_screen_text": "string",
      "caption": "string",
      "visual_prompt": "string",
      "motion": "slow_zoom",
      "asset_refs": {}
    }
  ],
  "qa": {
    "verdict": "PENDING_CLAUDE_QA"
  }
}
```

Allowed layouts:

```text
hook_center
myth_split
explain_card
step_card
calm_cta
```

Rules:

```text
- orientation must be portrait
- total_duration_sec: 22–35
- scene count: 5–7
- scene IDs sequential: scene-01, scene-02, ...
- visual_prompt must be English
- on_screen_text must be Spanish es-419
- asset_refs must be object, never array
```

---

### 5.5 `shorts-seo.schema.json`

Expected shape:

```json
{
  "source_job_id": "string",
  "short_id": "string",
  "title": "string",
  "description": "string",
  "tags": ["string"],
  "language": "es-419",
  "ai_disclosure": true,
  "related_video": {
    "source_job_id": "string",
    "title": "string",
    "url": ""
  }
}
```

Rules:

```text
- title: 35–70 chars
- tags: 3–6
- description: 150–400 chars
- language: es-419
- ai_disclosure: true
```

---

## 6. New Orchestrator Module

Refactor current file:

```text
src/video_agent/orchestrator/shorts_stages.py
```

Keep the file but replace implementation with production version.

Required functions:

```python
async def auto_shorts_plan_stage(job_dir: Path, channel_path: Path, session_fn) -> Path
async def auto_shorts_script_stage(job_dir: Path, channel_path: Path, session_fn) -> Path
async def auto_shorts_scenes_stage(job_dir: Path, channel_path: Path, session_fn) -> Path
async def auto_shorts_qa_stage(job_dir: Path, channel_path: Path, qa_fn) -> Path
async def auto_shorts_assets_stage(job_dir: Path, channel_path: Path) -> Path
async def auto_shorts_tts_stage(job_dir: Path, channel_path: Path) -> Path
async def auto_shorts_render_stage(job_dir: Path, channel_path: Path) -> Path
def write_shorts_review(job_dir: Path) -> Path
```

Important:

```text
Do not use current regex-only JSON extraction for production.
Use existing extract_json_objects / validate_json pattern.
```

---

## 7. Shorts State

Do not add Shorts stages to long-form `job.json`.

Create separate state file:

```text
jobs/<job_id>/shorts/shorts_state.json
```

Example:

```json
{
  "source_job_id": "abc",
  "status": "pending | in_progress | completed | failed",
  "current_stage": "shorts_plan",
  "stages": [
    {"name": "shorts_plan", "status": "pending"},
    {"name": "shorts_script", "status": "pending"},
    {"name": "shorts_scenes", "status": "pending"},
    {"name": "shorts_qa", "status": "pending"},
    {"name": "shorts_assets", "status": "pending"},
    {"name": "shorts_tts", "status": "pending"},
    {"name": "shorts_render", "status": "pending"},
    {"name": "shorts_review", "status": "pending"}
  ]
}
```

This avoids breaking the existing long-form pipeline.

---

## 8. New API Routes

Add routes:

```http
POST /jobs/{job_id}/shorts/run-all
GET  /jobs/{job_id}/shorts
GET  /jobs/{job_id}/shorts/review
POST /jobs/{job_id}/shorts/stages/{stage_name}/run
```

Minimum required for this task:

```http
POST /jobs/{job_id}/shorts/run-all
GET  /jobs/{job_id}/shorts
```

---

### 8.1 `POST /jobs/{job_id}/shorts/run-all`

Behavior:

```text
- validate long-form job exists
- require script.json, scenes.json, seo.json
- if video.mp4 exists, include it as source metadata, but do not require it
- create shorts/ directory
- run all shorts stages sequentially
- return shorts_state + artifact paths
```

Response:

```json
{
  "source_job_id": "abc",
  "completed": [
    "shorts_plan",
    "shorts_script",
    "shorts_scenes",
    "shorts_qa",
    "shorts_assets",
    "shorts_tts",
    "shorts_render",
    "shorts_review"
  ],
  "shorts": [
    {
      "short_id": "short-01",
      "video": "shorts/short-01/video.mp4",
      "title": "..."
    }
  ]
}
```

---

## 9. Prompt Requirements

### 9.1 Shorts Plan Prompt

Input:

```text
script.json
scenes.json
seo.json
channel.yaml
```

Prompt must ask ChatGPT to select the best 3 micro-topics.

Prompt constraints:

```text
- choose micro-topics from the long-form script
- avoid summarizing the whole video
- each Short must target one specific pain/desire
- output JSON only
- no medical claims
- no miracle language
```

---

### 9.2 Shorts Script Prompt

For each planned Short, generate one script.

Prompt must enforce:

```text
0–3s pain
3–7s myth
7–18s explanation
18–28s practical step
28–35s calm ending + CTA
```

CTA example:

```text
Mira el video completo en el canal para entenderlo paso a paso.
```

Do not fabricate URL. Leave URL empty unless the long-form URL exists in metadata.

---

### 9.3 Shorts Scenes Prompt

Do not reuse `_chatgpt_scenes_prompt()` directly. It is optimized for long-form.

Create new helper:

```python
_chatgpt_shorts_scenes_prompt(channel_config, short_script)
```

Must produce:

```text
5–7 portrait scenes
large readable text
vertical visual prompts
short scene durations
mobile-safe layout names
```

---

### 9.4 Shorts QA Prompt

Use Claude QA, but lightweight.

QA must check:

```text
schema validity
health safety
no exaggerated claims
Spanish es-419
readability for 45+
duration 22–35s
on-screen text not too long
CTA not misleading
```

Do not bypass QA with:

```python
{"verdict": "PASS"}
```

Remove current behavior where Shorts scenes are given fake PASS without actual QA.

---

## 10. Remotion Implementation

### 10.1 Add new component

Create:

```text
remotion/src/ChannelShort.tsx
```

Register in:

```text
remotion/src/Root.tsx
```

Add:

```tsx
<Composition
  id="ChannelShortStandard"
  component={ChannelShort}
  durationInFrames={Math.round(defaultRenderProps.render.duration_sec * fps)}
  fps={fps}
  width={1080}
  height={1920}
  defaultProps={defaultRenderProps}
  calculateMetadata={calculateVideoMetadata}
/>
```

---

### 10.2 ChannelShort layout

Design:

```text
Canvas: 1080x1920
Top safe area: channel tag
Middle: main hook / large text
Lower-middle: explanation card
Bottom safe area: avoid YouTube UI zone
```

Approx safe zones:

```text
Top: avoid y < 120
Bottom: avoid y > 1600
Main hook: y 360–850
Support card: y 950–1320
CTA: y 1320–1540
```

Typography:

```text
Hook: 82–96px
Support text: 48–58px
Tag: 34–42px
Max hook lines: 2
Max words per line: 3–5
Accent color: #F2C94C
```

Layouts:

```text
hook_center:
  large hook centered, background image/video darkened

myth_split:
  top: "NO ES..."
  bottom: "ES..."
  accent highlight on second phrase

explain_card:
  large white/cream card, one clear explanation

step_card:
  numbered practical step, max 2 lines

calm_cta:
  calm closing line, soft CTA
```

Do not use long-form bottom subtitle layout as primary Shorts layout.

---

## 11. Render Implementation

Current `render_operator_job()` changes resolution to `1080x1920` if job folder parent is `shorts`.

Keep that fallback, but implement explicit short render path.

Add function:

```python
def render_short_job(short_job_dir: Path, channel_path: Path) -> PipelineResult:
    ...
```

or extend `OperatorRenderOptions`:

```python
composition: str = "ChannelVideoStandard"
resolution: str | None = None
```

For Shorts:

```text
composition = "ChannelShortStandard"
resolution = "1080x1920"
thumbnail render optional
```

Update Remotion command builder so it can render different composition IDs:

```python
build_remotion_commands(
  render_props_path,
  video_path,
  thumbnail_path,
  composition="ChannelVideoStandard"
)
```

Short render should call:

```text
ChannelShortStandard
```

not:

```text
ChannelVideoStandard
```

---

## 12. Assets

For Shorts assets:

```text
Prefer vertical-friendly stock video.
Fallback to existing long-form assets.
Fallback to still image with slow zoom.
```

Do not require ChatGPT image generation for every Short.

Add visual config override:

```python
shorts_visuals = {
  **channel_config["visuals"],
  "orientation": "portrait",
  "providers": ["pexels_video"],
  "scene_count_target": 5
}
```

Existing `prepare_assets()` can be reused if it respects `visuals.orientation`.

---

## 13. SEO for Shorts

Current Shorts code writes dummy SEO with:

```json
"tags": ["shorts"]
```

This is not enough.

Create real `seo.json` per Short.

Example:

```json
{
  "source_job_id": "abc",
  "short_id": "short-01",
  "title": "¿Comes sano pero sigues cansada después de los 45?",
  "description": "Una explicación breve para adultos 45+ sobre por qué comer poco no siempre ayuda a tener más energía. Mira el video completo en Vida Plena 45+.",
  "tags": [
    "salud 45+",
    "bienestar después de los 45",
    "alimentación saludable",
    "energía después de los 45"
  ],
  "language": "es-419",
  "ai_disclosure": true,
  "related_video": {
    "source_job_id": "abc",
    "title": "Cómo comer mejor sin dietas después de los 45",
    "url": ""
  }
}
```

---

## 14. Review Page

Create:

```text
jobs/<job_id>/shorts/shorts_review.html
```

Should show:

```text
- source long-form title
- source job id
- Short 1 video player
- Short 1 title
- Short 1 description
- Short 1 tags
- Short 1 related video instruction
- Short 2...
- Short 3...
```

Dashboard integration can be basic for now.

---

## 15. Tests

Add tests:

```text
tests/test_shorts_stages.py
tests/test_shorts_api.py
tests/test_remotion_short_config.py
```

### 15.1 Unit tests

Test:

```text
- shorts plan stage requires script.json/scenes.json/seo.json
- shorts plan creates shorts/shorts_plan.json
- shorts_script creates short-01/script.json etc.
- shorts_scenes creates portrait scenes with 5–7 scenes
- shorts QA rejects bad health claims
- shorts_assets does not crash if no asset found
- shorts_render calls ChannelShortStandard
- shorts_state resumes from incomplete stage
```

### 15.2 API tests

Test:

```text
POST /jobs/{job_id}/shorts/run-all
→ returns 404 if job missing

POST /jobs/{job_id}/shorts/run-all
→ returns 409 if script/scenes/seo missing

POST /jobs/{job_id}/shorts/run-all
→ creates 3 short folders

GET /jobs/{job_id}/shorts
→ returns state + artifact list
```

### 15.3 Regression tests

Ensure:

```text
- existing /jobs/{id}/run-all still passes
- DEFAULT_STAGES unchanged for long-form
- long-form Remotion still uses ChannelVideoStandard
- thumbnail generation still works
```

---

## 16. Acceptance Criteria

Task is complete when:

```text
1. Long-form /run-all remains unchanged and still works.
2. Shorts are not part of DEFAULT_STAGES.
3. POST /jobs/{job_id}/shorts/run-all generates at least 3 Shorts.
4. Each Short has script.json, scenes.json, seo.json, render_props.json, video.mp4.
5. Each Short video is 1080x1920.
6. Each Short duration is 22–35 seconds by default.
7. Remotion uses ChannelShortStandard.
8. Shorts script follows:
   pain → myth → explanation → practical step → calm CTA.
9. No Shorts stage writes fake qa.verdict=PASS from the writer.
10. shorts_review.html is generated.
11. Tests pass.
```

---

## 17. Implementation Order

Recommended order:

```text
1. Add schemas.
2. Refactor shorts_stages.py.
3. Add shorts state helper.
4. Add /jobs/{id}/shorts/run-all and GET /jobs/{id}/shorts.
5. Add ChannelShort.tsx.
6. Register ChannelShortStandard in Root.tsx.
7. Update render command builder to accept composition ID.
8. Implement render_short_job.
9. Add shorts_review.html writer.
10. Add tests.
11. Run full test suite.
```

---

## 18. Important Design Decision

Do **not** do this:

```text
long-form run-all
→ render
→ review
→ shorts_script
→ shorts_scenes
→ shorts_render
```

Do this instead:

```text
long-form run-all
→ completed long-form job

separate shorts run-all
→ reads completed long-form artifacts
→ creates Shorts Pack
```

Reason:

```text
- long-form production remains stable
- Shorts failure does not fail the main video
- user can choose which long-form videos deserve Shorts
- easier dashboard/review
- easier retry
```

Final architecture:

```text
Long-form video = main product
Shorts Pack = post-production distribution module
```
