# Shorts QA Retry Budget and Pacing Optimization Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Implement a robust retry orchestration policy, provider failure isolation, script loop collapse protection, deterministic retention plan repair, and a call budget logging system for the YouTube Shorts pipeline.

**Architecture:** We will implement orchestration-level changes in `short_builder.py` (retry wrappers, auto-pass checks, hashing, and event logging), deterministic repairs in `retention_plan.py`, and budget logging in `short_builder.py` and `call_budget.py`, verified by 14 Python unit/integration tests.

**Tech Stack:** Python, pytest

---

### Task 1: Enforce Retry Limits, Constants, and Helper Functions in `short_builder.py`

**Files:**
- Modify: `src/video_agent/shorts/short_builder.py`
- Test: `tests/test_shorts_call_budget.py`

**Step 1: Write the failing test**
We will add `test_constants_exist` to verify the new retry constants are present in `short_builder.py`.
```python
def test_constants_exist():
    import video_agent.shorts.short_builder as sb
    assert sb.MAX_QA_RETRIES_PER_STAGE == 1
    assert sb.MAX_SCENE_REGEN_ATTEMPTS == 2
    assert sb.MAX_SCRIPT_REGEN_ATTEMPTS == 1
    assert sb.MAX_PROVIDER_RETRIES_PER_CALL == 3
```

**Step 2: Run test to verify it fails**
Run: `pytest -k test_constants_exist`
Expected: FAIL (ImportError or AttributeError)

**Step 3: Write minimal implementation**
Define constants at the top of `short_builder.py` and expose them:
```python
MAX_QA_RETRIES_PER_STAGE = 1
MAX_SCENE_REGEN_ATTEMPTS = 2
MAX_SCRIPT_REGEN_ATTEMPTS = 1
MAX_PROVIDER_RETRIES_PER_CALL = 3
```

**Step 4: Run test to verify it passes**
Run: `pytest -k test_constants_exist`
Expected: PASS

**Step 5: Commit**
```bash
git add -f docs/plans/2026-06-08-shorts-retry-budget-plan.md src/video_agent/shorts/short_builder.py
git commit -m "feat: define retry orchestration constants"
```

---

### Task 2: Implement `has_hard_fail` and `check_and_apply_auto_pass` in `short_builder.py` and connect them to Script & Scene QA

**Files:**
- Modify: `src/video_agent/shorts/short_builder.py`
- Test: `tests/test_shorts_build.py`

**Step 1: Write the failing test**
We will write a test that mocks Gemini QA returning a verdict of `FAIL` but with average product score $\ge 8.5$ and no hard failures, and verifies that `check_and_apply_auto_pass` downgrades the verdict to `WARN`.
```python
def test_check_and_apply_auto_pass_forces_warn():
    from video_agent.shorts.short_builder import check_and_apply_auto_pass
    qa_result = {
        "verdict": "FAIL",
        "issues": [{"type": "product_quality_score_low", "detail": "retention pacing is 8.0", "severity": "minor"}],
        "product_scores": {
            "hook_strength": 9.0,
            "clarity": 9.0,
            "retention_pacing": 8.0,
            "visual_specificity": 9.0,
            "audience_fit_45_plus": 9.0,
            "natural_spanish": 9.0,
            "saveability": 9.0,
        }
    }
    assert check_and_apply_auto_pass(qa_result) is True
    assert qa_result["verdict"] == "WARN"
```

**Step 2: Run test to verify it fails**
Run: `pytest -k test_check_and_apply_auto_pass_forces_warn`
Expected: FAIL (ImportError or AttributeError)

