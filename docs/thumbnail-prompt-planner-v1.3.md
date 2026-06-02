# Spec: Thumbnail Prompt Planner v1.3 for Vida Plena 45+

## 0. Purpose

Improve the existing ChatGPT image thumbnail generation flow so it can create **three meaningfully different, topic-aware YouTube thumbnail images** for many content types on the `Vida Plena 45+` channel.

This spec targets the current long-form thumbnail image flow in:

```text
src/video_agent/orchestrator/stages.py
```

Current behavior already generates up to three thumbnail images from `seo.title_variants`.

Required improvement:

```text
topic / SEO variants
→ thumbnail topic classifier
→ visual category preset
→ persona + locale rules
→ 3 distinct visual strategies
→ 3 complete prompts
→ ChatGPT image generation
→ enforce 1920x1080
→ log prompts
→ save thumbnail_1.jpg, thumbnail_2.jpg, thumbnail_3.jpg
```

---

## 0.1 Already shipped in P0/P1

This spec extends the P0/P1 thumbnail hardening already shipped.

Do **not** re-implement these as separate behavior. Keep the existing behavior and move/route it through the planner where appropriate.

Already shipped / expected to remain true:

1. Variant title binding is already fixed:
   - each prompt should use `title_variants[i].title`
   - fallback to top-level `seo.title` only when missing
2. Three visual strategies already exist conceptually:
   - `face_driven`
   - `object_driven`
   - `comparison_driven`
3. Spain-first subject language is already preferred over `Hispanic or Latina`.
4. Spanish diacritics preservation instruction is already included.
5. Hardcoded plate/energy example has been removed.
6. Generated thumbnails are post-processed to exact `1920x1080`.
7. Prompt markdown is logged per variant.

The real delta in v1.3 is:

```text
taxonomy
+ deterministic topic classifier
+ secondary category contract
+ risk-level inference contract
+ category visual presets
+ avoid-list merge rules
+ planner metadata logging
+ category-aware prompt plans
```


---

## 1. Problems to fix

### 1.1 Variant title binding bug

Current thumbnail generation uses the same top-level `seo.title` for all three image prompts and only varies `thumbnail_text`.

Required fix:

Each thumbnail variant must use its own:

```json
{
  "title": "...",
  "thumbnail_text": "..."
}
```

from `seo.title_variants[i]`.

The image prompt for variant `i` must use:

```text
variant_title = seo.title_variants[i].title
variant_thumbnail_text = seo.title_variants[i].thumbnail_text
```

Do not use the top-level `seo.title` for all three variants unless a variant title is missing.

---

### 1.2 Three variants are not visually different enough

Current behavior creates three prompts that can be too similar because the only guaranteed difference is text.

Required fix:

Use three fixed visual strategies:

```text
variant 1 → face_driven
variant 2 → object_driven
variant 3 → comparison_driven
```

Each strategy must change composition, subject/object priority, and visual intent.

---

### 1.3 Spain-first mismatch

Current prompt hardcodes:

```text
Hispanic or Latina woman aged 45-55
```

This is not ideal for a Spain-first channel.

Required fix:

Use Spain/Mediterranean visual language:

```text
Mediterranean Spanish adult aged 45–65
```

or, when a woman is appropriate:

```text
Mediterranean Spanish woman aged 45–60
```

Avoid visual language that implies US Hispanic / Latin America unless the channel config explicitly asks for that locale.

---

### 1.4 Spanish diacritics risk

Image models often drop or distort Spanish accents and punctuation.

Required fix:

Every text-baked thumbnail prompt must include:

```text
Render the hook text EXACTLY as written, preserving Spanish accents and punctuation:
ñ, á, é, í, ó, ú, ü, ¿, ¡.
```

---

### 1.5 Hardcoded pain-angle example biases topics

Current prompt contains food/plate-specific examples that can bias non-food topics.

Required fix:

Remove hardcoded examples such as:

```text
plate taking energy after 45
```

Replace with category-driven visual guidance.

---

### 1.6 Generated image size is not enforced

Current code converts PNG to JPG but does not enforce exact output dimensions.

Required fix:

After opening the generated PNG, crop/resize to exact `1920x1080` before saving JPG.

```python
from PIL import ImageOps, Image

img = Image.open(source_path).convert("RGB")
img = ImageOps.fit(img, (1920, 1080), method=Image.Resampling.LANCZOS)
img.save(jpg_path, "JPEG", quality=94, optimize=True)
```

---

### 1.7 Prompt is not persisted per variant

Current event logging records generated path/text, but not the actual prompt.

Required fix:

Write each prompt to:

```text
jobs/<job_id>/operator/chatgpt/thumbnail_prompt_1.md
jobs/<job_id>/operator/chatgpt/thumbnail_prompt_2.md
jobs/<job_id>/operator/chatgpt/thumbnail_prompt_3.md
```

---

## 2. Non-goals

Do not rewrite the whole thumbnail pipeline.

Do not remove current 3-image generation.

Do not switch to background-only plus code overlay in this phase.

Do not add OCR as a hard dependency in this phase.

Do not call live image generation in unit tests.

Do not change SEO schema except optional metadata fields if needed.

---

## 3. High-level flow

```text
seo.json
  └─ title_variants[0..2]
      ├─ title
      └─ thumbnail_text

for each variant:
  normalize variant
  classify thumbnail topic
  select visual category preset
  select persona and locale rules
  select visual strategy by index
  build prompt
  write prompt log
  generate image
  ImageOps.fit image to 1920x1080
  save outputs/thumbnail_N.jpg

copy first successful variant:
  outputs/thumbnail_N.jpg → thumbnail.jpg

update seo.thumbnail_path
```

---

## 4. Files to modify or add

### Required

```text
src/video_agent/orchestrator/stages.py
```

### Recommended new module

```text
src/video_agent/thumbnail_planner.py
```

This module should contain:

```python
class ThumbnailTopicProfile(TypedDict): ...
class ThumbnailVisualPreset(TypedDict): ...
class ThumbnailPromptPlan(TypedDict): ...

def classify_thumbnail_topic(title: str, thumbnail_text: str = "") -> ThumbnailTopicProfile: ...
def select_visual_preset(category: str) -> ThumbnailVisualPreset: ...
def normalize_thumbnail_variants(seo: dict) -> list[dict]: ...
def build_thumbnail_prompt(plan: ThumbnailPromptPlan) -> str: ...
def plan_thumbnail_prompts(seo: dict, channel_config: dict) -> list[ThumbnailPromptPlan]: ...
```

### Optional

```text
configs/vida-plena-45/thumbnail-visual-dna.yaml
```

If not added as a separate YAML file, keep the presets in Python constants for Phase 1.

---

## 5. Thumbnail topic taxonomy

Use this taxonomy for `Vida Plena 45+`.

The classifier should return:

```json
{
  "primary_category": "...",
  "secondary_category": "...",
  "keywords": [],
  "risk_level": "lifestyle|soft_health|medical_sensitive",
  "age_signal": "45+|50+|60+|unknown"
}
```

