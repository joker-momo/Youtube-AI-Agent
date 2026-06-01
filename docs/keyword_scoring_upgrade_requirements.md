# Yêu cầu nâng cấp logic chấm điểm từ khóa keyword scoring cho YouTube

## Mục tiêu

Nâng cấp pipeline tìm và chấm điểm từ khóa hiện tại để không chỉ phụ thuộc vào `keyword_score`, mà còn xét thêm mức độ phù hợp với kênh, ý định tìm kiếm, độ khó SERP thực tế và mức độ phù hợp ngôn ngữ/thị trường.

Kênh mục tiêu hiện tại:

- Tên kênh: `Vida Plena 45+: Salud y Bienestar`
- Ngôn ngữ chính: `Spanish`
- Thị trường: người nói tiếng Tây Ban Nha
- Audience: người 45+
- Niche: salud, bienestar, nutrición práctica, hábitos saludables, sueño, energía, movimiento suave, bienestar emocional
- Brand positioning: sống khỏe thực tế sau 45, không cực đoan, không áp lực, không dietas extremas

Pipeline hiện tại đã có:

1. Nhận danh sách seed keywords.
2. Dùng Playwright mở YouTube Search với extension keyword scoring.
3. Lấy các chỉ số từ keyword scoring: `score`, `volume`, `competition`, `related`.
4. Chấm điểm seed keywords.
5. Lấy related keywords từ keyword scoring.
6. Chấm điểm related keywords.
7. Gộp seed + related.
8. Deduplicate.
9. Sort theo `score DESC`.
10. Lấy `top_n` keyword để đưa cho ChatGPT tạo ý tưởng video.

Yêu cầu nâng cấp: thay vì chỉ sort theo `score DESC`, hãy tạo hệ thống `final_score` tổng hợp và xuất thêm các nhóm keyword có thể hành động được.

---

## Nguyên tắc triển khai

- Không rewrite toàn bộ project nếu không cần thiết.
- Trước tiên hãy tìm hàm hiện tại có vai trò giống `_discover_top_keywords` hoặc keyword discovery pipeline.
- Giữ nguyên phần Playwright + keyword scoring scraping nếu nó đang hoạt động.
- Chỉ thêm các lớp xử lý sau khi đã thu được dữ liệu keyword từ keyword scoring.
- Code phải dễ test, tách thành các hàm nhỏ.
- Không hard-code quá sâu vào một video cụ thể; nhưng có thể cấu hình mặc định cho kênh `Vida Plena 45+`.
- Không làm vỡ output cũ nếu nơi khác trong app đang dùng. Nếu cần, giữ field cũ và thêm field mới.

---

## Data model đề xuất

Mỗi keyword sau khi xử lý nên có schema gần như sau:

```json
{
  "keyword": "comer mejor después de los 45",
  "normalized_keyword": "comer mejor despues de los 45",
  "intent_cluster": "nutrition_after_45",
  "source": "seed|related",
  "source_seed": "alimentación después de los 45",
  "keyword_score": 78,
  "volume": "Medium",
  "competition": "Low",
  "related": [],
  "note": null,
  "audience_fit": 95,
  "intent_strength": 88,
  "content_fit": 90,
  "language_fit": 100,
  "serp_difficulty": 45,
  "serp_opportunity": 70,
  "final_score": 84.2,
  "bucket": "top_opportunity|long_tail_test|rejected",
  "rejection_reason": null,
  "recommended_angle": "sin dietas ni culpa",
  "risk_flags": []
}
```

Không bắt buộc phải đúng 100% tên field nếu repo đã có conventions khác, nhưng các ý nghĩa trên cần có.

---

## 1. Normalize keyword

Thêm hàm:

```python
def normalize_keyword(keyword: str) -> str:
    ...
```

Yêu cầu:

- Lowercase.
- Trim leading/trailing spaces.
- Collapse multiple spaces thành một space.
- Loại bỏ punctuation không cần thiết.
- Normalize accents ở bản `normalized_keyword` để dedupe tốt hơn:
  - `después` -> `despues`
  - `energía` -> `energia`
  - `nutrición` -> `nutricion`
- Không thay đổi bản keyword gốc dùng để hiển thị.

Ví dụ:

