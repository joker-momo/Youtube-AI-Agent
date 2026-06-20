# Shorts Rerender Entry-Point Discovery Audit

> Spec: *Vida Plena 45+ Shorts Visual Span, Compiled Visual Timeline & Quality-First Render Handoff v3.2.3*, §23A.
> Status: **PR A deliverable / PR B prerequisite.** Completed before any Phase 2 code change.
> Verified against repository at branch `feat/visual-spans-pr-a` (base `main`, commit `8237066`).

## 1. Why this audit exists

Phase 2 introduces a **prepared-short** render path: every entry point that renders an
*already-prepared* Short must converge on one shared final-props builder
(`build_prepared_short_render_props`) and must **not** re-run `prepare_assets(...)`,
re-acquire backgrounds, regenerate TTS, or rebuild `json/render_props.json` in a way that
drops the compiled `visual_schedule`.

Today there are **two** `json/render_props.json` writers and **one** `prepare_assets(...)`
re-run that fires *after* the Short builder has already finished assets + audio. Both must be
accounted for before PR B touches the render path.

## 2. Verified shared funnel (call graph)

Every Short render and rerender path converges on the same two functions:

```text
user / UI / worker / CLI action
  → enqueue_short_render | _run_short_render_job | render_selected_short_ideas | run_shorts_autopilot
    → render_short_video(short_dir, channel_config, …)        [shorts/renderer.py:132]
      → materialize_short_job_aliases(short_dir, …)           [shorts/renderer.py:25]
          • writes json/render_props.json  ← from short_render_props.json   (WRITER #1)
      → render_operator_job(OperatorRenderOptions(…))         [pipeline.py:763]
          • is_short_job branch                               [pipeline.py:798]
          • prepare_assets(…) RE-RUN                          [pipeline.py:825]   ⚠
          • rebuilds json/render_props.json (no visual_schedule) [pipeline.py:850-860] (WRITER #2) ⚠
          • duration snapshot/restore hack                    [pipeline.py:799/813/835]
        → render_with_remotion(…)                             [pipeline.py:869]
```

`render_short_video` is the single Short-render chokepoint. `render_operator_job` is shared with
long-form (CLI + `render_review`); the short-specific behavior is gated by `_is_short_job_dir(...)`
(pipeline.py:728).

## 3. Entry-point inventory