### 5.1 Categories

```text
food_choice
functional_foods_superfoods
shopping_label_choice
protein_muscle
fiber_digestion
hydration
blood_sugar_diabetes
blood_pressure_circulation_heart
sleep_rest
energy_fatigue
movement_stiffness
joint_pain_body_signal
walking_cardio
stress_mind
brain_memory_cognition
weight_loss_metabolism
aging_longevity_bad_habits
daily_routine
mistake_warning
myth_truth
general_45plus_lifestyle
```

### 5.2 Keyword mapping

#### `food_choice`

Triggers:

```text
pan, tostada, desayuno, cena, comida, plato, aceite, yogur, fruta, verduras, arroz, pasta, integral
```

Visuals:

```text
Spanish home kitchen, dining table, bread, toast, Mediterranean plate, olive oil, fruit, yogurt
```

---

#### `functional_foods_superfoods`

Triggers:

```text
café, cafe, chía, chia, avena, yogur, cúrcuma, curcuma, frutos secos, limón, limon, aceite de oliva
```

Visuals:

```text
specific food/drink object large and clear, kitchen table, hand holding cup/bowl, no product labels
```

---

#### `shopping_label_choice`

Triggers:

```text
etiqueta, supermercado, compra, elegir, mejor pan, integral, producto, ingredientes
```

Visuals:

```text
Spanish supermarket aisle, hand comparing products, labels blurred/unreadable, realistic basket
```

---

#### `protein_muscle`

Triggers:

```text
proteína, proteina, músculo, musculo, fuerza, sarcopenia, masa muscular, después de los 60
```

Visuals:

```text
simple protein meal, resistance band, light dumbbell, home exercise corner, strong but realistic adult
```

Avoid:

```text
bodybuilder, extreme gym, unrealistic abs
```

---

#### `fiber_digestion`

Triggers:

```text
fibra, digestión, digestion, estreñimiento, estrenimiento, hinchazón, hinchazon, intestino, barriga
```

Visuals:

```text
legumes, vegetables, water glass, gentle belly cue, kitchen table
```

Avoid:

```text
medical intestine graphics, toilet humor, embarrassing imagery
```

---

#### `hydration`

Triggers:

```text
agua, hidratación, hidratacion, sed, piel seca, beber, vaso de agua
```

Visuals:

```text
glass of water, bottle, morning kitchen, bedside water glass
```

---

#### `blood_sugar_diabetes`

Triggers:

```text
azúcar, azucar, glucosa, diabetes, prediabetes, pico de azúcar, pico de azucar, insulina, regular su azúcar, regular su azucar
```

Visuals:

```text
food choice, post-meal walking, chia, bread, plate, gentle caution
```

Avoid:

```text
syringe, insulin injection, hospital, scary blood glucose monitor close-up, medical panic
```

---

#### `blood_pressure_circulation_heart`

Triggers:

```text
presión alta, presion alta, tensión, tension, circulación, circulacion, corazón, corazon, pantorrilla, piernas, sangre
```

Visuals:

```text
walking shoes, calf exercise, coffee cup if topic mentions coffee, park path, home chair exercise
```

Avoid:

```text
heart attack imagery, ECG monitor, emergency room, fake red veins
```

---

#### `sleep_rest`

Triggers:

```text
dormir, sueño, sueno, insomnio, despertar cansado, noche, descanso, rutina nocturna
```

Visuals:

```text
bedroom, bedside lamp, alarm clock, herbal tea, phone face down, warm evening light
```

---

#### `energy_fatigue`

Triggers:

```text
cansancio, cansado, energía, energia, bajón, bajon, fatiga, agotamiento
```

Visuals:

```text
morning kitchen, sofa, coffee, sunlight, adult realizing a habit affects energy
```

---

#### `movement_stiffness`

Triggers:

```text
rigidez, rígido, rigido, estirar, movilidad, cuello, espalda, cadera, hombros, levantarte
```

Visuals:

```text
gentle stretching, chair stretch, yoga mat, home living room, park
```

Avoid:

```text
injury drama, hospital brace, severe pain
```

---

#### `joint_pain_body_signal`

Triggers:

```text
dolor, rodilla, espalda, manos, cuello, articulaciones, señales del cuerpo
```

Visuals:

```text
close-up hand on knee/shoulder/back, expressive face, body signal without medical fear
```

---

#### `walking_cardio`

Triggers:

```text
caminar, paseo, pasos, andar, escaleras, cardio, caminar después de comer
```

Visuals:

```text
walking shoes, park path, stairs, outdoor Mediterranean street, gentle movement
```

---

#### `stress_mind`

Triggers:

```text
estrés, estres, ansiedad, mente acelerada, calma, respiración, respiracion, preocupación
```

Visuals:

```text
quiet sofa, balcony, notebook, tea cup, phone face down, breathing posture
```

Avoid:

```text
panic attack imagery, crying, despair, psychiatric clinic
```

---

#### `brain_memory_cognition`

Triggers:

```text
memoria, demencia, Alzheimer, olvido, cerebro, señales tempranas, concentración, concentracion
```

Visuals:

```text
keys, calendar, reading glasses, notebook, adult concerned but dignified
```

Avoid:

```text
scary brain CGI, hospital, dementia stigma, helpless/frail senior imagery
```

---

#### `weight_loss_metabolism`

Triggers:

```text
adelgazar, perder peso, grasa, metabolismo, barriga, cintura, truco para adelgazar
```

Visuals:

```text
simple habit, measuring tape subtle, plate choice, walking shoes, realistic lifestyle cue
```

Avoid:

```text
before/after body transformation, body shame, extreme scale panic
```

---

#### `aging_longevity_bad_habits`

Triggers:

```text
envejecer, envejecimiento, más rápido, mas rapido, malos hábitos, malos habitos, te hace viejo, longevidad
```

Visuals:

```text
bad daily habit contrast, late-night phone, poor snack, sitting too long, tired face realizing a habit
```

Avoid:

```text
decrepit elderly stereotype, scary aging face morph, humiliation
```

---

#### `daily_routine`

Triggers:

```text
rutina, hábito, habito, mañana, manana, tarde, noche, cada día, todos los días
```

Visuals:

```text
calendar, checklist, kitchen counter, morning/evening routine objects
```

---

#### `mistake_warning`

Triggers:

```text
error, errores, malo, malos, evita, nunca, no hagas, cuidado, ignorado, nadie reconoce
```

Visuals:

```text
warning expression, hand gesture stop/wait, clear object cue, contrast between right/wrong choice
```

---

#### `myth_truth`

Triggers:

```text
mito, verdad, no es, nadie te dice, revela, engaña, confunde
```

Visuals:

```text
side-by-side comparison, appearance vs reality, two choices, simple contrast
```

---

#### `general_45plus_lifestyle`

Fallback when no category is confident.

Visuals:

```text
Mediterranean Spanish adult 45–65 at home, expressive face, clean thumbnail composition
```

Main prop:

```text
No specific prop required. Use only one simple daily-life object if clearly supported by the title.
```

