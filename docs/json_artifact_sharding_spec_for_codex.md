# JSON Artifact Sharding Spec — ChatGPT & Claude

## Purpose

Make all ChatGPT and Claude outputs safer, smaller, parseable, and recoverable by converting long model responses into **JSON artifact shards**.

The current pipeline already asks ChatGPT/Claude to return raw JSON. The problem is that long JSON responses can still be truncated, malformed, or incomplete.

This spec introduces:

```text
small JSON envelopes
→ saved as real .json files by Python
→ validated per shard
→ merged by Python into final artifacts
→ retried per failed shard instead of rerunning the whole stage
```

This applies to both:

```text
ChatGPT writing stages
Claude QA stages
```

Priority implementation:

```text
Phase 1: ChatGPT scenes sharding
Phase 2: Claude scenes QA sharding
Phase 3: optional script sharding
Phase 4: keep SEO single JSON unless needed
```

---

## Important Clarification

Do **not** rely on ChatGPT or Claude to create downloadable files.

The model should return exactly one JSON object per response. Python then writes that JSON object to disk as a `.json` file.

Correct flow:

```text
ChatGPT/Claude raw_response
→ parse JSON object
→ validate envelope
→ save to job folder
→ merge/validate final artifact
```

---

## Current Problem

The current scenes prompt asks ChatGPT to generate a large `scenes.json` in a single response, often 40–55 scenes.

That is fragile because:

```text
- response may be truncated
- closing } may be missing
- some scene fields may be skipped
- one bad scene invalidates the whole artifact
- retrying means regenerating everything
- Claude QA may miss issues when reviewing huge artifacts
```

Current continuation loops are useful as fallback, but sharding is a better design because it prevents overlong responses upfront.

---

## Design Principles

1. Every model response must be exactly one parseable JSON object.
2. Long artifacts must be split into small JSON shard files.
3. Python is responsible for writing files.
4. Python validates every shard before merge.
5. Python merges shards into the final canonical artifact.
6. Failed shards can be retried individually.
7. Final artifacts must remain backward-compatible with the existing pipeline.
8. The existing `script.json`, `scenes.json`, and `seo.json` final paths must not change.
9. Sharding should be implemented first for `scenes`, because scenes are the longest artifact.
10. SEO should remain single JSON for now.

---

## Standard JSON Envelope

Every ChatGPT/Claude response should use this envelope shape:

```json
{
  "artifact_type": "scenes_batch",
  "schema_version": "2026-05-json-shards-v1",
  "job_id": "example-job-id",
  "channel_id": "vida-plena-45",
  "status": "complete",
  "batch_index": 1,
  "batch_total": 6,
  "data": {},
  "next_batch_hint": "",
  "warnings": []
}
```

### Required envelope fields

| Field | Type | Required | Description |
|---|---:|---:|---|
| `artifact_type` | string | yes | Example: `scenes_plan`, `scenes_batch`, `scenes_qa_batch` |
| `schema_version` | string | yes | Use `2026-05-json-shards-v1` |
| `job_id` | string | yes | Must match current job |
| `channel_id` | string | yes | Must match channel |
| `status` | string | yes | `complete`, `partial`, or `error` |
| `data` | object | yes | Actual shard payload |
| `warnings` | array | yes | Non-blocking issues |
| `batch_index` | integer/null | optional | Required for batch artifacts |
| `batch_total` | integer/null | optional | Required for batch artifacts |
| `next_batch_hint` | string | optional | Used when `status="partial"` |

### Valid `status` values

```text
complete
partial
error
```

If the model cannot fit all requested content, it must return:

```json
{
  "status": "partial",
  "next_batch_hint": "Continue from scene-09"
}
```

Python should then request the missing content in a follow-up, rather than accepting incomplete JSON.

---

## File Layout

Save raw model JSON envelopes under the job folder.

Recommended paths:

```text
jobs/<job_id>/operator/chatgpt/scenes_plan.json
jobs/<job_id>/operator/chatgpt/scenes_batches/scenes_batch_01.json
jobs/<job_id>/operator/chatgpt/scenes_batches/scenes_batch_02.json
jobs/<job_id>/operator/chatgpt/scenes_batches/scenes_batch_03.json

jobs/<job_id>/operator/claude/scenes_qa_batches/scenes_qa_batch_01.json
jobs/<job_id>/operator/claude/scenes_qa_batches/scenes_qa_batch_02.json

jobs/<job_id>/scenes.json
jobs/<job_id>/operator/claude/scenes_qa.json
```

Final canonical artifacts remain:

```text
script.json
scenes.json
seo.json
operator/claude/script_qa.json
operator/claude/scenes_qa.json
operator/claude/seo_qa.json
```

---

