# Shorts multi-idea sequential render batch

Status: implementation-ready  
Date: 2026-07-13  
Owner: Shorts Studio  
Source of truth: this specification and `tests/test_shorts_render_batch.py` plus `tests/test_shorts_studio_batch_ui.py`

## 1. Problem

Shorts Studio already accepts `idea_ids[]` at the API and the narrated and
infographic builders iterate them sequentially. The browser UI, however, sends
only one idea ID per click. There is also no durable batch-level state, so the
operator cannot answer:

- which selected idea is rendering now;
- which item number it is in the batch;
- how many are complete, failed, or still waiting;
- whether a worker restart will repeat completed work.

The feature must let an operator select several eligible ideas, start one
ordered render batch, and follow that batch until it reaches a terminal state.

## 2. Product outcome

From the Ideas tab, the operator can select multiple unrendered ideas and click
one `Render selected (N)` action. The existing single worker renders one idea at
a time in selection order. A persistent progress panel shows, for example:

> Rendering 2 of 5 — idea-04 · 3 remaining

Completed items remain completed across refreshes and worker restarts. One
failed idea is visible but does not discard or block the remaining selected
ideas.

## 3. Scope

### In scope

- Multi-select controls in `src/video_agent/web/shorts_studio.html`.
- Ordered batch submission through the existing render endpoint.
- A durable, atomically written batch document under the parent long job.
- Batch progress API and UI polling.
- Sequential execution for both `infographic` and `narrated` short types.
- Continue-after-item-failure, explicit-stop cancellation, restart recovery,
  and idempotent resume.
- Backward compatibility for a one-item selection and existing queue commands.

### Out of scope

- Parallel Short rendering.
- Multiple simultaneously active render batches.
- Reordering a batch after it starts.
- Cross-parent-job batches.
- Scheduling, prioritization, ETA prediction, Telegram changes, or upload.
- Changes to Short content generation, visual quality, CTA, music, thumbnails,
  long-form pipeline, or Remotion concurrency.

`render.concurrency` remains `"auto"`; this feature must not add or change a
Remotion concurrency argument.

## 4. Existing patterns to preserve

- Queue entry point: `JobQueue.enqueue()` in
  `src/video_agent/orchestrator/queue.py`.
- API endpoint: `POST /shorts-studio/jobs/{job_id}/ideas/render` in
  `src/video_agent/web/routes/shorts_studio.py`.
- Sequential narrated loop:
  `video_agent.shorts.synthesis.render_selected_short_ideas()`.
- Sequential infographic loop:
  `video_agent.shorts.infographic.build.render_selected_infographic_ideas()`.
- Atomic JSON writes: `video_agent.storage.atomic.atomic_write_json()`.
- Canonical Shorts paths: `src/video_agent/shorts/paths.py`.
- The queue remains one row per parent `job_id`. A batch is one queue command
  whose payload contains the ordered idea IDs; do not create concurrent queue
  rows per idea.

## 5. UX contract

### 5.1 Idea selection

Each unrendered idea card has a checkbox. Rendered/in-progress ideas cannot be
selected. The Ideas toolbar contains:

- `Select all eligible` / `Clear selection`;
- selected count;
- one primary `Render selected (N)` button;
- the existing per-card single render action may remain, but it must call the
  same batch endpoint and produce the same one-item batch contract.

Selection order is the order in which IDs are sent. `Select all` uses the
current visible idea order. Duplicate IDs are never submitted.

### 5.2 Batch progress panel

The panel is visible while a batch is `queued`, `running`, `completed`,
`completed_with_errors`, `failed`, or `cancelled`. It displays:

- short type;
- status;
- `current_position` and `total_count`;
- current idea title and `idea_id` when running;
- `completed_count`, `failed_count`, and `remaining_count`;
- an ordered item list with status chips;
- a concise error beside a failed item;
- a terminal summary after completion.

The panel refreshes through the existing Shorts Studio poll loop. It must not
reset selected checkboxes merely because an unrelated poll response arrives.
All dynamic text is passed through the existing HTML escaping helper.

## 6. API contract

### 6.1 Start batch

Keep the endpoint:

`POST /shorts-studio/jobs/{job_id}/ideas/render`