Rules:

```text
Do not force food, plate, bread, sleep, exercise, or medical imagery unless clearly supported by the title.
```

---

## 6. Category classifier rules

The classifier must be deterministic.

Same input must always produce the same:

```text
primary_category
secondary_category
risk_level
age_signal
matched_keywords
```

Do not use LLM classification in Phase 1.

---

### 6.1 Normalize text

Use accent-insensitive matching while preserving original text for prompts.

```python
import re
import unicodedata

def normalize_for_thumbnail_classification(text: str) -> str:
    text = str(text or "").lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip()
    return text
```

This makes both forms match:

```text
azúcar / azucar
presión / presion
circulación / circulacion
cúrcuma / curcuma
```

---

### 6.2 Category priority order

When scores tie, use this deterministic priority order.

The order favors safety-critical categories first, then specific content categories, then general fallback.

```python
CATEGORY_PRIORITY = [
    "brain_memory_cognition",
    "blood_sugar_diabetes",
    "blood_pressure_circulation_heart",
    "weight_loss_metabolism",
    "joint_pain_body_signal",
    "movement_stiffness",
    "protein_muscle",
    "walking_cardio",
    "sleep_rest",
    "stress_mind",
    "fiber_digestion",
    "hydration",
    "aging_longevity_bad_habits",
    "functional_foods_superfoods",
    "shopping_label_choice",
    "food_choice",
    "energy_fatigue",
    "daily_routine",
    "mistake_warning",
    "myth_truth",
    "general_45plus_lifestyle",
]
```

Reason:

- `demencia`, `diabetes`, `presión alta`, `corazón` need safer visuals and must not be downgraded to generic food/lifestyle.
- `joint_pain_body_signal` and `movement_stiffness` rank above `aging_longevity_bad_habits` so topics such as "dolor de espalda por malos hábitos" keep the body-signal visual center.
- `functional_foods_superfoods` should usually be secondary when paired with a health axis such as sugar/circulation.
- `general_45plus_lifestyle` is always last.

---

### 6.3 Score categories

Pseudo:

```python
def score_categories(text: str) -> tuple[dict[str, int], dict[str, list[str]]]:
    scores: dict[str, int] = {}
    matches: dict[str, list[str]] = {}

    for category, triggers in CATEGORY_TRIGGERS.items():
        matched = [trigger for trigger in triggers if trigger in text]
        scores[category] = len(matched)
        matches[category] = matched

    return scores, matches
```

Primary category:

```python
def pick_primary_category(scores: dict[str, int]) -> str:
    best_score = max(scores.values() or [0])
    if best_score <= 0:
        return "general_45plus_lifestyle"

    tied = [cat for cat, score in scores.items() if score == best_score]
    return min(tied, key=lambda cat: CATEGORY_PRIORITY.index(cat))
```

---

### 6.4 Secondary category

`secondary_category` is optional and must be deterministic.

Rules:

```python
LOW_SIGNAL_SECONDARY_CATEGORIES = {
    "daily_routine",
    "mistake_warning",
    "myth_truth",
}

def pick_secondary_category(scores: dict[str, int], primary: str) -> str | None:
    primary_score = int(scores.get(primary, 0))

    candidates = []
    for cat, score in scores.items():
        if cat == primary or cat == "general_45plus_lifestyle":
            continue
        if score <= 0:
            continue

        # Specific health/food axes can be useful as secondary with one strong trigger.
        # Generic intent categories need stronger evidence to avoid noisy secondaries.
        if cat in LOW_SIGNAL_SECONDARY_CATEGORIES and score < 2:
            continue

        # If primary has multiple hits, single-trigger secondaries are allowed only
        # when they are category-specific, not generic.
        if primary_score >= 2 and score < max(1, primary_score * 0.5):
            if cat in LOW_SIGNAL_SECONDARY_CATEGORIES:
                continue

        candidates.append(cat)

    if not candidates:
        return None

    return min(candidates, key=lambda cat: CATEGORY_PRIORITY.index(cat))
```

Examples:

```text
café + circulación
→ primary: blood_pressure_circulation_heart
→ secondary: functional_foods_superfoods

chía + azúcar
→ primary: blood_sugar_diabetes
→ secondary: functional_foods_superfoods

pan + etiqueta/supermercado
→ primary: shopping_label_choice or food_choice depending score
→ secondary: food_choice or shopping_label_choice

ejercicios + pico de azúcar
→ primary: blood_sugar_diabetes
→ secondary: walking_cardio or movement_stiffness
```

If the secondary category would create conflicting visual instructions, merge only its `main_prop` hint and avoid list; do not override the primary scene.

Generic intent categories such as `daily_routine`, `mistake_warning`, and `myth_truth` require at least two hits to become secondary. This reduces false positives from common words like `rutina` or `revela`.

---

### 6.5 Risk level inference

Risk level is inferred from both matched text and primary category.

```python
MEDICAL_SENSITIVE_KEYWORDS = [
    # Use normalized, accent-stripped forms only.
    "demencia",
    "alzheimer",
    "diabetes",
    "presion alta",
    "tension alta",
    "corazon",
    "azucar",
    "glucosa",
    "insulina",
]

MEDICAL_SENSITIVE_CATEGORIES = {
    "brain_memory_cognition",
    "blood_sugar_diabetes",
    "blood_pressure_circulation_heart",
}

SOFT_HEALTH_CATEGORIES = {
    "weight_loss_metabolism",
    "joint_pain_body_signal",
    "movement_stiffness",
    "walking_cardio",
    "sleep_rest",
    "stress_mind",
    "energy_fatigue",
    "protein_muscle",
    "fiber_digestion",
    "hydration",
}
```

Function:

```python
def infer_risk_level(primary: str, normalized_text: str) -> str:
    if primary in MEDICAL_SENSITIVE_CATEGORIES:
        return "medical_sensitive"

    if any(keyword in normalized_text for keyword in MEDICAL_SENSITIVE_KEYWORDS):
        return "medical_sensitive"

    if primary in SOFT_HEALTH_CATEGORIES:
        return "soft_health"

    return "lifestyle"
```

If a lifestyle or food topic mentions diabetes, dementia, blood pressure, heart, insulin, or glucose, risk must become `medical_sensitive`.

---

### 6.6 Age signal inference

`age_signal` is used to tune persona age range.

```python
AGE_CONTEXT_PATTERNS = {
    "60+": [
        r"despues de los\s+60\b",
        r"despues de\s+60\b",
        r"mayores de\s+60\b",
        r"mas de\s+60\b",
        r"a partir de los\s+60\b",
        r"\b60\s*\+\b",
    ],
    "50+": [
        r"despues de los\s+50\b",
        r"despues de\s+50\b",
        r"mayores de\s+50\b",
        r"mas de\s+50\b",
        r"a partir de los\s+50\b",
        r"\b50\s*\+\b",
    ],
    "45+": [
        r"despues de los\s+45\b",
        r"despues de\s+45\b",
        r"mayores de\s+45\b",
        r"mas de\s+45\b",
        r"a partir de los\s+45\b",
        r"\b45\s*\+\b",
    ],
}

def infer_age_signal(normalized_text: str) -> str:
    # Match age context phrases, not arbitrary standalone numbers.
    # This avoids false positives such as "60 minutos", "60 segundos",
    # "60%", "60 secretos", or "60 dias".
    for signal in ("60+", "50+", "45+"):
        for pattern in AGE_CONTEXT_PATTERNS[signal]:
            if re.search(pattern, normalized_text):
                return signal
    return "unknown"
```

