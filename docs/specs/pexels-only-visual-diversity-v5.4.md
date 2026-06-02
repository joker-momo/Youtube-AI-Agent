# Spec v5: Pexels-Only Visual Diversity Upgrade for `Vida Plena 45+`

## 0. Purpose

Improve visual variety in the `joker-momo/Youtube-AI-Agent` video pipeline while keeping **Pexels as the only external stock provider**.

This is the canonical merged spec. It replaces v1–v4. There is no correction layer and no embedded older version.

The upgrade should reduce repetitive B-roll by improving:

- scene visual planning
- Pexels query expansion
- candidate scoring
- asset reuse control
- creator diversity
- visual bucket rotation
- shot-type rotation
- placeholder tracking
- QA/reporting

External stock source remains strictly Pexels-only.

---

## 1. Scope

### In scope

- Keep external stock source restricted to Pexels.
- Add channel-agnostic visual DNA loading.
- Add visual buckets and shot types.
- Add deterministic visual planning.
- Add Pexels-only query expansion.
- Add deterministic semantic matching without embeddings.
- Add token and phrase synonyms from config.
- Add asset quality, reuse, creator, and metadata scoring.
- Add SQLite migrations for existing asset metadata.
- Add race-aware cross-job asset reservation.
- Add visual QA and diversity reports.
- Add per-scene and per-video Pexels API budgets.
- Add 429 handling and batch-hourly warning/backoff state.
- Add optional graphic cards if renderer support exists.
- Add optional historical metadata backfill script.
- Add rollback switch.

### Out of scope

- No Pixabay, Mixkit, Envato, Storyblocks, Artgrid, or any other external stock provider.
- No paid stock APIs.
- No embedding/vector database.
- No CLIP model requirement.
- No manual per-scene curation requirement.
- No change to TTS, music, upload flow, subtitle style, render codec, or medical safety policy.

---

## 2. Existing codebase anchors

Implementation should extend the existing Python codebase:

```text
src/video_agent/stages/assets.py
src/video_agent/assets/service.py
src/video_agent/assets/library.py
configs/vida-plena-45/channel.yaml
```

Existing behavior to preserve:

1. `prepare_assets(...)` loops over `scene_doc["scenes"]`.
2. If no local image and no injected `asset_refs.primary` exists, it calls `StockAssetService.get_scene_asset(scene, channel_id, job_dir.name)`.
3. Stock assets are copied into the job assets folder and assigned to `scene["asset_refs"]["background"]`.
4. Manifest metadata already includes `asset_id`, `provider`, `provider_asset_id`, `source_url`, `attribution`, and `asset_selection`.
5. `StockAssetService` already has query cache, library cache, candidate scoring, per-job duplicate sets, and Spanish→English keyword fallback.
6. `AssetLibrary` already stores metadata in SQLite tables `assets` and `asset_usage`.

Do not create a parallel asset system. Extend the current services.

---

## 3. Channel-agnostic visual DNA loading

Although this upgrade targets `vida-plena-45`, code must not hardcode that channel.

Channel config may define:

```yaml
visuals:
  visual_dna_path: "configs/vida-plena-45/visual-dna.yaml"
```

Loader:

```python
def load_visual_dna(channel_config: dict, channel_id: str) -> dict:
    path = channel_config.get("visuals", {}).get("visual_dna_path")
    if path:
        return load_yaml(path)

    fallback = f"configs/{channel_id}/visual-dna.yaml"
    if path_exists(fallback):
        return load_yaml(fallback)

    return default_visual_dna()
```

Only channel config files may reference `vida-plena-45` explicitly.

---

## 4. Config update: `configs/vida-plena-45/channel.yaml`

Update the `visuals` block while preserving Pexels-only sourcing.

```yaml
visuals:
  strategy: "auto"
  source_dir: "asset_library/source"

  # External stock policy: Pexels only.
  providers: ["pexels_video"]
  fallback_providers: ["pexels"]

  query_cache_path: "caches/query_cache.db"
  query_cache_ttl_hours: 72
  asset_library_path: "asset_library"

  orientation: "landscape"

  # Search breadth.
  per_page: 30
  candidate_count_target: 12

  # Query/API budget.
  max_queries_per_scene: 4
  max_api_queries_per_scene: 2
  max_api_requests_per_video: 80
  max_api_requests_per_hour: 180
  api_backoff_on_429_sec: 120
  prefer_library_cache_first: true

  visual_dna_path: "configs/vida-plena-45/visual-dna.yaml"

  diversity:
    enabled: true
    rollout_mode: "report_only"   # report_only | warn | enforce

    # Long-form thresholds apply only when scene_count >= 25.
    long_form_min_scenes: 25

    max_same_visual_bucket_ratio_per_video: 0.35
    max_same_shot_type_consecutive: 2
    max_same_creator_ratio_per_video: 0.25

    # Current-job duplicate policy.
    allow_same_asset_twice_in_same_video: false
    duplicate_asset_escape_hatch: "warn_if_no_alternatives"  # never | warn_if_no_alternatives

    # Historical reuse policy.
    max_same_asset_reuse_last_30_days_soft: 1
    max_same_asset_reuse_last_90_days_soft: 2
    max_same_asset_reuse_last_30_days_hard: null

    # Lifetime saturation threshold for asset novelty scoring.
    lifetime_novelty_saturation_count: 6

    # Placeholder policy.
    max_placeholder_ratio_enforce: 0.05

    prefer_metadata_complete_assets: true
    asset_reservation_ttl_minutes: 120

  graphic_cards:
    enabled: true
    rollout_mode: "auto_if_supported"  # disabled | report_only | auto_if_supported | enforce
    min_per_long_video: 4
    min_per_short_video: 0
    supported_card_types:
      - "checklist"
      - "timeline"
      - "habit_matrix"
```

Notes:

- `fallback_providers: ["pexels"]` is allowed because it is still Pexels-only.
- Do not add any non-Pexels external stock provider.
- If old code still reads `scene_count_target`, keep backward compatibility but prefer `candidate_count_target`.

---

## 5. New file: `configs/vida-plena-45/visual-dna.yaml`

Create this canonical visual DNA file.