```text
"Cómo comer mejor después de los  45" -> "como comer mejor despues de los 45"
"DESPUÉS de los 45" -> "despues de los 45"
"45+" -> giữ được ý nghĩa 45+
```

---

## 2. Cluster keyword theo search intent

Hiện tại dedupe theo chuỗi là chưa đủ. Cần thêm intent clustering để nhóm các keyword khác chữ nhưng cùng ý định.

Thêm hàm:

```python
def classify_intent_cluster(keyword: str) -> str:
    ...
```

Các cluster mặc định cho kênh này:

```python
INTENT_CLUSTERS = {
    "nutrition_after_45": [
        "comer", "comida", "alimentacion", "alimentación", "nutricion", "nutrición",
        "plato", "dietas", "sin dieta", "comer mejor"
    ],
    "energy_after_45": [
        "energia", "energía", "cansancio", "fatiga", "bajones", "ritmo"
    ],
    "sleep_after_45": [
        "sueño", "sueno", "dormir", "descanso", "insomnio"
    ],
    "movement_after_45": [
        "ejercicio", "movimiento", "caminar", "fuerza", "articulaciones"
    ],
    "emotional_wellbeing_after_45": [
        "bienestar emocional", "estres", "estrés", "ansiedad", "calma", "culpa"
    ],
    "general_health_after_45": [
        "salud", "habitos", "hábitos", "vida saludable", "despues de los 45", "después de los 45"
    ]
}
```

Quy tắc:

- Một keyword có thể match nhiều cluster, nhưng output chính chỉ cần 1 cluster ưu tiên.
- Ưu tiên cluster cụ thể hơn cluster chung.
- Nếu không match gì, dùng `unknown`.

Ví dụ:

```text
"cómo comer mejor después de los 45" -> nutrition_after_45
"bajones de energía después de comer" -> energy_after_45 hoặc nutrition_after_45, ưu tiên energy_after_45
"rutina nocturna después de los 45" -> sleep_after_45
```

---

## 3. Language guardrail: chỉ giữ Spanish-native keywords

Kênh này đang làm tiếng Tây Ban Nha. Tránh trộn Portuguese/Brazilian keywords.

Thêm hàm:

```python
def detect_language_fit(keyword: str, target_language: str = "Spanish") -> tuple[int, list[str]]:
    ...
```

Output:

- `language_fit`: 0-100
- `risk_flags`: list string

Yêu cầu:

Reject hoặc phạt nặng keyword có dấu hiệu Portuguese rõ ràng, ví dụ:

```python
PORTUGUESE_MARKERS = [
    "depois dos", "como comer bem", "efeito sanfona", "saúde", "bem-estar",
    "sem culpa", "sem dietas", "hábitos saudáveis", "você", "energia depois dos"
]
```

Spanish markers tích cực:

```python
SPANISH_MARKERS = [
    "después de los", "despues de los", "cómo", "como", "salud", "bienestar",
    "sin culpa", "sin dietas", "hábitos saludables", "habitos saludables",
    "comer mejor", "energía", "energia"
]
```

Quy tắc:

- Nếu có Portuguese marker rõ ràng: `language_fit <= 30`, thêm risk flag `language_mismatch_portuguese`.
- Nếu có Spanish marker: tăng điểm.
- Nếu không rõ ngôn ngữ nhưng keyword ngắn, cho điểm trung bình 60-75, không reject ngay.

---

## 4. Audience fit cho người 45+

Thêm hàm:

```python
def score_audience_fit(keyword: str, channel_config: dict) -> int:
    ...
```

Mục tiêu: keyword càng rõ dành cho người 45+ càng tốt.

Tín hiệu cộng điểm:

- Có `después de los 45`, `despues de los 45`, `45+`, `mayores de 45`, `a partir de los 45`: rất cao.
- Có chủ đề hợp người 45+: energía, sueño, nutrición práctica, cansancio, hábitos, comer sin culpa, movimiento suave.
- Có tone không cực đoan: `sin dietas`, `simple`, `práctico`, `calma`, `sin culpa`.

Tín hiệu trừ điểm:

- Quá general: `bajar de peso rápido`, `dieta extrema`, `six pack`, `gym intenso`.
- Nhắm sai demographic: teenagers, jóvenes, bodybuilding hardcore.