Request:

```json
{
  "idea_ids": ["idea-03", "idea-01", "idea-08"],
  "force": false,
  "short_type": "infographic"
}
```

Rules:

- one to 20 IDs;
- IDs are non-empty, unique, and must exist in `short_ideas.json`;
- preserve request order;
- `short_type` is normalized to `infographic` or `narrated` only;
- reject a new request with `409 active_render_batch` while the same parent job
  has a non-terminal batch;
- retain the existing duplicate-render guard unless `force=true`.

Before enqueueing, persist the batch. If enqueueing fails or returns false,
mark it `failed` with `enqueue_failed`; never return a phantom queued batch.

Successful response is HTTP 202:

```json
{
  "status": "enqueued",
  "command": "shorts_render_infographic",
  "job_id": "parent-job",
  "batch_id": "srb-...",
  "idea_ids": ["idea-03", "idea-01", "idea-08"],
  "total_count": 3,
  "remaining_count": 3
}
```

Queue payload adds `batch_id` and preserves the ordered `idea_ids`.

### 6.2 Read progress

Add:

`GET /shorts-studio/jobs/{job_id}/ideas/render-batch`

When no batch exists, return HTTP 200 with a stable idle snapshot:

```json
{"status":"idle","total_count":0,"completed_count":0,"failed_count":0,"remaining_count":0,"items":[]}
```

When a batch exists, return the persisted document. Do not infer progress only
from directory order or the manifest because both lag during an in-flight item.

## 7. Durable batch document

Canonical path: `shorts/render_batch.json`, exposed by a new helper in
`src/video_agent/shorts/paths.py`. Writes must be atomic.

Schema `shorts_render_batch.v1`:

```json
{
  "schema_version": "shorts_render_batch.v1",
  "batch_id": "srb-20260713T010203Z-a1b2c3",
  "source_long_job_id": "parent-job",
  "generation_id": "ideas-...",
  "short_type": "infographic",
  "force": false,
  "status": "running",
  "created_at": "...",
  "started_at": "...",
  "updated_at": "...",
  "completed_at": null,
  "current_idea_id": "idea-01",
  "current_position": 2,
  "total_count": 3,
  "completed_count": 1,
  "failed_count": 0,
  "remaining_count": 2,
  "items": [
    {"position":1,"idea_id":"idea-03","title":"...","status":"completed","short_id":"short-...","error":null,"started_at":"...","completed_at":"..."},
    {"position":2,"idea_id":"idea-01","title":"...","status":"running","short_id":null,"error":null,"started_at":"...","completed_at":null},
    {"position":3,"idea_id":"idea-08","title":"...","status":"pending","short_id":null,"error":null,"started_at":null,"completed_at":null}
  ]
}
```

Allowed batch states: `queued`, `running`, `completed`,
`completed_with_errors`, `failed`, `cancelled`.

Allowed item states: `pending`, `running`, `completed`, `failed`, `cancelled`.

Derived-count invariants:

- `total_count == len(items)`;
- `completed_count == count(items.status == completed)`;
- `failed_count == count(items.status == failed)`;
- `remaining_count == count(items.status in {pending, running})`;
- positions are contiguous and one-based;
- at most one item is `running`.

## 8. Execution and recovery semantics

1. Worker receives the existing queue command and `batch_id`.
2. It verifies that the persisted batch ID and ordered payload agree. Mismatch
   fails closed; it must not render untracked ideas.
3. Before each item, atomically transition that item to `running`, set current
   fields, and emit a progress event.
4. The renderer completes one item before starting the next. Never overlap
   calls to `build_short`/`run_infographic_short`.
5. On success, persist `short_id` and transition the item to `completed`.
6. On ordinary item failure, persist a bounded error string, transition the
   item to `failed`, then continue to the next pending item.
7. On explicit stop, mark the current and all pending items `cancelled`, mark
   the batch `cancelled`, and stop immediately.
8. At terminal completion, status is:
   - `completed` when every item completed;
   - `completed_with_errors` when at least one completed and at least one failed;
   - `failed` when none completed and at least one failed.

### Restart/resume

On queue retry or stale-job recovery:

