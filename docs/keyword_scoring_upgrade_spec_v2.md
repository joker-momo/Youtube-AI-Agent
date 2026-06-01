# Coding Spec — Nâng cấp logic chấm điểm từ khóa keyword scoring cho YouTube

Tài liệu này là **yêu cầu triển khai trực tiếp cho Codex / Claude Code**.  
Mục tiêu là nâng cấp pipeline chọn keyword trong module `Idea Generator` để không còn phụ thuộc đơn thuần vào `score DESC` của keyword scoring, mà dùng điểm tổng hợp theo mức độ phù hợp với kênh, ngôn ngữ, intent, khả năng sản xuất nội dung và độ khó SERP.

---

## 0. Nguyên tắc triển khai

1. **Không hỏi lại người dùng trong lúc code.** Các quyết định mặc định đã được chốt trong tài liệu này.
2. **Không phá vỡ flow cũ.** Nếu nơi nào đang kỳ vọng `top_keywords` là `list`, phải có backward compatibility.
3. **Không làm crash pipeline khi keyword scoring / Playwright / YouTube SERP lỗi.** Ghi note/error vào item và tiếp tục.
4. **Ưu tiên deterministic logic.** Tránh phụ thuộc LLM ở phần scoring.
5. **Giữ code testable.** Các hàm scoring nên là pure function càng nhiều càng tốt.
6. **SERP inspection là optional.** Mặc định tắt để không làm chậm pipeline; có thể bật bằng config.

---

## 1. Bối cảnh hiện tại

Pipeline hiện tại:

```text
seeds
-> score seeds bằng keyword scoring
-> lấy related keywords
-> score related keywords
-> merge + dedupe
-> sort theo score DESC
-> lấy top_n
-> gửi cho ChatGPT tạo idea
```

Vấn đề:

- Chọn keyword chỉ dựa vào điểm thô `score` của keyword scoring.
- Dễ chọn nhầm keyword tiếng Bồ Đào Nha nếu kênh đang target tiếng Tây Ban Nha.
- Dễ chọn keyword quá chung, không sát audience 45+.
- Keyword có `score = None` / `not_enough_search_data` đang bị xem là tín hiệu yếu, trong khi nhiều long-tail keyword ngách có thể đáng test.
- Chưa có lớp kiểm tra SERP thực tế để biết top results hiện tại có khó cạnh tranh hay không.

---

## 2. Cấu hình mặc định

Thêm default config trong `idea_generator.py`. Nếu project đã có hệ thống config/channel config thì dùng config hiện có, nhưng phải fallback về object dưới đây.

```python
DEFAULT_CHANNEL_KEYWORD_CONFIG = {
    "channel_name": "Vida Plena 45+: Salud y Bienestar",
    "target_language": "spanish",
    "target_audience": "people_45_plus",
    "audience_markers": [
        "45", "45+", "despues de los 45", "después de los 45",
        "mayores de 45", "a partir de los 45", "despues de los cuarenta",
        "después de los cuarenta"
    ],
    "core_topics": [
        "nutricion", "nutrición", "alimentacion", "alimentación",
        "comer mejor", "comer bien", "salud", "bienestar",
        "energia", "energía", "sueño", "dormir", "descanso",
        "habitos", "hábitos", "movimiento", "caminar",
        "estres", "estrés", "ansiedad", "peso", "metabolismo"
    ],
    "content_positioning": [
        "sin dietas extremas", "sin culpa", "sin caos",
        "simple", "practico", "práctico", "realista",
        "calma", "vida plena"
    ],
    "enable_serp_inspection": False,
    "serp_max_results": 10,
    "max_keywords_per_intent_cluster": 3
}
```

Yêu cầu:

- Nếu sau này có `channel.yaml`, có thể load để override config này.
- Nếu không có `channel.yaml`, tuyệt đối không fail.
- Nếu `enable_serp_inspection=False`, set `serp_opportunity=50` và `serp_note="serp_inspection_skipped"`.

---

## 3. File cần sửa

