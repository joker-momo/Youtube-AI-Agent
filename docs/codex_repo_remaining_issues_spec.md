# Codex Spec — Remaining Repository Improvements

Repository: `joker-momo/Youtube-AI-Agent`  
Target branch: `main`  
Purpose: fix the remaining issues found in the latest repo review without reworking already-good parts.

This spec is intended to be pasted or referenced in Codex. Prefer implementing in small commits or separate PRs if Codex supports it.

---

## 0. Current repo state summary

The repo has already improved in these areas and **should not be rewritten from scratch**:

- Spain-first channel config is mostly correct: `audience.language: es-ES`, `primary_markets: ["ES"]`, `timezone: Europe/Madrid`, `seo.language: es-ES`.
- Prompt generation now has locale guidance for Spain-first Spanish.
- SEO prompt no longer hard-codes `es-419`; it derives language from config.
- Visual prompt rules now require English for Pexels/stock search.
- SEO validators now check placeholder social-link text, locale avoid terms, and expected language.
- `app.py` has been reduced to a router-including shell.
- Provider interface skeletons exist.

Do **not** undo these improvements.

The remaining important issues are:

1. SEO description normalization collapses timestamp lines into one line.
2. `app.py` is clean, but much of the old god-file logic still lives in `src/video_agent/web/routes/_legacy.py`.
3. `providers/__init__.py` overwrites `__all__` and drops `MockProvider` from the final export list.
4. README and `docs/PROJECT_STATUS.md` still describe stale architecture details such as host Chrome CDP and old `es-419`/Gemini references.
5. `channel.description` is Spain-compatible but too short and weak for Spain-first positioning.
6. SEO language validation should optionally become strict via config.

---

## 1. Fix SEO description normalization preserving timestamp newlines

### Problem

In `src/video_agent/operator.py`, the SEO normalization step currently collapses all single newlines within each paragraph:

```python
cleaned_p = p.replace("\n", " ").strip()
```

This breaks YouTube chapters. A timestamp block like:

```text
00:00 - Intro
01:30 - Activadores
03:00 - Cena
```

can become:

```text
00:00 - Intro 01:30 - Activadores 03:00 - Cena
```

This is a direct upload-quality bug.

### Required implementation

Add a helper in `src/video_agent/operator.py`:

```python
_TIMESTAMP_LINE_RE = re.compile(r"^\s*\d{2}:\d{2}\s+-\s+.+")


def _normalize_youtube_description(desc: str) -> str:
    ...
```

Behavior:

- Normalize `\r\n` and `\r` to `\n`.
- Preserve timestamp lines as separate lines.
- Preserve paragraph breaks.
- Collapse excessive spaces inside non-timestamp lines.
- Remove trailing spaces.
- Do not combine timestamp lines into one paragraph.
- Do not remove blank lines between major sections.

Suggested approach:

```python
def _normalize_youtube_description(desc: str) -> str:
    desc = desc.replace("\r\n", "\n").replace("\r", "\n")
    lines = desc.split("\n")
    out: list[str] = []

    for line in lines:
        raw = line.strip()
        if not raw:
            if out and out[-1] != "":
                out.append("")
            continue

        if _TIMESTAMP_LINE_RE.match(raw):
            out.append(" ".join(raw.split()))
        else:
            out.append(" ".join(raw.split()))

    # Collapse 3+ blank lines to 2 blank lines maximum.
    cleaned: list[str] = []
    blank_count = 0
    for line in out:
        if line == "":
            blank_count += 1
            if blank_count <= 1:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)

    return "\n".join(cleaned).strip() + "\n"
```

Then replace the current description normalization block with:

```python
if "description" in parsed and isinstance(parsed["description"], str):
    parsed["description"] = _normalize_youtube_description(parsed["description"])
```

### Tests

Add tests in a suitable file, e.g. `tests/test_operator_seo_normalization.py`.

Required test cases:

```python
def test_normalize_youtube_description_preserves_timestamp_lines():
    raw = """Dormir mejor después de los 45 puede empezar antes.\n\n00:00 - La noche empieza antes\n01:30 - Activadores de la tarde\n03:00 - Cena que acompaña\n\nSuscríbete al canal."""
    normalized = _normalize_youtube_description(raw)
    assert "00:00 - La noche empieza antes\n01:30 - Activadores de la tarde\n03:00 - Cena que acompaña" in normalized
    assert "La noche empieza antes 01:30" not in normalized
```

Also test:

- Windows newlines `\r\n` are normalized to `\n`.
- Multiple spaces are collapsed.
- Excess blank lines are reduced but section separation remains readable.

### Acceptance criteria

- Timestamps remain one per line after promotion.
- YouTube chapters are not broken by internal normalization.
- Existing title/thumbnail normalization still works.

---

## 2. Continue route refactor: reduce dependence on `_legacy.py`

### Problem

`src/video_agent/web/app.py` is now clean, but route modules such as `run.py` and `stages.py` still mount legacy routes by filtering paths from `_legacy.py`.

This means the god-file problem was moved from `app.py` to:

```text
src/video_agent/web/routes/_legacy.py
```

Do not attempt to remove `_legacy.py` in one risky rewrite. Incrementally move real handlers out of it.

### Required implementation plan

Refactor in phases. Start with the smallest safe modules.

#### Phase 2.1 — Extract config/env routes

Move these from `_legacy.py` into `src/video_agent/web/routes/config.py`:

- `/config/env`
- `/config/env/bootstrap`
- any env helper used only by config routes:
  - `_env_path`
  - `_env_example_path`
  - `_env_editor_enabled`
  - `_require_env_editor`
  - `_mask_env_value`
  - `_mask_env_content`

If helpers are shared by tests or other modules, place them in:

```text
src/video_agent/web/services/env_config.py
```

Keep the public behavior unchanged.

#### Phase 2.2 — Extract run routes

Move real handler implementations for:

- `/jobs/{job_id}/run-all`
- `/run-batch`

from `_legacy.py` into `src/video_agent/web/routes/run.py` or a service module:

```text
src/video_agent/web/services/run_commands.py
```

Keep the existing queue behavior:

- Production mode should enqueue `/run-all` and return quickly.
- Test mode behavior can remain direct execution if current tests depend on it.

#### Phase 2.3 — Extract stage routes gradually

Move stage handlers into:

```text
src/video_agent/web/routes/stages.py
src/video_agent/web/services/stage_commands.py
```

Prioritize the long-running routes first:

- `/jobs/{job_id}/stages/script/run`
- `/jobs/{job_id}/stages/scenes/run`
- `/jobs/{job_id}/stages/seo/run`
- `/jobs/{job_id}/stages/render/run`
- `/jobs/{job_id}/stages/thumbnail/run`

Do not change behavior yet. This is mainly structural.

### Tests

Add or update route tests to prove endpoints still exist and return compatible shapes.

Minimum tests:

- `GET /health` still works.
- `GET /` still returns dashboard HTML.
- Config env GET masks secrets.
- Config env POST requires `ENABLE_ENV_EDITOR=true`.
- `/jobs/{job_id}/run-all` still returns expected enqueue/direct response.
- At least one stage route still works or returns the same controlled missing-input error as before.

### Acceptance criteria

- `app.py` remains a shell.
- `_legacy.py` is smaller after the change.
- No endpoint path changes.
- No response schema changes.
- Existing tests pass.

---

## 3. Fix provider exports

### Problem

`src/video_agent/providers/__init__.py` currently imports `MockProvider`, sets `__all__ = ["MockProvider"]`, then imports adapters/interfaces and overwrites `__all__` without `MockProvider`.

This can break code that imports `MockProvider` from `video_agent.providers`.

### Required implementation

Replace the file with a clean export list:

```python
from video_agent.providers.mock import MockProvider
from video_agent.providers.browser_client_adapter import (
    BrowserClientImageProvider,
    BrowserClientKeywordScorer,
    BrowserClientLLMProvider,
)
from video_agent.providers.interfaces import (
    ImageProvider,
    KeywordScorer,
    LLMProvider,
    Renderer,
    TTSProvider,
)

__all__ = [
    "MockProvider",
    "BrowserClientImageProvider",
    "BrowserClientKeywordScorer",
    "BrowserClientLLMProvider",
    "ImageProvider",
    "KeywordScorer",
    "LLMProvider",
    "Renderer",
    "TTSProvider",
]
```

### Tests

Add:

```python
def test_provider_exports_include_mock_provider():
    from video_agent.providers import MockProvider
    assert MockProvider is not None
```

Also test adapter/interface exports if practical.

### Acceptance criteria

- `from video_agent.providers import MockProvider` works.
- `__all__` includes `MockProvider` and provider interfaces/adapters.

---

## 4. Update docs to match current architecture

### Problem

Docs still contain stale descriptions:

- Browser worker attaching to host Chrome on port 9222.
- Dedicated host Chrome profile.
- Old Gemini wording in places where Claude is now the QA reviewer.
- Old `es-419` validator language for Vida Plena 45+ even though the channel is now Spain-first `es-ES`.

### Files to update

```text
README.md
docs/PROJECT_STATUS.md
docs/VIDEO_AGENT_V3_STANDALONE_HANDOFF.md
```

Only update files that exist.

### Required wording changes

Replace host Chrome wording with Browser Appliance wording:

```text
browser-runtime container runs Chromium with a persisted profile under browser_profiles/default.
browser-worker connects to browser-runtime over the internal Docker network using CDP.
CDP port 9222 is not published to host.
KasmVNC remains bound to 127.0.0.1:7900 for manual sign-ins.
```

Replace Gemini wording where it refers to the active QA flow:

```text
Claude QA
```

If there are legacy folder names such as `operator/gemini`, explain them as historical compatibility only.

Replace old validator wording:

```text
SEO promotion validates language against channel_config.seo.language.
For Vida Plena 45+, expected language is es-ES.
```

### Acceptance criteria

- README does not describe host Chrome as the current architecture.
- PROJECT_STATUS does not claim Vida Plena 45+ SEO must be `es-419`.
- QA flow is described as ChatGPT writing + Claude QA.
- Any Gemini references are explicitly marked historical/legacy.

---

## 5. Update Spain-first channel description

### Problem

`configs/vida-plena-45/channel.yaml` has a correct but weak description:

```yaml
description: "Salud y bienestar práctico para personas de más de 45 años."
```

It is Spain-compatible but does not clearly signal Spain or the channel pillars.

### Required implementation

Update to:

```yaml
description: "Salud y bienestar práctico para personas de más de 45 años en España. Consejos sencillos sobre alimentación, descanso, movimiento suave y hábitos diarios, sin dietas extremas ni promesas milagro."
```

### Acceptance criteria

- Description includes `en España`.
- Description includes main pillars: alimentación, descanso, movimiento suave, hábitos diarios.
- Description avoids forbidden positioning: `adultos mayores`, `tercera edad`, `ancianos`, `personas mayores`, `abuelos`, `abuelitos`.
- Description avoids overclaim language: `milagro`, `cura`, `garantizado`.

---

## 6. Add optional strict SEO language validation

### Problem

The current validator allows some reworkable language mismatches such as `es-419` as warnings so Claude QA can force rework. This is flexible, but for Spain-first production we may want stricter behavior.

### Required config change

In `configs/vida-plena-45/channel.yaml`, add:

```yaml
seo:
  language: "es-ES"
  strict_language: true
  min_tags: 5
  max_tags: 8
```

Preserve existing `min_tags` and `max_tags`.

### Required validator behavior

In `src/video_agent/operator_validators.py`, update `_validate_seo`:

- Read `strict_language = bool(channel_config.get("seo", {}).get("strict_language", False))`.
- If `strict_language` is true, **any** language mismatch is an error.
- If `strict_language` is false, preserve current behavior: reworkable generic values such as `es-419` can be warnings, nonstandard wrong values can be errors.

