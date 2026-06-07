# Walkthrough - Cumulative Retry Memory and Final Hard Gates

We have implemented the cumulative retry memory feedback and final hard gates for the Shorts video generation pipeline, in compliance with the `vida_plena_retry_memory_final_gate_spec_v1_0.md` specification:

## 1. Retry Memory and State Tracking (`retry_memory.py`)
- Created `src/video_agent/shorts/retry_memory.py` to handle the pipeline state and retry memory.
- **`ScenePipelineState`**: Tracks the current version of the scenes, validation state, Gemini QA state, and audio tail status along with their version numbers.
- **`RetryIssue` and `RetryMemory`**: Dataclasses for tracking individual issues across attempts. Issues can be `active`, `resolved`, `suppressed`, or `stale`.
- **Stable ID Generation (`make_stable_issue_id`)**: Generates normalized, stable IDs for issues. Numbers are mapped to `n` and scene IDs `s\d+` are mapped to `scene_id` to prevent duplicate feedback due to minor numerical changes.
- **Cumulative Retry Feedback (`generate_cumulative_feedback`)**: Formats a detailed retry prompt containing active issues to fix, do-not-regress constraints from resolved issues, hard invariants, and latest candidate summaries.

## 2. Hard Gate Orchestration (`short_builder.py`)
- **`assert_latest_scenes_ready`**: Checks that the latest scene version has passed deterministic validation and Gemini scene QA.
- Added assertions before `audio_tail_repair`, SEO, and Remotion rendering.
- Increments `current_scenes_version` and invalidates validation/QA stamps on scene regeneration, pacing simplification, and other mutations.
- Whitelisted mechanical mutations (like duration auto-repair and audio-tail tail-repair) increment the version and reset validation, but propagate the Gemini QA OK stamp as they do not affect visual/text layout meaning.
- Fallback candidates (like pacing-simplified scenes) rerun Gemini QA before rendering.

## 3. Comprehensive Tests (`test_shorts_retry_memory.py`)
- Added 9 unit and regression tests in `tests/test_shorts_retry_memory.py` verifying all core spec scenarios:
  1. Stable ID normalization.
  2. Cumulative retry feedback generation.
  3. Pipeline state assertions.
  4. Cumulative feedback keeping all issues across attempts.
  5. Gemini QA FAIL followed by regenerated scenes requiring a QA rerun.
  6. Scene validation FAIL blocking render/audio/SEO.
  7. Stale validation result blocking render.
  8. Audio-tail OK not overriding deterministic scene validation failures.
  9. Mechanical CTA clamping triggering validation reset and re-check.

## Verification Results
- **Unit Tests**: Ran `pytest tests/test_shorts_retry_memory.py` - **9/9 tests passed**.
- **Full Test Suite**: Ran `pytest` - **All 931 tests passed successfully**.