```yaml
schema_version: "5.0"
channel_id: "vida-plena-45"

source_policy:
  external_stock_providers:
    - "pexels"
  allow_other_external_stock_providers: false

query_policy:
  pexels_query_language: "en"
  visual_brief_language: "scene_or_channel_language"
  max_query_terms: 14
  max_queries_per_scene: 4
  max_api_queries_per_scene: 2
  dedupe_queries: true

determinism:
  seed_fields:
    - "channel_id"
    - "job_id"
    - "scene_id"
    - "scene_index"
    - "topic_hash"

token_policy:
  preserve_numeric_terms:
    - "45"
    - "50"
    - "55"
    - "60"
    - "65"
    - "70"
  # Future override for meaningful non-numeric short tokens in other channels.
  # Vida Plena 45+ does not need extra non-numeric short tokens.
  preserve_short_terms: []
  stopwords:
    en:
      - "the"
      - "and"
      - "with"
      - "for"
      - "from"
      - "after"
      - "before"
      - "adult"
      - "adults"
    es:
      - "una"
      - "uno"
      - "unos"
      - "unas"
      - "para"
      - "por"
      - "con"
      - "sin"
      - "los"
      - "las"
      - "del"
      - "despues"
      - "después"

synonyms:
  tokens:
    walk: ["walking", "paseo", "caminar", "camina"]
    walking: ["walk", "paseo", "caminar", "camina"]
    sleep: ["sleeping", "sueño", "dormir", "duerme"]
    breakfast: ["desayuno", "oats", "yogurt"]
    dinner: ["cena"]
    hands: ["manos"]
    back: ["espalda"]
    neck: ["cuello"]
    legs: ["piernas"]
    stiff: ["rigido", "rígido", "rigidez"]
    tired: ["cansado", "cansada", "fatigue"]
    morning: ["mañana", "amanecer"]
    evening: ["tarde", "noche"]
    home: ["casa", "apartment", "kitchen", "bedroom"]

  phrases:
    middle_aged:
      - "middle aged"
      - "middle-aged"
      - "mature adult"
      - "midlife"
      - "woman 50"
      - "man 55"
      - "45 plus"
      - "45+"
      - "personas de más de 45"

role_keywords:
  es:
    hook:
      - "imagina"
      - "si te"
      - "te pasa"
      - "notas"
      - "cansado"
      - "cansada"
      - "rígido"
      - "rígida"
    problem:
      - "error"
      - "problema"
      - "evita"
      - "sin darte cuenta"
      - "te cuesta"
    explanation:
      - "porque"
      - "esto ocurre"
      - "la razón"
      - "significa"
      - "cuando"
    example:
      - "por ejemplo"
      - "como"
      - "un caso"
      - "en casa"
    habit_action:
      - "prueba"
      - "haz"
      - "empieza"
      - "añade"
      - "camina"
      - "estira"
    transition:
      - "ahora"
      - "después"
      - "además"
      - "siguiente"
    recap:
      - "resumen"
      - "recuerda"
      - "quédate con"
      - "en pocas palabras"
    cta:
      - "suscríbete"
      - "comenta"
      - "mira"
      - "vídeo completo"
  en:
    hook:
      - "imagine"
      - "if you"
      - "you feel"
      - "tired"
      - "stiff"
    problem:
      - "mistake"
      - "problem"
      - "avoid"
      - "without noticing"
      - "hard to"
    explanation:
      - "because"
      - "this happens"
      - "the reason"
      - "means"
      - "when"
    example:
      - "for example"
      - "like"
      - "at home"
    habit_action:
      - "try"
      - "do"
      - "start"
      - "add"
      - "walk"
      - "stretch"
    transition:
      - "now"
      - "after"
      - "also"
      - "next"
    recap:
      - "summary"
      - "remember"
      - "keep this"
      - "in short"
    cta:
      - "subscribe"
      - "comment"
      - "watch"
      - "full video"

global_visual_style:
  locale_feel:
    prefer:
      - "Spain"
      - "Mediterranean home"
      - "European apartment"
      - "Spanish market"
      - "quiet city street"
      - "neighborhood park"
      - "balcony morning light"
    avoid:
      - "US hospital stock"
      - "generic corporate wellness"
      - "luxury spa"
      - "extreme gym"
      - "clinical fear-based imagery"

  age_representation:
    prefer:
      - "45 to 65"
      - "active middle age"
      - "natural everyday people"
    avoid:
      - "frail elderly stereotype"
      - "overly polished fitness model"
      - "doctor diagnosing patient"
      - "sad isolated senior cliché"

video_length_profiles:
  short:
    max_scenes: 24
    min_distinct_visual_buckets: 3
    min_distinct_shot_types: 3
    min_local_graphic_cards: 0
  long:
    min_scenes: 25
    min_distinct_visual_buckets: 6
    min_distinct_shot_types: 5
    min_local_graphic_cards: 4

visual_buckets:
  persona_moment:
    weight: 1.20
    long_min_per_video: 3
    long_max_ratio_per_video: 0.25
    short_min_per_video: 1
    keyword_triggers:
      es: ["cansado", "cansada", "rigidez", "levantarte", "te notas", "energía", "mañana", "rutina"]
      en: ["tired", "stiff", "wake up", "morning", "energy", "routine"]
    pexels_queries_en:
      - "woman 50 morning kitchen coffee calm"
      - "middle aged woman home morning routine"
      - "mature adult morning kitchen coffee"
      - "man 55 at home stretching morning"
      - "woman 50 sitting on sofa calm lifestyle"

  spain_daily_life:
    weight: 1.35
    long_min_per_video: 3
    long_max_ratio_per_video: 0.25
    short_min_per_video: 1
    keyword_triggers:
      es: ["España", "calle", "mercado", "paseo", "barrio", "compra", "por la tarde"]
      en: ["Spain", "street", "market", "walk", "neighborhood", "shopping"]
    pexels_queries_en:
      - "Spain street morning people walking"
      - "Mediterranean market people shopping"
      - "European apartment balcony morning"
      - "Spanish cafe morning street"
      - "Madrid park people walking"
      - "Barcelona street daily life"

  body_signal:
    weight: 1.10
    long_min_per_video: 2
    long_max_ratio_per_video: 0.20
    short_min_per_video: 1
    keyword_triggers:
      es: ["cuello", "espalda", "piernas", "manos", "dolor", "rígido", "rígida", "fuerza", "músculo"]
      en: ["neck", "back", "legs", "hands", "pain", "stiff", "strength", "muscle"]
    pexels_queries_en:
      - "hands close up morning cup"
      - "person stretching neck at home"
      - "person stretching back at home"
      - "feet walking slowly close up"
      - "middle aged person rubbing hands morning"

  food_practical:
    weight: 1.10
    long_min_per_video: 2
    long_max_ratio_per_video: 0.20
    short_min_per_video: 0
    keyword_triggers:
      es: ["desayuno", "cena", "comer", "proteína", "fibra", "avena", "yogur", "legumbres", "pescado", "verduras"]
      en: ["breakfast", "dinner", "protein", "fiber", "oats", "yogurt", "legumes", "fish", "vegetables"]
    pexels_queries_en:
      - "simple breakfast oats yogurt fruit"
      - "Mediterranean food preparation home"
      - "hands preparing vegetables kitchen"
      - "olive oil vegetables kitchen close up"
      - "healthy dinner home kitchen"

  sleep_stress:
    weight: 1.05
    long_min_per_video: 2
    long_max_ratio_per_video: 0.18
    short_min_per_video: 0
    keyword_triggers:
      es: ["dormir", "sueño", "noche", "madrugada", "estrés", "mente", "descanso", "pantalla", "móvil"]
      en: ["sleep", "night", "stress", "mind", "rest", "screen", "phone"]
    pexels_queries_en:
      - "bedroom morning light curtains"
      - "person turning off phone night"
      - "calm bedroom evening lamp"
      - "woman relaxing sofa evening"
      - "tea cup evening home calm"

  gentle_movement:
    weight: 1.20
    long_min_per_video: 2
    long_max_ratio_per_video: 0.22
    short_min_per_video: 1
    keyword_triggers:
      es: ["caminar", "paseo", "movimiento", "estirar", "estiramiento", "suave", "escaleras", "parque"]
      en: ["walk", "walking", "movement", "stretch", "gentle", "stairs", "park"]
    pexels_queries_en:
      - "middle aged woman walking park"
      - "man walking park morning"
      - "gentle stretching at home"
      - "walking shoes close up park"
      - "person using stairs slowly"

  macro_texture:
    weight: 0.85
    long_min_per_video: 1
    long_max_ratio_per_video: 0.15
    short_min_per_video: 0
    keyword_triggers:
      es: ["detalle", "luz", "taza", "agua", "reloj", "hábitos", "lista"]
      en: ["detail", "light", "cup", "water", "clock", "habit", "list"]
    pexels_queries_en:
      - "sunlight through curtains close up"
      - "tea steam close up morning"
      - "hands notebook habit tracker"
      - "alarm clock morning light"
      - "water glass close up kitchen"

  local_graphic_card:
    weight: 1.20
    long_min_per_video: 0
    long_target_per_video: 4
    long_max_ratio_per_video: 0.20
    short_min_per_video: 0
    render_locally: true
    keyword_triggers:
      es: ["tres", "pasos", "lista", "recuerda", "resumen", "clave", "evita", "prueba"]
      en: ["three", "steps", "checklist", "remember", "summary", "key", "avoid", "try"]
    card_types:
      - "checklist"
      - "timeline"
      - "habit_matrix"

shot_types:
  wide:
    long_min_ratio: 0.10
    long_max_ratio: 0.30
  medium:
    long_min_ratio: 0.18
    long_max_ratio: 0.45
  medium_closeup:
    long_min_ratio: 0.12
    long_max_ratio: 0.35
  closeup:
    long_min_ratio: 0.10
    long_max_ratio: 0.30
  macro:
    long_min_ratio: 0.05
    long_max_ratio: 0.18
  graphic:
    long_min_ratio: 0.00
    long_max_ratio: 0.22

negative_patterns:
  strong_phrases_en:
    - "hospital bed"
    - "medical emergency"
    - "before after weight loss"
    - "miracle cure"
    - "diet pills"
    - "doctor diagnosis"
  weak_terms_en:
    - "hospital"
    - "doctor"
    - "medicine"
    - "pill"
    - "emergency"
    - "frail"
    - "sick"
    - "sad elderly"

role_to_buckets:
  hook: ["persona_moment", "body_signal", "macro_texture"]
  problem: ["persona_moment", "body_signal", "sleep_stress"]
  explanation: ["local_graphic_card", "macro_texture", "spain_daily_life"]
  example: ["food_practical", "gentle_movement", "sleep_stress"]
  habit_action: ["gentle_movement", "food_practical", "persona_moment"]
  transition: ["spain_daily_life", "macro_texture"]
  recap: ["local_graphic_card", "persona_moment"]
  cta: ["local_graphic_card", "spain_daily_life"]

default_bucket_sequence:
  - "persona_moment"
  - "macro_texture"
  - "spain_daily_life"
  - "local_graphic_card"
  - "gentle_movement"
  - "body_signal"
  - "food_practical"
  - "sleep_stress"

default_shot_sequence:
  - "medium_closeup"
  - "macro"
  - "wide"
  - "graphic"
  - "medium"
  - "closeup"
  - "wide"
  - "medium_closeup"
  - "medium"
  - "closeup"
```

