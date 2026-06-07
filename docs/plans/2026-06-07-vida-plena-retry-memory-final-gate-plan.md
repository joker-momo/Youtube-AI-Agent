# Vida Plena Retry Memory + Final Hard Gate Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Implement cumulative retry memory feedback and final scene gates in the Shorts pipeline to block invalid renders and prevent retry feedback loops.

**Architecture:** Define `ScenePipelineState`, `RetryIssue`, and `RetryMemory` in a dedicated `retry_memory.py` file. Track versions and validate/QA stamps in `short_builder.py`, invalidating them on mutations (excluding whitelisted mechanical patches). Render/audio/SEO operations are hard-gated by assertions checking the version stamps.

**Tech Stack:** Python 3.11, Dataclasses, Pytest.

---

### Task 1: Create retry_memory.py

**Files:**
- Create: `src/video_agent/shorts/retry_memory.py`
- Test: `tests/test_shorts_retry_memory.py`

**Step 1: Write the failing test**

```python
# tests/test_shorts_retry_memory.py
from video_agent.shorts.retry_memory import make_stable_issue_id, RetryIssue

def test_stable_id_normalization():
    detail1 = "CTA scene s07 must be <= 2.8 sec"
    id1 = make_stable_issue_id("scene_validation", "s07", "duration", detail1)
    
    detail2 = "CTA scene s07 must be <= 5.2 sec"
    id2 = make_stable_issue_id("scene_validation", "s07", "duration", detail2)
    
    assert id1 == id2
    assert id1 == "scene_validation:s07:duration:cta_scene_scene_id_must_be_n_sec"
```

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_shorts_retry_memory.py -k test_stable_id_normalization -v`
Expected: ModuleNotFoundError: No module named 'video_agent.shorts.retry_memory'

**Step 3: Write minimal implementation**

```python
# src/video_agent/shorts/retry_memory.py
import re
from dataclasses import dataclass, field, asdict

@dataclass
class ScenePipelineState:
    current_scenes_version: int = 0
    latest_scene_validation_ok: bool = False
    latest_scene_validation_version: int | None = None
    latest_scene_qa_ok: bool = False
    latest_scene_qa_version: int | None = None
    latest_audio_tail_ok: bool = False
    latest_audio_tail_version: int | None = None
    latest_seo_ok: bool = False

@dataclass
class RetryIssue:
    id: str
    stage: str
    attempt: int
    scene_id: str | None
    type: str
    severity: str
    detail: str
    required_change: str
    status: str  # active | resolved | suppressed | stale
    first_seen_attempt: int
    last_seen_attempt: int
    repeat_count: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class RetryMemory:
    stage: str
    active_issues: dict[str, RetryIssue] = field(default_factory=dict)
    resolved_issues: dict[str, RetryIssue] = field(default_factory=dict)
    suppressed_issues: dict[str, RetryIssue] = field(default_factory=dict)
    do_not_regress: list[str] = field(default_factory=list)
    hard_invariants: list[str] = field(default_factory=list)

def make_stable_issue_id(stage: str, scene_id: str | None, issue_type: str, detail_or_change: str) -> str:
    scene_str = str(scene_id or "global").lower().strip()
    type_str = str(issue_type or "unknown").lower().strip()
    
    clean_text = detail_or_change.lower()
    clean_text = re.sub(r'\b\d+(?:\.\d+)?\b', 'N', clean_text)
    clean_text = re.sub(r'\bs\d+\b', 'scene_id', clean_text)
    clean_text = re.sub(r'[^\w\s]', '', clean_text)
    clean_text = " ".join(clean_text.split())
    
    words = clean_text.split()
    normalized_rc = "_".join(words)
    return f"{stage}:{scene_str}:{type_str}:{normalized_rc}"
```

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_shorts_retry_memory.py -k test_stable_id_normalization -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/video_agent/shorts/retry_memory.py tests/test_shorts_retry_memory.py
git commit -m "feat: implement retry memory dataclasses and stable id normalization"
```

---

### Task 2: Implement RetryMemory Update and Prompt Generation Logic

**Files:**
- Modify: `src/video_agent/shorts/retry_memory.py`
- Modify: `tests/test_shorts_retry_memory.py`

**Step 1: Write the failing test**

