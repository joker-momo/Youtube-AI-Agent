# Design Doc — Vida Plena 45+ Scene Narration-Fit Repair (Spec v1.2)

## Status
Approved by User with 4 constraints:
1. Defensive parsing for `product_scores`.
2. Safe rename for `duration_sum_warning` / `total_duration_normalized` to maintain compatibility.
3. Auto-extend scene duration only when within layout duration caps.
4. Escalate back to script compression with a specific repair plan if `scene_narration_fit` fails $\ge 2$ times.

---

## Proposed Architecture & Component Changes

### 1. Scene Duration Auto-Extension & Warnings (`validate_scenes.py`)
- **Warning Rename**: Rename `duration_sum_warning` to `total_duration_normalized`. For compatibility, the validator can still accept/support both warnings in tests or other references.
- **Auto-Extension**: Implement `repair_scene_duration_if_possible(scene)`:
  - Estimate narration time using calibrated WPS (2.25).
  - Compute `required = round(est + 0.3, 1)`.
  - Fetch hard max cap for scene layout.
  - If `required <= cap` and current `duration_sec < required`, auto-extend `duration_sec` to `required`.
  - If `required > cap`, do not extend, letting it fail `scene_narration_fit` during validation.

### 2. Action-Specific Scene Repair Hints (`validate_scenes.py`)
- Inside `build_scene_repair_plan`, return detailed, layout-specific instructions for narration-fit failures:
  - `short_hook`: Suggest hook replacement "¿Pan marrón? No basta." and keeping the longer idea in on-screen text or the next scene.
  - `graphic_label_callout`: Suggest shortening to "Busca harina integral al principio." and moving detail to `layout_payload` callouts, or splitting into two specific scenes.
  - `short_quote`: Suggest shortening to "La etiqueta ayuda a elegir."
  - `short_cta`: Suggest shortening to "Guárdalo para la compra." or "Úsalo en el súper."

### 3. Prompt Updates (`prompts.py`)
- **Scene Prompt (`short_scene_prompt_v6`)**:
  - Add explicit `SCENE NARRATION WORD CAPS` (hook: 5-6 words, quote: 8-10 words, etc.).
  - Add rule permitting faithful compression/shortening of narration beats to fit timings.
- **QA Prompt (`gemini_scenes_qa_prompt`)**:
  - Update schema to return `product_scores` containing 7 dimensions (`audience_fit_45_plus`, `hook_strength`, `visual_specificity`, `clarity`, `retention_pacing`, `natural_spanish`, `saveability`) as integers (0 to 10).
  - Add scoring rules and thresholds (average $\ge 8$, min $\ge 7$) in the prompt rules.

### 4. QA Response Parsing & Validation (`qa.py`)
- Implement a defensive parser `parse_defensive_score(val)`:
  - Converts string/float/int safely.
  - Handles string patterns like `"8/10"` or `"8.5"`.
- Verify presence of all 7 `product_scores` keys.
- Check thresholds (average $\ge 8$, min $\ge 7$). Return `repairable_error` issues if not met.
- Ensure safety, source fidelity, and audio-fit remain as `blocking_error`.

### 5. Control Flow & Script Escalation (`short_builder.py`)
- Run `repair_scene_duration_if_possible` on generated scenes before running validation.
- Track `scene_narration_fit` failures.
- If it fails $\ge 2$ times, break the scenes loop, set `script_feedback` to the detailed `SCRIPT COMPRESSION REQUIRED` plan, and continue/retry the outer script generation loop.
- Update `best-candidate fallback` to check that the fallback candidate's product quality scores also meet the thresholds (average $\ge 8$, min $\ge 7$).

---

## Verification Plan

### Automated Unit Tests
- Update `test_total_duration_sec_normalization` to expect `total_duration_normalized`.
- Add regression tests covering:
  - Defensive `product_scores` parser.
  - Auto-extension within cap vs. failure exceeding cap.
  - Action-specific repair hints.
  - Script escalation loop after 2 scene narration fit failures.