### 3.1. Sửa chính

```text
src/video_agent/orchestrator/idea_generator.py
```

### 3.2. Sửa backward compatibility

```text
src/video_agent/web/app.py
```

### 3.3. Thêm / cập nhật tests

```text
tests/test_idea_generator.py
```

Nếu project có test file riêng cho web endpoint thì thêm:

```text
tests/test_web_ideas.py
```

Nếu chưa có test infra cho web endpoint, có thể chỉ thêm helper test trong `tests/test_idea_generator.py`.

---

## 4. Output schema bắt buộc

Sau khi nâng cấp, keyword discovery V2 phải trả về dict dạng:

```json
{
  "top_opportunity_keywords": [],
  "long_tail_test_keywords": [],
  "rejected_keywords": [],
  "all_scored_keywords": [],
  "metadata": {
    "version": "keyword_scoring_v2",
    "enable_serp_inspection": false,
    "target_language": "spanish",
    "target_audience": "people_45_plus"
  }
}
```

Mỗi keyword item phải có schema tối thiểu:

```json
{
  "keyword": "comer mejor después de los 45",
  "normalized_keyword": "comer mejor despues de los 45",
  "intent_cluster": "nutrition_after_45",

  "keyword_score": 78,
  "score": 78,
  "volume": "Medium",
  "competition": "Low",
  "related": [],

  "audience_fit": 95,
  "intent_strength": 85,
  "content_fit": 90,
  "language_fit": 100,
  "serp_opportunity": 50,

  "final_score": 84.2,
  "bucket": "top_opportunity_keywords",

  "recommended_angle": "Comer mejor sin culpa ni dietas extremas después de los 45",
  "thumbnail_hook_options": [
    "SIN CULPA",
    "COME CON CALMA",
    "TU PLATO BASE"
  ],

  "notes": [],
  "rejection_reasons": []
}
```

Yêu cầu tương thích:

- Giữ field `score` nếu code cũ đang dùng.
- Thêm field `keyword_score` bằng giá trị score gốc để rõ nghĩa.
- Nếu `score` gốc là `None`, giữ `score=None`, `keyword_score=None`.

---

## 5. Helper functions cần triển khai

Tất cả helper dưới đây nên nằm trong `idea_generator.py`, trừ khi project có module util phù hợp hơn.

---

### 5.1. `normalize_keyword(keyword: str) -> str`

Mục tiêu: chuẩn hóa keyword để dedupe và classify intent.

Yêu cầu:

- Lowercase.
- Strip đầu/cuối.
- Collapse nhiều khoảng trắng thành một khoảng trắng.
- Normalize accents: `después` -> `despues`, `energía` -> `energia`.
- Xóa punctuation không cần thiết, nhưng giữ dấu `+` nếu nằm trong `45+`.
- Chuẩn hóa các biến thể audience:
  - `45 plus` -> `45+`
  - `más de 45` / `mas de 45` -> `despues de los 45`
  - `mayores de 45` -> `despues de los 45`
  - `a partir de los 45` -> `despues de los 45`

Example:

```python
normalize_keyword("  Cómo comer mejor DESPUÉS   de los 45  ") == "como comer mejor despues de los 45"
normalize_keyword("Salud 45 Plus") == "salud 45+"
normalize_keyword("Alimentación para mayores de 45") == "alimentacion para despues de los 45"
```

---

### 5.2. `classify_intent_cluster(keyword: str) -> str`

Dùng normalized keyword để phân cụm intent.

Các cluster tối thiểu:

```python
INTENT_CLUSTERS = {
    "nutrition_after_45",
    "energy_after_45",
    "sleep_after_45",
    "movement_after_45",
    "emotional_wellbeing_after_45",
    "weight_management_after_45",
    "general_health_after_45",
    "unknown"
}
```

Rule gợi ý:

- `nutrition_after_45`: chứa `comer`, `alimentacion`, `nutricion`, `plato`, `comida`, `dieta`, `proteina`, `fibra`.
- `energy_after_45`: chứa `energia`, `cansancio`, `fatiga`, `bajones`, `ritmo`.
- `sleep_after_45`: chứa `sueno`, `dormir`, `descanso`, `insomnio`, `noche`.
- `movement_after_45`: chứa `movimiento`, `caminar`, `ejercicio`, `fuerza`, `musculo`, `articulaciones`.
- `emotional_wellbeing_after_45`: chứa `estres`, `ansiedad`, `calma`, `emocional`, `mente`, `motivacion`.
- `weight_management_after_45`: chứa `peso`, `adelgazar`, `bajar de peso`, `metabolismo`, `efecto rebote`, `efecto yoyo`.
- `general_health_after_45`: chứa `salud`, `bienestar`, `habitos`.

Nếu nhiều cluster match, ưu tiên theo thứ tự:

```text
nutrition > energy > sleep > movement > emotional > weight > general > unknown
```

---

### 5.3. `detect_language_fit(keyword: str, target_language: str) -> tuple[int, list[str]]`

Mục tiêu: phạt keyword không đúng ngôn ngữ target.

Target hiện tại: `spanish`.

Return:

```python
(language_fit_score, language_notes)
```

`language_fit_score` từ 0 đến 100.

Rule:

- Nếu keyword rỗng: `0`, note `empty_keyword`.
- Nếu target không phải `spanish`: trả `80` và note `language_guardrail_not_configured`.
- Nếu phát hiện Portuguese-specific tokens: trừ điểm mạnh.
- Nếu keyword có nhiều Spanish audience/topic markers: tăng/giữ điểm.

Portuguese-specific tokens/patterns:

```python
PORTUGUESE_MARKERS = [
    "depois", "voce", "você", "saude", "saúde", "bem-estar",
    "efeito sanfona", "comer bem", "sem culpa", "mais energia",
    "dieta maluca", "emagrecer", "sono", "cafe da manha", "café da manhã",
    "refeicao", "refeição", "almoco", "almoço", "jantar", "apos os 45",
    "após os 45", "aos 45"
]
```

Spanish positive markers:

```python
SPANISH_MARKERS = [
    "despues", "después", "sin culpa", "salud", "bienestar",
    "energia", "energía", "sueno", "sueño", "dormir",
    "alimentacion", "alimentación", "nutricion", "nutrición",
    "comer mejor", "habitos", "hábitos"
]
```

Scoring:

```text
start = 100
each Portuguese marker found: -30
if 2+ Portuguese markers: extra -20
clamp 0..100
```

Notes:

- Add `language_mismatch_portuguese` if Portuguese marker found.
- Add `spanish_language_ok` if no mismatch and Spanish marker found.

---

### 5.4. `score_audience_fit(keyword: str, channel_config: dict) -> int`

Mục tiêu: keyword có khớp người xem 45+ của kênh không?

Score 0..100.

Suggested deterministic scoring:

```text
start = 45

+30 if keyword contains explicit 45+ marker:
    45, 45+, despues de los 45, mayores de 45, a partir de los 45

+15 if keyword contains core health/wellness topic:
    salud, bienestar, comer, alimentacion, nutricion, sueno, energia, habitos, movimiento

+10 if keyword contains practical positioning:
    simple, practico, realista, sin dietas, sin culpa, calma

+10 if keyword contains middle-age pain point:
    cansancio, fatiga, bajones, peso, metabolismo, dormir, insomnio, estres

-20 if keyword is too broad:
    salud, bienestar, nutricion, dieta
    and has no 45+ marker and no pain point

-25 if keyword appears targeted to kids/teens/pregnancy/bodybuilding:
    niños, adolescentes, embarazo, embarazada, culturismo, volumen muscular

clamp 0..100
```

---

### 5.5. `score_intent_strength(keyword: str) -> int`

Mục tiêu: đo keyword có pain point / action intent rõ không.

Score 0..100.

Suggested scoring:

```text
start = 40

+20 if contains action verb:
    como, cómo, evitar, mejorar, organizar, recuperar, dormir, comer, bajar, cambiar

+20 if contains pain/problem:
    culpa, caos, cansancio, fatiga, bajones, insomnio, ansiedad, estres, efecto rebote, efecto yoyo

+15 if contains specific outcome:
    mas energia, más energía, dormir mejor, comer mejor, bajar de peso, sin dietas

+10 if contains audience/time context:
    despues de los 45, 45+, mayores de 45

-15 if keyword length < 3 words
-10 if too generic

clamp 0..100
```

---

### 5.6. `score_content_fit(keyword: str, channel_config: dict) -> int`

Mục tiêu: keyword có dễ biến thành video + thumbnail cho kênh này không?

Score 0..100.

Suggested scoring:

```text
start = 50

+20 if belongs to one of the main channel clusters:
    nutrition_after_45, energy_after_45, sleep_after_45, movement_after_45, emotional_wellbeing_after_45

+15 if has clear thumbnail hook potential:
    culpa, caos, energia, cansancio, plato, cuerpo, dormir, calma, edad, 45

+10 if can be framed as practical advice:
    simple, practico, organizar, habitos, rutina, consejos

-20 if medical/clinical topic requiring high expertise:
    diabetes, hipertension, tiroides, colesterol, menopausia, osteoporosis
    Note: Do not reject automatically; just reduce content_fit unless channel supports medical review.

-30 if unsafe/overclaim-prone:
    cura, curar, elimina para siempre, garantizado, milagro

clamp 0..100
```

---

### 5.7. `inspect_youtube_serp(page, keyword: str, max_results: int = 10) -> dict`

Mặc định không chạy nếu `enable_serp_inspection=False`.

Khi được bật, dùng Playwright page hiện có hoặc tạo page theo cách project đang dùng để mở:

```text
https://www.youtube.com/results?search_query={keyword}
```

Return schema:

```json
{
  "serp_opportunity": 50,
  "serp_difficulty": "unknown",
  "serp_notes": [],
  "top_results": [
    {
      "title": "...",
      "channel": "...",
      "views_text": "...",
      "age_text": "...",
      "url": "..."
    }
  ]
}
```

Scoring gợi ý:

```text
start = 50

+10 if many top results are older than 12 months
+10 if top titles do not include 45+ / after 45 angle
+10 if top results appear weakly relevant to exact keyword
+5 if several results have low/moderate views

-15 if top 3 results are exact-match and recent
-10 if top results are from obvious high-authority health channels
-10 if SERP is dominated by Shorts when creating long-form idea, or vice versa

clamp 0..100
```

Failure handling:

- Timeout, selector error, consent page, unavailable extension, parse error:
  - Return `serp_opportunity=50`
  - Add note `serp_inspection_failed:<short_error>`
  - Do not raise exception to caller.

---

### 5.8. `calculate_final_score(item: dict) -> float`

Formula:

```python
keyword_component = item["keyword_score"] if isinstance(item["keyword_score"], (int, float)) else 35

base_score = (
    0.40 * keyword_component +
    0.22 * item["audience_fit"] +
    0.15 * item["intent_strength"] +
    0.10 * item["content_fit"] +
    0.08 * item["language_fit"] +
    0.05 * item["serp_opportunity"]
)
```

Penalties:

```text
-30 if language_fit < 60
-20 if 60 <= language_fit < 80
-20 if audience_fit < 50
-15 if content_fit < 50
-10 if intent_strength < 50
-15 if keyword is too generic
-25 if unsafe overclaim marker exists:
    cura, curar, garantizado, milagro, elimina para siempre
```

Clamp final:

```text
0 <= final_score <= 100
round to 1 decimal
```

Yêu cầu:

- Add notes/rejection reasons when applying major penalties.
- Do not mutate unrelated fields unexpectedly.
- Preserve original `score`.

---

### 5.9. `assign_bucket(item: dict) -> str`

Return exactly one of:

```python
"top_opportunity_keywords"
"long_tail_test_keywords"
"rejected_keywords"
```

