# Shorts QA Retry Budget and Pacing Optimization Design

## Goal
Implement a robust retry orchestration policy, provider-failure isolation, script collapse protection, deterministic retention plan repair, and a call budget logging system for the YouTube Shorts pipeline. This prevents max-regeneration retry storms when output is acceptable, separates flakiness errors from quality gates, and repair retention plans.

## Proposed Changes

### 1. Separate Hard Fails from Soft Warnings in `qa.py` and `short_builder.py`
We will define hard and soft failures explicitly.
- **Hard Fails** (trigger LLM regeneration/retry):
  - Invalid JSON / schema mismatches.
  - Missing required fields (such as hook, CTA, or narration).
  - Renderer contract failure.
  - Unsupported health claims / safety violations.
  - Source fidelity / idea contract violations.
  - First scene missing / no renderable scenes.
  - Invalid layout.
  - Audio fit / duration cap violations that cannot be repaired deterministically.
- **Soft Warnings** (verdict mapped to WARN, do not trigger regeneration):
  - hook could be sharper.
  - visual rhythm could be improved.
  - retention pacing acceptable but not ideal.
  - product quality preferences or style suggestions.
  - missing optional graphics.
  - layout preferences.
  - audio delta within acceptable PASS/WARN thresholds.

We will implement `has_hard_fail(result: dict)` to identify these hard issues, and `check_and_apply_auto_pass(qa_result: dict)` which forces the verdict of a failed QA result to `WARN` if `has_hard_fail(result)` is false and either the average product score $\ge 8.5$ or average quality score $\ge 85$.

### 2. Retry Orchestration & Provider Budgets
- `MAX_SCRIPT_REGEN_ATTEMPTS = 1` (allowing up to 2 total script attempts).
- `MAX_SCENE_REGEN_ATTEMPTS = 2` (allowing up to 3 total scene attempts), which dynamically extends to 3 attempts (up to 4 total attempts) if the first attempt had a hard schema or layout failure.
- `MAX_PROVIDER_RETRIES_PER_CALL = 3` (transient errors are retried inside a transparent provider wrapper, which does not consume creative QA regeneration attempts).
- Every retry records a deterministic event in the history containing:
  - `retry_reason` (e.g. `provider_error`, `qa_retry`, `schema_error`, `scene_validation_fail`, `audio_fit_fail`, `renderer_contract_fail`, `retention_grammar_repair`, `unknown`).
  - `retry_scope` (e.g. `script_only`, `scenes_only`, `provider_only`, `audio_repair_only`).
  - `attempt` (attempt number).
  - `max_attempts`.
  - `hard_fail` (boolean).
  - `source_stage`.

### 3. Hashing & Loop Collapse Protection
- For script: Hash normalized `hook + narration + cta + idea_items`. If identical to the previous attempt, stop retrying. If renderable and safe, continue with `WARN`, else fail fast.
- For scenes: Hash normalized scene layout, narration, caption, on_screen_text, duration_sec rounded to 0.1, and layout_payload title/items. If identical, stop retrying. If renderable and safe, continue with `WARN`, else fail fast.

### 4. Deterministic Retention Plan Repair
We will add `deterministic_repair_retention_plan` in `retention_plan.py` to fix Spanish grammar issues and enforce specific comment triggers:
- Correct known awkward grammar case-insensitively:
  - `el acompañamientos` / `la acompañamientos` $\to$ `los acompañamientos`
  - `la pan` $\to$ `el pan`
  - `el tostada` $\to$ `la tostada`
- Truncation repair: If a line ends mid-word, check original context (`title`, `hook_angle`, `narration_seed`) to complete the word and append proper punctuation.
- Enforce topic-specific comment triggers:
  - Bread-shopping topics $\to$ `¿También giras el paquete?`
  - Toast-assembly topics $\to$ `¿Cómo montas tú la tostada?`
  - Unsure/neutral topics $\to$ `¿También te pasa?`

### 5. Always-On Call Budget Summary
Generate `call_budget_summary.json` at `paths.short_json_dir / "call_budget_summary.json"` and log it as a deterministic stage at the end of every build attempt (success, fail, or needs_review).

## Verification Plan
We will implement 14 specific acceptance tests under `tests/test_shorts_call_budget.py`, `tests/test_shorts_retry_memory.py`, and `tests/test_shorts_retention_plan.py` covering all the requirements.
