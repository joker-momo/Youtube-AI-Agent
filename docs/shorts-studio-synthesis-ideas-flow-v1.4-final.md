# Spec: Shorts Studio Synthesis Ideas Flow v1.4

## 0. Purpose

Replace the current Shorts Studio planning direction with a **human-selected synthesis idea workflow**.

The new workflow:

```text
rendered long-form job
→ read full long-form narration by scene
→ ChatGPT proposes multiple Short ideas from the whole video
→ system scores and validates the ideas
→ Web UI shows idea cards
→ user selects one or more ideas
→ system builds and renders Shorts directly
```

Explicitly removed from this workflow:

```text
No Confirm Render step.
No ready_for_render pause.
No excerpt/candidate mode based on one contiguous scene window.
No "select a 1–3 consecutive scene segment" logic for Shorts Studio synthesis.
```

This spec is for the **new Shorts Studio synthesis flow**. Existing legacy Shorts Autopilot may remain for backward compatibility, but the new Studio tab must use only the synthesis idea flow.

---

## 0.1 Final hardening decisions

This version locks the previously ambiguous implementation choices:

1. **Short ID allocation**: selected ideas are append-only by default. New renders allocate the next available `short-XX` ID and never overwrite existing rendered Shorts unless `force=true` with explicit archive semantics.
2. **State source of truth**: new Studio synthesis state is derived from `idea_generation_run.json`, `studio_render_run.json`, `short_ideas.json`, and `shorts_manifest.json`. Do not derive synthesis flow state from legacy `ready_for_render`.
3. **Job artifact resolution**: eligibility must use the existing route-compatible resolver that supports root, `json/`, and `outputs/` layouts.
4. **Deterministic scoring**: all vague scoring terms are replaced with explicit thresholds.
5. **Source prompt budget**: idea generation may use a large scene-block source, but selected-idea script generation must pass a compact, structured `source_artifacts` payload with a specified character budget.
6. **Unified state vocabulary**: API/UI uses one status enum only; run-internal terms are mapped to UI status.
7. **Lifecycle reset**: starting a new idea-generation cycle invalidates stale synthesis render run state.
8. **Skip semantics**: already-rendered selected ideas are counted explicitly and do not silently alter rendered/failed counts.
9. **Build boundary**: `build_short(...)` receives `source_artifacts` as an explicit optional parameter.
10. **Failed generation precedence**: a newer failed `idea_generation_run.json` must override old `short_ideas.json` / manifest state.
11. **Force archive manifest semantics**: archived Shorts must be removed from active manifest entries or marked archived so UI never points at moved files.
12. **Queue command mapping**: queued/running synthesis worker commands must map to the correct UI state even before lock files exist.


---

## 1. Current problem

The current Shorts planning logic is mostly extractive:

```text
scenes.json
→ single scene / 2-scene / 3-scene consecutive windows
→ score candidate windows
→ ChatGPT chooses candidate_id
→ builder rewrites and renders Short
```

That works for excerpt-style Shorts, but it is the wrong primary model for the channel's content.

The channel needs Shorts such as:

```text
3 señales...
4 errores...
5 hábitos...
lo más importante...
después de comer haz estos ejercicios...
resumen práctico...
mito vs verdad...
```

These Shorts often synthesize multiple ideas from non-contiguous scenes across the long video.

Therefore the new flow must analyze the **full long-form narration** and propose **Short ideas** first, before building scenes/rendering.

---

## 2. Product behavior

### 2.1 User flow

1. User opens the new Shorts Studio tab.
2. User selects a long-form job that has completed rendering.
3. User clicks **Generate Short Ideas**.
4. System reads the full long-form narration from `scenes.json` and supporting metadata from `seo.json` / `script.json`.
5. ChatGPT returns multiple grounded Short ideas.
6. System validates and scores each idea.
7. UI displays idea cards.
8. User selects one or more ideas.
9. User clicks **Create & Render Shorts**.
10. System generates the Short script, scenes, source map, SEO, QA, audio, cover, and video.
11. UI shows rendered Short outputs.

No second confirmation step after draft generation.

### 2.2 Required UI/API states

Use this single vocabulary everywhere in the synthesis flow:

```text
none
ideas_generating
ideas_ready
rendering_selected
completed
completed_with_warnings
failed
```

Definitions:

```text
none:
  no synthesis ideas or synthesis render output exists yet

ideas_generating:
  idea generation is currently active or queued/running

ideas_ready:
  short_ideas.json exists with at least one valid idea and no selected render is active/completed for the latest idea generation

rendering_selected:
  selected ideas are currently rendering or queued/running

completed:
  render selected cycle finished; at least one selected idea rendered; no blocked/failed/skipped-only outcome

completed_with_warnings:
  render selected cycle finished with at least one rendered Short and at least one warning, needs_review, failed, or skipped selected idea

failed:
  idea generation failed, or render selected cycle produced no rendered Shorts
```

Do not use these as synthesis flow UI/API statuses:

```text
idle
rendered
ready_for_render
```

Mapping:

```text
old/legacy rendered → completed
draft/ready_for_render → not used in synthesis flow
```

Do not show a Confirm Render button.

---

## 3. Non-goals

Do not delete legacy Shorts Autopilot unless existing tests require refactor.

Do not remove current `build_short(...)` rendering pipeline.

Do not use contiguous excerpt candidates in the new Studio synthesis flow.

Do not create a draft-only stopping point.

Do not require OCR or manual QA before rendering.

Do not require real ChatGPT/Gemini/Remotion calls in unit tests.

---

## 4. Source artifacts and job layout compatibility

A long-form job is eligible for this flow if it has all required artifacts, but the artifacts may exist in either legacy root layout or newer split layout.

Required logical artifacts:

```text
job.json
script.json
scenes.json
seo.json
video.mp4
```

Resolution rules:

```python
def resolve_long_job_artifact(job_dir: Path, logical_name: str) -> Path | None:
    if logical_name.endswith(".json"):
        candidates = [
            job_dir / logical_name,
            job_dir / "json" / logical_name,
        ]
    elif logical_name == "video.mp4":
        candidates = [
            job_dir / "video.mp4",
            job_dir / "outputs" / "video.mp4",
        ]
    else:
        candidates = [job_dir / logical_name]

    for path in candidates:
        if path.exists():
            return path
    return None
```

Eligibility must use this resolver rather than hardcoding root-only paths.

The idea generator reads resolved paths for:

```text
seo.json
script.json
scenes.json
```

Optional resolved artifacts:

```text
review.json
metadata.json
```

The long video file is required because the user should only generate Shorts from completed long-form videos.

---

## 5. New output artifacts

Under:

```text
jobs/<job_id>/shorts/
```

create:

```text
short_ideas.json
selected_short_ideas.json
idea_generation_run.json
studio_render_run.json
```