# Phase 1 — ChatGPT Scenes Sharding

## Goal

Replace single-response scene generation with:

```text
scenes_plan.json
→ scenes_batch_01.json
→ scenes_batch_02.json
→ ...
→ Python merge
→ scenes.json
```

Do not change downstream stages. After merge, `scenes.json` must look like the existing final artifact and pass current validators.

---

## New Artifact Types

### 1. `scenes_plan`

ChatGPT returns a plan describing how the scenes will be split into batches.

Envelope:

```json
{
  "artifact_type": "scenes_plan",
  "schema_version": "2026-05-json-shards-v1",
  "job_id": "job-id",
  "channel_id": "vida-plena-45",
  "status": "complete",
  "data": {
    "target_scene_count": 48,
    "target_total_duration_sec": 840,
    "batch_size": 8,
    "batches": [
      {
        "batch_index": 1,
        "scene_start": "scene-01",
        "scene_end": "scene-08",
        "purpose": "Opening hook and first practical framing",
        "script_sections": ["section-01"]
      }
    ],
    "global_layout_strategy": {
      "first_scene": "hook",
      "last_scene": "cta",
      "pattern_break_frequency": "every 4-6 scenes",
      "default_layout": "subtitle"
    }
  },
  "warnings": []
}
```

### 2. `scenes_batch`

ChatGPT returns only a small batch of scenes.

Suggested batch size:

```text
6–8 scenes per response
```

Envelope:

```json
{
  "artifact_type": "scenes_batch",
  "schema_version": "2026-05-json-shards-v1",
  "job_id": "job-id",
  "channel_id": "vida-plena-45",
  "status": "complete",
  "batch_index": 1,
  "batch_total": 6,
  "data": {
    "scene_start": "scene-01",
    "scene_end": "scene-08",
    "scenes": [
      {
        "id": "scene-01",
        "duration_sec": 7,
        "narration": "...",
        "on_screen_text": "...",
        "caption": "...",
        "visual_prompt": "...",
        "motion": "slow_zoom",
        "asset_refs": {},
        "layout": "hook",
        "layout_payload": {
          "title": "NO ES TU EDAD",
          "body": "",
          "bullets": [],
          "cta": ""
        },
        "layout_reason": "Opening hook supported by the narration.",
        "planner_warnings": []
      }
    ]
  },
  "warnings": []
}
```

---

## ChatGPT Scenes Prompt Changes

File:

```text
src/video_agent/operator.py
```

Current function:

```python
_chatgpt_scenes_prompt()
```

Do not remove existing single-response support immediately. Add new shard prompt builders:

```python
def _chatgpt_scenes_plan_prompt(channel_config: dict, script: dict) -> str:
    ...

def _chatgpt_scenes_batch_prompt(
    channel_config: dict,
    script: dict,
    plan: dict,
    batch: dict,
    previous_batch_summary: str | None = None,
) -> str:
    ...
```

### Plan prompt rules

The plan prompt must ask for:

```text
- exactly one JSON envelope
- artifact_type = "scenes_plan"
- no markdown
- no commentary
- target scene count based on channel config
- batch size 6–8 scenes
- scene ranges must cover the full target scene count
- scene IDs must be sequential scene-01, scene-02, ...
- final batch must include the final scene
```

### Batch prompt rules

The batch prompt must ask for:

```text
- exactly one JSON envelope
- artifact_type = "scenes_batch"
- only the requested scene range
- no markdown
- no commentary
- every scene must include all required scene fields
- every scene must include layout fields if scene-retention layout system exists
- layout_payload must be supported by narration/caption/on_screen_text
- asset_refs must be {}
- visual_prompt must be English and stock-search friendly
- narration must follow the approved script section/context
- scene IDs must exactly match the requested range
```

---

## New Python Module

Create:

```text
src/video_agent/operator_shards.py
```

Suggested functions:

```python
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2026-05-json-shards-v1"

class ShardValidationError(ValueError):
    pass

def extract_json_envelope(raw_text: str) -> dict[str, Any]:
    ...

def validate_envelope(
    envelope: dict[str, Any],
    *,
    expected_artifact_type: str,
    expected_job_id: str,
    expected_channel_id: str,
) -> None:
    ...

def save_envelope(path: Path, envelope: dict[str, Any]) -> Path:
    ...

def load_envelope(path: Path) -> dict[str, Any]:
    ...

def validate_scenes_plan(plan_envelope: dict[str, Any]) -> None:
    ...

def validate_scenes_batch(
    batch_envelope: dict[str, Any],
    *,
    expected_batch_index: int,
    expected_batch_total: int,
    scene_start: str,
    scene_end: str,
) -> None:
    ...

def merge_scene_batches(
    *,
    job_id: str,
    channel_id: str,
    batch_envelopes: list[dict[str, Any]],
) -> dict[str, Any]:
    ...
```