Gợi ý scoring:

```text
90-100: keyword nêu rõ 45+ và đúng pain point
70-89: đúng pain point nhưng không nêu rõ 45+
50-69: liên quan sức khỏe chung, có thể dùng nhưng chưa rõ audience
0-49: lệch audience hoặc quá general
```

---

## 5. Intent strength

Thêm hàm:

```python
def score_intent_strength(keyword: str) -> int:
    ...
```

Mục tiêu: đánh giá keyword có nỗi đau/hành động rõ không.

Tín hiệu intent mạnh:

- Có vấn đề cụ thể: `bajones de energía`, `cansancio`, `sin culpa`, `efecto rebote`, `dormir mejor`, `insomnio`, `organizar comidas`.
- Có kết quả mong muốn: `más energía`, `comer mejor`, `dormir mejor`, `recuperar ritmo`.
- Có đối tượng hoặc context: `después de los 45`.

Tín hiệu intent yếu:

- Keyword quá rộng: `salud`, `bienestar`, `nutrición`, `hábitos`.
- Keyword dạng định nghĩa chung, khó làm thumbnail/title cảm xúc.

Gợi ý scoring:

```text
85-100: vấn đề + kết quả + audience rõ
70-84: vấn đề hoặc kết quả rõ
50-69: chủ đề đúng nhưng còn chung
0-49: quá mơ hồ
```

---

## 6. Content fit

Thêm hàm:

```python
def score_content_fit(keyword: str, channel_config: dict) -> int:
    ...
```

Mục tiêu: keyword có phù hợp để kênh tạo video không, và có thể tạo title/thumbnail tốt không.

Cộng điểm nếu:

- Có thể tạo video giáo dục ngắn/dài rõ ràng.
- Có thể tạo thumbnail ít chữ, cảm xúc mạnh.
- Phù hợp brand: practical, calm, non-extreme, 45+.
- Không yêu cầu claim y tế quá nặng.

Trừ điểm nếu:

- Keyword cần chuyên môn y khoa sâu hoặc rủi ro cao.
- Keyword dễ tạo hứa hẹn quá mức: chữa bệnh, giảm cân nhanh, điều trị bệnh cụ thể.
- Keyword quá cạnh tranh hoặc quá generic.

Risk flags đề xuất:

```python
HEALTH_RISK_TERMS = [
    "curar", "cura", "tratamiento", "diabetes", "hipertensión", "cáncer",
    "medicamento", "hormonas", "menopausia severa"
]
```

Nếu có health risk terms:

- Không nhất thiết reject.
- Thêm flag `medical_claim_risk`.
- Yêu cầu downstream content phải có disclaimer: `contenido informativo, no sustituye consejo médico profesional`.

---

## 7. Không loại hẳn keyword `not_enough_search_data`

Logic hiện tại ghi `score = None` và xem như tín hiệu thấp. Hãy thay đổi thành:

- Nếu `score is None` và `note == "not_enough_search_data"`:
  - Không đưa vào `top_opportunity` trừ khi các score khác rất cao.
  - Nhưng nếu `audience_fit >= 80` và `intent_strength >= 75`, đưa vào bucket `long_tail_test`.

Ví dụ keyword đáng giữ làm long-tail test:

```text
comer sin culpa después de los 45
organizar comidas después de los 45
bajones de energía después de comer 45
plato simple después de los 45
```

---

## 8. SERP inspection: kiểm tra độ khó thực tế trên YouTube Search

Sau khi có candidate keywords, cần kiểm tra SERP thực tế cho mỗi keyword trước khi chọn output cuối.

Thêm hàm async nếu project dùng Playwright:

```python
async def inspect_youtube_serp(page, keyword: str, max_results: int = 10) -> dict:
    ...
```

Dữ liệu cần lấy từ top 10 kết quả YouTube Search nếu có thể:

```json
{
  "keyword": "comer mejor después de los 45",
  "top_results": [
    {
      "title": "...",
      "channel_name": "...",
      "views": 12345,
      "published_at_text": "hace 2 años",
      "duration": "11:42",
      "thumbnail_url": "..."
    }
  ],
  "serp_difficulty": 45,
  "serp_opportunity": 70,
  "serp_notes": ["old_results", "weak_titles"]
}
```