Per rendered Short remains under:

```text
jobs/<job_id>/shorts/short-01/
jobs/<job_id>/shorts/short-02/
...
```

Existing per-short files stay compatible:

```text
short_idea.json
short_script.json
short_scenes.json
short_source_map.json
short_seo.json
short_qa.json
short_render_props.json
short_status.json
short.mp4
short_cover.jpg
```

No `ready_for_render` artifact is required in this flow.

---

## 5.1 Synthesis lifecycle and stale-run invalidation

A new idea-generation cycle starts when the user calls:

```text
POST /shorts-studio/jobs/{job_id}/ideas/generate
```

At the beginning of a successful new idea-generation cycle:

1. Create a new `generation_id`.
2. Write/update `idea_generation_run.json` with:
   - `status = "ideas_generating"`
   - `generation_id = <new id>`
   - `invalidates_prior_render_run = true`
3. Mark any existing `studio_render_run.json` as stale, or archive it.

Preferred implementation:

```text
jobs/<job_id>/shorts/_archive/<timestamp>-studio_render_run.json
```

Alternative acceptable implementation:

```json
{
  "status": "stale",
  "stale_reason": "new_ideas_generation_started",
  "superseded_by_generation_id": "ideas-..."
}
```

State derivation must ignore stale `studio_render_run.json`.

When ideas finish successfully:

```text
idea_generation_run.status = "ideas_ready"
short_ideas.generation_id = idea_generation_run.generation_id
```

When selected ideas render:

```text
studio_render_run.generation_id = short_ideas.generation_id
```

A `studio_render_run.json` is current only if:

```python
studio_render_run.generation_id == short_ideas.generation_id
and studio_render_run.status != "stale"
```

---

## 5.2 Idea-generation failure precedence

A new idea-generation run is authoritative as soon as `idea_generation_run.json` is written with a new `generation_id`.

This remains true even if generation fails before `short_ideas.json` is updated.

State derivation must therefore consider `idea_generation_run.json` before falling back to old `short_ideas.json` when the idea run has a newer generation.

Rules:

```text
If idea_generation_run.status == "ideas_generating" or "running":
  state = ideas_generating

If idea_generation_run.status == "failed":
  state = failed

If idea_generation_run.status == "ideas_ready":
  then short_ideas.json must have matching generation_id and valid ideas.
  If not, state = failed and warning = ideas_ready_without_matching_short_ideas
```

A failed new idea-generation run must not show stale `ideas_ready`, `completed`, or `completed_with_warnings` from previous generations.

Implementation helper:

```python
def is_newer_generation_run_authoritative(idea_run: dict | None, short_ideas: dict | None) -> bool:
    if not idea_run:
        return False
    if not short_ideas:
        return True
    return idea_run.get("generation_id") != short_ideas.get("generation_id")
```

If `idea_run` is authoritative and failed, return `failed`.

If `idea_run` is authoritative and running/queued, return `ideas_generating`.

---

## 6. Data model

### 6.1 `short_ideas.json`

```json
{
  "schema_version": "short_ideas.v1",
  "source_long_job_id": "job-id",
  "source_title": "Long video title",
  "generated_at": "ISO datetime",
  "generation_id": "ideas-20260602T120000Z",
  "provider": "chatgpt",
  "input_source": {
    "scenes_count": 42,
    "narration_chars": 18500,
    "truncated": false
  },
  "ideas": [
    {
      "idea_id": "idea-01",
      "idea_type": "synthesis",
      "format": "mistake_list",
      "title": "4 malos hábitos que te envejecen más rápido",
      "hook_text": "TE ENVEJECE MÁS",
      "viewer_pain": "No saber qué hábitos diarios aceleran el cansancio y la edad percibida",
      "practical_payoff": "Identificar hábitos concretos que puede cambiar desde hoy",
      "source_scene_ids": ["scene-04", "scene-11", "scene-18", "scene-27"],
      "key_points": [
        {
          "point": "Dormir tarde aumenta el cansancio visible",
          "source_scene_ids": ["scene-04"]
        },
        {
          "point": "Comer mal por la noche afecta la energía",
          "source_scene_ids": ["scene-11"]
        }
      ],
      "narration_seed": "Condensed source-backed seed text for the builder...",
      "visual_angle": "bad daily habit contrast",
      "cta_angle": "long_video_channel_cta",
      "risk_level": "lifestyle",
      "scores": {
        "hook_strength": 92,
        "viewer_pain": 88,
        "practical_value": 90,
        "source_fidelity": 92,
        "visual_potential": 86,
        "safety": 95,
        "uniqueness": 84,
        "overall": 90
      },
      "risk_flags": [],
      "status": "idea_ready"
    }
  ],
  "warnings": []
}
```

### 6.2 `selected_short_ideas.json`

```json
{
  "schema_version": "selected_short_ideas.v1",
  "source_long_job_id": "job-id",
  "selected_at": "ISO datetime",
  "generation_id": "ideas-20260602T120000Z",
  "selected_idea_ids": ["idea-01", "idea-04"],
  "status": "selected"
}
```

### 6.3 `idea_generation_run.json`

```json
{
  "schema_version": "idea_generation_run.v1",
  "source_long_job_id": "job-id",
  "status": "ideas_ready",
  "started_at": "ISO datetime",
  "completed_at": "ISO datetime",
  "provider": "chatgpt",
  "idea_count": 10,
  "generation_id": "ideas-20260602T120000Z",
  "invalidates_prior_render_run": true,
  "errors": [],
  "warnings": []
}
```

### 6.4 `studio_render_run.json`

This is the canonical run report for the synthesis flow.

```json
{
  "schema_version": "studio_render_run.v1",
  "source_long_job_id": "job-id",
  "mode": "synthesis_ideas",
  "generation_id": "ideas-20260602T120000Z",
  "status": "completed|completed_with_warnings|failed",
  "started_at": "ISO datetime",
  "completed_at": "ISO datetime",
  "selected_idea_count": 2,
  "attempted_render_count": 2,
  "rendered_count": 2,
  "needs_review_count": 0,
  "failed_count": 0,
  "skipped_count": 0,
  "blocked_count": 0,
  "warnings": [],
  "errors": []
}
```

Rules:

- `selected_idea_count` is the number of requested idea IDs after validation.
- `attempted_render_count` is the number of selected ideas that were actually sent into `build_short(...)`.
- `skipped_count` is the number of selected ideas skipped because they were already rendered and `force=false`.
- `failed_count` means technical failure only.
- `needs_review_count` means generated but failed QA or requires manual review.
- `blocked_count = failed_count + needs_review_count`.
- Skipped ideas are not failures, but they are warnings.
- Overall status:
  - `completed` when `rendered_count > 0`, `blocked_count == 0`, and `skipped_count == 0`
  - `completed_with_warnings` when `rendered_count > 0` and (`blocked_count > 0` or `skipped_count > 0`)
  - `failed` when `rendered_count == 0`, even if all selected ideas were skipped