- completed and failed items are not rendered again;
- a stale `running` item becomes `pending` and is retried;
- execution resumes with the first remaining item in original order;
- `force=true` controls replacement of work that existed before batch creation;
  it does not cause already completed items in this same batch to repeat.

## 9. Module/interface decisions

Add `src/video_agent/shorts/render_batch.py` as the single owner of batch
schema, validation, atomic persistence, derived counts, lifecycle transitions,
and recovery. Renderers receive an optional progress callback or tracker so
existing direct callers remain source-compatible. Do not duplicate batch JSON
mutation logic in the route, worker, and two render loops.

The callback event contract is deterministic and shared by both render modes:

```text
item_started(idea_id, position, total)
item_completed(idea_id, short_id, position, total)
item_failed(idea_id, error, position, total)
batch_finished(status, counts)
```

Exact Python representation is an implementation choice; event meaning and
ordering are required.

## 10. Acceptance criteria

- **AC1:** UI can select multiple eligible ideas and submit one ordered request.
- **AC2:** Render button shows selected count and is disabled at zero selection.
- **AC3:** API validates uniqueness, existence, count, and short type; request
  order is preserved in response, queue payload, and batch items.
- **AC4:** Batch state is written atomically before enqueue and follows the v1
  schema/count invariants.
- **AC5:** Both narrated and infographic modes execute at most one selected idea
  at a time and preserve order.
- **AC6:** GET progress and UI show current item, position/total, completed,
  failed, and remaining counts.
- **AC7:** Ordinary item failure is recorded and later items continue.
- **AC8:** Explicit stop cancels current/pending items and prevents later work.
- **AC9:** Restart recovery retries only the interrupted item and never repeats
  completed items from the same batch.
- **AC10:** A one-ID request keeps existing behavior and produces a valid
  one-item batch.
- **AC11:** Existing duplicate-render and global busy guards remain effective.
- **AC12:** All dynamic UI content is escaped; path validation remains inside
  the parent job; batch errors are bounded and contain no secrets.
- **AC13:** No changes to render quality settings or `render.concurrency`.

## 11. Test plan

### `tests/test_shorts_render_batch.py`

- create document and preserve ordered items;
- reject duplicate/empty selections;
- lifecycle transitions maintain count invariants and one running item;
- ordinary failure continues and terminal status is `completed_with_errors`;
- recovery resets only stale running item and skips completed items;
- progress callbacks for narrated and infographic loops are sequential and
  ordered;
- infographic loop continues after an item exception;
- stop/cancel produces no event for later pending items.

### `tests/test_shorts_studio_batch_ui.py`

- POST three IDs persists a queued batch and enqueues one command with matching
  `batch_id` and ordered IDs;
- invalid/duplicate IDs and invalid short type are rejected;
- active non-terminal batch rejects a second POST;
- GET returns idle or the durable snapshot;
- one-ID request is backward compatible;
- HTML contains multi-select, select-all, selected-count, batch button, progress
  panel, current/remaining counters, and polls the batch endpoint;
- UI payload uses the selected ID array rather than wrapping one `ideaId`.

## 12. Verification commands

Run with the root virtual environment from the worktree:

```bash
PYTHONPATH=src /Volumes/DATA/YBT-Studio/Youtube-AI-Agent/.venv/bin/python -m pytest -q \
  tests/test_shorts_render_batch.py \
  tests/test_shorts_studio_batch_ui.py \
  tests/test_shorts_studio.py \
  tests/test_shorts_api.py \
  tests/test_queue.py \
  tests/test_shorts_synthesis.py \
  tests/shorts_build/infographic/test_build.py
```

Also run Ruff on touched Python files and `compileall`. UI verification must use
the real Shorts Studio page in a browser at desktop and narrow/mobile widths;
source-string checks alone are not sufficient for final acceptance.

## 13. Implementation sequence

1. Add canonical path and batch domain module with unit tests.
2. Add lifecycle/progress hooks to both sequential render loops.
3. Wire route creation, GET progress, worker validation/resume, and queue payload.
4. Add multi-select and progress UI using the existing polling loop.
5. Run focused and regression suites, then exercise a three-item fake/local
   batch to prove ordering and recovery before any expensive real render.