Không bắt buộc lấy subscriber count nếu khó scrape ổn định.

### SERP scoring heuristic

`serp_difficulty`: 0-100, càng cao càng khó.

Tăng difficulty nếu:

- Top results đều có view rất cao.
- Top results từ kênh lớn/authority cao.
- Nhiều video mới và tối ưu tốt.
- Title chứa exact keyword nhiều.

Tăng opportunity nếu:

- Nhiều video cũ hơn 1-2 năm.
- Title chung chung, không nêu 45+.
- Thumbnail yếu hoặc không rõ target audience.
- Có ít video nói trực tiếp đúng angle `después de los 45`.
- Có video từ kênh nhỏ vẫn rank được.

Nếu scrape SERP thất bại:

- Không crash pipeline.
- Set `serp_difficulty = 50`, `serp_opportunity = 50`, thêm flag `serp_inspection_failed`.

---

## 9. Composite final_score

Thay logic sort `score DESC` bằng `final_score` tổng hợp.

Thêm hàm:

```python
def calculate_final_score(item: dict) -> float:
    ...
```

Công thức đề xuất:

```python
final_score = (
    keyword_component * 0.40 +
    audience_fit * 0.22 +
    intent_strength * 0.15 +
    content_fit * 0.10 +
    language_fit * 0.08 +
    serp_opportunity * 0.05
)
```

Trong đó:

```python
keyword_component = item["keyword_score"] if item["keyword_score"] is not None else 45
```

Penalty:

```python
if "language_mismatch_portuguese" in risk_flags:
    final_score -= 35

if "medical_claim_risk" in risk_flags:
    final_score -= 5

if serp_difficulty >= 80:
    final_score -= 10

if audience_fit < 50:
    final_score -= 15
```

Clamp:

```python
final_score = max(0, min(100, final_score))
```

---

## 10. Bucket output

Sau khi tính `final_score`, mỗi keyword phải được đưa vào một bucket.

### `top_opportunity`

Điều kiện đề xuất:

```python
final_score >= 70
language_fit >= 70
audience_fit >= 65
serp_difficulty <= 75
```

### `long_tail_test`

Điều kiện đề xuất:

```python
(
    item["keyword_score"] is None
    and item.get("note") == "not_enough_search_data"
    and audience_fit >= 80
    and intent_strength >= 75
    and language_fit >= 70
)
or
(
    55 <= final_score < 70
    and audience_fit >= 80
    and intent_strength >= 75
)
```

### `rejected`

Reject nếu:

```python
language_fit < 50
or audience_fit < 45
or content_fit < 45
or keyword is empty
or keyword is duplicate with stronger same-intent keyword
```

Mỗi rejected keyword phải có `rejection_reason`, ví dụ:

```text
language_mismatch_portuguese
audience_mismatch
too_generic
weaker_duplicate_same_intent
medical_claim_risk_too_high
```

---

## 11. Deduplicate nâng cấp

Hiện tại deduplicate giữ keyword có `score` cao nhất. Hãy đổi thành:

1. Dedupe exact theo `normalized_keyword`.
2. Trong cùng `intent_cluster`, nếu nhiều keyword rất giống nhau, giữ keyword có `final_score` cao hơn.
3. Không xóa hết biến thể; giữ tối đa 2-3 keyword/cluster nếu chúng có angle khác nhau rõ ràng.

Ví dụ cùng cluster `nutrition_after_45`:

```text
cómo comer mejor después de los 45
alimentación saludable después de los 45
comer sin culpa después de los 45
```

Có thể giữ:

- `cómo comer mejor después de los 45` vì broad + SEO tốt.
- `comer sin culpa después de los 45` vì pain point cảm xúc mạnh.

Nhưng reject biến thể quá giống nếu chỉ đổi chữ nhẹ.

---

## 12. Output cuối cho ChatGPT/video idea generator

Thay vì chỉ gửi Top N theo keyword scoring, output cuối nên gồm 3 nhóm:

```json
{
  "top_opportunity_keywords": [],
  "long_tail_test_keywords": [],
  "rejected_keywords": [],
  "summary": {
    "total_scanned": 0,
    "total_top_opportunity": 0,
    "total_long_tail": 0,
    "total_rejected": 0,
    "target_language": "Spanish",
    "target_audience": "45+"
  }
}
```