Notes:

- `60 minutos`, `60 segundos`, `60%`, `60 secretos`, and `60 días` must not imply age `60+`.
- Phase 1 uses phrase-level age detection, not standalone numeric detection.
- If no explicit age phrase exists, use `unknown`.

Persona mapping:

```python
AGE_RANGE_BY_SIGNAL = {
    "45+": "45–60",
    "50+": "50–65",
    "60+": "55–70",
    "unknown": "45–65",
}
```

Prompt persona should use:

```text
Mediterranean Spanish adult aged {AGE_RANGE_BY_SIGNAL[age_signal]}
```

Do not make `60+` visuals frail, helpless, or sad.

---

### 6.7 Full classifier

```python
def classify_thumbnail_topic(title: str, thumbnail_text: str = "") -> ThumbnailTopicProfile:
    original = f"{title} {thumbnail_text}"
    text = normalize_for_thumbnail_classification(original)

    scores, matches = score_categories(text)
    primary = pick_primary_category(scores)
    secondary = pick_secondary_category(scores, primary)
    risk_level = infer_risk_level(primary, text)
    age_signal = infer_age_signal(text)

    return {
        "primary_category": primary,
        "secondary_category": secondary,
        "keywords": matches.get(primary, []),
        "matched_keywords_by_category": matches,
        "risk_level": risk_level,
        "age_signal": age_signal,
    }
```


---

## 7. Visual strategy per variant

Phase 1 uses a fixed strategy order to guarantee visual diversity:

```text
variant 1 → face_driven
variant 2 → object_driven
variant 3 → comparison_driven
```

This is intentionally rigid in v1.1.

Reason:

- The goal of the first planner release is to prevent 3 near-identical thumbnails.
- Category-specific strategy scoring can be added later.
- Even when a category is object-heavy, variant 1 remains face-driven to test emotional CTR.

Implementation:

```python
VISUAL_STRATEGIES = {
    1: "face_driven",
    2: "object_driven",
    3: "comparison_driven",
}
```

---

### 7.1 Variant 1 — `face_driven`

Goal:

```text
Emotion first. The person's face sells the curiosity/pain.
```

Composition:

```text
Left 45%: expressive Mediterranean Spanish adult.
Right 50%: hook text.
Prop visible but secondary.
```

Best for:

```text
warnings, mistakes, cognitive topics, aging habits, blood sugar warnings
```

---

### 7.2 Variant 2 — `object_driven`

Goal:

```text
The topic object is instantly readable at thumbnail size.
```

Composition:

```text
Main object large and sharp.
Person smaller, hands-only, or partially visible.
Text remains readable on the right or upper-right.
```

Best for:

```text
food, coffee, chia, bread, labels, hydration, protein
```

---

### 7.3 Variant 3 — `comparison_driven`

Goal:

```text
Show contrast or choice.
```

Composition:

```text
Two clear visual options or contrast zones.
No medical before/after body transformation.
No humiliating before/after.
```

Best for:

```text
myth/truth, food choice, shopping choice, bad vs good habit, sugar spike, wrong vs right routine
```

---


### 7.4 Strategy description helper

The prompt template must not inject raw enum names only. Convert strategy enums to readable instructions.

```python
def describe_strategy(strategy: str) -> str:
    if strategy == "face_driven":
        return (
            "FACE-DRIVEN. The emotional face is the main attention hook. "
            "The topic prop is visible but secondary."
        )

    if strategy == "object_driven":
        return (
            "OBJECT-DRIVEN. The topic object is the main attention hook, "
            "large and instantly understandable at thumbnail size. "
            "A person may be smaller, hands-only, or partially visible."
        )

    if strategy == "comparison_driven":
        return (
            "COMPARISON-DRIVEN. Show a clear contrast, choice, or before/after habit cue "
            "without medical fear or humiliating body comparison."
        )

    return "FACE-DRIVEN. Use a clear expressive face and one topic-relevant visual cue."
```


### 7.5 Future strategy fitness matrix

Do not implement in Phase 1, but keep the planner structure compatible with a future matrix:

```python
STRATEGY_FITNESS = {
    "food_choice": ["object_driven", "comparison_driven", "face_driven"],
    "brain_memory_cognition": ["face_driven", "comparison_driven", "object_driven"],
}
```


---

## 8. Persona and locale rules

### 8.1 Default persona

Use `age_signal` from the classifier.

```python
AGE_RANGE_BY_SIGNAL = {
    "45+": "45–60",
    "50+": "50–65",
    "60+": "55–70",
    "unknown": "45–65",
}
```

Prompt text:

```text
A natural-looking Mediterranean Spanish adult aged {age_range}.
```

### 8.2 Gender

Phase 1 may keep gender neutral:

```text
adult
```

If model needs specificity:

```text
woman or man depending on the topic and variant
```

For Phase 1, keep this deterministic and simple:

```python
def select_thumbnail_persona(profile: dict, strategy: str, variant_index: int) -> str:
    age_range = AGE_RANGE_BY_SIGNAL.get(profile.get("age_signal"), "45–65")

    if strategy == "object_driven":
        return f"hands or partial view of a Mediterranean Spanish adult aged {age_range}"

    if strategy == "comparison_driven":
        return f"Mediterranean Spanish adult aged {age_range}, or two simple choice zones with minimal people"

    # face_driven
    return f"natural-looking Mediterranean Spanish adult aged {age_range}"
```

Do not overuse the same woman in every thumbnail, but do not force gender alternation when it conflicts with the topic or composition.

### 8.3 Avoid

```text
frail elderly person
sad senior stereotype
hospital patient
doctor diagnosis scene
medical emergency
humiliating body image
before/after weight loss body comparison
```

### 8.4 Locale

Use Spain-first visual cues:

```text
Mediterranean Spanish home
Spanish kitchen
Spanish supermarket
Mediterranean dining table
quiet Spanish park path
Spanish apartment balcony
```

Avoid:

```text
US hospital visual style
LatAm stock-photo stereotypes
generic corporate wellness stock
```

---

## 9. Prompt plan schema

Each planned variant should be represented as:

```json
{
  "variant_index": 1,
  "variant_title": "...",
  "thumbnail_text": "...",
  "primary_category": "blood_sugar_diabetes",
  "secondary_category": "functional_foods_superfoods",
  "visual_strategy": "face_driven",
  "visual_strategy_description": "FACE-DRIVEN. The emotional face is the main attention hook...",
  "primary_category_label": "blood sugar and diabetes prevention lifestyle",
  "secondary_category_label": "functional foods and daily nutrition",
  "persona": "Mediterranean Spanish adult aged 55–70",
  "scene": "bright Spanish kitchen after lunch",
  "main_prop": "small bowl of chia seeds and a spoon",
  "avoid": ["hospital", "syringe", "doctor", "scary medical monitor"],
  "category_safety_rules": "Keep the image dignified, practical, and non-alarmist...",
  "accent_color": "#F2C94C",
  "prompt": "..."
}
```


---

## 9.1 Avoid-list merge rule

When a plan has both primary and secondary categories:

```python
def stable_dedupe(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        key = str(value).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(value).strip())
    return out

def merge_avoid_lists(primary_preset: dict, secondary_preset: dict | None, risk_level: str) -> list[str]:
    avoid = []
    avoid.extend(primary_preset.get("avoid") or [])

    if secondary_preset:
        avoid.extend(secondary_preset.get("avoid") or [])

    if risk_level == "medical_sensitive":
        avoid.extend([
            "hospital",
            "doctor diagnosis scene",
            "medical emergency",
            "pills as main visual",
            "syringe",
            "fear-based medical imagery",
        ])

    return stable_dedupe(avoid)
```

Rules:

- Use union of primary avoid list + secondary avoid list + risk-level avoid list.
- Secondary category must not override primary scene.
- Secondary category may add a supporting prop and avoid terms.
- Keep avoid list concise in prompt: if merged list is long, include the most important 8–12 items.



---

## 9.2 Main-prop merge rule

When a secondary category exists, it may add a supporting prop, but it must not override the primary visual concept.

```python
def merge_main_prop(primary_preset: dict, secondary_preset: dict | None) -> str:
    primary_prop = str(primary_preset.get("main_prop") or "").strip()
    secondary_prop = str((secondary_preset or {}).get("main_prop") or "").strip()

    if not primary_prop and not secondary_prop:
        return "No specific prop required; use only one simple daily-life object if clearly supported by the title."

    if not secondary_prop or secondary_prop == primary_prop:
        return primary_prop

    if not primary_prop:
        return secondary_prop

    return f"{primary_prop}; supporting secondary cue: {secondary_prop}"
```

Examples:

```text
primary blood_pressure_circulation_heart + secondary functional_foods_superfoods
→ main prop: walking/circulation cue; supporting secondary cue: coffee cup or relevant food/drink object

primary blood_sugar_diabetes + secondary walking_cardio
→ main prop: food/sugar-spike cue; supporting secondary cue: post-meal walking shoes or gentle movement
```



---

## 9.3 Category label helper

The prompt should not expose raw enum names such as `blood_pressure_circulation_heart` as the only category description.

```python
CATEGORY_LABELS = {
    "food_choice": "food choice",
    "functional_foods_superfoods": "functional foods and daily nutrition",
    "shopping_label_choice": "shopping and label choice",
    "protein_muscle": "protein and muscle maintenance",
    "fiber_digestion": "fiber and digestion",
    "hydration": "hydration habit",
    "blood_sugar_diabetes": "blood sugar and diabetes prevention lifestyle",
    "blood_pressure_circulation_heart": "blood pressure, circulation, and heart-friendly habits",
    "sleep_rest": "sleep and rest",
    "energy_fatigue": "energy and fatigue",
    "movement_stiffness": "movement and stiffness",
    "joint_pain_body_signal": "body signal and joint discomfort",
    "walking_cardio": "walking and gentle cardio",
    "stress_mind": "stress and calm mind",
    "brain_memory_cognition": "memory and cognitive health",
    "weight_loss_metabolism": "weight loss and metabolism",
    "aging_longevity_bad_habits": "aging, longevity, and bad habits",
    "daily_routine": "daily routine",
    "mistake_warning": "mistake or warning",
    "myth_truth": "myth versus truth",
    "general_45plus_lifestyle": "general lifestyle after 45",
}

def category_label(category: str | None) -> str:
    if not category:
        return "none"
    return CATEGORY_LABELS.get(category, category.replace("_", " "))
```

Prompt category line should use labels:

```text
Visual category:
{category_label(primary_category)}; secondary cue: {category_label(secondary_category)}
```


---

## 10. Prompt builder template

Implement a prompt builder using this structure.

```text
Create a complete photorealistic YouTube thumbnail, 16:9, 1920x1080.

Topic:
"{variant_title}"

Hook text to render exactly:
"{thumbnail_text}"

Channel:
Vida Plena 45+, practical wellness, nutrition and lifestyle for Spanish adults over 45.

Visual category:
{primary_category_label}; secondary cue: {secondary_category_label}

Visual strategy:
{visual_strategy_description}

Scene:
{scene_description}

Subject:
{persona_description}
Place the subject according to the visual strategy.
Face should be clear when the strategy is face_driven.
Expression should match the hook and topic: concerned but hopeful, practical urgency, curiosity, or realization.
Do not make the person look frail, sick, helpless, or like a sad senior stereotype.

Main prop:
{main_prop_description}
The prop must make the topic instantly clear at thumbnail size.
Use realistic physical objects only.
No icons, stickers, emojis, medical diagrams, or product labels.

Composition:
Design for YouTube thumbnail readability.
Keep the image simple, high contrast, and readable at small size.
Reserve clean space for the hook text.
Avoid clutter.

Text:
Render this EXACT text only:
"{thumbnail_text}"

Render the hook text EXACTLY as written, preserving Spanish accents and punctuation:
ñ, á, é, í, ó, ú, ü, ¿, ¡.

All caps, huge, bold, white letters.
Use thick black outline and strong dark drop shadow.
Use accent color {accent_color} only for underline, small glow, or emphasis.
Place text where it is most readable.
No other text, no labels, no logos, no watermark.

Style:
Photorealistic editorial YouTube thumbnail.
Warm natural light, high contrast, crisp details.
Sharp face when visible. Sharp topic prop.
No blur, no plastic skin, no warped anatomy, no extra fingers.

Safety and tone:
{category_safety_rules}
The image should feel like practical lifestyle advice, not fear-based medical content.
```

---

## 11. Category visual presets

Implement a dictionary similar to:

```python
THUMBNAIL_VISUAL_PRESETS = {
    "blood_sugar_diabetes": {
        "scene": "Spanish kitchen or dining table after a meal",
        "main_prop": "food choice related to the title, such as chia, bread, plate, or a post-meal walking cue",
        "emotion": "concerned realization about a sugar spike, but hopeful",
        "avoid": ["syringe", "insulin injection", "hospital", "scary glucose monitor"],
    },
    ...
}
```

### Required presets

Include presets for all categories in section 5.


---

## 11.1 Category safety rule text

The prompt template uses `{category_safety_rules}`. Implement it with a deterministic helper.