### 6.4 Per-short `short_idea.json`

When an idea is selected for rendering, persist it inside the short directory:

```json
{
  "short_id": "short-01",
  "idea_id": "idea-01",
  "idea_type": "synthesis",
  "format": "mistake_list",
  "title": "4 malos hábitos que te envejecen más rápido",
  "hook_text": "TE ENVEJECE MÁS",
  "source_scene_ids": ["scene-04", "scene-11", "scene-18", "scene-27"],
  "key_points": [],
  "narration_seed": "...",
  "scores": {}
}
```

---

## 7. Idea types

The new Studio flow uses only synthesis ideas.

Allowed `idea_type`:

```text
synthesis
```

Do not generate or expose:

```text
excerpt
contiguous_window
single_scene
```

Allowed formats:

```text
checklist
mistake_list
warning_signs
myth_truth
problem_solution
top_tips
recap
pain_to_tip
```

Even when `pain_to_tip` is selected, it must be a synthesis idea grounded in one or more source scenes, not a raw contiguous excerpt.

---

## 8. Modules to add

### 8.1 Required new modules

```text
src/video_agent/shorts/idea_generator.py
src/video_agent/shorts/idea_prompts.py
src/video_agent/shorts/idea_scorer.py
src/video_agent/shorts/idea_store.py
```

### 8.2 Web routes

```text
src/video_agent/web/routes/shorts_studio.py
```

If this file already exists, extend it.

### 8.3 Worker integration

Use existing queue/worker infrastructure if available.

New commands:

```text
shorts_generate_ideas
shorts_render_selected_ideas
```

Do not add `shorts_confirm_render` for this flow.

---

## 9. Full narration extraction

Create a structured source document from `scenes.json`.

### 9.1 Function

```python
def build_long_narration_source(long_job_dir: Path, *, max_chars: int = 24000) -> dict:
    ...
```

### 9.2 Output

```json
{
  "source_long_job_id": "job-id",
  "title": "...",
  "scenes": [
    {
      "scene_id": "scene-01",
      "index": 1,
      "start_sec": 0.0,
      "end_sec": 12.4,
      "duration_sec": 12.4,
      "narration": "...",
      "visual_prompt": "...",
      "layout": "..."
    }
  ],
  "full_narration": "...",
  "truncated": false,
  "narration_chars": 18500
}
```

Rules:

- Keep scene IDs.
- Preserve scene order.
- Strip empty narration scenes.
- Do not merge all text without scene markers.
- If `full_narration` is too large, truncate carefully and set `truncated=true`.
- Prefer keeping complete scenes over cutting mid-sentence.

### 9.3 Scene text format for prompt

Use scene blocks:

```text
SCENE scene-01 [0.0s–12.4s]
Narration text...

SCENE scene-02 [12.4s–23.8s]
Narration text...
```

Do not send a raw unstructured wall of text.

---

## 9.4 Truncation policy

If `build_long_narration_source(...).truncated == true`, Phase 1 behavior is:

```text
continue generation with warning
```

Write warning:

```text
source_truncated_for_idea_generation
```

Reason:

- User still gets ideas from the available source.
- Chunked summarization can be added in a future phase.

Phase 1 must not silently truncate without setting `truncated=true` and writing the warning.

Future phase:

```text
chunk scenes → summarize chunks → global ideas from chunk summaries
```

---

## 10. ChatGPT idea generation prompt

### 10.1 Function

```python
def short_ideas_prompt(channel_config: dict, source_doc: dict, target_count: int = 10) -> str:
    ...
```

### 10.2 Prompt requirements

The prompt must instruct ChatGPT to:

- analyze the full long-form narration
- produce 8–12 Short ideas
- use only source-backed claims
- support non-contiguous source scenes
- return JSON only
- make every idea useful as a standalone Short
- avoid medical overclaim
- use Spain Spanish for audience 45+
- score each idea
- include `source_scene_ids` for each idea
- include `key_points`, each with source scene IDs
- include a concise `narration_seed`
- include `visual_angle`
- include `risk_flags`

### 10.3 Prompt template

```text
You are a YouTube Shorts strategist for Vida Plena 45+.

Analyze the full long-form video narration below and propose high-retention YouTube Shorts ideas.

The channel is Spain-first practical wellness for adults over 45:
nutrition, sleep, movement, stress, daily habits, blood sugar, circulation, memory, weight, energy, and healthy routines.

IMPORTANT:
- Generate synthesis ideas only.
- Do not propose raw excerpts or contiguous scene clips.
- Each idea may combine multiple non-contiguous source scenes.
- Every idea must be grounded in source_scene_ids.
- Do not invent health claims not present in the source.
- Avoid diagnosis, cure, treatment, or miracle claims.
- Use Spanish for Spain, natural for adults 45+.
- Do not call the audience ancianos, tercera edad, abuelos, elderly, seniors, or adultos mayores.

Return exactly one raw JSON object.
No markdown.
No commentary.

Required output shape:
{
  "source_long_job_id": "...",
  "source_title": "...",
  "ideas": [
    {
      "idea_id": "idea-01",
      "idea_type": "synthesis",
      "format": "mistake_list",
      "title": "...",
      "hook_text": "...",
      "viewer_pain": "...",
      "practical_payoff": "...",
      "source_scene_ids": ["scene-04", "scene-11"],
      "key_points": [
        {
          "point": "...",
          "source_scene_ids": ["scene-04"]
        }
      ],
      "narration_seed": "...",
      "visual_angle": "...",
      "cta_angle": "long_video_channel_cta",
      "risk_level": "lifestyle|soft_health|medical_sensitive",
      "scores": {
        "hook_strength": 90,
        "viewer_pain": 85,
        "practical_value": 90,
        "source_fidelity": 90,
        "visual_potential": 85,
        "safety": 95,
        "uniqueness": 80,
        "overall": 88
      },
      "risk_flags": []
    }
  ],
  "warnings": []
}

Selection criteria:
- High retention in first 2 seconds.
- Clear pain, curiosity, mistake, number, or myth.
- One main idea per Short.
- Practical payoff before CTA.
- Strong visual potential.
- Source-backed, not invented.
- Safe wellness language.

Prefer idea formats:
- checklist
- mistake_list
- warning_signs
- myth_truth
- problem_solution
- top_tips
- recap
- pain_to_tip

SOURCE LONG VIDEO:
{scene_blocks}
```

---

## 11. Idea validation and scoring

ChatGPT returns scores, but Python must validate and normalize them with deterministic rules.

### 11.1 `idea_scorer.py`

Implement:

```python
def validate_and_score_ideas(raw: dict, source_doc: dict, target_count: int = 10) -> dict:
    ...
```

### 11.2 Constants

```python
MIN_HOOK_WORDS = 2
MAX_HOOK_WORDS = 7
MIN_SYNTHESIS_SCENES = 2
DISTINCT_PARTS_MIN_GAP_RATIO = 0.20

CURIOSITY_PHRASES = [
    "no lo sabias",
    "no lo sabes",
    "casi nadie",
    "nadie reconoce",
    "lo que pasa",
    "por que",
    "por qué",
    "la verdad",
    "el error",
    "no es",
    "te engaña",
    "te engana",
]
```

### 11.3 Source scene validation

```python
def valid_scene_id_set(source_doc: dict) -> set[str]:
    return {str(scene["scene_id"]) for scene in source_doc.get("scenes", [])}
```

Rules:

- `idea_type` must be `"synthesis"`.
- `idea_id` is normalized to `idea-01`, `idea-02`, ...
- `source_scene_ids` must exist in `source_doc`.
- duplicate source scene IDs are removed while preserving order.
- ideas with zero valid source scenes are rejected.
- synthesis ideas should have at least 2 valid source scenes when the source video has at least 2 narrated scenes.
- a one-scene idea is allowed only when `len(source_doc["scenes"]) == 1`.

No vague "one strong source scene" exception. If there are 2+ narrated source scenes, a valid synthesis idea needs 2+ valid source scenes.

### 11.4 Duplicate idea detection

Two ideas are duplicates when either condition is true:

```python
normalized_title_a == normalized_title_b
normalized_hook_a == normalized_hook_b
```

or:

```python
jaccard(source_scene_ids_a, source_scene_ids_b) >= 0.80
and token_jaccard(title_a + hook_a, title_b + hook_b) >= 0.60
```

Normalization:

```python
def normalize_text_key(text: str) -> str:
    # lowercase, strip accents, remove punctuation, collapse whitespace
```

Keep the higher `overall` idea. If tied, keep the earlier idea.

### 11.5 Non-contiguous / distinct-parts bonus

Use scene indexes from `source_doc`.

```python
def covers_distinct_parts(scene_ids: list[str], source_doc: dict) -> bool:
    indexes = [scene_index_by_id[sid] for sid in scene_ids if sid in scene_index_by_id]
    if len(indexes) < 2:
        return False

    total = max(1, len(source_doc.get("scenes", [])) - 1)
    span_ratio = (max(indexes) - min(indexes)) / total
    return span_ratio >= DISTINCT_PARTS_MIN_GAP_RATIO
```

This replaces vague "distinct parts of the video."

### 11.6 Strong curiosity phrase

```python
def has_strong_curiosity_phrase(text: str) -> bool:
    normalized = normalize_text_key(text)
    if re.search(r"\b\d+\b", normalized):
        return True
    return any(phrase in normalized for phrase in CURIOSITY_PHRASES)
```

This replaces vague "strong curiosity phrase."

### 11.7 Scoring formula

Clamp all raw score fields to 0–100 first.

```python
overall = round(
    0.22 * hook_strength
    + 0.18 * viewer_pain
    + 0.20 * practical_value
    + 0.18 * source_fidelity
    + 0.10 * visual_potential
    + 0.07 * safety
    + 0.05 * uniqueness,
    1
)
```

### 11.8 Python adjustments

Penalties:

```text
-30 if no valid source scenes
-20 if fewer than 2 source scenes and source_doc has 2+ narrated scenes
-20 if risk_level is medical_sensitive and safety < 80
-15 if duplicate idea
-15 if hook_text has more than 7 words
-10 if visual_angle is empty
-10 if practical_payoff is empty
```

Bonuses:

```text
+10 if covers_distinct_parts(source_scene_ids, source_doc) is true
+8 if format is checklist/mistake_list/warning_signs and valid source scenes >= 3
+5 if has_strong_curiosity_phrase(title + " " + hook_text) is true
```

Clamp final `overall` to 0–100 after adjustments.

### 11.9 Sort order

Sort ideas by:

```text
overall descending
source_fidelity descending
safety descending
idea_id ascending
```

Keep top `target_count`.

### 11.10 Rejected idea reporting

Return rejected ideas under diagnostics:

```json
{
  "rejected_ideas": [
    {
      "original_idea_id": "...",
      "reason": "invalid_source_scene_ids|duplicate|not_synthesis|no_valid_source_scenes"
    }
  ]
}
```


---

## 12. Idea store

### 12.1 Functions

```python
def write_short_ideas(long_job_dir: Path, ideas_doc: dict) -> None: ...
def read_short_ideas(long_job_dir: Path) -> dict: ...
def write_selected_ideas(long_job_dir: Path, selected: dict) -> None: ...
def read_selected_ideas(long_job_dir: Path) -> dict: ...
def write_idea_generation_run(long_job_dir: Path, run: dict) -> None: ...
```

Use atomic writes.

### 12.2 Paths

```python
def short_ideas_path(long_job_dir: Path) -> Path:
    return paths.shorts_dir(long_job_dir) / "short_ideas.json"

def selected_short_ideas_path(long_job_dir: Path) -> Path:
    return paths.shorts_dir(long_job_dir) / "selected_short_ideas.json"

def idea_generation_run_path(long_job_dir: Path) -> Path:
    return paths.shorts_dir(long_job_dir) / "idea_generation_run.json"
```

Add to `src/video_agent/shorts/paths.py` if appropriate.

---

## 13. Rendering selected ideas

### 13.1 Convert idea to short plan

Implement:

```python
def idea_to_short_plan(
    idea: dict,
    *,
    short_id: str,
    source_long_job_id: str,
    channel_config: dict,
) -> dict:
    ...
```

Output shape compatible with existing `build_short(...)`:

```json
{
  "short_id": "short-01",
  "source_long_job_id": "job-id",
  "idea_id": "idea-01",
  "candidate_type": "synthesis",
  "format": "mistake_list",
  "scene_ids": ["scene-04", "scene-11", "scene-18"],
  "source_scene_ids": ["scene-04", "scene-11", "scene-18"],
  "source_start_sec": null,
  "source_end_sec": null,
  "score": 90,
  "reason": "User-selected synthesis idea",
  "hook_angle": "4 hábitos que envejecen más rápido",
  "viewer_pain": "...",
  "practical_payoff": "...",
  "music_track": "shorts_nutrition_energy",
  "cover_strategy": "idea_hook_cover",
  "voice_preset": {},
  "narration_seed": "..."
}
```

Do not require source scenes to be contiguous.

### 13.2 Short ID allocation and rerender semantics

This flow is **append-only by default**.

Do not blindly assign `short-01`, `short-02`, ... starting at 1 on every render request.