Suggested logic:

```python
strict_language = bool(seo_config.get("strict_language", False))
if language != expected_language:
    if strict_language:
        result.errors.append(
            f"language must be '{expected_language}' from channel_config.seo.language, got '{language}'."
        )
    elif str(language) in QA_REWORKABLE_LANGUAGE_VALUES:
        result.warnings.append(...)
    else:
        result.errors.append(...)
```

### Tests

Add tests:

```python
def test_validator_strict_language_rejects_es_419():
    cfg = _spain_config()
    cfg["seo"]["strict_language"] = True
    seo = _valid_seo(language="es-419")
    result = _validate_seo(seo, cfg)
    assert not result.is_valid
    assert "language must be 'es-ES'" in result.format_report()
```

Also keep existing non-strict behavior test.

### Acceptance criteria

- With `strict_language: true`, `language: es-419` blocks promotion.
- With `strict_language: false` or missing, current warning behavior remains.

---

## 7. Change stale QA wording from Gemini to dedicated QA reviewer / Claude

### Problem

In `src/video_agent/operator_validators.py`, `_detect_prefilled_qa` still says:

```text
QA must come from Gemini, not ChatGPT.
```

The active flow is Claude QA, and old Gemini wording can confuse future changes.

### Required implementation

Change the error message to either:

```text
QA must come from Claude, not ChatGPT.
```

or better, more future-proof:

```text
QA must come from the dedicated QA reviewer, not ChatGPT.
```

Use the future-proof wording unless other docs strongly prefer Claude explicitly.

### Tests

If a test asserts the exact message, update it.

### Acceptance criteria

- No active validator error message says QA must come from Gemini.
- Existing prefilled QA protection remains.

---

## 8. Optional: enforce English visual prompts with tests

This may already be implemented. Do not rewrite it if tests already cover it.

If tests are missing, add tests for:

```python
def test_visual_prompt_rejects_spanish_prompt():
    scene = {"visual_prompt": "Persona en una habitación con luz cálida", ...}
    result = _validate_visual_prompt(scene, "scene-01")
    assert not result.is_valid


def test_visual_prompt_accepts_english_prompt():
    scene = {"visual_prompt": "Mature woman in a calm bedroom at dusk, warm tungsten light", ...}
    result = _validate_visual_prompt(scene, "scene-01")
    assert result.is_valid
```

Acceptance criteria:

- Spanish visual prompt is an error.
- English stock-search prompt passes.

---

## 9. Commands to run

Run the relevant tests through Docker if this is the project convention:

```bash
docker compose run --rm video-agent pytest -v
```

If iteration time is too slow, run targeted tests first:

```bash
docker compose run --rm video-agent pytest -v tests/test_operator_validators.py
```

Add the new test files and run them directly.

---

## 10. Non-goals

Do not implement these in this task:

- YouTube upload automation.
- Analytics dashboard.
- New LLM API integration.
- Rewriting the entire orchestrator.
- Large Docker image split.
- Changing title/thumbnail generation logic beyond preserving descriptions and locale validation.
- Removing `_legacy.py` completely in one PR.

---

## 11. Final acceptance checklist

This task is complete only if:

- [ ] SEO description timestamps remain one per line after normalization/promotion.
- [ ] `configs/vida-plena-45/channel.yaml` has Spain-first enriched description.
- [ ] `seo.strict_language: true` is supported and tested.
- [ ] Provider exports include `MockProvider` and all interfaces/adapters.
- [ ] Active docs describe browser-runtime + browser-worker, not host Chrome CDP.
- [ ] Active docs say Claude QA or dedicated QA reviewer, not Gemini QA, except historical notes.
- [ ] No active validator message says “QA must come from Gemini”.
- [ ] `_legacy.py` is reduced or at least one real route group has been extracted without endpoint changes.
- [ ] Tests pass.