### `extract_json_envelope`

Use the existing JSON extraction logic where possible.

Current repo already has:

```python
extract_json_object()
extract_json_objects()
```

Re-use or wrap these functions rather than duplicating parser logic.

### `validate_envelope`

Must check:

```text
artifact_type matches expected
schema_version exists
job_id matches current job
channel_id matches current channel
status is complete or partial or error
data is object
warnings is list
```

If `status="error"`:

```text
raise ShardValidationError
```

If `status="partial"`:

```text
raise ShardValidationError or trigger retry/follow-up logic
```

For Phase 1, it is acceptable to treat `partial` as invalid and require retry.

### `validate_scenes_batch`

Must check:

```text
batch_index correct
batch_total correct
data.scenes is list
scene count matches requested range
scene IDs sequential and exact
each scene has required old fields:
  id, duration_sec, narration, on_screen_text, caption, visual_prompt, motion, asset_refs
asset_refs is object
visual_prompt is non-empty
```

If layout fields are present, do not reject unless malformed. The scene retention planner/validator can handle layout safety separately.

### `merge_scene_batches`

Must return final canonical scenes artifact:

```json
{
  "channel_id": "vida-plena-45",
  "job_id": "job-id",
  "scenes": [],
  "total_duration_sec": 0,
  "qa": {
    "verdict": "PENDING_CLAUDE_QA"
  }
}
```

Rules:

```text
- concatenate batch scenes by batch_index
- verify scene IDs are sequential from scene-01
- reject duplicate scene IDs
- compute total_duration_sec from sum(scene.duration_sec)
- set qa.verdict = PENDING_CLAUDE_QA
```

After merge, run existing validation and existing operator scene validation.

---

## Stage Integration

Current auto flow for scenes eventually calls:

```python
auto_scenes_stage()
```

and uses a single model response.

Implement a new function:

```python
async def auto_scenes_stage_sharded(
    job_dir: Path,
    channel_path: Path,
    session_fn: SessionFn,
) -> Path:
    ...
```

Suggested flow:

```text
1. Ensure current_stage is scenes or scenes_promote.
2. Load script.json and channel config.
3. Build and send scenes_plan prompt.
4. Parse/validate/save scenes_plan.json.
5. For each batch in plan:
   a. Build batch prompt
   b. Send prompt
   c. Parse/validate/save batch envelope
   d. Retry once if invalid
6. Merge all batch envelopes into scenes.json.
7. Run normalize/layout planner if needed.
8. Validate final scenes.json with existing validators.
9. Complete scenes + scenes_promote stages appropriately.
```

Implementation options:

### Option A — Replace `auto_scenes_stage`

Replace the internal behavior of `auto_scenes_stage` with sharded generation.

### Option B — Add feature flag

Add config/env flag:

```text
SCENES_SHARDED_GENERATION=1
```

If enabled, use sharded generation. Otherwise fall back to current single-response logic.

Recommended for safer rollout:

```text
Option B
```

---

## Retry Rules

For each model response:

```text
parse failure → retry same prompt once
envelope validation failure → retry same prompt once
batch scene ID mismatch → retry same batch once
partial status → send continuation prompt or retry same batch
```

After retry fails:

```text
raise StageInputMissingError
```

Include a clear error message:

```text
Scenes batch 03 failed validation: expected scene-17..scene-24, got scene-18..scene-25
```

Do not silently continue with missing batches.

---

# Phase 2 — Claude Scenes QA Sharding

## Goal

Avoid asking Claude to review a huge `scenes.json` in one response.

Use:

```text
scenes_qa_batch_01.json
scenes_qa_batch_02.json
...
scenes_qa.json
```

---

## New Artifact Type: `scenes_qa_batch`

Envelope:

```json
{
  "artifact_type": "scenes_qa_batch",
  "schema_version": "2026-05-json-shards-v1",
  "job_id": "job-id",
  "channel_id": "vida-plena-45",
  "status": "complete",
  "batch_index": 1,
  "batch_total": 6,
  "data": {
    "verdict": "PASS",
    "youtube_policy": {
      "compliant": true,
      "risk_level": "none",
      "violations": []
    },
    "scene_checks": [
      {
        "scene_id": "scene-01",
        "verdict": "PASS",
        "issues": [],
        "required_changes": []
      }
    ],
    "issues": [],
    "required_changes": [],
    "scores": {
      "schema_fit": 5,
      "channel_fit": 5,
      "safety": 5,
      "clarity": 5,
      "youtube_policy": 5
    }
  },
  "warnings": []
}
```

---

## Claude QA Prompt Changes