#### Default behavior: append

When rendering selected ideas with `force=false`:

1. Read existing short directories under `jobs/<job_id>/shorts/short-*`.
2. Find the highest existing numeric short id.
3. Allocate new IDs starting after the highest existing ID.
4. Never overwrite existing `short-*` directories.
5. If an idea was already rendered before, skip it and report `already_rendered_idea:<idea_id>` unless `force=true`.
6. Skipped ideas increment `skipped_count`, not `rendered_count`, `failed_count`, or `needs_review_count`.
7. Skipped ideas are included in `selected_idea_count`, but excluded from `attempted_render_count`.

Example:

```text
existing: short-01, short-02
new selected ideas: idea-05, idea-07
allocated: short-03, short-04
```

#### Force behavior: archive then regenerate selected idea

When `force=true`:

1. If selected `idea_id` already has prior Shorts, archive those short directories.
2. Archive path:

```text
jobs/<job_id>/shorts/_archive/<timestamp>-short-01/
```

3. Allocate new IDs after the current highest non-archived short ID.
4. Write warning:

```text
archived_prior_short_for_idea:<idea_id>
```

Do not overwrite a short directory in place.

#### Manifest update when archiving prior Shorts

When `force=true` archives existing short directories for an `idea_id`, update `shorts_manifest.json` in the same operation.

Required behavior:

1. Find manifest entries with matching `idea_id`.
2. For archived prior Shorts, do one of these two approaches:

Preferred active/history split:

```json
{
  "shorts": [
    {
      "short_id": "short-05",
      "idea_id": "idea-01",
      "status": "rendered",
      "video_path": "shorts/short-05/short.mp4"
    }
  ],
  "archived_shorts": [
    {
      "short_id": "short-01",
      "idea_id": "idea-01",
      "status": "archived",
      "archived_at": "ISO datetime",
      "archive_path": "shorts/_archive/20260602T120000Z-short-01"
    }
  ]
}
```

Acceptable alternative:

```json
{
  "short_id": "short-01",
  "idea_id": "idea-01",
  "status": "archived",
  "rendered": false,
  "archived_at": "ISO datetime",
  "archive_path": "shorts/_archive/20260602T120000Z-short-01",
  "video_path": null,
  "cover_path": null
}
```

Rules:

- Active `shorts` entries must never point to moved/archived files.
- UI rendered-card queries should ignore `status=archived`.
- Do not show duplicate active rendered Shorts for the same `idea_id` after `force=true`.
- Archive manifest update and directory move should be best-effort atomic: write manifest after successful move; on failure, record error and do not delete the old manifest entry.


#### Existing idea lookup

A prior Short is considered to be for an idea when:

```text
short_status.json.idea_id == selected idea_id
or short_idea.json.idea_id == selected idea_id
or manifest entry idea_id == selected idea_id
```

### 13.2.1 Skipped idea run-status semantics

When `force=false` and a selected idea was already rendered:

```text
selected_idea_count += 1
skipped_count += 1
attempted_render_count += 0
rendered_count += 0
failed_count += 0
needs_review_count += 0
warnings += ["already_rendered_idea:<idea_id>"]
```

No new manifest entry is created for a skipped idea.

The UI may display the prior rendered Short for that `idea_id`, but the current run report must not count it as newly rendered.

Status cases:

```text
all selected ideas skipped:
  status = "failed"
  rendered_count = 0
  skipped_count = selected_idea_count
  warnings include already_rendered_idea entries
  errors include no_new_shorts_rendered

partial skipped, at least one newly rendered:
  status = "completed_with_warnings"

none skipped, all newly rendered and no blocked:
  status = "completed"
```

Rationale:

- `completed` should mean the current render request produced new rendered Shorts.
- Skip-only is not a successful render operation.
- Prior rendered Shorts remain visible through manifest/history, not counted as new output.


### 13.3 Render selected ideas command

Implement:

```python
def render_selected_short_ideas(
    long_job_dir: Path,
    channel_config: dict,
    idea_ids: list[str],
    *,
    build_short_fn: Callable[..., dict] | None = None,
    force: bool = False,
) -> dict:
    ...
```

Rules:

- load `short_ideas.json`
- validate selected idea IDs
- allocate short IDs using append/force semantics above
- write `selected_short_ideas.json`
- for each selected idea:
  - write `short_idea.json`
  - convert idea to short_plan
  - pass compact `source_artifacts` to the script builder/build path
  - call existing `build_short(...)`
  - `require_render_confirmation=False`
- do not stop after QA PASS
- render video immediately
- one failed Short does not block remaining selected ideas unless config says otherwise
- update `shorts_manifest.json`
- update `studio_render_run.json`


### 13.4 Status

For per-short status in this flow:

```text
PASS + rendered → rendered
QA fail → needs_review
technical exception → failed
skipped existing idea → no new per-short status
```

For UI/API job-level status, map per-run results to the unified vocabulary:

```text
rendered_count > 0 and blocked_count == 0 and skipped_count == 0 → completed
rendered_count > 0 and (blocked_count > 0 or skipped_count > 0) → completed_with_warnings
rendered_count == 0 → failed
```

Do not use:

```text
ready_for_render
requires_render_confirmation=true
```

---

## 14. Build boundary and script prompt update for synthesis ideas

### 14.0 `build_short(...)` signature change

Update public `build_short(...)` signature to accept optional source artifacts:

```python
def build_short(
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    *,
    llm_fn: Callable[..., str] = _default_llm_fn,
    gemini_fn: Callable[[str], str] | None = None,
    tts_fn: Callable[..., Path] = _default_tts_fn,
    mix_fn: Callable[..., Path] = _default_mix_fn,
    render_fn: Callable[..., Path] = _default_render_fn,
    cover_fn: Callable[..., Path] = _default_cover_fn,
    long_video_url: str = "",
    require_render_confirmation: bool = False,
    source_artifacts: dict | None = None,
) -> dict[str, Any]:
    ...
```

Then pass it through:

```python
short_script_builder.build_short_script(
    long_job_dir,
    plan_for_prompt,
    channel_config,
    llm_fn,
    source_artifacts=source_artifacts,
    feedback=feedback,
    attempt=attempts,
)
```

Backward compatibility:

- default `source_artifacts=None`
- legacy callers do not need to change
- synthesis flow must pass compact source artifacts



Current `build_short(...)` can reuse `short_script_builder`, but the prompt must understand synthesis source.

Update `short_script_prompt(...)` to include idea fields when present:

```text
SHORT IDEA:
Title: ...
Hook text: ...
Viewer pain: ...
Practical payoff: ...
Format: ...
Key points:
- point, source_scene_ids
Narration seed:
...
```

The script prompt must instruct:

```text
Use the selected synthesis idea.
Do not summarize the entire long video.
Only use claims supported by the provided key points and source scenes.
Create a 20–45 second Short.
Keep one main idea.
```

### 14.1 Source artifacts and prompt budget

When rendering selected ideas, pass compact source artifacts into `build_short_script(...)`.

`source_artifacts` should include:

```json
{
  "idea": {},
  "source_scenes": [
    {
      "scene_id": "scene-04",
      "narration": "...",
      "start_sec": 123.4,
      "end_sec": 139.2
    }
  ],
  "key_points": []
}
```

Budget rules:

```text
max_source_artifacts_chars = 8000
max_scene_narration_chars = 1200 per source scene
max_key_points = 6
```

Serialization order:

```text
idea summary
key points
source scenes in original video order
```

Update `short_script_prompt(...)` source block limit from its current small limit to support this compact source payload:

```python
json.dumps(source_artifacts, ensure_ascii=False)[:8000]
```

This fixes the current issue where `short_script_prompt` supports `SOURCE` but the builder often only gets `narration_seed`.

---

## 15. Source map for synthesis

Existing `source_map.build_source_map(...)` already supports multiple `scene_ids`.

For synthesis ideas:

- `source_start_sec` and `source_end_sec` may be null.
- `used_source_scenes` must list every selected source scene.
- `short_rewrite` remains the generated Short narration.
- Include idea metadata:

```json
{
  "idea_id": "idea-01",
  "idea_type": "synthesis",
  "key_points": []
}
```

Update source map if needed to include these fields.

---

## 16. QA changes

QA must validate synthesis source fidelity.

Add deterministic checks where possible:

- `source_map.used_source_scenes` is non-empty.
- every `idea.key_points[*].source_scene_ids` appears in `source_map.used_source_scenes`.
- medical-sensitive ideas must not contain cure/diagnosis/treatment claims.
- `short_script.narration` should not introduce unsupported named claims such as new foods/exercises not in idea/source scenes.

Gemini QA prompt should include:

```text
This Short is generated from a user-selected synthesis idea.
Validate that every key point is supported by the provided source scenes.
Do not require source scenes to be contiguous.
Fail if the Short invents a health claim not present in the idea/source scenes.
```

---

## 17. Web API

### 17.1 `GET /shorts-studio/state`

Return long jobs and synthesis idea status.

#### State source of truth

For the synthesis flow, derive state from these artifacts in this order:

1. active queue command or lock state
2. current `idea_generation_run.json`
3. current non-stale `studio_render_run.json` matching `short_ideas.generation_id`
4. `short_ideas.json`
5. `shorts_manifest.json`

Do not derive synthesis flow state from legacy `ready_for_render`.

Do not require `autopilot_run.json` for synthesis flow state.

Important:

`idea_generation_run.json` is checked before `studio_render_run.json` because a newer idea-generation cycle may fail before writing `short_ideas.json`. In that case, the UI must show `failed`, not stale `completed` / `ideas_ready` from the previous generation.


#### Status derivation

```python
def queued_or_running_synthesis_state(job_dir: Path, queue: JobQueue | None = None) -> str | None:
    job_id = job_dir.name

    # Queue command mapping must work before lock files are created.
    active = queue.active_jobs() if queue else []
    for item in active:
        if item.get("job_id") != job_id:
            continue
        command = str(item.get("command") or "")
        if command == "shorts_generate_ideas":
            return "ideas_generating"
        if command == "shorts_render_selected_ideas":
            return "rendering_selected"

    if has_active_render_selected_lock(job_dir):
        return "rendering_selected"
    if has_active_idea_generation_lock(job_dir):
        return "ideas_generating"

    return None

def derive_synthesis_shorts_status(job_dir: Path, queue: JobQueue | None = None) -> str:
    active_state = queued_or_running_synthesis_state(job_dir, queue)
    if active_state:
        return active_state

    ideas = read_short_ideas(job_dir) or {}
    idea_run = read_idea_generation_run(job_dir)

    # A newer/current idea-generation run is authoritative even when
    # short_ideas.json is still from an older generation.
    if idea_run:
        idea_status = idea_run.get("status")
        idea_generation_id = idea_run.get("generation_id")
        ideas_generation_id = ideas.get("generation_id")

        if idea_status in ("running", "ideas_generating", "queued"):
            return "ideas_generating"

        if idea_status == "failed":
            return "failed"

        if idea_status == "ideas_ready":
            if not ideas or idea_generation_id != ideas_generation_id or not ideas.get("ideas"):
                return "failed"

    current_generation_id = ideas.get("generation_id")

    render_run = read_studio_render_run(job_dir)
    if (
        render_run
        and render_run.get("status") != "stale"
        and render_run.get("generation_id") == current_generation_id
    ):
        status = render_run.get("status", "failed")
        if status in ("completed", "completed_with_warnings", "failed"):
            return status

    if ideas and ideas.get("ideas"):
        return "ideas_ready"

    manifest = read_manifest(job_dir)
    if manifest and manifest.get("mode") == "synthesis_ideas":
        status = manifest.get("status")
        if status == "rendered":
            return "completed"
        if status in ("completed", "completed_with_warnings", "failed"):
            return status

    return "none"
```

Example:

```json
{
  "can_start": true,
  "active_jobs": [],
  "jobs": [
    {
      "job_id": "job-id",
      "eligible": true,
      "missing": [],
      "shorts_status": "none|ideas_generating|ideas_ready|rendering_selected|completed|completed_with_warnings|failed",
      "idea_count": 10,
      "rendered_short_count": 2
    }
  ]
}
```

### 17.2 `POST /shorts-studio/jobs/{job_id}/ideas/generate`

Generates ideas from full long-form narration.

Body:

```json
{
  "target_count": 10,
  "force": false
}
```

Behavior:

- require system idle
- validate eligible long job
- enqueue `shorts_generate_ideas` or run according to existing worker pattern
- write `idea_generation_run.json`
- write `short_ideas.json`
- return status

### 17.3 `GET /shorts-studio/jobs/{job_id}/ideas`

Returns `short_ideas.json`.

### 17.4 `POST /shorts-studio/jobs/{job_id}/ideas/render`

Renders selected ideas immediately.

Body:

```json
{
  "idea_ids": ["idea-01", "idea-04"],
  "force": false
}
```

Behavior:

- require system idle
- validate `short_ideas.json` exists
- validate idea IDs
- enqueue `shorts_render_selected_ideas`
- no Confirm Render step
- render immediately

---

## 18. Web UI

Add or update Shorts Studio tab.

### 18.1 Panels

1. System status panel
2. Long job picker
3. Idea generation panel
4. Idea cards grid
5. Selected ideas panel
6. Rendered Shorts panel

### 18.2 Idea card

Each card shows:

```text
Title
Hook text
Format
Overall score
Viewer pain
Practical payoff
Source scene IDs
Risk level
Risk flags
Visual angle
```

