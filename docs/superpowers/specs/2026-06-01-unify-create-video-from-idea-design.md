# Design: Unify "Create Video from Idea" with Idea Generator

Date: 2026-06-01
Status: Approved (pending spec review)

## Problem

There are two overlapping features:

- **Idea Generator** (`POST /channels/{id}/ideas/generate`): input `seed_topics[]` +
  `count`; output a list of fully-formed idea objects (idea fields + keyword
  scores + bucket + duplicate flags), saved to `inputs/ideas/{id}/`. Does **not**
  create jobs.
- **Create video from idea** (two endpoints):
  - `POST /jobs/from-idea-title`: input `title_seed` + many duration/dup options →
    ChatGPT expands → validates → dup-check → **creates a job immediately**.
  - `POST /jobs/from-idea`: input a full idea dict → validates → dup-check →
    **creates a job**.

Pain points (confirmed with user):

1. The title path's output is a job-creation response, not an enriched idea like
   Idea Generator emits.
2. Input is rigid (full valid idea, or a title plus many knobs).
3. The two features duplicate each other.

## Goal

Feeding a **title** should behave exactly like Idea Generator producing **one**
idea: ChatGPT expands it into a full idea object, it is rendered in the same idea
card UI, saved alongside generated ideas, and the video is only created when the
user clicks **Create Job / Run Job** (the existing per-idea path).

## Decisions (locked)

- **Single endpoint**, input is **only `title_seed`**. Every other parameter
  (duration mode/bounds, notes, duplicate policy) is hidden and defaulted, or left
  for ChatGPT to infer from the title.
- **Enrichment level: idea fields + duplicate-check only.** No keyword scoring
  (no browser-extension session). Score-related fields are left empty/null; the
  existing per-idea `scoreOneIdea` button can score on demand later.
- **Save** the produced idea to `inputs/ideas/{channel_id}/` like generated ideas.
- **Remove** the immediate job-from-title path entirely. Jobs are created only via
  `POST /jobs/from-idea` (the Run Job button on an idea card).

## Architecture

### New endpoint

`POST /channels/{channel_id}/ideas/from-title`

Request body:

```json
{ "title_seed": "string (10-160 chars)" }
```

Behavior:

1. Validate `title_seed` (10–160 chars), validate channel exists.
2. Load `published_videos` for the channel; pass their titles to the expansion
   prompt (avoid already-published angles) and reuse them for duplicate detection
   — same as `generate`.
3. Call `expand_title_to_idea(...)` with **hidden defaults**:
   - `duration_mode="auto"`, `min_duration_sec=360`, `max_duration_sec=1200`,
     `target_duration_sec=None`.
   - `duplicate_policy="warn_only"` (flag, never block, no rewrite loop).
   - `notes=None`.
   - Internal validation retries (`max_attempts`) kept as today so a malformed
     ChatGPT response is retried.
4. Run the same duplicate detection Idea Generator uses (`find_duplicate` against
   published videos); set `is_duplicate` / `duplicate_of` on the idea. Duplicates
   are flagged, **not** blocked (idea-only, no job yet).
5. Leave keyword-score fields absent/null (`keyword_source_score`,
   `keyword_final_score`, `bucket`, `intent_strength`, etc.) so the card renders
   the same shape with an empty score block.
6. Save the idea via `save_ideas(...)` to `inputs/ideas/{channel_id}/`.
7. **Return one element matching Idea Generator's `ideas[]` shape**, plus the saved
   relative path. No job is created.

Response:

```json
{
  "channel_id": "vida-plena-45",
  "idea": { "...same shape as one element of generate's ideas[] ..." },
  "saved": "ideas/vida-plena-45/<file>.json",
  "is_duplicate": false,
  "duplicate_of": null
}
```

### Removals

- Delete `POST /jobs/from-idea-title` route.
- Delete `create_job_from_title_seed` from `services/video_job_creator.py`.
- Keep `expand_title_to_idea` (reused by the new endpoint).
- Keep `POST /jobs/from-idea` and `create_job_from_full_idea` unchanged — the sole
  job-creation path.

### UI (`dashboard.html`)

- Modal "Create video from idea" reduces to a **single title input** (+ submit).
  Remove duration mode/target/min/max controls, notes, and the run-now button.
- `submitIdeaTitleJob` → rename/retarget to call `POST /ideas/from-title`.
  On success, render the returned idea as a card in the ideas panel (reuse
  `renderIdeaCard`) and refresh saved ideas so it persists.
- The card's existing **Run Job** button (`createJobFromIdea` → `/jobs/from-idea`)
  remains the only way to create the video.

## Data flow

```
title_seed
  → /channels/{id}/ideas/from-title
      → expand_title_to_idea (ChatGPT, hidden defaults)
      → find_duplicate vs published_videos  (flag only)
      → save_ideas → inputs/ideas/{id}/
      → return idea (generate ideas[] shape, score fields null)
  → renderIdeaCard (same UI as Idea Generator)
  → [user clicks Run Job]
      → /jobs/from-idea (create_job_from_full_idea) → job + enqueue
```

## Error handling

- Invalid `title_seed` → 400 `invalid_title_seed`.
- Unknown channel → 404.
- ChatGPT unavailable → 502 `chatgpt_unavailable`.
- Expansion fails validation after retries → 422 `idea_expansion_failed` with
  `last_validation_error`.
- Duplicates are **not** an error here — flagged on the idea, surfaced in the card.

## Testing

- Update existing tests that hit `/jobs/from-idea-title` → point at
  `/channels/{id}/ideas/from-title`; assert it returns an idea and **does not**
  create a job dir.
- New tests:
  - from-title returns an idea whose keys match a `generate` idea element (minus
    score fields, which are null/absent).
  - Hidden defaults applied (auto duration within 360–1200, duration_reason set).
  - Duplicate title is flagged (`is_duplicate=true`) but still returned and saved.
  - Idea saved under `inputs/ideas/{channel_id}/`.
  - No job directory created.
- Remove `create_job_from_title_seed` tests; keep `create_job_from_full_idea`
  coverage intact.

## Out of scope (YAGNI)

- Keyword scoring on the from-title path (available on demand via existing button).
- Batch from-title (multiple titles at once).
- Changing the Idea Generator (`generate`) endpoint itself.
