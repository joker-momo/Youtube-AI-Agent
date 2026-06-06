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