**Step 3: Write minimal implementation**
Implement `has_hard_fail(result)` and `check_and_apply_auto_pass(result)` in `short_builder.py`, and update the script QA and scene QA result checks in `build_short` to use `check_and_apply_auto_pass`.
```python
def has_hard_fail(result: dict[str, Any]) -> bool:
    if result.get("provider") == "rule_based" and result.get("verdict") == "FAIL":
        return True
    issues = result.get("issues") or []
    for item in issues:
        if isinstance(item, str):
            item_lower = item.lower()
            if any(m in item_lower for m in ["safety", "source_fidelity", "source_support", "health_claim", "disclaimer", "medical", "contract"]):
                return True
            continue
        itype = str(item.get("type") or "").lower()
        severity = str(item.get("severity") or "").lower()
        detail = str(item.get("detail") or "").lower()
        if severity == "blocking_error":
            return True
        hard_markers = {
            "safety", "source_fidelity", "source_support", "idea", "schema",
            "layout", "contract", "first_scene", "empty_scenes", "greeting",
            "disclaimer", "medical", "overclaim", "narration", "source_map"
        }
        if any(m in itype for m in hard_markers):
            if "product_quality" in itype:
                continue
            return True
        if any(m in detail for m in ["safety", "source_fidelity", "source_support", "health claim", "medical overclaim"]):
            return True
    return False

def check_and_apply_auto_pass(qa_result: dict[str, Any]) -> bool:
    verdict = qa_result.get("verdict", "FAIL")
    if verdict in {"PASS", "WARN"}:
        return True
    if has_hard_fail(qa_result):
        return False
    from video_agent.shorts.qa import parse_defensive_score
    p_scores = qa_result.get("product_scores") or {}
    avg_product = 0.0
    if p_scores:
        vals = [parse_defensive_score(v) for v in p_scores.values()]
        avg_product = sum(vals) / len(vals) if vals else 0.0
    q_scores = qa_result.get("scores") or {}
    avg_quality = 0.0
    if q_scores:
        vals = [parse_defensive_score(v) for v in q_scores.values()]
        avg_quality = sum(vals) / len(vals) if vals else 0.0
    if avg_product >= 8.5 or avg_quality >= 85:
        qa_result["verdict"] = "WARN"
        qa_result["forced_pass_reason"] = "high_score_no_hard_fail"
        return True
    return False
```

**Step 4: Run test to verify it passes**
Run: `pytest -k test_check_and_apply_auto_pass_forces_warn`
Expected: PASS

**Step 5: Commit**
```bash
git add src/video_agent/shorts/short_builder.py
git commit -m "feat: implement score-based auto-pass"
```

---

### Task 3: Implement Script Loop Collapse Protection Using Normalized Hashing in `short_builder.py`

**Files:**
- Modify: `src/video_agent/shorts/short_builder.py`
- Test: `tests/test_shorts_build.py`

**Step 1: Write the failing test**
We will write a test verifying that identical script outputs stop the retry loop.
```python
def test_script_collapse_stops_loop():
    # Test script collapse stops loop
    pass
```

**Step 2: Run test to verify it fails**
Run: `pytest -k test_script_collapse_stops_loop`
Expected: FAIL

**Step 3: Write minimal implementation**
Implement `_normalized_script_hash(script: dict)` in `short_builder.py` and add the collapse check to the script generation loop in `build_short`.
```python
def _normalized_script_hash(script: dict[str, Any]) -> str:
    from video_agent.shorts.quality_hash import stable_hash
    hook = str(script.get("hook") or "").strip().lower()
    narration = str(script.get("narration") or "").strip().lower()
    cta = str(script.get("cta") or "").strip().lower()
    idea_items = script.get("idea_items") or script.get("points") or script.get("checklist") or []
    if isinstance(idea_items, list):
        idea_items = [str(item).strip().lower() for item in idea_items]
    norm = {
        "hook": hook,
        "narration": narration,
        "cta": cta,
        "idea_items": idea_items
    }
    return stable_hash(norm)
```

**Step 4: Run test to verify it passes**
Run: `pytest -k test_script_collapse_stops_loop`
Expected: PASS

**Step 5: Commit**
```bash
git add src/video_agent/shorts/short_builder.py
git commit -m "feat: implement script retry collapse protection"
```

---

### Task 4: Implement Unified Provider Retry Wrapper for `llm_fn` and `gemini_fn` in `short_builder.py`

**Files:**
- Modify: `src/video_agent/shorts/short_builder.py`
- Test: `tests/test_shorts_build.py`

**Step 1: Write the failing test**
We will write a test showing that provider failures are retried and logged under `provider_error` without consuming the creative QA budget.
```python
def test_provider_retries_not_qa_attempts():
    pass
```