Mỗi item trong `top_opportunity_keywords` và `long_tail_test_keywords` cần có:

```json
{
  "keyword": "...",
  "final_score": 84.2,
  "keyword_score": 78,
  "volume": "Medium",
  "competition": "Low",
  "intent_cluster": "nutrition_after_45",
  "audience_fit": 95,
  "intent_strength": 88,
  "serp_difficulty": 45,
  "recommended_angle": "sin dietas ni culpa",
  "title_guidance": "Title should naturally include the keyword or a close Spanish-native phrasing.",
  "thumbnail_hook_options": ["SIN CULPA", "COME CON CALMA", "TU PLATO TE HABLA"]
}
```

---

## 13. Recommended angle và thumbnail hook

Thêm helper:

```python
def generate_keyword_pack(item: dict) -> dict:
    ...
```

Gợi ý angle theo cluster:

### nutrition_after_45

Thumbnail hooks:

```text
SIN CULPA
COME CON CALMA
TU PLATO TE HABLA
SIN DIETAS LOCAS
CAMBIA TU PLATO
```

Angles:

```text
sin dietas ni culpa
plato simple para más calma
organizar comidas sin caos
```

### energy_after_45

Thumbnail hooks:

```text
MÁS ENERGÍA
RECUPERA TU RITMO
NO ES TU EDAD
EVITA BAJONES
```

Angles:

```text
bajones de energía después de comer
comidas simples para energía estable
hábitos prácticos después de los 45
```

### sleep_after_45

Thumbnail hooks:

```text
DUERME MEJOR
DESCANSA HOY
NOCHE EN CALMA
```

### movement_after_45

Thumbnail hooks:

```text
MUÉVETE SIN DOLOR
CAMINA MEJOR
FUERZA DESPUÉS DE 45
```

### emotional_wellbeing_after_45

Thumbnail hooks:

```text
SIN CULPA
VIVE CON CALMA
TU MENTE DESCANSA
```

---

## 14. Tests cần thêm

Thêm unit tests cho các hàm mới.

### Test normalize

Input:

```text
"Cómo comer mejor después de los  45"
```

Expected:

```text
"como comer mejor despues de los 45"
```

### Test language guardrail

Input:

```text
"Adeus ao efeito sanfona depois dos 45"
```

Expected:

```text
language_fit <= 30
risk_flags contains "language_mismatch_portuguese"
bucket should be rejected unless explicitly configured for Portuguese
```

Input:

```text
"Cómo comer mejor después de los 45 sin dietas"
```

Expected:

```text
language_fit >= 85
no language_mismatch flag
```

### Test audience fit

Input:

```text
"comer mejor después de los 45"
```

Expected:

```text
audience_fit >= 90
```

Input:

```text
"bajar de peso rápido para jóvenes"
```

Expected:

```text
audience_fit <= 50
```

### Test not enough search data long-tail

Input item:

```json
{
  "keyword": "comer sin culpa después de los 45",
  "keyword_score": null,
  "note": "not_enough_search_data",
  "audience_fit": 90,
  "intent_strength": 85,
  "language_fit": 95
}
```

Expected:

```text
bucket == "long_tail_test"
```

### Test final_score penalty

Input keyword Portuguese marker:

```text
"como comer bem depois dos 45"
```

Expected:

```text
final_score receives strong language penalty
bucket == "rejected"
```

---

## 15. Acceptance criteria

Implementation được coi là đạt khi:

1. Pipeline vẫn lấy được seed + related keywords từ keyword scoring như trước.
2. Không còn chọn Top N chỉ bằng `score DESC`.
3. Mỗi keyword output có `final_score`, `audience_fit`, `intent_strength`, `content_fit`, `language_fit`, `intent_cluster`, `bucket`.
4. Portuguese keywords bị phạt/reject khi target language là Spanish.
5. Keyword `not_enough_search_data` nhưng rất đúng audience/intent được đưa vào `long_tail_test`, không bị mất hoàn toàn.
6. Output cuối có 3 nhóm: `top_opportunity_keywords`, `long_tail_test_keywords`, `rejected_keywords`.
7. Có unit tests cho normalize, language guardrail, audience fit, final score và bucket assignment.
8. Nếu SERP inspection fail, pipeline không crash.
9. Tất cả thay đổi có log/debug đủ để biết vì sao một keyword được chọn hoặc bị reject.