---

## 6. Scene visual fields

Extend scene dicts with optional fields. Backward compatibility is mandatory.

```python
from typing import Any, TypedDict

class SceneVisualFields(TypedDict, total=False):
    visual_brief: str
    visual_bucket: str
    shot_type: str
    mood: str
    locale_feel: str
    must_include: list[str]
    avoid: list[str]
    fallback_visual_query: str
    graphic_card: dict[str, Any]
```

Existing scenes without these fields must still render.

Input priority:

```python
raw_visual_text = (
    scene.get("visual_brief")
    or scene.get("visual_prompt")
    or scene.get("on_screen_text")
    or ""
)
```

---

## 7. Deterministic helpers

Do not use Python built-in `hash()` for reproducible tie-breaking.

```python
import hashlib
import re
import unicodedata

def normalize_text(value: str) -> str:
    """Lowercase, strip accents, normalize whitespace, keep alnum/+/- spaces."""
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9+ -]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value

def stable_hash(value: str) -> int:
    return int(hashlib.sha1(value.encode("utf-8")).hexdigest()[:12], 16)

def deterministic_argmax(scores: dict[str, float], seed: str) -> str:
    if not scores:
        raise ValueError("deterministic_argmax requires at least one score")

    return sorted(
        scores.items(),
        key=lambda item: (
            -item[1],
            stable_hash(f"{seed}:{item[0]}"),
            item[0],
        ),
    )[0][0]

def stable_dedupe(values: list[str]) -> list[str]:
    """Preserve first occurrence. Query order matters for API budget."""
    seen = set()
    out = []
    for value in values:
        key = normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
```

Video topic source:

```python
def resolve_video_topic(scene_doc: dict, job_metadata: dict | None = None) -> str:
    job_metadata = job_metadata or {}
    return (
        str(scene_doc.get("topic") or "")
        or str(scene_doc.get("title") or "")
        or str(job_metadata.get("topic") or "")
        or str(job_metadata.get("title") or "")
        or str(job_metadata.get("youtube_title") or "")
        or ""
    )
```

Seed convention:

```python
def visual_seed(channel_id: str, job_id: str, scene: dict, scene_index: int, topic: str | None = None) -> str:
    topic_hash = hashlib.sha1((topic or "").encode("utf-8")).hexdigest()[:12]
    return f"{channel_id}:{job_id}:{scene.get('id','scene')}:{scene_index}:{topic_hash}"

def candidate_tiebreak_seed(base_seed: str, provider_asset_id: str) -> str:
    return f"{base_seed}:{provider_asset_id}"
```

Use this seed convention across planner, query selection, scoring tie-breaks, and report ordering.

---

## 8. Long/short quota rules

Do not apply long-form quotas to Shorts.

```python
def classify_video_length(scene_count: int, visual_dna: dict) -> str:
    short_max = int(visual_dna["video_length_profiles"]["short"]["max_scenes"])
    return "short" if scene_count <= short_max else "long"
```

### Long-form

For `scene_count >= 25`:

- target at least 6 distinct visual buckets
- target at least 5 distinct shot types
- target 4 graphic cards only when cards are supported or report-only planned
- bucket minimums are targets in `report_only` / `warn`
- bucket minimums become hard constraints only in `enforce`

### Short-form

For `scene_count <= 24`:

- target at least 3 visual buckets when feasible
- target at least 3 shot types when feasible
- do not require graphic cards
- never fail because long-form quotas are not met

### Largest-remainder normalization

If bucket minimums exceed 60% of total scenes, scale using largest remainder.

```python
from math import floor

def normalize_long_minimums_largest_remainder(
    bucket_mins: dict[str, int],
    scene_count: int,
    priority_order: list[str],
) -> dict[str, int]:
    max_reserved = int(scene_count * 0.60)
    total = sum(bucket_mins.values())

    if total <= max_reserved:
        return dict(bucket_mins)

    raw = {k: (v * max_reserved / total) for k, v in bucket_mins.items()}
    base = {k: floor(v) for k, v in raw.items()}
    remaining = max_reserved - sum(base.values())

    priority_index = {bucket: i for i, bucket in enumerate(priority_order)}
    ranked = sorted(
        raw.keys(),
        key=lambda k: (-(raw[k] - base[k]), priority_index.get(k, 999), k),
    )

    for bucket in ranked[:remaining]:
        base[bucket] += 1

    for bucket in priority_order:
        if bucket_mins.get(bucket, 0) > 0 and base.get(bucket, 0) == 0 and sum(base.values()) < max_reserved:
            base[bucket] = 1

    return base
```

---

## 9. Visual planner

Add:

```text
src/video_agent/assets/visual_planner.py
```

### Matching strategy note

The system intentionally uses two matching styles:

1. Bucket triggers use normalized phrase substring matching.
2. Candidate semantic match uses token, token-synonym, and phrase-synonym scoring.

Reason:

- Bucket planning benefits from phrases like `te notas`, `por la tarde`, `wake up`.
- Pexels metadata is sparse, so candidate matching needs token-level scoring.

### Role detection

Role keywords come from `visual-dna.yaml`.

```python
def scene_text(scene: dict) -> str:
    return " ".join([
        str(scene.get("narration_text") or ""),
        str(scene.get("on_screen_text") or ""),
        str(scene.get("visual_prompt") or ""),
        str(scene.get("visual_brief") or ""),
    ])

def detect_scene_role(scene: dict, visual_dna: dict) -> str:
    text = normalize_text(scene_text(scene))
    best_role = "explanation"
    best_score = 0

    for lang in ("es", "en"):
        for role, terms in visual_dna.get("role_keywords", {}).get(lang, {}).items():
            score = sum(1 for term in terms if normalize_text(term) in text)
            if score > best_score:
                best_role = role
                best_score = score

    return best_role
```

### Allowed buckets

Do not mutate config-derived lists.

```python
def allowed_visual_buckets(visual_dna: dict, renderer_caps: dict) -> list[str]:
    buckets = list(visual_dna["visual_buckets"].keys())
    if not renderer_caps.get("graphic_cards", False):
        return [b for b in buckets if b != "local_graphic_card"]
    return buckets
```

### Bucket keyword score

```python
def bucket_keyword_score(text_value: str, bucket_cfg: dict) -> int:
    text = normalize_text(text_value)
    score = 0

    for term in bucket_cfg.get("keyword_triggers", {}).get("es", []):
        if normalize_text(term) in text:
            score += 2

    for term in bucket_cfg.get("keyword_triggers", {}).get("en", []):
        if normalize_text(term) in text:
            score += 1

    return score
```

### Bucket choice

```python
def choose_visual_bucket(
    scene: dict,
    scene_index: int,
    scene_count: int,
    channel_id: str,
    job_id: str,
    topic: str | None,
    visual_dna: dict,
    current_counts: dict[str, int],
    renderer_caps: dict,
) -> str:
    base_seed = visual_seed(channel_id, job_id, scene, scene_index, topic)
    role = detect_scene_role(scene, visual_dna)
    allowed = allowed_visual_buckets(visual_dna, renderer_caps)
    length_profile = classify_video_length(scene_count, visual_dna)

    scores: dict[str, float] = {}

    for bucket_id in allowed:
        cfg = visual_dna["visual_buckets"][bucket_id]
        score = float(cfg.get("weight", 1.0))
        score += bucket_keyword_score(scene_text(scene), cfg) * 0.25

        if bucket_id in visual_dna.get("role_to_buckets", {}).get(role, []):
            score += 0.75

        if would_exceed_bucket_ratio(bucket_id, current_counts, scene_count, cfg, length_profile):
            score -= 1.00

        if under_minimum_target(bucket_id, current_counts, scene_index, scene_count, cfg, length_profile):
            score += 0.50

        scores[bucket_id] = score

    if not scores:
        return "persona_moment"

    return deterministic_argmax(scores, seed=base_seed)
```