| # | Entry-point | File & symbol | Caller / trigger | Current props writer | Re-runs `prepare_assets`? | Can regen TTS? | Current handoff behavior | Target prepared-short behavior | Shared helper call | Required test | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Initial builder render | `shorts/builder/defaults.py:45` → `render_short_video` | `short_builder._stage_render` after a fresh build | materialize (W#1) + `render_operator_job` rebuild (W#2) | **yes** (pipeline.py:825) | no (TTS already done) | builder writes `short_render_props.json` (`_write_render_props`, short_builder.py:605/625) | route `render_operator_job(prepared_short=True)`; build final props once via shared helper; embed/omit schedule per mode | `build_prepared_short_render_props` | `initial render uses prepared-short mode` | **TODO PR B** |
| 2 | Render-only rebuild (`shorts_render_one`) | `orchestrator/worker.py:353 _run_short_render_job` → `render_short_video` (worker.py:498) | queue cmd `shorts_render_one` | W#1 + W#2 | **yes** | no | no schedule preserved | same as #1; must NOT re-acquire assets / regen TTS | `build_prepared_short_render_props` | `existing-Short single render uses prepared-short mode`; `rerender does not call prepare_assets` | **TODO PR B** |
| 3 | API render endpoint | `web/routes/shorts.py:180 post_render` → `enqueue_short_render` (shorts.py:78, cmd `shorts_render_one`) | `POST /jobs/{job_id}/shorts/{short_id}/render` | (via #2) | via #2 | no | (via #2) | (via #2) | (via #2) | `render endpoint enqueues prepared-short render` | **TODO PR B** |
| 4 | Render-confirmation flow | `orchestrator/worker.py:554 _run_shorts_confirm_render_job` → `run_shorts_autopilot(require_render_confirmation=True)` (worker.py:264) → build → `render_short_video` | queue cmd `shorts_confirm_render`; enqueued `web/routes/shorts_studio.py:806` | W#1 + W#2 | **yes** | yes (full build path) | builder writes handoff | same shared helper after (re)build; same schedule hash as direct render | `build_prepared_short_render_props` | `render-confirmation and queued rerender produce the same final props` | **TODO PR B** |
| 5 | Render-selected ideas | `orchestrator/worker.py:291 _run_shorts_render_selected_ideas_job` → `render_selected_short_ideas` (worker.py:344) → build → `render_short_video` | queue cmd `shorts_render_selected_ideas`; enqueued `shorts_studio.py:733` | W#1 + W#2 | **yes** | yes (full build path) | builder writes handoff | same shared helper after build | `build_prepared_short_render_props` | `queued/render-selected flow uses prepared-short mode` | **TODO PR B** |
| 6 | Retry after render/encoder failure | `orchestrator/worker.py:632 mark_retry` (re-dispatch of the same queue cmd) | `_is_retryable_exception` requeue | (re-dispatch of #2/#4/#5) | same as re-dispatched cmd | same | reuse same final inputs | retry must reuse identical final props (no asset/TTS rebuild) | `build_prepared_short_render_props` | `retry reuses the same final inputs` | **TODO PR B** |
| 7 | CLI operator render | `cli.py:217 render_operator_job(...)` | `video_agent.cli` operator command | W#2 | yes (long-form or short) | no | long-form path | if pointed at a Short dir, must use prepared-short; long-form unchanged | `build_prepared_short_render_props` (short only) | `CLI short render uses prepared-short mode` | **TODO PR B** |
| 8 | Long-form render_review | `orchestrator/stages/render_review.py:43 render_operator_job(...)` | long-form pipeline render stage | W#2 | yes (long-form) | no | long-form path | **unchanged** — not a Short; `_is_short_job_dir` False | none (legacy) | `legacy long-form render unchanged` | **N/A (legacy preserved)** |
| 9 | Alias materializer | `shorts/renderer.py:25 materialize_short_job_aliases` (W#1) | called by `render_short_video` | json/render_props.json from short_render_props.json | no | no | copies handoff → render_props.json (legacy shape) | must NOT be final owner; must not strip/rewrite the Short handoff schedule | (feeds shared helper) | `alias materialization does not strip handoff` | **TODO PR B** |

## 4. `render_props.json` writers (must converge in PR B)

| Writer | Location | Role today | PR B target |
|---|---|---|---|
| W#1 `materialize_short_job_aliases` | `shorts/renderer.py:85` | copies `short_render_props.json` → `json/render_props.json` (no schedule) | keep materializing `script/scenes/seo`; stop being final `render_props.json` owner; preserve handoff |
| W#2 `render_operator_job` | `pipeline.py:860` | rebuilds `json/render_props.json` (no `visual_schedule`) after re-running `prepare_assets` | prepared-short branch builds final props **once** via shared helper and merges validated schedule |

## 5. `prepare_assets(...)` call sites

| Site | Location | Short-relevant? | PR B action |
|---|---|---|---|
| Short TTS stage | `shorts/audio.py:67` | yes (TTS) | unchanged (pre-handoff) |
| Short background stage | `shorts/audio.py:84` | yes (backgrounds) | unchanged (pre-handoff) |
| **Render re-run** | `pipeline.py:825` | **yes — the problem** | **skip when `prepared_short=True`** |
| Long-form assets | `pipeline.py:547` | no | unchanged |
| Subprocess audio | `pipeline.py:715` | conditional | unchanged (audio already final) |
| Audio task provider | `audio_tasks.py:45` | no | unchanged |

## 6. `_write_render_props` (short handoff writer)

- Definition: `shorts/builder/render_props.py:12`.
- Call sites: `short_builder.py:605`, `short_builder.py:625`.
- Writes `json/short_render_props.json` (constant `paths.SHORT_RENDER_PROPS_FILE`).
- PR B: extend signature with `visual_schedule` + `scene_version`; write schedule hash + scene timing hash; keep existing call sites valid (new args default to `None`).

## 7. PR B completion checklist (every row resolved)

- [ ] All rows #1–#9 routed through `build_prepared_short_render_props` (except #8 long-form legacy).
- [ ] No unexplained direct final-props writer remains (W#1 demoted, W#2 prepared-short only).
- [ ] No existing-Short path bypasses the shared helper.
- [ ] `prepare_assets(...)` does not run after schedule compile in prepared-short mode.
- [ ] Tests cover every materially distinct call path (#1, #2/#3, #4, #5, #6, #7).
- [ ] If a new entry point is discovered during PR B, this audit is updated **before** the code change.

## 8. Discovery procedure (repository-wide searches run)

```text
grep -rn 'render_short_video('            src/   → defaults.py:45, worker.py:498
grep -rn 'render_operator_job('           src/   → cli.py:217, renderer.py:153, render_review.py:43
grep -rn 'materialize_short_job_aliases'  src/   → renderer.py:25/132 (def + call)
grep -rn 'short_render_props.json'        src/   → renderer.py:37, builder/render_props.py:46, pipeline.py:731
grep -rn 'render_props.json'              src/   → renderer.py:85, pipeline.py:860, contracts.py:12, …
grep -rn 'prepare_assets('                src/   → audio.py:67/84, pipeline.py:547/715/825, audio_tasks.py:45
grep -rn 'shorts_render_one|render_selected|confirm_render|retry' src/ → worker.py:92-99/632, shorts*.py
```

No additional Short render entry points were found beyond rows #1–#9.