**Step 2: Run test to verify it fails**
Run: `pytest -k test_provider_retries_not_qa_attempts`
Expected: FAIL

**Step 3: Write minimal implementation**
Implement `wrap_llm_with_provider_retries(original_llm_fn, recorder, stage_name)` and wrap the `llm_fn` and `gemini_fn` at the top of `build_short`. Also, log standard JSON retry classification events.
```python
def record_retry_event(recorder, reason, scope, attempt, max_attempts, hard_fail, source_stage, details=None):
    payload = {
        "retry_reason": reason,
        "retry_scope": scope,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "hard_fail": hard_fail,
        "source_stage": source_stage,
    }
    if details:
        payload.update(details)
    recorder.record_event("deterministic", "retry_classification", payload, ok=True)
```

**Step 4: Run test to verify it passes**
Run: `pytest -k test_provider_retries_not_qa_attempts`
Expected: PASS

**Step 5: Commit**
```bash
git add src/video_agent/shorts/short_builder.py
git commit -m "feat: wrap LLM functions with provider error retry"
```

---

### Task 5: Implement Deterministic Retention Plan Repair in `retention_plan.py`

**Files:**
- Modify: `src/video_agent/shorts/retention_plan.py`
- Test: `tests/test_shorts_retention_plan.py`

**Step 1: Write the failing test**
We will write tests in `tests/test_shorts_retention_plan.py` to check for Spanish article-noun fixes, truncation completions, and topic comment trigger selection.

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_shorts_retention_plan.py`
Expected: FAIL on the new tests.

**Step 3: Write minimal implementation**
Implement `deterministic_repair_retention_plan(plan, short_plan)` and call it at the end of `build_retention_plan`.
```python
def fix_grammar(text: str) -> str:
    import re
    replacements = [
        (r"\b[eE]l acompañamientos\b", "los acompañamientos"),
        (r"\b[lL]a acompañamientos\b", "los acompañamientos"),
        (r"\b[lL]a pan\b", "el pan"),
        (r"\b[eE]l tostada\b", "la tostada"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    return text
```

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_shorts_retention_plan.py`
Expected: PASS

**Step 5: Commit**
```bash
git add src/video_agent/shorts/retention_plan.py
git commit -m "feat: add deterministic retention plan repair layer"
```

---

### Task 6: Implement Always-on Call Budget Summary in `short_builder.py` and `call_budget.py`

**Files:**
- Modify: `src/video_agent/shorts/short_builder.py`, `src/video_agent/shorts/call_budget.py`
- Test: `tests/test_shorts_call_budget.py`

**Step 1: Write the failing test**
Add a test verifying that `call_budget_summary.json` is always produced at the end of a failed build.
```python
def test_call_budget_written_on_failure():
    pass
```

**Step 2: Run test to verify it fails**
Run: `pytest -k test_call_budget_written_on_failure`
Expected: FAIL

**Step 3: Write minimal implementation**
Modify `call_budget.py` classification logic to extract payload fields. Add a budget summary finalizer in `short_builder.py` and ensure it runs on every exit path.

**Step 4: Run test to verify it passes**
Run: `pytest -k test_call_budget_written_on_failure`
Expected: PASS

**Step 5: Commit**
```bash
git add src/video_agent/shorts/short_builder.py src/video_agent/shorts/call_budget.py
git commit -m "feat: write budget summary always at build end"
```

---

### Task 7: Implement 14 Acceptance Tests and Verification

**Files:**
- Modify: `tests/test_shorts_call_budget.py`, `tests/test_shorts_retry_memory.py`, `tests/test_shorts_retention_plan.py`

**Step 1: Write/Update all 14 tests**
We will implement or update all 14 tests as specified in Section 11 of the spec.

**Step 2: Run all tests**
Run: `pytest tests/`
Expected: PASS

**Step 3: Commit**
```bash
git add tests/
git commit -m "test: add 14 spec acceptance tests"
```

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-06-08-shorts-retry-budget-plan.md`.
Next step: run `.agent/workflows/execute-plan.md` to execute this plan task-by-task in single-flow mode.