```python
# tests/test_shorts_retry_memory.py
from video_agent.shorts.retry_memory import RetryMemory, add_or_update_issue, generate_cumulative_feedback

def test_retry_memory_feedback():
    memory = RetryMemory(stage="scenes")
    memory.hard_invariants = ["Preserve source fidelity."]
    
    issue = RetryIssue(
        id="scene_validation:s07:duration:cta_too_long",
        stage="scene_validation",
        attempt=1,
        scene_id="s07",
        type="duration",
        severity="repairable_error",
        detail="CTA scene s07 duration exceeds 2.8s",
        required_change="Clamp CTA scene s07 to <= 2.8s",
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1
    )
    add_or_update_issue(memory, issue)
    
    feedback = generate_cumulative_feedback(memory, attempt_number=2)
    assert "ACTIVE ISSUES TO FIX NOW:" in feedback
    assert "1. [scene_validation][s07][duration] Clamp CTA scene s07 to <= 2.8s" in feedback
    assert "Preserve source fidelity." in feedback
```

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_shorts_retry_memory.py -k test_retry_memory_feedback -v`
Expected: FAIL (ImportError or AttributeError for add_or_update_issue/generate_cumulative_feedback)

**Step 3: Write minimal implementation**

Add the following to `src/video_agent/shorts/retry_memory.py`:

```python
def add_or_update_issue(memory: RetryMemory, issue: RetryIssue) -> None:
    if issue.id in memory.active_issues:
        existing = memory.active_issues[issue.id]
        existing.last_seen_attempt = issue.attempt
        existing.repeat_count += 1
        existing.detail = issue.detail
        existing.required_change = issue.required_change
    else:
        memory.active_issues[issue.id] = issue

def make_do_not_regress_line(issue: RetryIssue) -> str:
    # Build a friendly constraint line based on resolved issues
    if issue.stage == "scene_validation" and issue.type == "duration":
        return f"- Keep {issue.scene_id or 'CTA'} duration within layout caps."
    return f"- Do not reintroduce: {issue.required_change or issue.detail}."

def resolve_issue_by_id(memory: RetryMemory, issue_id: str) -> None:
    if issue_id in memory.active_issues:
        issue = memory.active_issues[issue_id]
        issue.status = "resolved"
        memory.resolved_issues[issue_id] = issue
        del memory.active_issues[issue_id]
        memory.do_not_regress.append(make_do_not_regress_line(issue))

def suppress_issue_by_id(memory: RetryMemory, issue_id: str) -> None:
    if issue_id in memory.active_issues:
        issue = memory.active_issues[issue_id]
        issue.status = "suppressed"
        memory.suppressed_issues[issue_id] = issue
        del memory.active_issues[issue_id]

def generate_cumulative_feedback(memory: RetryMemory, attempt_number: int, candidate_summary: str = "") -> str:
    active_lines = []
    for idx, (issue_id, issue) in enumerate(memory.active_issues.items(), 1):
        active_lines.append(f"{idx}. [{issue.stage}][{issue.scene_id or 'global'}][{issue.type}] {issue.required_change or issue.detail}")
    
    active_issues_str = "\n".join(active_lines) if active_lines else "None. All previously identified issues are resolved/addressed."
    do_not_regress_str = "\n".join(memory.do_not_regress) if memory.do_not_regress else "None."
    hard_invariants_str = "\n".join(memory.hard_invariants) if memory.hard_invariants else "None."
    
    suppressed_lines = [
        f"- {issue.required_change or issue.detail}" 
        for issue in memory.suppressed_issues.values()
    ]
    suppressed_str = "\n".join(suppressed_lines) if suppressed_lines else "None."
    
    return f"""RETRY FEEDBACK — CUMULATIVE

This is retry attempt {attempt_number}.
You must satisfy ALL active requirements below.
Do not only fix the newest issue.
Do not reintroduce resolved issues.

ACTIVE ISSUES TO FIX NOW:
{active_issues_str}

DO NOT REGRESS:
{do_not_regress_str}

HARD INVARIANTS:
{hard_invariants_str}

SUPPRESSED / STALE ISSUES:
{suppressed_str}

LATEST CANDIDATE SUMMARY:
{candidate_summary or "None."}

OUTPUT REQUIREMENTS:
- Return a full corrected JSON object.
- Do not return partial patches.
- Do not remove source-supported idea items.
- Do not change the approved script meaning."""
```

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_shorts_retry_memory.py -k test_retry_memory_feedback -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/video_agent/shorts/retry_memory.py tests/test_shorts_retry_memory.py
git commit -m "feat: implement RetryMemory updating and feedback generation"
```

