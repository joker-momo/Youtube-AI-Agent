# Fix RetryMemory Validation Issues Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Fix RetryMemory not carrying validation/repair issues to ensure retry feedback contains actual active issues and repair instructions, preventing ChatGPT loops.

**Architecture:**
- Update `build_scene_repair_plan` in `validate_scenes.py` to not skip warning-level issues so their instructions are generated.
- Update `generate_cumulative_feedback` in `retry_memory.py` to append the issue detail if it's not already present in the required change text.
- Verify through pytest that all validation issues (including warnings) are correctly preserved, repaired, and included in retry feedback.

**Tech Stack:** Python, Pytest

---

### Task 1: Generate Repair Plan Instructions for Warnings in `validate_scenes.py`

**Files:**
- Modify: `src/video_agent/shorts/validate_scenes.py`

**Step 1: Do not skip warnings in `build_scene_repair_plan`**
Modify `build_scene_repair_plan` to process all issues (including warnings) to populate their `issue.instructions` field.

```python
    for issue in issues:
        # Do not skip warning-severity issues, so they get repair instructions
        issue_instrs = []
        if issue.type in {"duration_cap", "scene_narration_fit"} and issue.scene_id:
            # ...
```

### Task 2: Include Details in Cumulative Feedback in `retry_memory.py`

**Files:**
- Modify: `src/video_agent/shorts/retry_memory.py`

**Step 1: Update `generate_cumulative_feedback` formatting**
Modify `generate_cumulative_feedback` to append `issue.detail` to the feedback text if it is not already a substring of `issue.required_change`.

```python
def generate_cumulative_feedback(memory: RetryMemory, attempt_number: int, candidate_summary: str = "") -> str:
    active_lines = []
    for idx, (issue_id, issue) in enumerate(memory.active_issues.items(), 1):
        stage_str = str(issue.stage).upper().replace("_", "-")
        type_str = str(issue.type).upper().replace("_", "-")
        desc = issue.required_change or issue.detail
        if issue.detail and issue.detail not in desc:
            desc = f"{desc} - {issue.detail}"
        active_lines.append(f"{idx}. [{stage_str}][{issue.scene_id or 'global'}][{type_str}] {desc}")
```

### Task 3: Verification & Test Execution

**Files:**
- Test: `tests/test_shorts_retry_memory.py`

**Step 1: Run the retry memory tests**
Run: `uv run pytest tests/test_shorts_retry_memory.py -v`
Expected: PASS (all 12 tests passing, including `test_scene_validation_fail_slideshow_risk_in_feedback`)

**Step 2: Run the full test suite**
Run: `uv run pytest`
Expected: PASS