Helper stubs:

`scene_index` is **0-based** everywhere in the visual planner. For the last scene, `scene_index == scene_count - 1`.

Long-form ratio/minimum helpers must only apply when `length_profile == "long"`. Short videos must not be constrained by long-form bucket quotas.

```python
def would_exceed_bucket_ratio(
    bucket_id: str,
    current_counts: dict[str, int],
    scene_count: int,
    bucket_cfg: dict,
    length_profile: str,
) -> bool:
    if length_profile != "long":
        return False

    max_ratio = bucket_cfg.get("long_max_ratio_per_video")
    if max_ratio is None or scene_count <= 0:
        return False

    projected_count = int(current_counts.get(bucket_id, 0)) + 1
    return (projected_count / scene_count) > float(max_ratio)

def under_minimum_target(
    bucket_id: str,
    current_counts: dict[str, int],
    scene_index: int,
    scene_count: int,
    bucket_cfg: dict,
    length_profile: str,
) -> bool:
    if length_profile != "long":
        return False

    min_target = int(bucket_cfg.get("long_min_per_video") or 0)
    if min_target <= 0:
        return False

    # 0-based index: at the last scene, remaining_scenes == 1.
    remaining_scenes = max(0, scene_count - scene_index)
    current = int(current_counts.get(bucket_id, 0))
    return current < min_target and remaining_scenes >= (min_target - current)
```

Fallback:

```python
def fallback_bucket_for_index(index: int, visual_dna: dict, renderer_caps: dict) -> str:
    seq = [
        b for b in visual_dna["default_bucket_sequence"]
        if b in allowed_visual_buckets(visual_dna, renderer_caps)
    ]
    return seq[index % len(seq)] if seq else "persona_moment"
```

---

## 10. Shot-type assignment

```python
def choose_shot_type(scene: dict, bucket: str, scene_index: int, previous_shot_types: list[str], visual_dna: dict, renderer_caps: dict) -> str:
    if bucket == "local_graphic_card":
        return "graphic"

    preferred_by_bucket = {
        "macro_texture": ["macro", "closeup"],
        "spain_daily_life": ["wide", "medium"],
        "persona_moment": ["medium", "medium_closeup"],
        "body_signal": ["closeup", "medium_closeup"],
        "food_practical": ["closeup", "medium"],
        "sleep_stress": ["medium", "closeup"],
        "gentle_movement": ["wide", "medium"],
    }

    seq = list(preferred_by_bucket.get(bucket) or visual_dna["default_shot_sequence"])

    if not renderer_caps.get("graphic_cards", False):
        seq = [s for s in seq if s != "graphic"]

    max_consecutive = 2
    for candidate in seq:
        if len(previous_shot_types) >= max_consecutive and all(s == candidate for s in previous_shot_types[-max_consecutive:]):
            continue
        return candidate

    return "medium"
```

---

## 11. Pexels query expansion

Pexels queries must be English.

Scene briefs can remain Spanish/channel-language, but queries sent to Pexels must be normalized to English.

```python
def expand_pexels_queries(scene: dict, visual_dna: dict) -> list[str]:
    bucket_id = scene.get("visual_bucket") or "persona_moment"
    bucket_cfg = visual_dna["visual_buckets"].get(bucket_id, {})

    source_texts = [
        scene.get("visual_brief"),
        scene.get("visual_prompt"),
        scene.get("fallback_visual_query"),
        scene.get("on_screen_text"),
    ]

    normalized_scene_queries = [
        normalize_to_english_pexels_query(text, visual_dna)
        for text in source_texts
        if text
    ]

    bucket_queries = bucket_cfg.get("pexels_queries_en", [])

    queries: list[str] = []
    queries.extend(normalized_scene_queries)
    queries.extend(bucket_queries)

    queries = [truncate_query_terms(q, max_terms=visual_dna["query_policy"]["max_query_terms"]) for q in queries]
    queries = [q for q in queries if q and not is_strong_negative_query(q, visual_dna)]
    queries = stable_dedupe(queries)

    return queries[: int(visual_dna["query_policy"]["max_queries_per_scene"])]
```

Search order per scene:

1. Try library cache for all expanded queries.
2. Try query cache before live API.
3. Hit Pexels API for only the first `max_api_queries_per_scene` queries.
4. If budget is exhausted, use cached candidates, supported graphic cards, or placeholder fallback with report warning.

---

## 12. Semantic matching

No embeddings. No model dependency.

Synonyms and stopwords come from `visual-dna.yaml`.

### Token normalization

```python
def normalize_terms(text: str, visual_dna: dict) -> set[str]:
    text = normalize_text(text)
    # Keep hyphen and plus so terms like "middle-aged" and "45+" survive tokenization.
    # Phrase synonyms still handle multi-token phrases such as "middle aged".
    raw_terms = re.findall(r"[a-z0-9+-]+", text)

    policy = visual_dna.get("token_policy", {})
    stopwords = set(policy.get("stopwords", {}).get("en", []))
    stopwords.update(policy.get("stopwords", {}).get("es", []))

    preserve_numeric = set(policy.get("preserve_numeric_terms", []))
    preserve_short = set(policy.get("preserve_short_terms", []))

    terms = set()
    for term in raw_terms:
        # Regex keeps + and - so terms like "45+" and "middle-aged" survive.
        # Drop punctuation-only artifacts such as "---" or "+++".
        if not any(ch.isalnum() for ch in term):
            continue

        if term in preserve_numeric or term in preserve_short:
            terms.add(term)
            continue
        if len(term) <= 2:
            continue
        if term in stopwords:
            continue
        terms.add(term)

    return terms
```

### Phrase hits

```python
def phrase_hits(text: str, phrases: list[str]) -> set[str]:
    normalized = normalize_text(text)
    hits = set()
    for phrase in phrases:
        p = normalize_text(phrase)
        if p and p in normalized:
            hits.add(p)
    return hits
```

### Candidate text

For API candidates:

```python
candidate_text = " ".join([
    str(candidate.get("source_url") or ""),
    str(candidate.get("photographer") or ""),
    " ".join(candidate.get("tags") or []),
    str(candidate.get("attribution") or ""),
])
```

For stored SQLite assets:

```python
stored_text = " ".join([
    str(asset.get("original_query") or ""),
    str(asset.get("provider_tags_json") or ""),
    str(asset.get("photographer") or ""),
    str(asset.get("attribution") or ""),
])
```

### Semantic score

Phrase hits are capped at 3 so phrase synonyms cannot dominate.

```python
def semantic_match_score(query: str, candidate_text: str, visual_dna: dict) -> tuple[float, int, int, int, list[str]]:
    q_terms = normalize_terms(query, visual_dna)
    c_terms = normalize_terms(candidate_text, visual_dna)

    token_synonyms = visual_dna.get("synonyms", {}).get("tokens", {})
    phrase_synonyms = visual_dna.get("synonyms", {}).get("phrases", {})

    if not q_terms or not c_terms:
        return 0.0, 0, 0, 0, []

    direct_hits = q_terms & c_terms

    synonym_hits = set()
    for q in q_terms:
        syns = {normalize_text(s) for s in token_synonyms.get(q, [])}
        if syns & c_terms:
            synonym_hits.add(q)

    phrase_hit_count = 0
    phrase_matched_labels = []
    combined_text = f"{query} {candidate_text}"

    for label, phrases in phrase_synonyms.items():
        hits = phrase_hits(combined_text, phrases)
        if hits:
            phrase_hit_count += 1
            phrase_matched_labels.append(label)

    phrase_hit_count_capped = min(phrase_hit_count, 3)

    weighted_hits = (
        len(direct_hits)
        + 0.75 * len(synonym_hits - direct_hits)
        + 0.50 * phrase_hit_count_capped
    )
    denom = max(3, min(len(q_terms), 12))
    score = min(1.0, weighted_hits / denom)

    matched_terms = sorted(direct_hits | synonym_hits | set(phrase_matched_labels))

    return score, len(direct_hits), len(synonym_hits), phrase_hit_count_capped, matched_terms
```

Strict gate:

```python
def passes_semantic_gate(score: float, direct_hits: int, synonym_hits: int, phrase_hits_count: int) -> bool:
    return score >= 0.34 and (direct_hits + synonym_hits + phrase_hits_count) >= 2
```

---

## 13. Candidate quality score