```python
def safety_rules_for_category(primary_category: str, risk_level: str, avoid: list[str]) -> str:
    avoid_text = ", ".join(avoid[:12])

    category_hint = ""
    if primary_category == "brain_memory_cognition":
        category_hint = "Show cognitive concern with dignity; avoid stigma or helplessness. "
    elif primary_category == "blood_sugar_diabetes":
        category_hint = "Show lifestyle context around food or gentle movement; avoid injections or diagnosis scenes. "
    elif primary_category == "blood_pressure_circulation_heart":
        category_hint = "Show gentle circulation-friendly lifestyle cues; avoid emergency heart imagery. "
    elif primary_category == "weight_loss_metabolism":
        category_hint = "Avoid body shame, extreme scales, and transformation imagery. "

    if risk_level == "medical_sensitive":
        return (
            category_hint
            + "Keep the image dignified, practical, and non-alarmist. "
            + "Avoid fear-based medical visuals. "
            + f"Do not show: {avoid_text}."
        )

    if risk_level == "soft_health":
        return (
            category_hint
            + "Keep the image practical and lifestyle-oriented. "
            + "Show realistic daily habits, not diagnosis or treatment scenes. "
            + f"Avoid: {avoid_text}."
        )

    return (
        category_hint
        + "Keep the image lifestyle-oriented, warm, and realistic. "
        + f"Avoid: {avoid_text}."
    )
```

Rules:

- Always derive safety text from `primary_category`, `risk_level`, and merged `avoid`.
- Do not invent new medical claims in the prompt.
- Medical-sensitive topics should feel like practical lifestyle advice, not diagnosis or fear content.

---

## 12. Variant binding implementation

Replace current variant extraction logic.

### Current bad pattern

```python
variants = [
    v.get("thumbnail_text") or ""
    for v in raw_variants[:3]
    if v.get("thumbnail_text")
]
```

### Required pattern

```python
def normalize_thumbnail_variants(seo: dict) -> list[dict]:
    title = str(seo.get("title") or "").strip()
    raw_variants = seo.get("title_variants") or []

    variants = []
    for i, v in enumerate(raw_variants[:3], start=1):
        if not isinstance(v, dict):
            continue

        variant_title = str(v.get("title") or title).strip()
        thumbnail_text = str(v.get("thumbnail_text") or "").strip()

        if not thumbnail_text:
            continue

        variants.append({
            "variant_index": i,
            "title": variant_title,
            "thumbnail_text": thumbnail_text,
        })

    if not variants:
        fallback_text = str(seo.get("thumbnail_text") or "").strip()
        if not fallback_text:
            fallback_text = " ".join(title.split()[:5]).upper() or "VIDA PLENA 45+"

        variants.append({
            "variant_index": 1,
            "title": title,
            "thumbnail_text": fallback_text,
        })

    return variants
```

---

## 13. Planning implementation

```python
VISUAL_STRATEGIES = {
    1: "face_driven",
    2: "object_driven",
    3: "comparison_driven",
}

def plan_thumbnail_prompts(seo: dict, channel_config: dict) -> list[dict]:
    variants = normalize_thumbnail_variants(seo)
    plans = []

    for variant in variants[:3]:
        index = int(variant["variant_index"])
        strategy = VISUAL_STRATEGIES.get(index, "face_driven")

        profile = classify_thumbnail_topic(
            variant["title"],
            variant["thumbnail_text"],
        )
        primary_preset = select_visual_preset(profile["primary_category"])
        secondary_preset = (
            select_visual_preset(profile["secondary_category"])
            if profile.get("secondary_category")
            else None
        )
        persona = select_thumbnail_persona(profile, strategy, index)
        accent_color = resolve_thumbnail_accent_color(channel_config)

        plan = {
            "variant_index": index,
            "variant_title": variant["title"],
            "thumbnail_text": variant["thumbnail_text"],
            "primary_category": profile["primary_category"],
            "secondary_category": profile.get("secondary_category"),
            "primary_category_label": category_label(profile["primary_category"]),
            "secondary_category_label": category_label(profile.get("secondary_category")),
            "risk_level": profile["risk_level"],
            "age_signal": profile["age_signal"],
            "visual_strategy": strategy,
            "visual_strategy_description": describe_strategy(strategy),
            "persona": persona,
            "scene": primary_preset["scene"],
            "main_prop": merge_main_prop(primary_preset, secondary_preset),
            "avoid": merge_avoid_lists(primary_preset, secondary_preset, profile["risk_level"]),
            "accent_color": accent_color,
        }
        plan["category_safety_rules"] = safety_rules_for_category(
            plan["primary_category"],
            plan["risk_level"],
            plan["avoid"],
        )
        plan["prompt"] = build_thumbnail_prompt(plan)
        plans.append(plan)

    return plans
```


### 13.1 Determinism contract

`plan_thumbnail_prompts(...)` must be deterministic.

Same `seo` and `channel_config` input must produce the same list of plans, excluding timestamped file-write side effects.

Do not use randomness in:

- category tie-break
- secondary category selection
- persona selection
- strategy selection
- prompt ordering



### 13.2 Accent color resolution

`accent_color` must be preserved from existing thumbnail behavior.

Use this helper so both real channel config and wrapper/test config shapes work:

```python
def resolve_thumbnail_accent_color(channel_config: dict) -> str:
    # New optional thumbnail-specific config.
    thumbnail_cfg = channel_config.get("thumbnail") or {}
    if thumbnail_cfg.get("accent_color"):
        return str(thumbnail_cfg["accent_color"])

    # Existing/common style palette config.
    style = channel_config.get("style") or {}
    palette = style.get("palette") or {}
    for key in ("accent", "secondary", "primary"):
        if palette.get(key):
            return str(palette[key])

    # Safe fallback used in prior prompt behavior.
    return "#F2C94C"
```


---

## 14. Integration in `auto_thumbnail_image_stage`

Replace direct prompt building:

```python
prompt = _build_thumbnail_prompt(title, thumb_text, accent_color, channel_description)
```

with:

```python
plans = plan_thumbnail_prompts(seo, channel_config)

for plan in plans:
    i = plan["variant_index"]
    prompt = plan["prompt"]
    thumb_text = plan["thumbnail_text"]
    ...
```


### 14.1 Backward-compatible prompt builder wrapper

If existing tests or code directly reference `_build_thumbnail_prompt(...)`, keep a wrapper during migration.

```python
def _build_thumbnail_prompt(
    title: str,
    thumbnail_text: str,
    accent_color: str,
    channel_description: str,
    variant_index: int = 1,
) -> str:
    seo = {
        "title": title,
        "title_variants": [
            {"title": title, "thumbnail_text": thumbnail_text}
        ],
    }

    # Wrapper/test config shape is intentionally minimal.
    # resolve_thumbnail_accent_color(...) must support this shape.
    channel_config = {
        "description": channel_description,
        "thumbnail": {"accent_color": accent_color},
    }

    plan = plan_thumbnail_prompts(seo, channel_config)[0]
    return plan["prompt"]
```

The wrapper can be deprecated later, but Phase 1 should not break direct tests/imports unless all callers are updated.


### Prompt logging

Before image generation, write:

```python
prompt_dir = job_dir / "operator" / "chatgpt"
prompt_dir.mkdir(parents=True, exist_ok=True)
(prompt_dir / f"thumbnail_prompt_{i}.md").write_text(prompt, encoding="utf-8")
```

Also write structured metadata:

```python
plans_json = []
for plan in plans:
    item = dict(plan)
    # Keep prompt out of the compact metadata JSON because full prompts are
    # already persisted as operator/chatgpt/thumbnail_prompt_N.md.
    item.pop("prompt", None)
    plans_json.append(item)

(job_dir / "json" / "thumbnail_prompt_plans.json").write_text(
    json.dumps(plans_json, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

Do not duplicate large full prompts inside `thumbnail_prompt_plans.json`; use per-variant markdown files as the prompt source of truth.

---

## 15. Image post-processing

After image generation:

```python
from PIL import ImageOps, Image

img = Image.open(source_path).convert("RGB")
img = ImageOps.fit(img, (1920, 1080), method=Image.Resampling.LANCZOS)
img.save(jpg_path, "JPEG", quality=94, optimize=True)
```

Rules:

- Always export exact `1920x1080`.
- Keep `thumbnail.jpg` as alias of the first successfully generated variant.
- Continue copying generated thumbnails to `remotion/public`.
- Continue updating `seo.thumbnail_path`.

---

## 16. Text fidelity policy

Phase 1:

- Add stronger prompt instruction for Spanish diacritics.
- Do not add OCR dependency.
- Do not fail generation if text is slightly imperfect.
- Log prompt and generated image so manual QA can review.

Future phase:

- Add OCR or visual QA.
- If OCR fails, optionally fallback to code overlay.

---

## 17. Example mappings

### 17.1 Café and circulation

Input:

```text
Lo que el CAFÉ sin azúcar hace a tu circulación después de los 60
```

Output profile:

```json
{
  "primary_category": "blood_pressure_circulation_heart",
  "secondary_category": "functional_foods_superfoods",
  "age_signal": "60+",
  "risk_level": "soft_health"
}
```

Visuals:

```text
coffee cup, Spanish morning kitchen, adult 55–65, subtle circulation / walking cue
```

Avoid:

```text
heart attack, hospital, fake veins, medical monitor
```

---

### 17.2 Weight loss

Input:

```text
El Truco Más Ignorado Para Adelgazar
```

Output profile:

```json
{
  "primary_category": "weight_loss_metabolism",
  "secondary_category": "daily_routine",
  "risk_level": "soft_health"
}
```

Visuals:

```text
small daily habit, walking shoes, plate choice, subtle measuring tape
```

Avoid:

```text
body shame, before/after transformation, extreme scale panic
```

---

### 17.3 Dementia signs

Input:

```text
NO ES MEMORIA: Las 5 Señales de Demencia Que Aparecen Años Antes
```

Output profile:

```json
{
  "primary_category": "brain_memory_cognition",
  "secondary_category": "mistake_warning",
  "age_signal": "unknown",
  "risk_level": "medical_sensitive"
}
```

Visuals:

```text
keys, calendar, reading glasses, dignified concerned adult
```

Avoid:

```text
scary brain CGI, helpless elderly stereotype, hospital
```

---

### 17.4 Chia and sugar

Input:

```text
Cómo debe comer CHÍA para regular su AZÚCAR y evitar la diabetes
```

Output profile:

```json
{
  "primary_category": "blood_sugar_diabetes",
  "secondary_category": "functional_foods_superfoods",
  "risk_level": "medical_sensitive"
}
```

Visuals:

```text
chia seeds, spoon, breakfast bowl, kitchen table, gentle caution
```

Avoid:

```text
syringe, insulin shot, scary glucose monitor
```

---

### 17.5 Bad habits and aging

Input:

```text
4 malos hábitos que te hacen envejecer más rápido
```

Output profile:

```json
{
  "primary_category": "aging_longevity_bad_habits",
  "secondary_category": "mistake_warning",
  "risk_level": "lifestyle"
}
```

Visuals:

```text
late-night phone, poor snack, sitting too long, tired realization
```

Avoid:

```text
decrepit elderly stereotype, scary aging morph
```

---

### 17.6 Exercises after eating

Input:

```text
Después de comer: haz estos 4 ejercicios y evita el pico de azúcar
```

Output profile:

```json
{
  "primary_category": "blood_sugar_diabetes",
  "secondary_category": "walking_cardio",
  "risk_level": "soft_health"
}
```

Visuals:

```text
post-meal gentle movement, walking shoes, dining table in background
```

---

### 17.7 High blood pressure and calf exercises

Input:

```text
¿Presión alta después de los 60? Estos 4 ejercicios de pantorrilla pueden ayudarte
```

Output profile:

```json
{
  "primary_category": "blood_pressure_circulation_heart",
  "secondary_category": "walking_cardio",
  "age_signal": "60+",
  "risk_level": "medical_sensitive"
}
```

Visuals:

```text
calf raise, chair support, walking shoes, gentle movement
```

Avoid:

```text
medical emergency, heart attack, ECG monitor
```

---

### 17.8 Best bread

Input:

```text
Experta en nutrición revela: el mejor pan y cómo usarlo todos los días
```

Output profile:

```json
{
  "primary_category": "food_choice",
  "secondary_category": "shopping_label_choice",
  "risk_level": "lifestyle"
}
```

Visuals:

```text
whole grain bread, toast, Spanish kitchen or supermarket comparison
```

---

## 18. Tests

Add unit tests for:

1. `normalize_thumbnail_variants` keeps each variant title.
2. `normalize_thumbnail_variants` falls back safely when `title_variants` is missing.
3. `classify_thumbnail_topic` maps café/circulation to `blood_pressure_circulation_heart`.
4. `classify_thumbnail_topic` maps chía/azúcar/diabetes to `blood_sugar_diabetes`.
5. `classify_thumbnail_topic` maps demencia/memoria to `brain_memory_cognition`.
6. `classify_thumbnail_topic` maps adelgazar to `weight_loss_metabolism`.
7. `classify_thumbnail_topic` maps malos hábitos/envejecer to `aging_longevity_bad_habits`.
8. `classify_thumbnail_topic` maps presión alta/pantorrilla to `blood_pressure_circulation_heart`.
9. `plan_thumbnail_prompts` returns up to 3 plans.
10. Variant 1 uses `face_driven`.
11. Variant 2 uses `object_driven`.
12. Variant 3 uses `comparison_driven`.
13. Prompt includes exact `thumbnail_text`.
14. Prompt includes Spanish diacritics preservation instruction.
15. Prompt uses Mediterranean Spanish persona, not Hispanic/Latina hardcode.
16. Prompt avoids medical fear imagery for medical-sensitive categories.
17. Prompt is written to `operator/chatgpt/thumbnail_prompt_N.md`.
18. Generated images are post-processed to exact `1920x1080`.
19. `thumbnail.jpg` aliases the first successful generated variant.
20. Existing batch and sequential image generation paths both use planned prompts.
21. Classifier tie-break is deterministic for equal scores.
22. `pick_secondary_category` returns `None` when no secondary score is positive.
23. Mixed signal topic with diabetes mention returns `medical_sensitive`.
24. `age_signal=60+` changes persona age range to `55–70`.
25. Avoid list is the union of primary, secondary, and risk-level avoid terms.
26. Plan generation is deterministic across repeated calls.
27. Plan includes `accent_color`.
28. `general_45plus_lifestyle` does not force a food/sleep/exercise prop.
29. Backward-compatible `_build_thumbnail_prompt(...)` wrapper still works if kept.
30. `select_visual_preset` accepts a category string.
31. `merge_main_prop` combines primary and secondary props without overriding primary scene.
32. `safety_rules_for_category` fills `{category_safety_rules}` for all risk levels.
33. `infer_age_signal` does not treat `60 minutos`, `60 segundos`, or `60%` as age `60+`.
34. `resolve_thumbnail_accent_color` supports both `thumbnail.accent_color` and `style.palette.*`.
35. `thumbnail_prompt_plans.json` excludes full prompt text.
36. `describe_strategy` returns readable text for all three strategies.
37. Prompt uses category labels instead of raw enum names only.
38. `safety_rules_for_category` uses `primary_category` for brain/sugar/pressure/weight-loss hints.
39. `infer_age_signal` does not treat `60 secretos` or `60 días` as age `60+`.
40. `pick_secondary_category` does not return low-signal generic categories from one weak trigger.
41. `CATEGORY_PRIORITY` keeps body-signal categories above aging when both are tied.
42. `category_safety_rules` is present in prompt plan schema and metadata.




Mock image generation. Do not call ChatGPT in unit tests.

---

## 19. Implementation phases

### Phase 1 — Planner module

- Treat P0/P1 thumbnail hardening as already shipped behavior and route it through planner contracts.
- Add `src/video_agent/thumbnail_planner.py`.
- Add category taxonomy.
- Add keyword classifier.
- Add visual presets.
- Add variant normalization.
- Add prompt builder.
- Add unit tests for planner.

### Phase 2 — Stage integration

- Update `auto_thumbnail_image_stage`.
- Replace direct `_build_thumbnail_prompt(...)` use with `plan_thumbnail_prompts(...)`.
- Use variant-specific title.
- Add prompt logging.
- Add prompt plan JSON logging.
- Preserve existing batch/sequential generation support.

### Phase 3 — Image post-processing

- Add `ImageOps.fit(..., (1920, 1080))`.
- Save JPG quality 94.
- Keep `thumbnail.jpg` alias behavior.
- Keep remotion/public copy behavior.
- Add tests for dimensions.

### Phase 4 — Regression

- Verify existing SEO `title_variants` flow still works.
- Verify fallback when no title variants exist.
- Verify one failed variant does not prevent other variants.
- Verify `seo.thumbnail_path` is updated.

---

## 20. Acceptance criteria

Complete when:

1. Three thumbnail variants use their own variant titles when available.
2. Three variants use distinct visual strategies.
3. Prompt no longer hardcodes `Hispanic or Latina`.
4. Prompt uses Spain-first / Mediterranean visual language.
5. Prompt includes Spanish diacritics preservation instruction.
6. Prompt category is selected from deterministic taxonomy.
7. Food, sleep, movement, sugar, pressure, dementia, weight loss, aging, habits, and shopping topics map to appropriate categories.
8. Prompt no longer contains plate/energy hardcoded example.
9. Prompt includes category-specific visual props.
10. Prompt includes category-specific avoid rules.
11. Prompt is logged per variant.
12. Prompt plan metadata is logged.
13. Generated images are exported at exact `1920x1080`.
14. Existing `thumbnail_1.jpg`, `thumbnail_2.jpg`, `thumbnail_3.jpg`, and `thumbnail.jpg` behavior remains.
15. Existing batch generation path still works.
16. Existing sequential generation path still works.
17. Unit tests do not call live ChatGPT image generation.
18. Classifier has deterministic tie-break behavior.
19. Secondary category selection is deterministic and explicit.
20. Risk-level inference is deterministic and text/category based.
21. Age signal changes persona age range.
22. Avoid list merges primary, secondary, and risk-level avoid terms.
23. Plans include `accent_color`.
24. Prompt plans are deterministic across repeated calls.
25. `general_45plus_lifestyle` fallback does not force food/sleep/exercise/medical imagery.
26. Backward-compatible prompt builder wrapper is kept or all direct callers/tests are updated.
27. `select_visual_preset` signature is consistent and accepts a category string.
28. `merge_main_prop` is implemented.
29. `safety_rules_for_category` is implemented and used by prompt builder.
30. Age signal uses word-boundary matching and avoids false positives like `60 minutos` / `60%`.
31. Accent color resolver supports both real channel config and wrapper/test config.
32. Prompt plan metadata excludes full prompt text; full prompts live in markdown files.
33. `describe_strategy` is implemented and prompt does not rely on raw strategy enum only.
34. Prompt category line uses human-readable labels, not only raw enum names.
35. `safety_rules_for_category` uses `primary_category` for category-specific safety hints.
36. Secondary category threshold avoids one-trigger generic noisy categories.
37. Age signal phrase matching avoids false positives such as `60 secretos` and `60 días`.
38. Category priority keeps body-signal visuals ahead of generic aging when appropriate.
39. `category_safety_rules`, `visual_strategy_description`, and category labels are included in prompt plan schema.




---

## 21. Codex prompt

```text
Implement Thumbnail Prompt Planner v1.3 described in docs/specs/thumbnail-prompt-planner-v1.3.md.

