| Task | Status | Notes |
| :--- | :--- | :--- |
| Task 1: Descriptive `short_id` generation | [x] | Completed `planner.py` + `synthesis.py` and verified via tests |
| Task 2: Update regex matching | [x] | Completed `synthesis.py` + `shorts_studio.py` and verified via tests |
| Task 3: Restructure output paths in `short_thumbnail_builder.py` | [x] | Relocated raw/output paths and metadata under json and outputs subdirectories |
| Task 4: Fix script loading in `qa.py` | [x] | Modified `qa.py` and verified with all tests passing |
| Task 5: Fix idea loading in `renderer.py` | [x] | Modified `renderer.py` `_save_friendly_copy` to use `resolve_short_json` |
| Task 6: Implement public jobs cleanup on delete | [x] | Added `remotion/public/jobs/` cleanup to `_legacy.py` + `shorts_studio.py` |
| Task 7: Update and fix unit tests | [x] | Adjusted test_shorts_build.py, test_shorts_render.py to use json/ and outputs/ paths |
| Task 8: Fix missing description bug | [x] | Modified dashboard.html/shorts_studio.html to match subfolders and merged cleanly |
| Task 9: Brainstorm and design README.md updates | [x] | Reviewing project state and proposing README changes |
| Task 10: Update README.md | [x] | Apply approved changes to README.md |
| Task 11: Explore and debug stock video search queries and hit counts | [x] | Completed exploration and proposed fallback design |
| Task 12: Implement query optimization for Pixabay and Coverr | [x] | Completed symbol-splitting and retry fallback loops |
| Task 13: Render test jobs for Pexels, Pixabay, and Coverr | [x] | Completed test rendering for Pexels, Pixabay, and Coverr |
| Task 14: Verify outputs and complete | [x] | Verified output manifests, successfully resolved and passed all tests |
| Task 15: Fix progressive search exclusion logic in StockAssetService | [x] | Fix exclude_ids check during client search and cache lookup to resolve s07 placeholder fallback |
| Task 16: Fix render props layout schema validation | [x] | Add graphic layouts to schemas/render-props.schema.json |
| Task 17: Fix audio sync B m4a fallback | [x] | Update _sync_scene_durations_from_audio in pipeline.py |
| Task 18: Verify changes and run render tests | [x] | Run pytest and verify rendering of short-01 |
| Task 19: Phase A Inspection & Spec Evaluation | [x] | Reviewing codebase and answering Phase A questions to evaluate spec |
| Task 20: Scene QA Prompt Fix in prompts.py | [x] | Add hard rules instructing Gemini that layout optimization is warnings-only |
| Task 21: QA Response Normalization in qa.py | [x] | Implement graphic preference downgrading layer |
| Task 22: Deterministic Scene Validation in validate_scenes.py | [x] | Programmatically normalize total_duration_sec, enforce scene caps and graphic limits |
| Task 23: Build Control Flow & Exact Audio-Fit Gate in short_builder.py | [x] | Move exact audio-fit check before SEO and implement the repair loop |
| Task 24: Add Regression Tests | [x] | Add tests for graphic preference warning, total_duration_sec, and audio-fit repair |
| Task 25: Verify all tests passing | [x] | Run the full test suite to verify no regressions |
| Task 26: Recompute total_duration_sec and normalize it | [x] | Normalize total_duration_sec and warning rename |
| Task 27: Auto-extend scene duration within layout cap | [x] | Implement repair_scene_duration_if_possible |
| Task 28: Implement action-specific scene repair hints | [x] | Update build_scene_repair_plan instructions |
| Task 29: Update scene and QA prompt templates | [x] | Add word caps and product_scores to prompts.py |
| Task 30: Implement defensive product scores parsing | [x] | Add defensive parsing and thresholds check in qa.py |
| Task 31: Control flow script escalation and fallback score checks | [x] | Implement escalation loop and fallback scores check in short_builder.py |
| Task 32: Add regression tests and verify all passing | [x] | Verify full test suite passes with new changes |
| Task 33: Contract Item Derivation & Support Validation | [x] | Modify `idea_preservation.py` to copy overall scene IDs, update support references check, and backfill item 5 |
| Task 34: Mechanical Duration Repair | [x] | Update `repair_scene_duration_if_possible` to clamp overlong checklist/payoff scene durations |
| Task 35: Strengthen `visual_only_unreadable` Guard | [x] | Suppress false positives by checking spoken/captioned modes and improving ordinal token/short phrase matching |
| Task 36: Retry/Fallback Improvements | [x] | Ensure scene validation loop auto-repairs duration and proceeds to scene QA if no other hard issues exist |
| Task 37: Verification & Regression Tests | [x] | Run the full test suite and confirm all tests pass |
| Task 38: Contract Merging in `idea_preservation.py` | [x] | Merge derived plan contract fields into ChatGPT script contract |
| Task 39: Fix `validate_script_checklist_point_cap` | [x] | Update validator to properly check if points <= allowed_spoken_points and return None |
| Task 40: Verification & Regression Tests for Point Cap | [x] | Write regression tests and run all unit tests |
| Task 41: Create `retry_memory.py` | [x] | Implement retry memory classes and stable ID normalization |
| Task 42: Implement update and feedback generation logic | [x] | Update RetryMemory and generate cumulative feedback |
| Task 43: Integrate ScenePipelineState & Whitelist/Mutation Logic | [x] | Enforce hard gates and version tracking in `short_builder.py` |
| Task 44: Integrate Retry Memory in script and scene loops | [x] | Parse and persist active/resolved/suppressed issues across attempts |
| Task 45: Add regression tests and verify all passing | [x] | Verify full test suite passes with new changes |
| Task 46: Generate Repair Plan Instructions for Warnings in `validate_scenes.py` | [x] | Do not skip warnings in build_scene_repair_plan |
| Task 47: Include Details in Cumulative Feedback in `retry_memory.py` | [x] | Update generate_cumulative_feedback formatting |
| Task 48: Verification & Regression Tests for Validation Issues | [x] | Verify all 12 retry memory tests pass and run full suite |
| Task 49: Update Visual Styling & Opacity in Remotion | [x] | Reduce overlay opacity, move captions up, enlarge caption text |
| Task 50: Implement Deterministic Pacing and Payoff Layout in `validate_scenes.py` | [x] | Enforce pacing targets, 25.5s floor, graphic_checklist payoff layout |
| Task 51: Enforce Strict QA Thresholds in `qa.py` | [x] | Define REQUIRED_PRODUCT_SCORE_THRESHOLDS and update validation |
| Task 52: Update Prompt Templates in `prompts.py` | [x] | Instruct ChatGPT and Gemini on new visual, pacing, and SEO rules |
| Task 53: Verification & Test Execution | [x] | Verify all tests pass |
| Task 54: Brainstorming: Explore project context (shorts QA and retry architecture) | [x] | Checked files, docs, and code for current retry policies and structures |
| Task 55: Brainstorming: Ask clarifying questions | [x] | Asked clarifying questions on retry collapse behavior |
| Task 56: Brainstorming: Propose 2-3 approaches | [x] | Proposed options for separating hard fail/soft warn, limits, collapse, budget summary, and retention repair |
| Task 57: Brainstorming: Present design sections and get approval | [x] | Presented architecture, classes, and loops for user review |
| Task 58: Brainstorming: Write design doc `docs/plans/2026-06-08-shorts-retry-budget-design.md` | [x] | Save design doc to git |
| Task 59: Transition to implementation: Invoke writing-plans skill | [x] | Create detailed implementation plan |
| Task 60: Executing Plan: Task 1 - Enforce limits, constants in `short_builder.py` | [x] | Implement MAX_QA_RETRIES_PER_STAGE, MAX_SCENE_REGEN_ATTEMPTS, etc. |
| Task 61: Executing Plan: Task 2 - has_hard_fail, check_and_apply_auto_pass in `short_builder.py` | [x] | Implement auto-pass checking for script/scene QA |
| Task 62: Executing Plan: Task 3 - Script collapse protection in `short_builder.py` | [x] | Implement script hashing and collapse check |
| Task 63: Executing Plan: Task 4 - Unified provider retry wrapper in `short_builder.py` | [x] | Intercept provider errors/timeouts, retry up to 3 times, resolved regressions |
| Task 64: Executing Plan: Task 5 - Deterministic retention repair in `retention_plan.py` | [x] | Repair Spanish grammar/topics deterministically |
| Task 65: Executing Plan: Task 6 - Always-on call budget summary in `short_builder.py` | [x] | Ensure call_budget_summary is written on exits |
| Task 66: Executing Plan: Task 7 - Write 14 acceptance tests | [x] | Write and run all 14 spec tests |
| Task 67: Executing Plan: Task 8 - Verify all tests pass | [x] | Confirm entire test suite is green |
| Task 68: Brainstorming: Explore project context (Retry Memory + QA loop) | [x] | Explore validate_scenes, retry_memory, and qa modules |
| Task 69: Brainstorming: Ask clarifying questions | [x] | Ask clarifying question about suffix completion |
| Task 70: Brainstorming: Propose 2-3 approaches | [x] | Propose Approach A and B, recommend A |
| Task 71: Brainstorming: Write design doc `docs/plans/2026-06-08-shorts-retry-memory-qa-loop-fix-design.md` | [x] | Write and save design doc |
| Task 72: Transition to implementation: Invoke writing-plans skill | [x] | Create detailed implementation plan |
| Task 73: Task 1 - Severity classes & normalizer in `qa.py` | [x] | Implement IssueClass, NormalizedIssue, and normalize_qa_issue in qa.py |
| Task 74: Task 2 - Rule context & wrong-context filtering | [x] | Implement get_short_rule_context and template context filtering in qa.py |
| Task 75: Task 3 - Retry feedback formatting in `retry_memory.py` | [x] | Restructure retry memory prompt layout |
| Task 76: Task 4 - Loop gating & exit report in `short_builder.py` | [x] | Implement severity-gated loop decisions and summary exit reporting |
| Task 77: Task 5 - Deterministic repair functions for hook/unreadable | [x] | Implement deterministic repairs for weak hook motion and unreadable items |
| Task 78: Task 6 - Expand reasons in `call_budget.py` | [x] | Add and classify new reasons in call budget |
| Task 79: Task 7 - Write 8 new spec acceptance tests | [x] | Add acceptance tests and verify full suite passes |
| Task 80: Count Authority in Script QA Prompt (`prompts.py`) | [x] | Add COUNT AUTHORITY section to gemini_script_qa_prompt |
| Task 81: Mismatch Suppression logic (`qa.py`) | [x] | Suppress mismatching count QA issues in normalize_qa_issue |
| Task 82: Contract Protection (`idea_preservation.py`) | [x] | Prevent contract mutation in ensure_script_idea_fields |
| Task 83: Suppressed issues Retry Memory routing (`short_builder.py`) | [x] | Route stale_or_suppressed issues in script/scene QA loops |
| Task 84: Call budget reasons and classification events mapping | [x] | Expand call budget reasons and log qa_classification override events |
| Task 85: Add unit/integration tests and verify | [x] | Verify with automated tests and ensure all tests pass |
| Task 86: Update repair_weak_hook_motion in validate_scenes.py | [x] | Deterministic weak hook motion repair |
| Task 87: Pass attempt and downgrade slideshow_risk on attempts >= 2 | [x] | slideshow_risk downgrade logic |
| Task 88: Filter repair instructions for warnings | [x] | Filter warnings from build_scene_repair_plan |
| Task 89: Update issue classifications in normalize_qa_issue | [x] | Classify duration_pacing and total_duration_normalized |
| Task 90: Pass attempt and populate RetryIssue fields in short_builder.py | [x] | Update loop, cap enforcement, and retry memory fields |
| Task 91: Whitelist reasons and improve call budget classification | [x] | Whitelist and map classifications in call_budget.py |
| Task 92: Add path constant in paths.py | [x] | |
| Task 93: Implement decision summary and update stage status in short_builder.py | [/] | |
| Task 94: Update web dashboard UI notice in shorts_studio.html | [ ] | |
| Task 95: Implement product score and wrong CTA suppression in `qa.py` | [x] | |
| Task 96: Implement deterministic `visual_only_unreadable` repair in `validate_scenes.py` | [x] | |
| Task 97: Implement visual repair tracker and loop flow in `short_builder.py` | [x] | |
| Task 98: Implement event classification in `call_budget.py` | [x] | |
| Task 99: Add unit/regression tests and verify all tests passing | [x] | |
