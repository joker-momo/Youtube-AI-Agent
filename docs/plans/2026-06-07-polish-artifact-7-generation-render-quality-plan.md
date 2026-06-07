# Polish Generation and Render Quality Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Polish artifact-7 generation/render quality to reach 10/10 for Vida Plena 45+ by implementing pacing targets, two-line hook, descriptive on-screen labels, reduced dark overlay, checklist payoff cards, specific bread visuals, larger/higher captions, optimized SEO, and strict score gates.

**Architecture:**
- Update `ShortLayoutConstants.ts`, `ShortLayouts.tsx`, and `ShortText.tsx` to reduce overlay opacity, move captions up, and enlarge caption text.
- Modify `validate_scenes.py` to enforce new pacing targets, a 25.5s duration floor for 5-error videos, and `graphic_checklist` payoff scene layout.
- Modify `qa.py` to enforce strict QA score thresholds.
- Update `prompts.py` to instruct ChatGPT and Gemini on these new requirements.

**Tech Stack:** React, Remotion, Python, Pytest

---

### Task 1: Update Visual Styling & Opacity in Remotion

**Files:**
- Modify: `remotion/src/shorts/ShortLayoutConstants.ts`
- Modify: `remotion/src/shorts/ShortLayouts.tsx`
- Modify: `remotion/src/shorts/ShortText.tsx`

**Step 1: Reduce overlay opacity and adjust caption zone in `ShortLayoutConstants.ts`**
Reduce opacities in `SHORT_OVERLAYS` by 20% and shift `captionZone` up.
```typescript
export const SHORT_OVERLAYS = {
  default: {fullDarkenOpacity: 0.24, bottomGradientOpacity: 0.48, centerTextScrimOpacity: 0.34},
  dark: {fullDarkenOpacity: 0.16, bottomGradientOpacity: 0.42, centerTextScrimOpacity: 0.27},
  bright: {fullDarkenOpacity: 0.32, bottomGradientOpacity: 0.53, centerTextScrimOpacity: 0.40},
} as const;

// ...
captionZone: {yMin: 1100, yMax: 1380, width: 800},
```

**Step 2: Move caption zones up in `ShortLayouts.tsx`**
Update all caption `Zone` yMin/yMax ranges to match the new caption zone:
- Shift `yMin` from `1180` to `1100`.
- Shift `yMax` up by 80px (e.g. `1380` -> `1300`, `1400` -> `1320`).

**Step 3: Increase caption font size in `ShortText.tsx`**
Change `fontSize = 42` to `fontSize = 48` for `CaptionText`.

---

### Task 2: Implement Deterministic Pacing and Payoff Layout in `validate_scenes.py`

**Files:**
- Modify: `src/video_agent/shorts/validate_scenes.py`

**Step 1: Update `LAYOUT_DURATION_TARGETS` and `GRAPHIC_LAYOUT_DURATION_TARGETS`**
Adjust pacing targets:
- `short_pain`: `(3.2, 4.0, 4.5)`
- `graphic_checklist`: `(4.2, 5.0, 5.0)`
- `short_cta`: `(2.4, 2.8, 2.8)`

**Step 2: Enforce 25.5s duration floor and graphic_checklist payoff**
In `validate_scene_structure`, add validation logic:
- If it's a 5-error bread Short, enforce total duration >= 25.5s.
- Enforce that the payoff scene (scene before CTA) uses layout `graphic_checklist`.
- Add corresponding instructions in `build_scene_repair_plan` for `payoff_layout`.

---

### Task 3: Enforce Strict QA Thresholds in `qa.py`

**Files:**
- Modify: `src/video_agent/shorts/qa.py`

**Step 1: Define thresholds and update validation logic**
Add `REQUIRED_PRODUCT_SCORE_THRESHOLDS` mapping and update `normalize_gemini_scenes_qa` and `summarize_product_scores` to fail QA if any threshold is not met.

---

### Task 4: Update Prompt Templates in `prompts.py`

**Files:**
- Modify: `src/video_agent/shorts/prompts.py`

**Step 1: Update scene, SEO, and QA prompts**
- Instruct `short_scene_prompt_v6` to use the two-line hook, descriptive labels, payoff checklist, visual specificity, and pacing targets.
- Instruct `short_seo_prompt` on preferred titles and hashtags.
- Update `gemini_scenes_qa_prompt` with strict score expectations.

---

### Task 5: Verification & Testing

**Files:**
- Test: `tests/test_shorts_build.py`
- Test: `tests/test_shorts_retry_memory.py`

**Step 1: Run pytest and verify all tests pass**
Run: `uv run pytest`
Expected: PASS
