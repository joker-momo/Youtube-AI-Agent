# Design Doc - Shorts Retry Memory + QA Loop Fix

## Goal
Optimize the YouTube Shorts generation pipeline to prevent retry exhaustion/max regeneration attempts by categorizing issues into severity levels, filtering out wrong-context template rules, implementing deterministic repairs, and providing a detailed exit report on retry termination.

## Proposed Architecture

### 1. Issue Severity Classification
We define four explicit issue severity classes under `IssueClass`:
- `HARD_BLOCKER`: Safety, contract violations, rendering failure, missing required fields. Blocks render and triggers regeneration.
- `REPAIRABLE_BLOCKER`: Missing required checklist points, unreadable required item, malformed graphic payload, scene duration cap violation. Triggers one scoped regeneration or deterministic repair.
- `SOFT_WARNING`: Aesthetic preferences, soft suggestions (e.g. hook could be sharper), low product quality score (scores 7-8). Does not trigger regeneration.
- `STALE_OR_SUPPRESSED`: Resolved issues, wrong context rules, or soft warnings that have repeated more than 2 attempts. Not included in active retry prompt.

### 2. Centralized Issue Normalizer
We implement `normalize_qa_issue(issue, *, idea, script, scenes, deterministic_validation) -> NormalizedIssue` in `qa.py` or `validate_scenes.py`.
- Evaluates title/format context using `get_short_rule_context`.
- Classifies each issue into one of the `IssueClass` severities.
- Keeps track of `repeat_count` for soft warnings; if it exceeds 2, promotes the issue to `STALE_OR_SUPPRESSED`.

### 3. Wrong-Context Rule Suppression
- Checks if the idea matches `is_five_errors_bread_short`.
- If `is_five_errors_bread_short` is `False`, suppresses all template-specific rules like `NO ES EL PAN`, `GUÁRDALO` (when script CTA is valid), error scene duration (3.2–4.0s), etc., classifying them as `STALE_OR_SUPPRESSED` with reason `wrong_context_five_errors_rule`.

### 4. Deterministic Repairs
- **`weak_hook_motion`**: If first scene lacks motion or has weak motion, repair deterministically by setting `motion = "push_in"` and `pattern_interrupt = "text_pop at 0.5s"` in-place instead of calling LLM.
- **`visual_only_unreadable`**: If required checklist item is missing from captions/on_screen_text but appears in visual prompts, append it to the caption and/or layout_payload items.

### 5. Exit Summary & Call Budget Classification
- Replaces generic loop failure messages with a detailed breakdown:
  - Remaining blockers
  - Remaining warnings
  - Renderable (True/False)
  - Decision (`continued_with_warn` or `failed_hard_blocker`).
- Map reasons in `call_budget.py`:
  - `qa_soft_warn`
  - `qa_hard_fail`
  - `wrong_context_suppressed`
  - `retry_collapse`
  - `scene_validation_fail`, etc.
- Target: `unknown` failures $\le 1$.