No unexplained magic values.

```python
def quality_score(candidate: dict, required_orientation: str = "landscape") -> float:
    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    duration = float(candidate.get("duration_sec") or candidate.get("duration") or 0)
    quality_label = str(candidate.get("quality") or "").lower()

    score = 0.0

    if width >= 1920 and height >= 1080:
        score += 0.40
    elif width >= 1280 and height >= 720:
        score += 0.25
    elif width and height:
        score += 0.10

    if width and height:
        ratio = width / height
        if required_orientation == "landscape" and abs(ratio - 16 / 9) < 0.12:
            score += 0.25
        elif required_orientation == "landscape" and ratio > 1.3:
            score += 0.15

    if duration >= 20:
        score += 0.20
    elif duration >= 10:
        score += 0.12
    elif duration > 0:
        score += 0.05

    if quality_label in {"large2x", "fullhd", "hd", "original"}:
        score += 0.15

    return min(1.0, score)
```

If duration is missing, do not hard reject solely because duration is unknown.

---

## 14. Negative matching

Medical-consultation context only relaxes weak terms directly tied to neutral medical advice, such as `doctor` and `medicine`.

It must not relax:

- `hospital`
- `emergency`
- `frail`
- `sick`
- `sad elderly`
- strong phrases such as `doctor diagnosis`, `hospital bed`, `medical emergency`

```python
def negative_match_score(query: str, candidate_text: str, scene_text_value: str, visual_dna: dict) -> tuple[float, list[str]]:
    text = normalize_text(" ".join([query, candidate_text]))

    strong_hits = [
        phrase for phrase in visual_dna["negative_patterns"]["strong_phrases_en"]
        if normalize_text(phrase) in text
    ]
    weak_hits = [
        term for term in visual_dna["negative_patterns"]["weak_terms_en"]
        if normalize_text(term) in text
    ]

    consult_context = any(
        phrase in normalize_text(scene_text_value)
        for phrase in [
            "consulta con tu medico",
            "consulta a tu medico",
            "acude al medico",
            "seek medical advice",
            "talk to your doctor",
        ]
    )

    if consult_context:
        weak_hits = [h for h in weak_hits if h not in {"doctor", "medicine"}]

    if strong_hits:
        return 1.0, strong_hits
    if len(weak_hits) >= 2:
        return 0.5, weak_hits
    if len(weak_hits) == 1:
        return 0.20, weak_hits
    return 0.0, []
```

Application:

- `1.0`: hard reject
- `0.5`: strong penalty
- `0.2`: weak penalty

---

## 15. Candidate eligibility

Hard constraints must run before scoring. Do not use giant penalties.

```python
from dataclasses import dataclass

@dataclass
class EligibilityResult:
    eligible: bool
    hard_reject_reason: str | None = None
    can_escape_hatch: bool = False
```

```python
def candidate_eligibility(candidate, scene, job_state, visual_config, visual_dna) -> EligibilityResult:
    if not is_pexels_provider(candidate.get("provider")):
        return EligibilityResult(False, "non_pexels_provider")

    if is_duplicate_asset_in_current_job(candidate, job_state):
        if visual_config["diversity"].get("duplicate_asset_escape_hatch") == "warn_if_no_alternatives":
            return EligibilityResult(False, "duplicate_asset_current_job", can_escape_hatch=True)
        return EligibilityResult(False, "duplicate_asset_current_job")

    if strong_negative_match(candidate, scene, visual_dna):
        return EligibilityResult(False, "strong_negative_pattern")

    if is_invalid_local_cache_file(candidate):
        return EligibilityResult(False, "invalid_cache_file")

    if is_resolution_too_low(candidate) and rollout_mode_is_enforce(visual_config):
        return EligibilityResult(False, "resolution_too_low", can_escape_hatch=True)

    return EligibilityResult(True)
```

Selection behavior:

1. Score eligible candidates.
2. If none exist, inspect escape-hatch candidates.
3. If escape-hatch candidates exist, select best one and emit QA warning.
4. If no candidate exists, use supported graphic card or generated placeholder as last resort.

---

## 16. Candidate scoring

Use structured scoring.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class CandidateScore:
    total: float
    semantic_match: float
    bucket_match: float
    shot_type_match: float
    locale_fit: float
    recent_freshness: float
    lifetime_novelty: float
    quality: float
    metadata_completeness: float
    penalty_total_raw: float
    penalty_total_capped: float
    penalties: dict[str, float]
    reasons: list[str]
    matched_terms: list[str]
```

Formula:

```python
raw_penalty_total = sum(penalties.values())
penalty_total = min(1.0, raw_penalty_total)

total = (
    semantic_match * 0.34
    + bucket_match * 0.12
    + shot_type_match * 0.08
    + locale_fit * 0.04
    + recent_freshness * 0.10
    + lifetime_novelty * 0.10
    + quality * 0.18
    + metadata_completeness * 0.04
    - penalty_total
)
```

Report both raw and capped penalties.

### Recent freshness

Define the helper semantics clearly:

- `last_used_older_than_days(asset_usage, 30)` means strictly older than 30 × 24 hours.
- exactly 30 days old is not older than 30 days.

```python
def recent_freshness(asset_usage: list[dict]) -> float:
    if not asset_usage:
        return 1.0
    if last_used_older_than_days(asset_usage, 90):
        return 0.8
    if last_used_older_than_days(asset_usage, 30):
        return 0.5
    return 0.1
```

Interpretation:

- used yesterday → `0.1`
- used 25 days ago → `0.1`
- used 45 days ago → `0.5`
- used 120 days ago → `0.8`
- never used → `1.0`

### Lifetime novelty

```python
def lifetime_novelty(use_count: int, visual_config: dict) -> float:
    saturation_count = int(
        visual_config.get("diversity", {}).get("lifetime_novelty_saturation_count", 6)
    )
    saturation_count = max(1, saturation_count)
    return max(0.0, 1.0 - min(1.0, use_count / saturation_count))
```

Default interpretation for `vida-plena-45`:

- `lifetime_novelty_saturation_count: 6`
- asset used 0 times → `1.0`
- asset used 3 times → `0.5`
- asset used 6+ times → `0.0`

This keeps the old v5.2 behavior as the default while making the threshold channel-configurable.

Call-site requirement:

- Every caller that computes `CandidateScore.lifetime_novelty` must call `lifetime_novelty(use_count, visual_config)`.
- Do not keep the old one-argument call shape.
- Candidate scoring tests must fail if `visual_config` is not passed through.

### Locale fit

Keep low weight because Pexels metadata often lacks explicit location.

```python
def locale_fit_score(scene: dict, candidate_text: str) -> float:
    text = normalize_text(candidate_text)
    locale_terms = ["spain", "spanish", "madrid", "barcelona", "mediterranean", "european"]

    if any(term in text for term in locale_terms):
        return 1.0

    if scene.get("locale_feel") in {None, "", "Generic"}:
        return 0.6

    return 0.35
```

### Metadata completeness

This is source/audit completeness, not semantic richness. `original_query` and `provider_tags_json` are reported separately.

```python
def metadata_completeness_score(asset: dict) -> float:
    checks = [
        bool(asset.get("provider")),
        bool(asset.get("provider_asset_id")),
        bool(asset.get("original_url")),
        bool(asset.get("photographer") or asset.get("photographer_url")),  # one creator-audit slot
        bool(asset.get("license")),
        bool(asset.get("width")),
        bool(asset.get("height")),
    ]
    return sum(1 for value in checks if value) / len(checks)

def semantic_metadata_present(asset: dict) -> dict[str, bool]:
    return {
        "original_query": bool(asset.get("original_query")),
        "provider_tags_json": bool(asset.get("provider_tags_json")),
    }
