# Vida Plena Shorts Pipeline — Retry Memory + Final Hard Gate Design

## 1. Overview
The goal of this design is to prevent invalid scene candidates from being rendered or moving forward in the pipeline, and to prevent ChatGPT retry loops where old errors are forgotten because retry feedback only includes the newest issues.

## 2. Requirements & Architecture

### 2.1 Scene Version & Validation State (`ScenePipelineState`)
We will track the following state in a dataclass `ScenePipelineState`:
- `current_scenes_version`: `int` (default `0`)
- `latest_scene_validation_ok`: `bool` (default `False`)
- `latest_scene_validation_version`: `int` (default `None`)
- `latest_scene_qa_ok`: `bool` (default `False`)
- `latest_scene_qa_version`: `int` (default `None`)
- `latest_audio_tail_ok`: `bool` (default `False`)
- `latest_audio_tail_version`: `int` (default `None`)
- `latest_seo_ok`: `bool` (default `False`)

Whenever scenes are regenerated (ChatGPT output) or mutated (mechanical duration adjustment, layout changes, pacing simplification, audio-tail adjustment), we increment `current_scenes_version` and reset the `_ok` flags and `_version` attributes.

#### Safe Mechanical Patches (Whitelist)
Certain mechanical changes are safe and do not require rerunning Gemini scene QA (though they still require rerunning deterministic scene validation):
- Clamping short_cta duration to <= 2.8 sec when narration is already <= 5 words.
- Recomputing `total_duration_sec`.
- Normalizing `covers_items` type and removing invalid IDs from non-content CTA scenes.
- Sorting/deduplicating `covers_items`.
- Setting missing optional fields to empty defaults.

If a whitelisted patch is applied:
1. Increment `current_scenes_version`.
2. Invalidate deterministic validation (`latest_scene_validation_ok = False`, `latest_scene_validation_version = None`).
3. Run deterministic validation. If OK, set `latest_scene_validation_ok = True` and `latest_scene_validation_version = current_scenes_version`.
4. Propagate Gemini QA status: if `latest_scene_qa_ok` was True, set `latest_scene_qa_version = current_scenes_version` to prevent it from becoming stale.

Any non-whitelisted patch (such as layout simplification, text/meaning edits, graphic/payload edits) will reset `latest_scene_qa_ok = False`.

### 2.2 Final Hard Gate Checks
Before `audio_tail_repair`, SEO, or Remotion rendering, the pipeline must enforce:
- Deterministic scene validation has passed for the latest scene version.
- Gemini scene QA has passed (or was bypassed via whitelisted best-candidate fallback) for the latest scene version.
- If audio generation happened, audio-tail repair is OK.

```python
def assert_latest_scenes_ready(state: ScenePipelineState) -> None:
    if not state.latest_scene_validation_ok:
        raise RuntimeError("Cannot proceed: latest scenes have not passed deterministic scene_validation.")
    if state.latest_scene_validation_version != state.current_scenes_version:
        raise RuntimeError("Cannot proceed: scene_validation result is stale.")
    if not state.latest_scene_qa_ok:
        raise RuntimeError("Cannot proceed: latest scenes have not passed Gemini scene QA.")
    if state.latest_scene_qa_version != state.current_scenes_version:
        raise RuntimeError("Cannot proceed: scene QA result is stale.")
```

### 2.3 Retry Memory & Cumulative Feedback
We introduce `RetryIssue` and `RetryMemory` to keep track of active, resolved, and suppressed issues:
- **Stable Issue ID**: Format is `{stage}:{scene_id or global}:{type}:{normalized_required_change}`.
- **Normalization**: Remove casing, strip punctuation/extra whitespace, and map numbers/scene IDs to placeholders (`N`, `scene_id`).
- **State Updates**:
  - Add/update issues when a validator/QA fails.
  - Check latest candidate against active issues to mark them resolved.
  - Explicitly suppress stale/false-positive issues (e.g., visual readability warnings that are resolved by spoken narration).
- **Cumulative Prompt Construction**: Replace the plain `RETRY FEEDBACK` with a structured cumulative template containing:
  1. Active Issues to Fix Now
  2. Do Not Regress Constraints
  3. Hard Invariants
  4. Suppressed/Stale Summary
  5. Latest Candidate Summary

## 3. Detailed Component Plan
- **New Module**: `src/video_agent/shorts/retry_memory.py` will contain `RetryIssue`, `RetryMemory`, helper functions for loading/saving retry memory, stable ID normalization, and prompt formatting.
- **Integration**: Update `src/video_agent/shorts/short_builder.py` to:
  - Maintain a `ScenePipelineState` instance.
  - Update scene versions and invalidate status upon mutations.
  - Enforce final hard gates at each step.
  - Maintain `RetryMemory` and compile cumulative retry prompts.
- **Tests**: Add regression tests in a new test file `tests/test_shorts_retry_memory.py` or within `tests/test_shorts_build.py` covering all scenarios in Section 15 of the spec.