Rules:

#### Rejected

Assign `rejected_keywords` if any condition is true:

```text
keyword is empty
language_fit < 70
audience_fit < 45
content_fit < 40
unsafe overclaim marker exists and content_fit < 50
```

Add rejection reasons:

```text
empty_keyword
language_mismatch
audience_mismatch
content_mismatch
unsafe_health_claim_risk
```

#### Top opportunity

Assign `top_opportunity_keywords` if all conditions are true:

```text
final_score >= 70
language_fit >= 80
audience_fit >= 70
intent_strength >= 60
content_fit >= 60
keyword_score is not None
```

#### Long-tail test

Assign `long_tail_test_keywords` if all conditions are true:

```text
language_fit >= 80
audience_fit >= 70
intent_strength >= 60
content_fit >= 55
AND (
    keyword_score is None
    OR note == "not_enough_search_data"
    OR 60 <= final_score < 70
)
```

#### Fallback

If not rejected and not top opportunity:

```text
long_tail_test_keywords
```

But if `final_score < 50`, reject with reason `low_final_score`.

---

### 5.10. `dedupe_by_normalized_keyword_and_intent(items: list[dict]) -> list[dict]`

Mục tiêu: khử trùng lặp nâng cấp.

Requirements:

1. Exact normalized duplicate:
   - Same `normalized_keyword`.
   - Keep item with highest `final_score`.
   - If tie, keep higher `keyword_score`.
   - Merge notes/rejection reasons.

2. Intent-level cap:
   - Group by `intent_cluster`.
   - Keep max `channel_config["max_keywords_per_intent_cluster"]`, default 3.
   - Exception: do not cap `rejected_keywords`; cap only candidate keywords before final bucket output or cap only output buckets.

3. Do not incorrectly merge different angles:
   - Example: keep both if they have same intent but different pain angle:
     - `comer mejor después de los 45 sin dietas`
     - `evitar bajones de energía después de comer`
   - But do not keep 5 variants of the same phrase.

---

### 5.11. `generate_keyword_pack(item: dict) -> dict`

Add:

```python
recommended_angle: str
thumbnail_hook_options: list[str]
```

Suggested mapping:

```python
if intent_cluster == "nutrition_after_45":
    recommended_angle = "Comer mejor después de los 45 sin culpa ni dietas extremas"
    thumbnail_hook_options = ["SIN CULPA", "COME CON CALMA", "TU PLATO BASE"]

elif intent_cluster == "energy_after_45":
    recommended_angle = "Evitar bajones de energía después de los 45 con comidas simples"
    thumbnail_hook_options = ["MÁS ENERGÍA", "NO ES TU EDAD", "RECUPERA TU RITMO"]

elif intent_cluster == "sleep_after_45":
    recommended_angle = "Dormir mejor después de los 45 con una rutina realista"
    thumbnail_hook_options = ["DUERME MEJOR", "DESCANSA HOY", "NOCHE EN CALMA"]

elif intent_cluster == "movement_after_45":
    recommended_angle = "Moverte más después de los 45 sin rutinas imposibles"
    thumbnail_hook_options = ["MUÉVETE SIN DOLOR", "EMPIEZA SUAVE", "TU CUERPO PIDE MOVIMIENTO"]

elif intent_cluster == "emotional_wellbeing_after_45":
    recommended_angle = "Cuidar tu bienestar emocional después de los 45 con hábitos simples"
    thumbnail_hook_options = ["MENTE EN CALMA", "MENOS ESTRÉS", "RESPIRA HOY"]

elif intent_cluster == "weight_management_after_45":
    recommended_angle = "Manejar el peso después de los 45 sin efecto rebote ni dietas extremas"
    thumbnail_hook_options = ["SIN REBOTE", "NO MÁS YOYÓ", "SIN DIETAS LOCAS"]

else:
    recommended_angle = "Un hábito simple para sentirte mejor después de los 45"
    thumbnail_hook_options = ["DESPUÉS DE LOS 45", "CAMBIO SIMPLE", "VIDA PLENA"]
```