Actions:

```text
Select
Unselect
Preview source scenes
```

No Confirm Render button.

Primary button:

```text
Create & Render Selected Shorts
```

### 18.3 Sorting/filtering

Default sort:

```text
overall score desc
```

Filters:

```text
format
risk_level
idea_type = synthesis
selected only
```

---

## 18.4 Queue command mapping

The UI state must reflect queued and running jobs, not only lock files.

Map queue commands:

```text
shorts_generate_ideas         → ideas_generating
shorts_render_selected_ideas  → rendering_selected
```

This mapping applies when queue status is:

```text
pending
running
```

If both a queue item and artifact status exist, queue state wins.

Reason:

```text
A queued command may exist before the worker creates .ideas.lock or .render-selected.lock.
The UI must still show the correct active state.
```


---

## 19. Locks and active job guard

Use the same global busy guard as existing Studio work.

Active means:

```text
pending/running queue job
long-job run lock
shorts autopilot lock
per-short lock
idea generation lock
selected ideas render lock
```

Add locks:

```text
jobs/<job_id>/shorts/.ideas.lock
jobs/<job_id>/shorts/.render-selected.lock
```

Avoid overlapping:

- idea generation for same job
- rendering selected ideas for same job
- legacy autopilot for same job
- long job render for same job

---

## 20. Manifest and run reporting

### 20.1 Manifest

`shorts_manifest.json` should include rendered and needs-review Shorts from selected ideas.

Example:

```json
{
  "source_long_job_id": "job-id",
  "status": "completed",
  "mode": "synthesis_ideas",
  "shorts": [
    {
      "short_id": "short-01",
      "idea_id": "idea-01",
      "idea_type": "synthesis",
      "format": "mistake_list",
      "status": "rendered",
      "rendered": true,
      "qa_verdict": "PASS",
      "source_scene_ids": ["scene-04", "scene-11"],
      "video_path": "shorts/short-01/short.mp4",
      "cover_path": "shorts/short-01/short_cover.jpg"
    }
  ]
}
```

### 20.1.1 Manifest archive rules

For `force=true` rerenders, manifest must not leave active entries pointing to archived files.

Required:

```text
active manifest entries = current non-archived Shorts only
archived entries = moved to archived_shorts or marked status=archived
```

Rendered-card UI must filter:

```python
entry.get("status") != "archived" and entry.get("rendered") is True
```

When a new Short is rendered for the same `idea_id`, the active manifest should contain only the new Short for that `idea_id`.


### 20.2 Run report

Use `studio_render_run.json` as the synthesis flow run report.

Do not use `autopilot_run.json` as the source of truth for this flow.

```json
{
  "source_long_job_id": "job-id",
  "mode": "synthesis_ideas",
  "generation_id": "ideas-20260602T120000Z",
  "status": "completed|completed_with_warnings|failed",
  "selected_idea_count": 2,
  "attempted_render_count": 2,
  "rendered_count": 2,
  "needs_review_count": 0,
  "failed_count": 0,
  "skipped_count": 0,
  "blocked_count": 0,
  "warnings": [],
  "errors": []
}
```

Do not count `needs_review` as `failed`.

The UI state endpoint may still read `shorts_manifest.json` for rendered card details, but run status must come from `studio_render_run.json` when present.

---

## 21. Backward compatibility

Existing legacy flow may remain:

```text
run_shorts_autopilot
planner.extract_candidates
candidate_scorer
contiguous candidates
ready_for_render
confirm-render
```

But the new Shorts Studio synthesis flow must not use contiguous excerpt planning.

Do not break existing tests for legacy autopilot unless intentionally updated.

If both systems exist:

```text
Legacy Autopilot = automatic excerpt-like flow
Shorts Studio = user-selected synthesis ideas flow
```

---

## 22. Implementation phases

### Phase 1 — Idea generation core

Add:

```text
idea_generator.py
idea_prompts.py
idea_scorer.py
idea_store.py
```

Implement:

- build full narration source
- ChatGPT prompt
- validate/score ideas
- write/read artifacts

Tests:

- full scene extraction preserves IDs/order
- prompt includes scene blocks
- invalid scene IDs are rejected
- duplicate ideas removed
- scores clamped/recomputed
- only `idea_type=synthesis` accepted

### Phase 2 — Render selected ideas

Implement:

- idea_to_short_plan
- render_selected_short_ideas
- pass source_artifacts into short_script_builder
- source map synthesis metadata
- manifest/run reporting

Tests:

- selected idea becomes compatible short_plan
- non-contiguous scene IDs render path accepted
- build_short called with `require_render_confirmation=False`
- no `ready_for_render`
- selected idea writes `short_idea.json`
- source map includes all source scenes

### Phase 3 — Web API

Implement:

- generate ideas endpoint
- list ideas endpoint
- render selected ideas endpoint
- state includes idea counts/status
- locks

Tests:

- generate ideas requires eligible rendered long job
- render selected requires existing ideas
- render selected rejects invalid idea IDs
- busy guard returns 409
- no confirm-render endpoint required for this flow

### Phase 4 — UI

Implement:

- Shorts Studio tab
- idea cards
- selection state
- render selected button
- rendered results panel

Tests:

- UI shows ideas
- UI selects/unselects ideas
- no Confirm Render button appears
- render selected posts selected idea IDs

### Phase 5 — Regression

Verify:

- legacy autopilot still works
- existing rendered Shorts summary still displays
- synthesis flow works with mocked LLM/render
- no live browser/Remotion in unit tests

---

## 23. Tests checklist

Add tests for:

1. `build_long_narration_source` preserves scene IDs.
2. Empty narration scenes are skipped.
3. Long source truncation preserves complete scenes.
4. `short_ideas_prompt` includes scene blocks.
5. Prompt forbids raw excerpts/contiguous clips.
6. Parser accepts valid ChatGPT ideas JSON.
7. Validator rejects non-synthesis idea types.
8. Validator rejects invalid source scene IDs.
9. Validator dedupes source scene IDs.
10. Validator removes duplicate ideas.
11. Scores are clamped 0–100.
12. Overall score is recomputed.
13. Non-contiguous source scene bonus works.
14. Idea store writes and reads `short_ideas.json`.
15. Idea selection writes `selected_short_ideas.json`.
16. `idea_to_short_plan` includes `candidate_type=synthesis`.
17. `idea_to_short_plan` keeps non-contiguous scene IDs.
18. `render_selected_short_ideas` calls `build_short` with `require_render_confirmation=False`.
19. Render selected flow does not produce `ready_for_render`.
20. `short_idea.json` is written.
21. Synthesis source map includes `idea_id`.
22. Synthesis source map includes all selected source scenes.
23. Manifest includes `idea_id` and `idea_type`.
24. `needs_review_count` is separate from `failed_count`.
25. Generate ideas API requires eligible long job.
26. Generate ideas API writes run report.
27. List ideas API returns ideas.
28. Render selected API rejects invalid idea IDs.
29. Render selected API requires system idle.
30. UI has no Confirm Render button.
31. Legacy autopilot tests still pass.
32. Short ID allocation appends after existing `short-*` directories.
33. Re-render with `force=false` skips already rendered `idea_id`.
34. Re-render with `force=true` archives prior short directories before regenerating.
35. `studio_render_run.json` is written and used as synthesis run source of truth.
36. State derivation ignores legacy `ready_for_render` for synthesis flow.
37. Eligibility resolver accepts root/json/outputs layouts.
38. Validation duplicate detection uses deterministic title/hook/source thresholds.
39. `covers_distinct_parts` uses explicit span ratio threshold.
40. Curiosity bonus uses explicit phrase list or number.
41. Source artifact payload for script generation is capped at 8000 chars.
42. Truncated long narration writes `source_truncated_for_idea_generation` warning.
43. State vocabulary contains no `rendered` job-level status.
44. New idea generation archives or marks stale prior `studio_render_run.json`.
45. Current render run is ignored when generation_id does not match current `short_ideas.json`.
46. All-skipped render selected request returns `failed` with `no_new_shorts_rendered`.
47. Partially skipped render selected request returns `completed_with_warnings`.
48. Skipped ideas increment `skipped_count` but not `rendered_count` or `failed_count`.
49. `build_short(...)` accepts `source_artifacts=None` without breaking legacy callers.
50. Synthesis render passes compact `source_artifacts` into `build_short(...)`.
51. Failed new idea-generation run with old `short_ideas.json` returns state `failed`.
52. `ideas_ready` run without matching `short_ideas.generation_id` returns `failed`.
53. Queue command `shorts_generate_ideas` maps to `ideas_generating` before `.ideas.lock` exists.
54. Queue command `shorts_render_selected_ideas` maps to `rendering_selected` before `.render-selected.lock` exists.
55. `force=true` archive updates manifest so active entries do not point to archived files.
56. Rendered-card UI ignores manifest entries with `status=archived`.
57. Force rerender for same `idea_id` leaves only one active manifest entry for that idea.




---

## 24. Acceptance criteria

Complete when:

1. Shorts Studio generates ideas from full long-form narration, not contiguous candidates.
2. Every idea has `idea_type=synthesis`.
3. Every idea has valid `source_scene_ids`.
4. Ideas can use non-contiguous scenes.
5. UI shows scored idea cards before rendering.
6. User can select one or more ideas.
7. Selected ideas render immediately after user starts render.
8. There is no Confirm Render step.
9. There is no `ready_for_render` state in synthesis flow.
10. Rendered Shorts include source map back to original scenes.
11. Source map supports non-contiguous scenes.
12. QA checks source fidelity for synthesis ideas.
13. Manifest includes `idea_id` and `idea_type`.
14. `needs_review` is reported separately from technical failure.
15. Legacy autopilot remains backward compatible.
16. Short ID allocation is append-only by default and never overwrites existing short dirs.
17. `force=true` archives prior Shorts for selected ideas before regenerating.
18. `studio_render_run.json` is the synthesis flow run status source of truth.
19. State endpoint derives synthesis status without relying on `ready_for_render` or `autopilot_run.json`.
20. Eligibility supports root, `json/`, and `outputs/` job layouts.
21. Idea validation/scoring thresholds are deterministic and testable.
22. Selected idea script generation receives compact source artifacts with an 8000-character budget.
23. Truncated source generation is explicit and warned.
24. Job-level state vocabulary is exactly: `none`, `ideas_generating`, `ideas_ready`, `rendering_selected`, `completed`, `completed_with_warnings`, `failed`.
25. Starting a new idea-generation cycle invalidates stale `studio_render_run.json`.
26. `studio_render_run.json` is current only when its `generation_id` matches `short_ideas.generation_id`.
27. All-skipped selected render requests are reported as failed/no new output.
28. Partial-skip selected render requests are reported as completed_with_warnings.
29. `build_short(...)` has an optional `source_artifacts` parameter and remains backward compatible.
30. A newer failed idea-generation run overrides stale prior ideas/render state.
31. Queue command mapping shows `ideas_generating` / `rendering_selected` before locks exist.
32. Force archive updates manifest so UI never points to moved files.
33. Rendered-card UI ignores archived manifest entries.
34. Force rerender leaves only one active rendered manifest entry per `idea_id`.




---

## 25. Codex prompt

```text
Implement the Shorts Studio Synthesis Ideas Flow v1.4 described in docs/specs/shorts-studio-synthesis-ideas-flow-v1.4.md.

Requirements:
- New Shorts Studio flow must generate Short ideas from full long-form narration.
- Do not use contiguous excerpt candidate planning in this new flow.
- Do not implement a Confirm Render step.
- Do not use ready_for_render in this new flow.
- Add idea_generator.py, idea_prompts.py, idea_scorer.py, idea_store.py.
- Add artifacts: short_ideas.json, selected_short_ideas.json, idea_generation_run.json.
- ChatGPT should propose synthesis ideas only, grounded in source_scene_ids.
- UI must show idea cards with scores before rendering.
- User selects ideas, then system renders selected ideas immediately.
- Reuse existing build_short(...) pipeline with require_render_confirmation=False.
- Allocate short IDs append-only by default; never overwrite existing short directories.
- Use force=true to archive prior Shorts for selected ideas before regenerating.
- Add optional source_artifacts parameter to build_short(...) and pass selected idea/source scenes as compact source_artifacts to short_script_builder with an 8000-character budget.
- Source map must support non-contiguous scenes and include idea metadata.
- Manifest/run reporting must include idea_id, idea_type, rendered/needs_review/failed counts.
- Use studio_render_run.json as synthesis flow run status source of truth.
- State endpoint must use the unified synthesis status vocabulary only: none, ideas_generating, ideas_ready, rendering_selected, completed, completed_with_warnings, failed.
- State endpoint must not derive synthesis status from ready_for_render or autopilot_run.json.
- Starting a new idea-generation cycle must archive or mark stale prior studio_render_run.json.
- A failed newer idea-generation run must override stale prior short_ideas/render state.
- Treat all-skipped render requests as failed/no new output; partial skipped as completed_with_warnings.
- Queue command mapping must show shorts_generate_ideas as ideas_generating and shorts_render_selected_ideas as rendering_selected before lock files exist.
- force=true archive must update shorts_manifest.json so active entries never point to moved files.
- needs_review must not increment failed_count.
- Keep legacy autopilot backward compatible.
- Add unit tests with mocked LLM/render only.
```
```