---

## 16. Pseudocode tổng thể

```python
async def discover_top_keywords_v2(seeds, channel_config, top_n=8, max_related=15):
    seed_results = await score_keywords_with_keyword(seeds)

    related_pool = extract_related_keywords(seed_results)
    related_pool = remove_seed_duplicates(related_pool, seeds)
    related_pool = related_pool[:max_related]

    related_results = await score_keywords_with_keyword(related_pool)

    all_items = merge_results(seed_results, related_results)

    enriched_items = []
    for item in all_items:
        item["normalized_keyword"] = normalize_keyword(item["keyword"])
        item["intent_cluster"] = classify_intent_cluster(item["keyword"])

        language_fit, language_flags = detect_language_fit(
            item["keyword"],
            target_language=channel_config.get("language", "Spanish")
        )
        item["language_fit"] = language_fit
        item.setdefault("risk_flags", []).extend(language_flags)

        item["audience_fit"] = score_audience_fit(item["keyword"], channel_config)
        item["intent_strength"] = score_intent_strength(item["keyword"])
        item["content_fit"] = score_content_fit(item["keyword"], channel_config)

        serp_data = await safe_inspect_youtube_serp(item["keyword"])
        item.update(serp_data)

        item["final_score"] = calculate_final_score(item)
        item["bucket"] = assign_bucket(item)
        item.update(generate_keyword_pack(item))

        enriched_items.append(item)

    deduped_items = dedupe_by_normalized_keyword_and_intent(enriched_items)

    top_opportunity = sorted(
        [x for x in deduped_items if x["bucket"] == "top_opportunity"],
        key=lambda x: x["final_score"],
        reverse=True
    )[:top_n]

    long_tail = sorted(
        [x for x in deduped_items if x["bucket"] == "long_tail_test"],
        key=lambda x: x["final_score"],
        reverse=True
    )[:top_n]

    rejected = [x for x in deduped_items if x["bucket"] == "rejected"]

    return {
        "top_opportunity_keywords": top_opportunity,
        "long_tail_test_keywords": long_tail,
        "rejected_keywords": rejected,
        "summary": {
            "total_scanned": len(all_items),
            "total_top_opportunity": len(top_opportunity),
            "total_long_tail": len(long_tail),
            "total_rejected": len(rejected),
            "target_language": channel_config.get("language", "Spanish"),
            "target_audience": channel_config.get("target_audience", "45+")
        }
    }
```

---

## 17. Cấu hình mặc định đề xuất

```python
DEFAULT_CHANNEL_CONFIG = {
    "channel_name": "Vida Plena 45+: Salud y Bienestar",
    "language": "Spanish",
    "market": "Spanish-speaking",
    "target_audience": "45+",
    "niche": [
        "salud",
        "bienestar",
        "nutrición práctica",
        "hábitos saludables",
        "sueño",
        "energía",
        "movimiento suave",
        "bienestar emocional"
    ],
    "positioning": "salud práctica después de los 45 sin dietas extremas ni rutinas imposibles",
    "avoid": [
        "Portuguese keywords unless explicitly configured",
        "medical cure claims",
        "rapid weight loss promises",
        "extreme diets",
        "hardcore bodybuilding angles"
    ]
}
```

---

## 18. Ghi chú quan trọng cho downstream ChatGPT idea generation

Khi gửi keyword cho ChatGPT để tạo ý tưởng video:

- Mỗi idea phải target đúng 1 keyword.
- Title phải chứa keyword hoặc biến thể Spanish-native tự nhiên.
- Không trộn Spanish và Portuguese.
- Không dùng claims y tế quá mạnh.
- Với keyword có `medical_claim_risk`, script phải thêm disclaimer.
- Thumbnail text nên ngắn, 2-5 từ, dễ đọc trên mobile.
- Với kênh 45+, tránh gọi khán giả là `senior` nếu không cần thiết; dùng `después de los 45`, `45+`, `vida plena`, `bienestar`.