---

## 6. Nâng cấp `_discover_top_keywords`

Nếu muốn giữ backward compatibility tốt, không cần đổi tên public function. Có thể:

- Giữ `_discover_top_keywords(...)`.
- Thêm param `use_v2: bool = True`.
- Hoặc tạo `_discover_top_keywords_v2(...)` và cho `_discover_top_keywords(...)` gọi V2 theo default.

Suggested flow:

```python
def _discover_top_keywords(..., channel_config=None, use_v2=True):
    config = merge_default_channel_config(channel_config)

    seed_results = score seeds with keyword scoring
    related_pool = extract related keywords
    related_results = score related keywords

    raw_items = merge seed_results + related_results

    enriched_items = []
    for item in raw_items:
        enriched = enrich_keyword_item(item, config)
        enriched_items.append(enriched)

    deduped = dedupe_by_normalized_keyword_and_intent(enriched_items)

    bucketed = {
        "top_opportunity_keywords": [],
        "long_tail_test_keywords": [],
        "rejected_keywords": [],
        "all_scored_keywords": [],
        "metadata": {...}
    }

    for item in deduped:
        bucket = assign_bucket(item)
        item["bucket"] = bucket
        bucketed[bucket].append(item)
        bucketed["all_scored_keywords"].append(item)

    sort each bucket:
        top_opportunity_keywords by final_score desc
        long_tail_test_keywords by final_score desc
        rejected_keywords by final_score desc

    apply output limits:
        top_opportunity_keywords: top_n
        long_tail_test_keywords: max(3, top_n // 2)
        rejected_keywords: can keep all or limit 20 for debug

    return bucketed
```

Important:

- Keep old behavior available if `use_v2=False`.
- If existing callers expect a list, do not silently return dict unless caller is updated.
- `generate_ideas(...)` should use the new dict properly.

---

## 7. Update `generate_ideas(...)`

Current expected behavior: send Top N keywords to ChatGPT.

New behavior:

1. Call keyword discovery V2.
2. Use only `top_opportunity_keywords` first.
3. If not enough top opportunity keywords, fill with `long_tail_test_keywords`.
4. Send selected list to ChatGPT.
5. Prompt must tell ChatGPT:
   - Create exactly X ideas.
   - Each idea targets exactly one keyword.
   - `title_seed` must naturally contain or reflect the target keyword.
   - Keep language Spanish.
   - Avoid Portuguese.
   - Avoid medical overclaims.

Suggested selected keywords:

```python
selected_keywords = (
    keyword_result["top_opportunity_keywords"] +
    keyword_result["long_tail_test_keywords"]
)[:top_n]
```

If no selected keywords:

- Fallback to legacy top keywords if available.
- Else raise a controlled error with useful message.

---

## 8. Backward compatibility in `src/video_agent/web/app.py`

At endpoint:

```text
/channels/{channel_id}/ideas/generate
```

Add helper:

```python
def flatten_keyword_result_for_ui(top_keywords):
    if isinstance(top_keywords, list):
        return top_keywords

    if isinstance(top_keywords, dict):
        return (
            top_keywords.get("top_opportunity_keywords", []) +
            top_keywords.get("long_tail_test_keywords", [])
        )

    return []
```

Use it anywhere UI mapping expects list.

If UI needs score mapping:

```python
keywords_for_ui = flatten_keyword_result_for_ui(top_keywords)

score_by_keyword = {
    item.get("keyword"): item.get("final_score", item.get("score"))
    for item in keywords_for_ui
    if isinstance(item, dict) and item.get("keyword")
}
```

Requirements:

- Legacy list format must still work.
- New dict format must not crash.
- If item is string, handle it.
- If item is dict, prefer `final_score`, fallback `score`.

---

## 9. Tests bắt buộc

Add/update `tests/test_idea_generator.py`.

### 9.1. `test_normalize_keyword`

Cases:

```python
assert normalize_keyword("  Cómo comer mejor DESPUÉS   de los 45  ") == "como comer mejor despues de los 45"
assert normalize_keyword("Alimentación para mayores de 45") == "alimentacion para despues de los 45"
assert normalize_keyword("Salud 45 Plus") == "salud 45+"
```

### 9.2. `test_language_guardrail_portuguese`

```python
score, notes = detect_language_fit("como comer bem depois dos 45", "spanish")
assert score < 70
assert "language_mismatch_portuguese" in notes
```

### 9.3. `test_language_guardrail_spanish_ok`

```python
score, notes = detect_language_fit("como comer mejor despues de los 45 sin culpa", "spanish")
assert score >= 80
```

### 9.4. `test_audience_fit_45_plus_high`

```python
score = score_audience_fit("como comer mejor despues de los 45 sin dietas", DEFAULT_CHANNEL_KEYWORD_CONFIG)
assert score >= 80
```

### 9.5. `test_audience_fit_generic_lower`

```python
score = score_audience_fit("nutricion", DEFAULT_CHANNEL_KEYWORD_CONFIG)
assert score < 70
```

### 9.6. `test_intent_cluster_nutrition`

```python
assert classify_intent_cluster("como comer mejor despues de los 45") == "nutrition_after_45"
```

### 9.7. `test_composite_final_score_with_penalties`

Create item with:

```python
{
    "keyword": "como comer bem depois dos 45",
    "keyword_score": 85,
    "audience_fit": 80,
    "intent_strength": 80,
    "content_fit": 80,
    "language_fit": 40,
    "serp_opportunity": 50,
    "notes": [],
    "rejection_reasons": []
}
```

Expected:

```python
final_score < 70
```

Because Portuguese mismatch penalty must beat high keyword score.

### 9.8. `test_bucket_assignment_top_opportunity`

Item:

```python
final_score=82
language_fit=100
audience_fit=90
intent_strength=80
content_fit=80
keyword_score=75
```

Expected:

```python
"top_opportunity_keywords"
```

### 9.9. `test_bucket_assignment_long_tail_not_enough_data`

Item:

```python
keyword_score=None
score=None
notes=["not_enough_search_data"]
final_score=65
language_fit=100
audience_fit=85
intent_strength=80
content_fit=75
```

Expected:

```python
"long_tail_test_keywords"
```

### 9.10. `test_bucket_assignment_rejected_language`

Item:

```python
language_fit=50
```

Expected:

```python
"rejected_keywords"
```

### 9.11. `test_dedupe_keeps_highest_final_score`

Input: two items with same `normalized_keyword`.

Expected:

- Output length 1.
- Kept item has higher `final_score`.

### 9.12. `test_flatten_keyword_result_for_ui_legacy_list`

Only if helper is accessible/testable.

### 9.13. `test_flatten_keyword_result_for_ui_new_dict`

Only if helper is accessible/testable.

---

## 10. Acceptance criteria

Implementation is done only when:

1. `pytest tests/test_idea_generator.py` passes.
2. Existing idea generation flow still works.
3. Portuguese keyword with high keyword score does not enter `top_opportunity_keywords` for Spanish channel.
4. Spanish 45+ keyword with clear intent can enter `top_opportunity_keywords`.
5. `not_enough_search_data` but high audience/intent fit can enter `long_tail_test_keywords`.
6. `generate_ideas(...)` sends only Spanish-compatible keywords to ChatGPT.
7. Web endpoint does not crash whether `top_keywords` is legacy `list` or new `dict`.
8. SERP inspection failure never fails the whole keyword discovery flow.
9. Output includes enough debug fields to understand why a keyword was selected/rejected.

---

## 11. Suggested implementation order

1. Add constants/config.
2. Implement pure helper functions:
   - `normalize_keyword`
   - `classify_intent_cluster`
   - `detect_language_fit`
   - `score_audience_fit`
   - `score_intent_strength`
   - `score_content_fit`
   - `calculate_final_score`
   - `assign_bucket`
   - `generate_keyword_pack`
