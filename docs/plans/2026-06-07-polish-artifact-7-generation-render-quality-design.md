# Polish Generation and Render Quality Design Document

## Goal
Improve the generation and render quality of the vertical Shorts video pipeline for adults aged 45+ (specifically "Vida Plena 45+") to achieve a 10/10 rating. This involves strict pacing controls, a two-line hook structure, descriptive on-screen labels, reduced dark overlay opacities, readable saveable checklist cards, specific bread visuals, elevated and larger captions, optimized SEO metadata, and strict product score gates.

## Proposed Changes

### 1. Visual Styling & Opacity (remotion)
- **`remotion/src/shorts/ShortLayoutConstants.ts`**:
  - Reduce opacities in `SHORT_OVERLAYS` by 20% to avoid a dark cinematic feel and keep the footage warm, bright, and wellness-focused:
    - `default`: `{fullDarkenOpacity: 0.24, bottomGradientOpacity: 0.48, centerTextScrimOpacity: 0.34}`
    - `dark`: `{fullDarkenOpacity: 0.16, bottomGradientOpacity: 0.42, centerTextScrimOpacity: 0.27}`
    - `bright`: `{fullDarkenOpacity: 0.32, bottomGradientOpacity: 0.53, centerTextScrimOpacity: 0.40}`
  - Move captions up 80px by adjusting `captionZone` Safe zone boundaries:
    - `captionZone`: `{yMin: 1100, yMax: 1380, width: 800}`
- **`remotion/src/shorts/ShortLayouts.tsx`**:
  - Update `Zone` rendering for captions to use `yMin={1100}` and shift `yMax` values up by 80px (e.g. `1100-1300` for `short_pain`, `1100-1320` for `short_tip`) to avoid Shorts UI overlap.
- **`remotion/src/shorts/ShortText.tsx`**:
  - Increase default caption font size by ~14% from `42` to `48` to improve mobile readability.

### 2. Deterministic Validation (`validate_scenes.py`)
- **Pacing Targets**:
  - Update target pacing range in `LAYOUT_DURATION_TARGETS` and `GRAPHIC_LAYOUT_DURATION_TARGETS`:
    - `short_pain` (used for error scenes): `(3.2, 4.0, 4.5)`
    - `graphic_checklist` (used for payoff scene): `(4.2, 5.0, 5.0)`
    - `short_cta`: `(2.4, 2.8, 2.8)`
- **Duration Floor**:
  - In `validate_scene_structure`, add a check that fails validation with a `repairable_error` if a 5-error video has a total duration less than 25.5s.
- **Payoff Layout Enforcer**:
  - In `validate_scene_structure`, enforce that the payoff scene (right before CTA) uses `graphic_checklist` instead of `short_tip` or other layouts for 5-error bread Shorts.
  - Update `build_scene_repair_plan` to provide explicit instructions for converting the payoff scene to `graphic_checklist` with the exact checklist items.

### 3. Strict Quality Gates (`qa.py`)
- Define `REQUIRED_PRODUCT_SCORE_THRESHOLDS` mapping the required minimum scores for each product dimension:
  ```python
  REQUIRED_PRODUCT_SCORE_THRESHOLDS = {
      "audience_fit_45_plus": 9.0,
      "hook_strength": 9.0,
      "visual_specificity": 9.0,
      "clarity": 9.0,
      "retention_pacing": 9.0,
      "natural_spanish": 9.0,
      "saveability": 8.5,
  }
  ```
- Update `normalize_gemini_scenes_qa` and `summarize_product_scores` to compare scores against these thresholds and block rendering/fail QA if any threshold is not met.

### 4. Prompt Engineering (`prompts.py`)
- **`short_scene_prompt_v6`**:
  - Update instructions to require the two-line hook (Title: "NO ES EL PAN", Subtitle: "MIRA CÓMO LO USAS"), specific descriptive error labels, bread visual specificity, payoff checklist structure, and pacing limits.
- **`short_seo_prompt`**:
  - Instruct the model to prefer specific SEO titles ("5 errores con el pan después de los 45" or "Pan después de los 45: 5 errores comunes") and exactly the 5 requested hashtags.
- **`gemini_scenes_qa_prompt`**:
  - Make Gemini QA reviewer aware of the new strict thresholds to ensure critical grading.

## Verification Plan
- Run existing tests to ensure no regressions.
- Add new tests in `tests/test_shorts_build.py` or `tests/test_shorts_retry_memory.py` verifying the new duration gates, payoff layout enforcement, and strict product score gates.