```

### Penalties

```python
penalties = {
    "same_creator_current_job": 0.12 if creator_used_current_job else 0.0,
    "same_creator_ratio": 0.20 if creator_ratio_after_pick > max_same_creator_ratio else 0.0,
    "reuse_last_30_days": 0.20 if used_last_30_days else 0.0,
    "reuse_last_90_days": 0.08 if used_last_90_days else 0.0,
    "consecutive_shot_type": 0.20 if would_make_more_than_two_consecutive else 0.0,
    "bucket_overuse": 0.25 if would_exceed_bucket_ratio else 0.0,
    "negative_pattern": negative_score,
    "active_reservation": 0.20 if actively_reserved_by_other_job else 0.0,
}
```

Tie-breaker:

```python
sort_key = (
    -score.total,
    -score.metadata_completeness,
    asset.use_count,
    -asset.width * asset.height,
    stable_hash(candidate_tiebreak_seed(base_seed, provider_asset_id)),
)
```

Wiring note:

- `base_seed` is created once per scene via `visual_seed(...)`.
- The selector must pass `base_seed` into candidate ranking/scoring or keep it in the ranking context.
- `CandidateScore` itself does not need to store `base_seed`, but the selected asset report should include the final `tie_break_seed` used for auditability.

---

## 17. Creator key

Use stable Pexels creator identity.

```python
def creator_key(candidate_or_asset: dict) -> str | None:
    user_id = candidate_or_asset.get("user_id") or candidate_or_asset.get("photographer_id")
    if user_id:
        return f"pexels:{user_id}"

    photographer_url = candidate_or_asset.get("photographer_url")
    if photographer_url:
        return "pexels:url:" + normalize_url(photographer_url)

    photographer = candidate_or_asset.get("photographer")
    if photographer:
        return "pexels:namehash:" + hashlib.sha1(photographer.strip().lower().encode("utf-8")).hexdigest()[:12]

    return None