Focus:
- Add topic-aware thumbnail prompt planning for Vida Plena 45+.
- Use seo.title_variants[i].title and seo.title_variants[i].thumbnail_text for each thumbnail variant.
- Generate up to 3 prompt plans with visual strategies: face_driven, object_driven, comparison_driven.
- Add deterministic category classifier covering food, functional foods, blood sugar, blood pressure/circulation, brain/memory, weight loss, aging habits, sleep, movement, stress, hydration, and fallback lifestyle.
- Implement deterministic tie-break, secondary category selection with noisy-secondary filtering, risk-level inference, phrase-level age-signal persona mapping, avoid-list merge rules, main-prop merge rules, safety-rule text generation, strategy descriptions, human-readable category labels, and accent-color resolution.
- Replace Hispanic/Latina hardcode with Spain-first Mediterranean adult persona.
- Include exact Spanish diacritics preservation instruction in every text-baked prompt.
- Remove hardcoded plate/energy example and use category presets instead.
- Persist prompt markdown per variant under operator/chatgpt/thumbnail_prompt_N.md.
- Persist compact structured prompt plan metadata without full prompt text.
- Preserve accent_color in prompt plans.
- Treat P0/P1 hardening as already shipped and do not duplicate divergent behavior.
- Enforce exact 1920x1080 output with PIL ImageOps.fit before saving JPG.
- Preserve existing batch and sequential image generation paths.
- Preserve thumbnail.jpg alias and remotion/public copy behavior.
- Add unit tests with mocked image generation.
```
```