---

### Task 3: Integrate ScenePipelineState & Whitelist/Mutation Logic

**Files:**
- Modify: `src/video_agent/shorts/short_builder.py`
- Modify: `tests/test_shorts_retry_memory.py`

**Step 1: Write the failing test**

```python
# tests/test_shorts_retry_memory.py
from video_agent.shorts.retry_memory import ScenePipelineState

def test_pipeline_state_assertions():
    state = ScenePipelineState()
    state.current_scenes_version = 1
    
    # Assert latest validation fails because validation version is None
    import pytest
    with pytest.raises(RuntimeError) as exc_info:
        from video_agent.shorts.short_builder import assert_latest_scenes_ready
        assert_latest_scenes_ready(state)
    assert "deterministic" in str(exc_info.value)
```

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_shorts_retry_memory.py -k test_pipeline_state_assertions -v`
Expected: FAIL (ImportError for assert_latest_scenes_ready)

**Step 3: Write minimal implementation**

In `src/video_agent/shorts/short_builder.py`, define `assert_latest_scenes_ready` and integrate `ScenePipelineState` initialization.

```python
from video_agent.shorts.retry_memory import ScenePipelineState

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

Also, in `build_short` function in `short_builder.py`, initialize:
`state = ScenePipelineState()`

Whenever a new version of scenes is produced or mutated:
- Regeneration (ChatGPT):
  ```python
  state.current_scenes_version += 1
  state.latest_scene_validation_ok = False
  state.latest_scene_validation_version = None
  state.latest_scene_qa_ok = False
  state.latest_scene_qa_version = None
  state.latest_audio_tail_ok = False
  state.latest_audio_tail_version = None
  ```
- Whitelisted mutation (e.g. initial `repair_scene_duration_if_possible` loop, duration auto-repair, and audio-tail repair):
  Define a helper `mark_whitelisted_mutation(state)`:
  ```python
  state.current_scenes_version += 1
  state.latest_scene_validation_ok = False
  state.latest_scene_validation_version = None
  ```
  And after running validation on the mutated scenes, if it passes:
  ```python
  state.latest_scene_validation_ok = True
  state.latest_scene_validation_version = state.current_scenes_version
  if state.latest_scene_qa_ok:
      state.latest_scene_qa_version = state.current_scenes_version
  ```

Add final gate assertions before:
1. `audio_tail_repair`:
   `assert_latest_scenes_ready(state)`
2. SEO:
   `assert_latest_scenes_ready(state)`
3. Rendering:
   `assert_latest_scenes_ready(state)`
   If audio generated:
   ```python
   if not state.latest_audio_tail_ok:
       raise RuntimeError("Cannot proceed: audio-tail repair is not OK.")
   ```

**Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_shorts_retry_memory.py -k test_pipeline_state_assertions -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/video_agent/shorts/short_builder.py
git commit -m "feat: integrate ScenePipelineState and final hard gates in build_short"
```

---

### Task 4: Integrate Retry Memory in the Script and Scene loops

**Files:**
- Modify: `src/video_agent/shorts/short_builder.py`
- Modify: `tests/test_shorts_retry_memory.py`

**Step 1: Write the failing test**

Write a test simulating the loop where an issue is kept across attempts:
```python
# tests/test_shorts_retry_memory.py
# (We will implement a mock run of build_short or test the retry memory integration directly)
```

**Step 2: Run test to verify it fails**

Run pytest.

**Step 3: Write minimal implementation**

In `short_builder.py`:
- Initialize `script_retry_memory = RetryMemory(stage="script")` and `scene_retry_memory = RetryMemory(stage="scenes")`.
- Fill in hard invariants for `scene_retry_memory`:
  ```python
  scene_retry_memory.hard_invariants = [
      "- Preserve source fidelity.",
      "- Preserve idea_contract.original_count when must_preserve_count=true.",
      "- Do not invent unsupported claims.",
      "- Do not use unsafe/medical fear framing.",
      "- Latest scene_validation and latest Gemini scene QA must pass before audio/SEO/render.",
      "- If scenes are regenerated after Gemini QA, Gemini QA must run again."
  ]
  ```
- In the script and scene loops, parse failures and register them in retry memory.
  For deterministic scene validation issues:
  ```python
  # After running validate_scene_structure:
  active_validation_ids = set()
  for issue in structure_issues:
      if issue.severity in ("blocking_error", "repairable_error"):
          issue_id = make_stable_issue_id("scene_validation", issue.scene_id, issue.type, issue.detail)
          active_validation_ids.add(issue_id)
          retry_issue = RetryIssue(
              id=issue_id,
              stage="scene_validation",
              attempt=scenes_attempts,
              scene_id=issue.scene_id,
              type=issue.type,
              severity=issue.severity,
              detail=issue.detail,
              required_change=issue.repair_hint or issue.detail,
              status="active",
              first_seen_attempt=scenes_attempts,
              last_seen_attempt=scenes_attempts
          )
          add_or_update_issue(scene_retry_memory, retry_issue)
  ```
- Also, resolve issues when they are no longer present in the validator output!
  ```python
  # Any scene_validation issue that was active but is no longer returned in structure_issues is resolved:
  for issue_id in list(scene_retry_memory.active_issues.keys()):
      issue = scene_retry_memory.active_issues[issue_id]
      if issue.stage == "scene_validation" and issue_id not in active_validation_ids:
          resolve_issue_by_id(scene_retry_memory, issue_id)
  ```
- Similarly, handle Gemini QA required changes:
  ```python
  # For Gemini scenes QA:
  active_qa_ids = set()
  if scenes_qa_result.get("verdict") == "FAIL":
      for item in scenes_qa_result.get("issues", []):
          detail = item if isinstance(item, str) else item.get("detail", "")
          issue_type = "qa_issue" if isinstance(item, str) else item.get("type", "qa_issue")
          scene_id = None if isinstance(item, str) else item.get("scene_id")
          issue_id = make_stable_issue_id("scene_qa", scene_id, issue_type, detail)
          active_qa_ids.add(issue_id)
          retry_issue = RetryIssue(
              id=issue_id,
              stage="scene_qa",
              attempt=scenes_attempts,
              scene_id=scene_id,
              type=issue_type,
              severity="major",
              detail=detail,
              required_change=detail,
              status="active",
              first_seen_attempt=scenes_attempts,
              last_seen_attempt=scenes_attempts
          )
          add_or_update_issue(scene_retry_memory, retry_issue)
  ```
- Suppress false positives (like `visual_only_unreadable`):
  Check if an active issue is a visual unreadability warning, but the latest scenes/narration covers it (as specified in 5.3):
  ```python
  # Implement resolve_if_fixed or suppress stale rules
  for issue_id in list(scene_retry_memory.active_issues.keys()):
      issue = scene_retry_memory.active_issues[issue_id]
      if issue.type == "visual_only_unreadable" and issue.scene_id:
          # If the scene's narration speaks the item:
          scene = next((s for s in scenes if str(s.get("id") or s.get("scene_id") or "") == issue.scene_id), None)
          if scene and issue.scene_id in str(scene.get("covers_items") or ""):
              suppress_issue_by_id(scene_retry_memory, issue_id)
  ```
- Replace `scenes_feedback = ...` with:
  `scenes_feedback = generate_cumulative_feedback(scene_retry_memory, scenes_attempts + 1)`

**Step 4: Run test to verify it passes**

Run the tests.

**Step 5: Commit**

```bash
git add src/video_agent/shorts/short_builder.py
git commit -m "feat: integrate RetryMemory updates and feedback generation in loops"
```

---

### Task 5: Add Comprehensive Spec Regression Tests

**Files:**
- Create/Modify: `tests/test_shorts_retry_memory.py`

**Step 1: Write tests for section 15 of spec**
- 15.1: Cumulative feedback keeps all issues
- 15.4: Gemini QA FAIL then regenerated scenes must rerun QA
- 15.5: Scene validation FAIL blocks audio/SEO/render
- 15.6: Stale validation result blocks render
- 15.7: Audio-tail OK does not override scene validation fail
- 15.8: Mechanical CTA clamp must revalidate

**Step 2: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_shorts_retry_memory.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_shorts_retry_memory.py
git commit -m "test: add spec regression tests for retry memory and hard gates"
```