```

Do not use photographer name directly as primary key.

---

## 18. SQLite migration

Extend the existing `asset_library/metadata.db`. Do not create a parallel JSONL index.

Idempotent migration helper:

```python
def add_column_if_missing(db, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
```

Add to `assets` if missing:

```sql
visual_bucket TEXT
shot_type TEXT
mood TEXT
locale_feel TEXT
creator_key TEXT
duration_sec REAL
quality_score REAL
metadata_json TEXT
```

Add to `asset_usage` if missing:

```sql
visual_bucket TEXT
shot_type TEXT
duration_used_sec REAL
topic TEXT
```

Indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_assets_creator_key ON assets(creator_key);
CREATE INDEX IF NOT EXISTS idx_assets_visual_bucket ON assets(visual_bucket);
CREATE INDEX IF NOT EXISTS idx_assets_shot_type ON assets(shot_type);
CREATE INDEX IF NOT EXISTS idx_usage_asset_used_at ON asset_usage(asset_id, used_at);
CREATE INDEX IF NOT EXISTS idx_usage_job_scene ON asset_usage(job_id, scene_id);
```

Lazy backfill:

- Existing rows with null `visual_bucket`, `shot_type`, or `creator_key` remain valid.
- When an asset is selected, infer missing fields and update the row.
- Historical metrics should mark unknown metadata as `unknown`, not force it into a bucket.

Rollback safety:

- Extra columns must not break legacy code paths.
- Test rollback after migration explicitly.

---

## 19. Optional backfill script

Add:

```text
scripts/backfill_asset_visual_metadata.py
```

Purpose:

- infer `visual_bucket`, `shot_type`, `creator_key`, `quality_score`, and `duration_sec`
- optionally inspect historical manifests/published jobs
- never call live Pexels API by default

CLI:

```bash
python scripts/backfill_asset_visual_metadata.py \
  --channel-id vida-plena-45 \
  --visual-dna configs/vida-plena-45/visual-dna.yaml \
  --asset-db asset_library/metadata.db \
  --dry-run
```

Modes:

```text
--dry-run
--write
--limit N
--since YYYY-MM-DD
```

Backfill metadata must use the nested `backfill` shape below. Do not use flat keys such as `backfill_confidence`, `backfilled_at`, or `backfill_source`.

Store this object inside `assets.metadata_json` under a `backfill` key, preserving any existing JSON keys:

```json
{
  "backfill": {
    "confidence": "low|medium|high",
    "backfilled_at": "ISO datetime",
    "source": "query_tags_heuristic"
  }
}
```

---

## 20. Cross-job reservation concurrency

Current-job duplicate prevention is handled by `StockAssetService` job state and eligibility.

Cross-job race prevention is handled by SQLite `asset_reservations`.

Do not rely on reservations to prevent duplicates inside the same video.

Reservation storage:

- Use the existing asset library SQLite database.
- Resolve DB path from `visuals.asset_library_path`, defaulting to `asset_library/metadata.db`.
- Do not create a separate reservation database.

Create table:

```sql
CREATE TABLE IF NOT EXISTS asset_reservations (
    reservation_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reservations_asset ON asset_reservations(asset_id);
CREATE INDEX IF NOT EXISTS idx_reservations_expires ON asset_reservations(expires_at);
```

Reservation function:

```python
def try_reserve_asset(db_path: Path, asset_id: str, channel_id: str, job_id: str, scene_id: str, ttl_minutes: int) -> bool:
    now = utc_now_iso()
    expires = utc_now_plus_minutes_iso(ttl_minutes)

    with sqlite3.connect(db_path, timeout=30, isolation_level="IMMEDIATE") as db:
        db.row_factory = sqlite3.Row

        db.execute("DELETE FROM asset_reservations WHERE expires_at <= ?", (now,))

        active = db.execute(
            """
            SELECT 1
            FROM asset_reservations
            WHERE asset_id = ?
              AND expires_at > ?
              AND job_id != ?
            LIMIT 1
            """,
            (asset_id, now, job_id),
        ).fetchone()

        if active:
            return False

        db.execute(
            """
            INSERT INTO asset_reservations (
                reservation_id, asset_id, channel_id, job_id, scene_id, reserved_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), asset_id, channel_id, job_id, scene_id, now, expires),
        )

        return True
```

When `record_usage(...)` succeeds:

```sql
DELETE FROM asset_reservations
WHERE asset_id = ? AND job_id = ? AND scene_id = ?
```

If the job fails, reservation expires automatically.

---

## 21. API budget, throttling, and 429 handling

Pexels API usage must be bounded per scene and per video.

```python
class ApiBudget:
    def __init__(self, max_per_video: int):
        self.max_per_video = max_per_video
        self.used = 0
        self.rate_limited = False

    def can_call(self) -> bool:
        return not self.rate_limited and self.used < self.max_per_video

    def record_call(self) -> None:
        self.used += 1

    def record_429(self) -> None:
        self.rate_limited = True
```

Behavior:

- always try library cache first
- always try query cache before API
- if API returns 429:
  - record `rate_limited = true`
  - stop live API calls for the rest of the video
  - continue with library/query-cache results
  - write report warning
  - write batch-level backoff state when possible

Suggested backoff state path:

```text
caches/pexels_api_backoff.json
```

Example:

```json
{
  "provider": "pexels",
  "rate_limited_at": "ISO datetime",
  "backoff_until": "ISO datetime",
  "source_job_id": "job-id"
}
```

Subsequent jobs should read this file before live Pexels calls. If `now < backoff_until`, skip live API and use cache/library only.

Hourly budget:

- Enforce per-scene and per-video budgets inside `StockAssetService`.
- Enforce hourly budget only if batch orchestration exists.
- If no orchestrator support exists, emit report warning:

```json
{
  "api_budget": {
    "hourly_budget_enforced": false,
    "hourly_budget_warning": "No batch-level API budget manager is available."
  }
}
```

Report fields:

```json
{
  "api_budget": {
    "max_api_requests_per_video": 80,
    "api_requests_used": 37,
    "rate_limited": false,
    "query_cache_hits": 61,
    "library_cache_hits": 19,
    "hourly_budget_enforced": false
  }
}
```

---

## 22. Graphic card gating

Graphic cards must never break render unless explicitly enforced.

```python
def should_fail_for_missing_graphic_renderer(visual_config: dict, renderer_caps: dict) -> bool:
    card_cfg = visual_config.get("graphic_cards", {})
    return (
        card_cfg.get("enabled") is True
        and card_cfg.get("rollout_mode") == "enforce"
        and not renderer_caps.get("graphic_cards", False)
    )
```

Behavior matrix:

| Mode | Renderer missing | Behavior |
|---|---|---|
| `disabled` | any | do nothing |
| `report_only` | yes | plan cards in report only; no render failure |
| `auto_if_supported` | yes | skip cards, warn |
| `auto_if_supported` | no | render cards |
| `enforce` | yes | fail visual QA before render |
| `enforce` | no | render cards; fail if quota unmet |

Minimum first implementation:

- `checklist`
- `timeline`
- `habit_matrix`

Do not require `body_area_map` or `ingredient_card`.

---

## 23. Placeholder policy and baseline

Generated placeholders are last-resort fallback, but must be tracked.

Resolve placeholder baseline in this order:

```python
def resolve_placeholder_baseline(channel_id: str, current_job_id: str) -> float | None:
    # 1. Previous visual-diversity reports:
    #    glob outputs/*/visual-diversity-report.json
    #    filter matching channel_id and job_id != current_job_id
    # 2. Existing job asset manifests where scene source == "generated_placeholder".
    # 3. published_videos.json if it references artifact paths/manifests.
    # 4. None if no baseline exists.
```

Rules:

- In `report_only`: placeholders are allowed but reported.
- In `warn`: placeholders are allowed but QA warning is emitted.
- In `enforce`: placeholders are allowed only if:
  - no Pexels candidate exists
  - no cached candidate exists
  - no graphic card renderer exists
  - render would otherwise fail

If baseline exists:

```python
fail_if(current_placeholder_ratio > baseline_placeholder_ratio)
```

If baseline is unavailable:

```python
fail_if(current_placeholder_ratio > visual_config["diversity"]["max_placeholder_ratio_enforce"])
```

If baseline is `None`:

- `report_only`: report `"baseline_placeholder_ratio": null`
- `warn`: warn `"placeholder baseline unavailable"`
- `enforce`: use `max_placeholder_ratio_enforce`

---

## 24. Duration handling

Existing stock filters should request clips long enough for the scene:

```python
filters["min_duration_sec"] = max(10, scene_duration_sec)
```

Selection rule:

- If candidate duration is known and shorter than `min(scene_duration_sec, 10)`, apply penalty.
- If candidate duration is unknown, do not hard reject; score lower.
- Do not add video looping in this change.
- Keep existing image-to-video conversion behavior.

---

## 25. Visual diversity QA

Add:

```text
configs/vida-plena-45/qa-rules/visual-diversity.yaml
```

```yaml
schema_version: "5.0"

rollout_mode: "report_only"

hard_fail:
  non_pexels_external_stock_asset: true
  duplicate_asset_in_same_video_when_alternatives_exist: true
  more_than_two_consecutive_same_shot_type: true
  pexels_missing_source_metadata: true
  graphic_cards_required_but_renderer_missing: true

soft_fail:
  bucket_ratio_exceeded: true
  too_many_generic_stock_scenes: true
  too_few_graphic_cards_when_supported: true
  too_few_spain_locale_scenes: true
  repeated_creator_over_threshold: true
  asset_reused_recently: true
  pexels_api_rate_limited: true
  placeholder_ratio_increased: true
  placeholder_baseline_unavailable: true

thresholds:
  long_form_min_scenes: 25
  min_distinct_visual_buckets_per_long_video: 6
  min_distinct_shot_types_per_long_video: 5
  min_local_graphic_cards_per_long_video_when_supported: 4
  min_spain_or_mediterranean_locale_ratio: 0.20
  max_generic_stock_ratio: 0.35
  max_same_creator_ratio: 0.25
```

Verdict behavior:

```python
def apply_rollout_mode(raw_verdict: str, rollout_mode: str) -> str:
    if rollout_mode == "report_only":
        return "pass_with_report"
    if rollout_mode == "warn":
        return "warn" if raw_verdict in {"warn", "fail"} else "pass"
    if rollout_mode == "enforce":
        return raw_verdict
    return "pass_with_report"
```

---

## 26. Visual diversity report

Write per job:

```text
outputs/<job_id>/visual-diversity-report.json
outputs/<job_id>/visual-diversity-report.md
```

Required JSON fields:

```json
{
  "job_id": "example",
  "channel_id": "vida-plena-45",
  "provider_policy": "pexels_only",
  "scene_count": 45,
  "video_length_profile": "long",
  "rollout_mode": "report_only",

  "assets_selected": 41,
  "graphic_cards_rendered": 4,
  "graphic_cards_planned": 4,
  "graphic_cards_supported": true,

  "bucket_distribution": {},
  "shot_type_distribution": {},
  "creator_distribution": {},

  "api_budget": {
    "max_api_requests_per_video": 80,
    "api_requests_used": 37,
    "rate_limited": false,
    "query_cache_hits": 61,
    "library_cache_hits": 19,
    "hourly_budget_enforced": false,
    "hourly_budget_warning": "No batch-level API budget manager is available."
  },

  "placeholder_count": 0,
  "placeholder_ratio": 0.0,
  "baseline_placeholder_ratio": null,

  "reuse_warnings": [],
  "negative_pattern_warnings": [],

  "semantic_metadata_present": {
    "original_query": true,
    "provider_tags_json": true
  },

  "qa_verdict": "pass_with_report",
  "visual_diversity_score": 0.72,
  "baseline_visual_diversity_score": 0.48,
  "delta_vs_baseline": 0.24
}
```

---

## 27. Baseline metrics

Compute before/after metrics for old and new jobs.

Use denominator caps that do not make tiny videos trivially perfect.

```python
def visual_diversity_score(report: dict) -> float:
    scene_count = max(1, int(report["scene_count"]))
    distinct_buckets = len(report.get("bucket_distribution", {}))
    distinct_shots = len(report.get("shot_type_distribution", {}))
    max_creator_ratio = max(report.get("creator_distribution", {"unknown": 1}).values()) / scene_count
    reused_recent_assets = len(report.get("reuse_warnings", []))
    locale_count = report.get("spain_or_mediterranean_scene_count", 0)
    rendered_cards = report.get("graphic_cards_rendered", 0)
    target_cards = max(1, report.get("graphic_cards_target", 4))

    bucket_denominator = min(max(scene_count, 6), 8)
    shot_denominator = min(max(scene_count, 4), 6)

    distinct_bucket_ratio = min(1.0, distinct_buckets / bucket_denominator)
    distinct_shot_type_ratio = min(1.0, distinct_shots / shot_denominator)
    creator_diversity_score = 1.0 - min(1.0, max_creator_ratio)
    low_reuse_score = 1.0 - min(1.0, reused_recent_assets / scene_count)
    locale_fit_score = min(1.0, locale_count / scene_count)
    graphic_card_score = min(1.0, rendered_cards / target_cards)

    return (
        distinct_bucket_ratio * 0.25
        + distinct_shot_type_ratio * 0.20
        + creator_diversity_score * 0.15
        + low_reuse_score * 0.20
        + locale_fit_score * 0.10
        + graphic_card_score * 0.10
    )
```

Target:

- Long-form score improves by at least `+0.20` over baseline.
- Placeholder ratio does not increase in `warn` or `enforce`.
- No non-Pexels external stock asset appears.

Candidate-score penalty caps are internal to asset scoring and do not change this report-level diversity metric.

---

## 28. Rollout and rollback

### Rollout

1. `report_only`: planner/scoring/reporting enabled, final selection unchanged or minimally changed.
2. `warn`: selector uses new scoring, QA emits warnings but render proceeds.
3. `enforce`: hard constraints block only when configured.

### Rollback

Single config switch:

```yaml
visuals:
  diversity:
    enabled: false
```

Rollback behavior:

- If disabled, revert to legacy `StockAssetService` behavior.
- Keep SQLite migrations harmless.
- Keep old manifests readable.
- Do not delete new metadata.
- Do not remove cached assets.

Regression rollback procedure:

1. Set `visuals.diversity.enabled=false`.
2. Set `graphic_cards.enabled=false`.
3. Re-run affected job.
4. Keep `visual-diversity-report.json` for debugging.

---

## 29. Test fixtures

Suggested structure:

```text
tests/assets/
  test_visual_planner.py
  test_pexels_query_expansion.py
  test_candidate_scoring.py
  test_asset_library_migrations.py
  test_asset_reservations.py
  test_api_budget.py
  test_visual_diversity_report.py
  fixtures/
    pexels_video_search_morning.json
    pexels_video_search_walking.json
    pexels_photo_search_food.json
    scenes_long_45.json
    scenes_short_8.json
```

Rules:

- No live Pexels API calls in unit tests.
- Use temporary SQLite databases.
- Use mocked/recorded normalized Pexels responses.
- Seed deterministic choices with `channel_id + job_id + scene_id + scene_index + topic_hash`.

Required tests:

1. Provider enforcement rejects non-Pexels external stock.
2. Spanish visual text becomes English Pexels query.
3. Age terms `45`, `50`, `55`, `60` are preserved.
4. Short-term preservation can be configured without duplicating numeric age preservation.
5. Token synonyms are config-driven.
6. Phrase synonyms are config-driven and capped.
7. Bucket assignment uses config keyword triggers and role mapping.
8. Bucket assignment seed differs across job IDs.
9. Long-form quotas do not apply to short videos.
10. Largest-remainder quota normalization preserves total allocation.
11. `normalize_text` strips accents and normalizes whitespace.
12. Tokenization preserves hyphen and plus in terms such as `middle-aged` and `45+`.
13. Tokenization drops punctuation-only artifacts such as `---` and `+++`.
14. `stable_dedupe` preserves first occurrence.
15. `deterministic_argmax` is stable and does not use Python `hash()`.
16. Semantic match is deterministic and config-driven.
17. Quality score is computed from resolution/aspect/duration/quality label.
18. Strong negative patterns hard reject.
19. Medical consultation only relaxes `doctor`/`medicine` weak terms.
20. Weak negative terms apply penalty.
21. Duplicate current-job asset is eligibility-rejected, not scored with giant penalty.
22. Recent freshness and lifetime novelty are separate scoring dimensions.
23. Penalty cap reports raw and capped values.
24. Creator ratio penalty uses stable creator key.
25. Reservation transaction reduces simultaneous active selection of the same asset across jobs.
26. API budget stops live calls after 429.
27. API backoff state is read by subsequent jobs.
28. Graphic cards are skipped with warning when renderer support is missing.
29. Enforce mode fails when graphic cards are required but renderer is missing.
30. SQLite migrations are idempotent.
31. Optional backfill script dry-run does not mutate DB.
32. Placeholder baseline resolves from previous reports/manifests when available.
33. Missing placeholder baseline uses `max_placeholder_ratio_enforce` in enforce mode.
34. Visual report contains API budget, cache stats, bucket distribution, shot distribution, semantic metadata presence, placeholders, and QA verdict.
35. Rollback switch disables new diversity behavior.
36. Rollback after SQLite migration uses legacy selection path.
---

## 30. Implementation phases

### Phase 1 — Config and report-only foundation

- Add `visual-dna.yaml`.
- Add visual diversity QA config.
- Add channel-agnostic visual DNA loader.
- Add deterministic helpers.
- Add visual planner.
- Add visual report generation.
- Add rollback switch.
- Do not enforce selection changes yet.

### Phase 2 — Query expansion and scoring

- Add English-only Pexels query expansion.
- Add token and phrase synonym matching from config.
- Add deterministic semantic match.
- Add quality score.
- Add negative matching.
- Replace hard-constraint numeric penalties with eligibility checks.
- Add penalty cap.

### Phase 3 — SQLite and reuse

- Add idempotent migrations.
- Add `creator_key`, reuse windows, recent freshness, lifetime novelty.
- Add cross-job reservations.
- Add optional backfill script.

### Phase 4 — API budget and 429 handling

- Add per-scene and per-video API budget.
- Add query-cache/library-cache stats.
- Add 429 handling.
- Add `caches/pexels_api_backoff.json`.
- Add hourly-budget warning when no batch orchestrator exists.

### Phase 5 — Graphic cards

- Add renderer capability detection.
- Insert graphic cards only when supported or report-only planned.
- Implement only checklist/timeline/habit_matrix initially.

### Phase 6 — Warn/enforce rollout

- Run at least three long-form dry runs.
- Compare baseline metrics.
- Move from `report_only` to `warn`.
- Move to `enforce` only after diversity improves and placeholders do not increase.

---

## 31. Final acceptance criteria

Complete when all 30 criteria are met:

1. External stock remains Pexels-only.
2. Old scene files without new visual fields still render.
3. New behavior can be disabled with `visuals.diversity.enabled=false`.
4. Visual DNA loading is channel-agnostic.
5. Token synonyms, phrase synonyms, stopwords, role keywords, bucket triggers, and role mappings live in config.
6. `normalize_text`, `stable_dedupe`, and `deterministic_argmax` have deterministic implementations.
7. Built-in Python `hash()` is not used for reproducible tie-breaking.
8. All deterministic seeds include `channel_id`, `job_id`, scene identity, and topic hash.
9. Video topic source is resolved from `scene_doc` or job metadata.
10. Long-form quotas apply only when `scene_count >= 25`.
11. Quota normalization uses largest-remainder allocation.
12. Short videos do not use long-form quotas.
13. `semantic_match` is deterministic and implemented without embeddings.
14. Phrase synonyms are supported separately from token synonyms and capped; tokenization preserves hyphen/plus terms such as `middle-aged` and `45+` while dropping punctuation-only artifacts.
15. Age terms such as `45`, `50`, `55`, `60` are preserved as demographic signal without duplicating them in `preserve_short_terms`.
16. Candidate quality score is heuristic-defined.
17. Strong negative patterns are hard rejected.
18. Medical consultation context only relaxes `doctor`/`medicine` weak terms.
19. Duplicate current-job assets are eligibility-rejected before scoring.
20. If no alternative exists, duplicate/reuse escape hatch may warn rather than crash.
21. Scoring penalties are capped at 1.0 while raw components are reported.
22. Recent freshness and lifetime novelty are separate scoring dimensions, lifetime novelty saturation is configurable via `lifetime_novelty_saturation_count`, and candidate scoring passes `visual_config` into `lifetime_novelty`.
23. Creator repetition uses stable `creator_key`.
24. Asset reservations reduce concurrent cross-job duplicate selection; same-job duplicates are handled by eligibility.
25. Pexels API calls are bounded by per-scene and per-video budgets; hourly budget is warned if no batch orchestrator exists.
26. 429 responses stop further live API calls for the current video and write backoff state for subsequent jobs.
27. SQLite migrations are idempotent and rollback after migration is tested.
28. Optional backfill script works in dry-run mode.
29. Graphic cards do not hard fail unless `graphic_cards.rollout_mode=enforce` and renderer support is missing or quota is unmet.
30. Placeholder ratio is tracked against previous visual reports/manifests when available; if unavailable, enforce mode uses `max_placeholder_ratio_enforce`.

---

## 32. Pre-flight confirmation

This spec includes the final rollout/rollback section, required test fixtures, implementation phases, final acceptance criteria, and Codex prompt.

The canonical implementation target is this v5.4 file only. Do not consult v1–v5.3 unless doing historical comparison.

Final cleanup applied in v5.4:

- `preserve_short_terms` no longer duplicates numeric age terms.
- Tokenization preserves hyphen and plus characters, and drops punctuation-only artifacts.
- `lifetime_novelty_saturation_count` is configurable.
- Candidate scoring must pass `visual_config` into `lifetime_novelty(use_count, visual_config)`.
- Sections §28–§33 are present and verified.

---

## 33. Codex prompt

```text
Implement the Pexels-only visual diversity upgrade in docs/specs/pexels-only-visual-diversity-v5.4.md.

Hard constraints:
- Keep Pexels as the only external stock source.
- Do not add Pixabay, Mixkit, Envato, Storyblocks, Artgrid, or any other stock provider.
- Align with the existing Python codebase:
  - src/video_agent/stages/assets.py
  - src/video_agent/assets/service.py
  - src/video_agent/assets/library.py
- Preserve backward compatibility with old scene JSON.
- Add a rollback switch: visuals.diversity.enabled=false must restore old selection behavior.
- Keep token synonyms, phrase synonyms, stopwords, role keywords, bucket triggers, and role mappings in config, not hardcoded Python.
- Implement normalize_text with Unicode accent stripping and deterministic normalization.
- Use deterministic token/synonym/phrase semantic matching, not embeddings.
- Preserve age terms such as 45/50/55/60 as demographic signal.
- Include channel_id + job_id + scene identity + topic hash in deterministic seeds.
- Define topic source from scene_doc/job metadata.
- Use deterministic_argmax and stable_dedupe exactly as specified.
- Do not use Python built-in hash() for tie-breaking.
- Use largest-remainder quota normalization.
- Apply long-form quotas only when scene_count >= 25.
- Use SQLite migrations for existing asset_library/metadata.db.
- Use eligibility checks for hard rejects, not giant numeric penalties.
- Cap scoring penalties at 1.0 while reporting raw penalty components.
- Add race-aware cross-job asset reservations; same-job duplicates remain blocked by eligibility.
- Add per-scene/per-video API budgets, 429 handling, and hourly-budget warnings when no batch orchestrator exists.
- Graphic cards must be feature-gated and must not break render unless explicitly enforced.
- Track placeholder ratio against prior visual reports/manifests when available.
- Add optional backfill dry-run script.
- Add tests with mocked Pexels responses; do not call the live Pexels API in tests.
```