3. Add unit tests for helpers.
4. Add `enrich_keyword_item(item, config)` wrapper.
5. Add dedupe function.
6. Upgrade `_discover_top_keywords` or add `_discover_top_keywords_v2`.
7. Update `generate_ideas(...)`.
8. Add `flatten_keyword_result_for_ui(...)` in `app.py`.
9. Add backward compatibility tests.
10. Run tests and fix regressions.

---

## 12. Example final output

```json
{
  "top_opportunity_keywords": [
    {
      "keyword": "como comer mejor después de los 45 sin dietas",
      "normalized_keyword": "como comer mejor despues de los 45 sin dietas",
      "intent_cluster": "nutrition_after_45",
      "keyword_score": 76,
      "score": 76,
      "volume": "Medium",
      "competition": "Low",
      "audience_fit": 95,
      "intent_strength": 90,
      "content_fit": 90,
      "language_fit": 100,
      "serp_opportunity": 50,
      "final_score": 82.4,
      "bucket": "top_opportunity_keywords",
      "recommended_angle": "Comer mejor después de los 45 sin culpa ni dietas extremas",
      "thumbnail_hook_options": ["SIN CULPA", "COME CON CALMA", "TU PLATO BASE"],
      "notes": ["spanish_language_ok", "serp_inspection_skipped"],
      "rejection_reasons": []
    }
  ],
  "long_tail_test_keywords": [
    {
      "keyword": "bajones de energía después de comer 45",
      "normalized_keyword": "bajones de energia despues de comer 45",
      "intent_cluster": "energy_after_45",
      "keyword_score": null,
      "score": null,
      "volume": "Low",
      "competition": "Very Low",
      "audience_fit": 85,
      "intent_strength": 85,
      "content_fit": 85,
      "language_fit": 100,
      "serp_opportunity": 50,
      "final_score": 65.7,
      "bucket": "long_tail_test_keywords",
      "recommended_angle": "Evitar bajones de energía después de los 45 con comidas simples",
      "thumbnail_hook_options": ["MÁS ENERGÍA", "NO ES TU EDAD", "RECUPERA TU RITMO"],
      "notes": ["not_enough_search_data", "serp_inspection_skipped"],
      "rejection_reasons": []
    }
  ],
  "rejected_keywords": [
    {
      "keyword": "como comer bem depois dos 45",
      "normalized_keyword": "como comer bem depois dos 45",
      "intent_cluster": "unknown",
      "keyword_score": 88,
      "score": 88,
      "audience_fit": 70,
      "intent_strength": 60,
      "content_fit": 60,
      "language_fit": 40,
      "serp_opportunity": 50,
      "final_score": 48.2,
      "bucket": "rejected_keywords",
      "recommended_angle": "Un hábito simple para sentirte mejor después de los 45",
      "thumbnail_hook_options": ["DESPUÉS DE LOS 45", "CAMBIO SIMPLE", "VIDA PLENA"],
      "notes": ["language_mismatch_portuguese"],
      "rejection_reasons": ["language_mismatch"]
    }
  ],
  "all_scored_keywords": [],
  "metadata": {
    "version": "keyword_scoring_v2",
    "enable_serp_inspection": false,
    "target_language": "spanish",
    "target_audience": "people_45_plus"
  }
}
```

---

## 13. Không làm trong scope này

Không cần triển khai:

- Paid keyword scoring API.
- Full semantic embeddings.
- LLM-based keyword scoring.
- UI redesign.
- Database migration, trừ khi project hiện tại bắt buộc.
- Medical fact-checking engine.

---

## 14. Ghi chú về nội dung sức khỏe

Vì kênh thuộc health/wellness, keyword/title generation phải tránh claim quá đà:

Avoid:

```text
cura
curar
garantizado
milagro
elimina para siempre
```

Prefer:

```text
puede ayudarte
hábitos simples
consejos prácticos
rutina realista
consulta con un profesional si tienes una condición médica
```
