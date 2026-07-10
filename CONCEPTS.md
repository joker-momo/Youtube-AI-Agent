# CONCEPTS.md — Shared Domain Vocabulary

> The project's canonical glossary of domain terms. Read this before generating
> code so you ground in the team's vocabulary instead of inventing your own.
>
> **North star (see `CLAUDE.md` / `.wolf/cerebrum.md`):** every concept here
> exists to raise final video quality and audience fit. When a term's behavior
> trades quality for speed/cost, that is a violation, not an optimization.
>
> Grow this file via the OpenWolf maintenance rule (see `.wolf/OPENWOLF.md`):
> add a term the first time a real, non-obvious domain word surfaces in work.
> One entry = a short definition + where it lives. Do not bulk-dump the repo.

---

## Pipeline & orchestration

- **Pipeline** — the end-to-end long-form video build (script → assets → audio →
  render). Entry: `src/video_agent/pipeline.py`.
- **Operator** — workflow facade over a single video job: promotion / review /
  status glue. Entry: `src/video_agent/operator.py` (prompts in
  `operator_prompts.py`, validators in `operator_validators.py`, sharding in
  `operator_shards.py`).
- **Stage** — one discrete, promotable step in a job (e.g. scenes, SEO, audio,
  render). Lives under `src/video_agent/orchestrator/stages/`. A stage can be
  *run*, then *promoted* (accepted as the job's current truth), and some support
  an *auto* mode that runs + promotes in one pass.
- **Orchestrator** — coordinates stage order and job state.
  `src/video_agent/orchestrator/`.
- **Browser worker** — headless-Chrome driver layer used to drive web tools
  (e.g. ChatGPT image gen). `src/video_agent/browser_worker/`. Returns 503 when
  CDP is unreachable.

## Shorts (vertical short-form)

- **Short** — one vertical short-form video produced by the Shorts Autopilot,
  built standalone end-to-end. `src/video_agent/shorts/`.
- **Shorts Autopilot** — sequential runner that builds Shorts one after another.
  `src/video_agent/shorts/autopilot.py`.
- **short_builder** — builds ONE Short end to end: generate → QA (regen loop) →
  audio → mix → render. `src/video_agent/shorts/short_builder.py`.
- **Retention plan** — per-Short plan optimizing viewer retention / funnel.
  `src/video_agent/shorts/retention_plan.py`
  (`build_retention_plan`, `safe_topic`).
- **Candidate scorer** — scores Short candidates for standalone retention /
  funnel potential. `src/video_agent/shorts/candidate_scorer.py`.
- **Short SEO** — `short_seo.json`, the LLM-generated + normalized SEO payload
  for a Short. Built by `src/video_agent/shorts/short_seo_builder.py`.
- **Spain-first prompts** — the Shorts prompts are strict JSON-only and
  Spain-first by spec. `src/video_agent/shorts/prompts.py`.
- **Structured graphic intent** — a planning-only `graphic_*` scene (checklist,
  comparison, label callout, etc.) whose payload becomes a ChatGPT image brief.
  The asset stage must acquire `provider=ai_generated`, persist the scene as an
  image-backed `short_tip`, and fail if generation is unavailable. Remotion has
  no native graphic-card fallback.
- **Short music flow** — `music_selector.py` maps the Short pillar/topic to one
  of four canonical `music_library` tracks under `assets/music/`.
  `audio.py` produces dry narration; `audio_mixer.py` is the sole music mixer
  and writes `audio/short_mix.m4a`, `music_selection.json`, and the canonical
  `assets_manifest.audio` render reference.
- **Original procedural BGM** — a deterministic, math-synthesized instrumental
  bed keyed by a Short id. It uses no external audio input and writes an AAC
  output plus `json/original_bgm.json` provenance. The music-only infographic
  path opts into it through `shorts.infographic.music_source`.
  `src/video_agent/shorts/original_bgm.py`.

## Visual timeline (spec v3.2.3)

> Goal: a Short feels deliberately edited, not a slideshow. One strong native
> clip can cover several contiguous scenes without the playhead resetting at each
> scene boundary. PR A ships the report-only planner; PR B compiles + renders it.

- **Scene** — the editorial / narration / subtitle / layout / retention unit
  (owns `duration_sec`). Unchanged by the visual-timeline work.
- **Visual span** — planning/grouping sidecar over one or more **contiguous**
  scenes that can share one coherent continuous visual. Never owns an
  authoritative duration (derived from member scene frames).
  `src/video_agent/shorts/visual_spans.py`; artifact `visual_spans.json` +
  `visual_span_qa.json`. Built by stage
  `src/video_agent/shorts/builder/stages/visual_spans.py` (runs after anti-AI
  review, before background). Default mode **report_only** (no render change).
- **Visual beat** — optional subdivision inside one span (deferred; PR B/Phase 4).
- **Visual mode** — `continuous_clip`, `legacy_scene_assets`, `multi_clip`,
  `clip_plus_graphic`, `generated_image_fallback`. A graphic intent is rendered
  only through an acquired generated-image media track.
- **Structured evidence conflict** — a *hard* span split happens only when
  structured tag sets (`required_/forbidden_ evidence|subject|action`) intersect
  or explicit mode flags collide; free-form text is warning-only.
  `detect_structured_span_conflicts` (§11A).
- **Frame contract** — `seconds_to_frames` = `floor(s*fps + 0.5)` (JS
  `Math.round` parity, never Python `round()`). `src/video_agent/shorts/frames.py`.
- **Compiled asset schedule** *(PR B)* — schema-v2 deterministic frame-based
  visual timeline (`compiled_asset_schedule.json`); sole renderer source of truth.
- **Short render handoff** *(PR B)* — builder→render supplement
  `short_render_props.json`; NOT the final Remotion props.
- **Prepared-short render** *(PR B)* — `render_operator_job(prepared_short=True)`
  path: skip re-running `prepare_assets`/TTS, build final `render_props.json` once
  via the shared builder, embed the validated schedule. Rerender entry points
  inventoried in `docs/implementation/shorts_rerender_entrypoint_audit.md`.
- **Visual acquisition context** *(v4.0.3 PR C)* — metadata-only, report-only
  search contract for one validated visual span. Uses scene-plan duration buckets
  and structured tags, never final frame timing. Built by
  `src/video_agent/shorts/visual_acquisition.py`; artifact
  `visual_acquisition_context.json`.
- **Visual metadata QA** *(v4.0.3 PR C)* — capability-aware QA artifact for
  provider metadata search and provisional selections. It may record
  `CAPABILITY_REDUCED`, but never claims pixel/semantic validation or render
  eligibility. Built by
  `src/video_agent/shorts/builder/stages/visual_acquisition.py`; artifact
  `visual_span_metadata_qa.json`.
- **Visual local QA** *(v4.0.3 PR D)* — bounded finalist download plus
  deterministic local media analysis after final TTS/audio-tail timing. It
  promotes, replaces, or rejects PR C provisional selections and records
  capability-qualified candidate/span QA. Built by
  `src/video_agent/shorts/builder/stages/visual_local_qa.py`; artifact
  `visual_span_asset_qa.json`.
- **Trim window plan** *(v4.0.3 PR D)* — final frame-based source trim contract
  for locally validated visual-span assets. It is compiled into
  `compiled_asset_schedule.json` and keeps playback rate at 1.0 with loop policy
  `forbid`. Built by `src/video_agent/shorts/visual_local_analysis.py`; artifact
  `trim_window_plan.json`.
- **Visual beat plan** *(v4.0.3 PR E)* — bounded non-legacy plan selection over
  PR D-validated assets. Compares at most three `continuous_clip`, `two_clip`,
  and `clip_plus_graphic` plans, then chooses the simplest plan within the
  configured score margin. Built by
  `src/video_agent/shorts/visual_beat_planner.py`; artifact
  `visual_beat_plan.json`.
- **Visual sequence QA** *(v4.0.3 PR E)* — sequence-level QA over selected beats
  and expected tracks, not raw scene count. It records plan distribution,
  simplicity decisions, cut-count changes, and sequence warnings without PR F
  performance analytics. Built by
  `src/video_agent/shorts/visual_sequence_qa.py`; artifact
  `visual_sequence_qa.json`.
- **Visual performance features** *(v4.0.3 PR F)* — versioned report-only
  feature vector for completed Shorts. It joins available YouTube metrics by
  stable IDs, captures optional manual review, and records proof that no
  production weights/config changed automatically. Built by
  `src/video_agent/shorts/visual_performance.py`; artifact
  `visual_performance_features.json`.
- **Visual performance report** *(v4.0.3 PR F)* — offline report over visual
  feature samples. Findings must include sample count, date range, channel,
  caveats, and confounders, and must say insufficient evidence when sample or
  metric coverage is too small. Built by
  `src/video_agent/shorts/visual_performance.py`; artifact
  `visual_performance_report.json`.

## Quality gates (QA)

- **QA dual gate** — Shorts QA runs two gates: a deterministic/structural gate
  AND an LLM gate. Deterministically-valid output must NOT be hard-blocked by the
  LLM gate (see git history `fix(shorts-qa)`). Vocabulary lives across
  `src/video_agent/shorts/` QA modules and `src/video_agent/qa/`.
- **Scene validation / repair** — validators that check a scene and *repair*
  rather than fail when safe. `src/video_agent/shorts/validation/`
  (`scene_structure.py`, `script_checks.py`, `repairs.py`, `graphic_checks.py`).
- **Audio-fit** — validates scene durations sync to the spoken audio (tail
  constants, audio-visual delta thresholds).
  `src/video_agent/shorts/validation/audio_fit.py`.
- **scene_narration_fit** — QA dimension: does the narration fit the scene?
  (recurring tag in `.wolf/buglog.json`).
- **visual-only-unreadable repair** — repair path that injects the actual item
  label when a scene is visual-only and otherwise unreadable (see git history).
- **TTS report** — QA report over generated speech. `src/video_agent/qa/tts_report.py`.

## Assets & render

- **Music-only infographic Short** — a single 9:16 poster held static for a configured reading duration, with no TTS narration. Its BGM can be a deterministic original procedural bed (default) or an explicitly configured licensed library track. Built by `src/video_agent/shorts/infographic/build.py` and rendered by `remotion/src/shorts/InfographicShort.tsx`.
- **TTS** — text-to-speech narration generation. `src/video_agent/tts.py`.
- **Thumbnail planner** — plans thumbnails for a video.
  `src/video_agent/thumbnail_planner.py`.
- **Remotion renderer** — the React/Remotion layer that renders final frames.
  `remotion/` (Shorts components under `remotion/src/shorts/`). The Python side
  holds source-inspection *contracts* against the renderer
  (`tests/.../test_shorts_remotion_contract.py`).
- **Assets stage** — scene asset generation (images etc.).
  `src/video_agent/assets/` and `orchestrator/stages/`.

## Cross-cutting

- **Prime directive** — quality + audience fit beat throughput/cost/convenience
  on every conflict unless the user explicitly overrides. Defined in `CLAUDE.md`
  and `.wolf/cerebrum.md`.
- **Reasoning tier** — declare low/medium/high/max before each task to match
  effort to difficulty (see `.wolf/cerebrum.md`).
- **buglog** — `.wolf/buglog.json`, the structured bug/fix database. Grep it
  before fixing; dedup before appending (see `.wolf/OPENWOLF.md`).
- **Agent Bridge** — file-based Codex ↔ Claude Code handoff mechanism. Codex
  writes bug/audit tasks into `.agent/bridge/claude/inbox/` via
  `scripts/agent_bridge.py`; Claude replies to `.agent/bridge/codex/inbox/`
  with fix status and verification evidence. Contract documented in
  `docs/agent_bridge.md` and `.claude/rules/agent-bridge.md`.