Add new prompt builder:

```python
def _claude_scenes_qa_batch_prompt(
    channel_config: dict,
    scenes_batch: dict,
    batch_index: int,
    batch_total: int,
) -> str:
    ...
```

Rules:

```text
- exactly one JSON envelope
- artifact_type = scenes_qa_batch
- no markdown
- no commentary
- review only the given batch
- include scene_checks for every scene in the batch
- if any scene has policy/safety/schema issue, verdict = NEEDS_REWORK
- youtube_policy.compliant = false if there is any concern
```

---

## Merge Claude QA Batches

Add function:

```python
def merge_scenes_qa_batches(
    *,
    job_id: str,
    channel_id: str,
    qa_batch_envelopes: list[dict[str, Any]],
) -> dict[str, Any]:
    ...
```

Final `operator/claude/scenes_qa.json` shape should remain compatible with current QA promotion:

```json
{
  "artifact": "scenes",
  "verdict": "PASS",
  "youtube_policy": {
    "compliant": true,
    "risk_level": "none",
    "violations": []
  },
  "scores": {
    "schema_fit": 5,
    "channel_fit": 5,
    "safety": 5,
    "clarity": 5,
    "youtube_policy": 5
  },
  "issues": [],
  "required_changes": [],
  "batch_results": []
}
```

Merge rules:

```text
if any batch verdict != PASS → final verdict = NEEDS_REWORK
if any youtube_policy.compliant == false → final youtube_policy.compliant = false
risk_level = max risk among batches
issues = concatenated batch issues
required_changes = concatenated batch required_changes
scores = min score per category across batches
batch_results = compact references to all batch results
```

---

# Phase 3 — Optional Script Sharding

Only implement after scenes sharding is stable.

Potential split:

```text
script_outline.json
script_section_01.json
script_section_02.json
...
script.json
```

Do not implement unless needed.

---

# Phase 4 — Keep SEO Single JSON

SEO is short. Do not shard SEO unless it becomes unstable.

---

## Backward Compatibility

The following must continue working:

```text
existing single-response script generation
existing single-response scenes generation if feature flag disabled
existing SEO generation
existing render pipeline
existing validators
existing final artifact paths
```

Final merged `scenes.json` must be indistinguishable from a normal promoted scenes artifact for downstream stages.

---

## Tests

Add tests for new sharding utilities.

Suggested file:

```text
tests/test_operator_shards.py
```

Required tests:

1. Valid envelope parses and validates.
2. Envelope with wrong job_id fails.
3. Envelope with wrong artifact_type fails.
4. Partial status fails or triggers retry path.
5. Valid scenes plan validates.
6. Valid scenes batch validates.
7. Scenes batch with wrong scene range fails.
8. Scenes batch with duplicate scene IDs fails.
9. Merge scene batches produces final `scenes.json`.
10. Merge scene batches computes total_duration_sec.
11. Merge scene batches sets `qa.verdict = PENDING_CLAUDE_QA`.
12. Claude QA batch merge returns PASS when all batches pass.
13. Claude QA batch merge returns NEEDS_REWORK when any batch fails.
14. Claude QA batch merge aggregates issues and required_changes.
15. Existing non-sharded scenes path still works when feature flag is disabled.

---

## Acceptance Criteria

Implementation is complete when:

1. ChatGPT scenes generation can run in batch mode.
2. Each ChatGPT scenes batch is saved as a separate JSON file.
3. Invalid scene batches are rejected with clear errors.
4. Python merges all batches into canonical `scenes.json`.
5. Final `scenes.json` passes existing scene validators.
6. Claude scenes QA can run in batch mode.
7. Claude QA batches are merged into canonical `operator/claude/scenes_qa.json`.
8. Existing pipeline can still run without sharding.
9. No downstream render logic needs to know whether scenes came from one response or many.
10. Retry can rerun a failed batch without regenerating all scenes.

---

## Non-goals

Do not implement in this task:

```text
OpenAI API migration
Anthropic API migration
tool-calling
database migration
render template changes
subtitle/karaoke implementation
YouTube upload automation
analytics dashboard
```

---

## Final Target Flow

```text
ChatGPT scenes plan prompt
→ scenes_plan.json

For each planned batch:
  ChatGPT scenes batch prompt
  → scenes_batch_N.json
  → validate

Python:
  merge scenes batches
  → scenes.json
  → existing validators
  → scenes QA

Claude:
  QA each scenes batch
  → scenes_qa_batch_N.json

Python:
  merge QA batches
  → operator/claude/scenes_qa.json

Pipeline:
  seo
  thumbnail_image
  whisper_timestamps
  render
  review
```

This keeps model outputs small and JSON-safe while preserving the current canonical artifact structure.
