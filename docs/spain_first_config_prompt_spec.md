# Codex Spec — Spain-first config và prompt update cho kênh Vida Plena 45+

## Mục tiêu

Chuyển kênh `Vida Plena 45+` từ cấu hình **Latin America-first** sang **Spain-first** vì kênh được đặt ở Spain và muốn tối ưu nội dung cho người xem Tây Ban Nha.

Hiện trạng cần sửa:

- `configs/vida-plena-45/channel.yaml` đang dùng:
  - `audience.language: es-419`
  - `primary_markets: ["MX", "CO", "ES"]`
  - `publish_schedule.timezone: America/Mexico_City`
  - `publish_schedule.time_local: 19:00`
  - `seo.language: es-419`
- SEO prompt trong `src/video_agent/operator.py` đang hard-code:
  - `language: must be es-419`
  - tags là `Spanish/LatAm wellness search terms`
  - description section 4 yêu cầu `other social links`, gây output kiểu `Redes adicionales: no proporcionadas` khi không có social links.
- QA prompt / validator vẫn dùng wording `Latin American Spanish` trong một số chỗ.
- `idea_generator.py` đã đọc `audience.language`, nhưng prompt vẫn nên định hướng rõ Spain-first style để nội dung tự nhiên hơn.

Yêu cầu: sửa config và prompt để mọi artifact sinh ra bằng **Spanish for Spain (`es-ES`)**, ưu tiên thị trường Spain, nhưng vẫn giữ khả năng phụ cho Latin America.

---

## Files cần sửa

### Bắt buộc

```text
configs/vida-plena-45/channel.yaml
src/video_agent/operator.py
src/video_agent/operator_validators.py
src/video_agent/orchestrator/idea_generator.py
```

### Nên thêm/sửa tests

```text
tests/test_operator_prompts.py
tests/test_operator_validators.py
tests/test_idea_generator.py
```

Nếu các file test trên chưa tồn tại hoặc cấu trúc repo đang dùng test khác, thêm test vào file phù hợp hiện có.

---

## 1. Update `configs/vida-plena-45/channel.yaml`

### 1.1. Audience

Thay block `audience` hiện tại bằng Spain-first config:

```yaml
audience:
  language: "es-ES"
  age_range: [45, 75]
  primary_markets: ["ES"]
  secondary_markets: ["MX", "CO", "AR", "CL", "PE"]
```

### 1.2. Channel description

Sửa description để có dấu tiếng Tây Ban Nha chuẩn:

```yaml
channel:
  id: "vida-plena-45"
  name: "Vida Plena 45+"
  youtube_channel_id: "UCKUswqsAaLsEkcsgzTuKAmw"
  description: "Salud y bienestar práctico para personas de más de 45 años."
```

### 1.3. Publish schedule

Sửa lịch đăng sang giờ Spain:

```yaml
content_format:
  publish_schedule:
    cadence: "3 per week"
    days: ["Monday", "Wednesday", "Friday"]
    time_local: "20:00"
    timezone: "Europe/Madrid"
```

Giữ nguyên các field khác trong `content_format` như `target_duration_sec`, `duration_sec_min`, `duration_sec_max`, `scenes_count_min`, `scenes_count_max`, trừ khi test đang yêu cầu khác.

### 1.4. SEO language

Sửa:

```yaml
seo:
  language: "es-ES"
  min_tags: 5
  max_tags: 8
```

### 1.5. Positioning

Hiện config đang forbid nhiều cụm tuổi già. Giữ phần cấm này, nhưng chỉnh preferred phrase phù hợp Spain hơn:

```yaml
positioning:
  forbidden_phrases:
    - "adultos mayores"
    - "tercera edad"
    - "ancianos"
    - "personas mayores"
    - "abuelos"
    - "abuelitos"
  preferred_phrases:
    - "personas de más de 45 años"
    - "adultos 45+"
    - "mediana edad"
    - "bienestar práctico"
```

Ghi chú:

- Không dùng `senior`, `ancianos`, `tercera edad`, `abuelos`, `abuelitos` trong title/description/tags/script.
- Với Spain, phrase tự nhiên nhất là `personas de más de 45 años`, nhưng `adultos 45+` vẫn có thể dùng cho thumbnail/brand.

### 1.6. Optional config mới: `locale_style`

Thêm block mới để prompts đọc được style cụ thể:

```yaml
locale_style:
  target_locale: "Spain"
  language_code: "es-ES"
  timezone: "Europe/Madrid"
  lexical_preferences:
    prefer:
      - "móvil"
      - "ordenador"
      - "por la tarde"
      - "de madrugada"
      - "hábitos sencillos"
      - "personas de más de 45 años"
    avoid:
      - "celular"
      - "computadora"
      - "LatAm"
      - "adultos mayores"
      - "tercera edad"
      - "ancianos"
      - "abuelitos"
```

Nếu không muốn thêm block mới, vẫn phải hard-code logic từ `audience.language == es-ES`. Nhưng thêm `locale_style` sẽ tốt hơn cho prompt.

---

## 2. Update `src/video_agent/operator.py`

### 2.1. Thêm helper lấy locale guidance

Thêm helper gần các prompt helpers:

```python
def _locale_guidance(channel_config: dict[str, Any]) -> dict[str, Any]:
    audience = channel_config.get("audience", {}) or {}
    seo = channel_config.get("seo", {}) or {}
    locale_style = channel_config.get("locale_style", {}) or {}
    language = str(seo.get("language") or audience.get("language") or "es-ES")
    target_locale = str(locale_style.get("target_locale") or ("Spain" if language == "es-ES" else "Latin America"))
    lexical = locale_style.get("lexical_preferences") or {}
    prefer = lexical.get("prefer") or []
    avoid = lexical.get("avoid") or []
    return {
        "language": language,
        "target_locale": target_locale,
        "prefer": prefer,
        "avoid": avoid,
    }
```

### 2.2. Update `_chatgpt_script_prompt`

Hiện script prompt nói `natural Spanish`, nhưng chưa ép Spain style. Thêm block sau `Required JSON schema` hoặc trước `Channel config`:

```text
LOCALE AND LANGUAGE RULES (MANDATORY):
• Write in Spanish for Spain, language code es-ES.
• Use a natural Spain-first tone for adults 45+.
• Prefer words such as: móvil, ordenador, por la tarde, de madrugada, hábitos sencillos, personas de más de 45 años.
• Avoid Latin America-specific wording when a Spain-native alternative is more natural, such as celular or computadora.
• Never use forbidden age-positioning terms from channel_config.positioning.forbidden_phrases.
• Avoid calling the audience senior, elderly, ancianos, tercera edad, abuelos, or adultos mayores.
```

Implementation should not hard-code only this text. It should interpolate from `_locale_guidance(channel_config)`:

```python
locale = _locale_guidance(channel_config)
```

Then include:

```python
f"• Write in Spanish for {locale['target_locale']}, language code {locale['language']}.",
f"• Prefer these terms when natural: {', '.join(locale['prefer'])}.",
f"• Avoid these terms: {', '.join(locale['avoid'])}.",
```

### 2.3. Update `_chatgpt_scenes_prompt`

Scenes prompt currently asks visual prompts to match adults 45+ sleep-wellness context. Keep that. Add locale-specific on-screen text rule:

```text
LOCALE RULES:
• All Spanish scene fields (narration, caption, on_screen_text, layout_payload) must use es-ES Spanish.
• Use Spain-native wording when relevant: móvil, ordenador, por la tarde, de madrugada.
• Do not use LatAm-only words like celular/computadora unless they appear in the approved script.
• on_screen_text must sound natural in Spain and remain 2-4 words.
```

Important:

- `visual_prompt` must remain English because stock search/generation works better in English.
- Do not change the English visual prompt requirement.

### 2.4. Update `_chatgpt_scenes_plan_prompt` and `_chatgpt_scenes_batch_prompt`

Add the same locale rules to both planning and batch prompts so sharded scene generation does not drift back to LatAm wording.

Minimum addition:

```text
Locale rules:
- Spanish text fields must use es-ES Spanish for Spain.
- Prefer Spain-native terms from channel_config.locale_style.lexical_preferences.prefer.
- Avoid terms from channel_config.locale_style.lexical_preferences.avoid.
```

### 2.5. Update `_chatgpt_seo_prompt`

This is the most important section.

#### Replace hard-coded language rule

Current prompt says:

```text
- language: must be es-419
- tags: 5-8 concise Spanish/LatAm wellness search terms
```

Replace with dynamic language:

```python
locale = _locale_guidance(channel_config)
seo_language = locale["language"]
```

Prompt should say:

```text
- language: must be {seo_language}
- tags: 5-8 concise Spain-first Spanish wellness search terms
```

If `seo_language != "es-ES"`, fallback text can be generic:

```text
- tags: 5-8 concise Spanish wellness search terms matching the configured audience locale
```

#### Fix description section 4 to avoid placeholder social links

Current prompt asks for subscription link `and other social links`. This caused output like:

```text
Redes adicionales: no proporcionadas.
```

Replace section 4 instruction with:

```text
4. Section 4 (CTA & Subscription Link): A call-to-action asking viewers to subscribe, accompanied by the subscription link 'https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1'. Do NOT mention social links unless they are explicitly provided in channel_config.upload.social_links or channel_config.channel.social_links. Never write placeholder text such as 'Redes adicionales: no proporcionadas'.
```

#### Fix description structure for YouTube chapters

Add:

```text
- Timestamps must be one timestamp per line, not a single long paragraph.
- Each timestamp must use the format 'MM:SS - Section title'.
- Do not combine timestamps on one line.
```

#### Add Spain-first SEO guidance

Add:

```text
SEO LOCALE RULES:
• Optimize title, description, tags, and pinned comment for Spain-first Spanish (es-ES).
• Prefer 'móvil' over 'celular', 'ordenador' over 'computadora', 'por la tarde' over LatAm phrasing when natural.
• Use 'personas de más de 45 años' or 'adultos 45+'; avoid 'adultos mayores', 'tercera edad', 'ancianos'.
• Do not use LatAm label text like 'Spanish/LatAm' in output.
```

#### Add title/thumbnail guidance for current channel

Add:

```text
• For thumbnail_text, use 2-5 words, all caps, Spain-natural Spanish, strong but not exaggerated.
• Title and thumbnail_text must share the same pain angle.
• Avoid medical certainty claims. Use 'puede ayudarte', 'hábitos sencillos', 'rutina realista'.
```

### 2.6. Update `_claude_qa_prompt`

Current QA prompt says:

```text
You are QA reviewer for the ... artifact of a Spanish-language YouTube health channel.
```

Keep that, but add channel-config aware locale criteria:

```text
LOCALE QA:
• Check that the artifact uses the configured language from channel_config.seo.language or channel_config.audience.language.
• For this channel, expected language is es-ES unless config says otherwise.
• Flag LatAm-only terms if they appear repeatedly and a Spain-native equivalent is expected.
• Flag forbidden age-positioning terms from channel_config.positioning.forbidden_phrases.
```

However `_claude_qa_prompt` currently only receives `artifact_name` and `artifact`, not `channel_config`. Change signature to:

```python
def _claude_qa_prompt(
    artifact_name: str,
    artifact: dict[str, Any] | None,
    channel_config: dict[str, Any] | None = None,
) -> str:
```

Then update callers in `write_operator_prompts`:

```python
_claude_qa_prompt("script", script, channel_config)
_claude_qa_prompt("scenes", scenes, channel_config)
_claude_qa_prompt("seo", seo, channel_config)
```

Backward compatibility: default `channel_config=None` should still work.

---

## 3. Update `src/video_agent/operator_validators.py`

### 3.1. Fix misleading error message in `_validate_seo`

Currently `_validate_seo` builds expected language dynamically from config, but the error string says `(Latin American Spanish)` even when expected language is `es-ES`.

Change:

```python
result.errors.append(f"language must be '{expected_language}' (Latin American Spanish), got '{language}'.")
```

To:

```python
result.errors.append(
    f"language must be '{expected_language}' from channel_config.seo.language, got '{language}'."
)
```

### 3.2. Add locale lexical validation warning

Add optional validator for SEO text:

```python
def _validate_locale_style(seo: dict[str, Any], channel_config: dict[str, Any]) -> ValidationResult:
    ...
```

It should:

- Read `locale_style.lexical_preferences.avoid`.
- Scan title, description, tags, thumbnail_text, suggested_pinned_comments.
- Add `errors` for forbidden positioning phrases already handled by `_validate_forbidden_positioning`.
- Add `warnings` for non-blocking locale mismatches like repeated `celular` or `computadora` when `language == es-ES`.
- Add `errors` for template placeholder text:
  - `no proporcionadas`
  - `redes adicionales`
  - `social links not provided`

Call this from `_validate_seo`:

```python
result.merge(_validate_locale_style(seo, channel_config))
```

### 3.3. Block placeholder social links

In `_validate_locale_style`, if SEO description or pinned comment contains:

```text
Redes adicionales: no proporcionadas
no proporcionadas
not provided
```

Add error:

```text
SEO contains placeholder social-link text. Remove missing social links instead of mentioning them.
```

---

## 4. Update `src/video_agent/orchestrator/idea_generator.py`

### 4.1. Ensure Spain-first config is respected

`merge_keyword_channel_config` already maps `audience.language` starting with `es` to `spanish`. Keep that.

Add locale fields to DEFAULT config:

```python
"target_locale": "Spain",
"locale_language_code": "es-ES",
"lexical_prefer": ["móvil", "ordenador", "por la tarde", "de madrugada", "personas de más de 45 años"],
"lexical_avoid": ["celular", "computadora", "LatAm", "adultos mayores", "tercera edad", "ancianos"],
```

In `merge_keyword_channel_config`, if `channel_config.locale_style` exists, copy:

```python
cfg["target_locale"] = locale_style.get("target_locale", cfg["target_locale"])
cfg["locale_language_code"] = locale_style.get("language_code", audience.get("language", cfg["locale_language_code"]))
cfg["lexical_prefer"] = locale_style.get("lexical_preferences", {}).get("prefer", cfg["lexical_prefer"])
cfg["lexical_avoid"] = locale_style.get("lexical_preferences", {}).get("avoid", cfg["lexical_avoid"])
```

### 4.2. Update `_idea_gen_prompt`

Prompt already uses `language = audience.get("language", "es-419")`. Once config changes, it should say `es-ES` automatically.

Add locale guidance:

```text
## Locale style
- Target locale: Spain
- Language code: es-ES
- Use Spain-natural Spanish.
- Prefer: móvil, ordenador, por la tarde, de madrugada, personas de más de 45 años.
- Avoid: celular, computadora, LatAm, adultos mayores, tercera edad, ancianos.
```

Also add to rules:

```text
- All text must be in Spanish for Spain ({language}), not Latin America Spanish unless the config says otherwise.
- Do not use Portuguese.
- Do not use forbidden age-positioning phrases from channel_config.positioning.forbidden_phrases.
```

---

## 5. Update prompts to avoid content issues seen in recent operator review

The recent generated description contained a template artifact: `Redes adicionales: no proporcionadas.` Prevent this class of issue.

### Required prompt rule

Add to SEO prompt:

```text
Never mention missing resources. If social links, website, Instagram, Facebook, or other links are not explicitly provided in channel_config, omit them entirely. Do not write placeholders like 'no proporcionadas', 'not provided', 'sin enlaces', or 'redes adicionales'.
```

### Required normalizer guard

In `_normalize_seo_candidate`, after description normalization, remove or error on placeholder lines.

Preferred implementation: validator should block promotion rather than silently remove.

If implementing cleanup is easier, add safe cleanup:

```python
def _remove_placeholder_social_lines(description: str) -> str:
    bad_markers = ["redes adicionales", "no proporcionadas", "not provided", "social links"]
    lines = description.splitlines()
    kept = [line for line in lines if not any(marker in line.lower() for marker in bad_markers)]
    return "\n".join(kept).strip()
```

But still add validator error so Codex/test catches it.

---

## 6. Tests cần thêm

### 6.1. Config test

Add test that reads `configs/vida-plena-45/channel.yaml` and asserts:

```python
assert cfg["audience"]["language"] == "es-ES"
assert cfg["audience"]["primary_markets"] == ["ES"]
assert cfg["content_format"]["publish_schedule"]["timezone"] == "Europe/Madrid"
assert cfg["content_format"]["publish_schedule"]["time_local"] == "20:00"
assert cfg["seo"]["language"] == "es-ES"
```

### 6.2. SEO prompt language test

Test `_chatgpt_seo_prompt` with Spain config:

Expected prompt contains:

```text
language: must be es-ES
Spain-first Spanish
móvil
ordenador
```

Expected prompt does not contain:

```text
language: must be es-419
Spanish/LatAm wellness search terms
other social links
```

### 6.3. Script prompt locale test

Test `_chatgpt_script_prompt` contains:

```text
Spanish for Spain
es-ES
móvil
ordenador
```

### 6.4. Scenes prompt locale test

Test `_chatgpt_scenes_prompt` contains:

```text
es-ES
móvil
ordenador
```

and still contains:

```text
visual_prompt: English
```

### 6.5. QA prompt locale test

Test `_claude_qa_prompt("seo", seo, channel_config)` contains:

```text
expected language is es-ES
forbidden age-positioning
```

### 6.6. Validator language test

Given:

```python
seo = {"language": "es-419", "tags": [...], "description": "..."}
channel_config["seo"]["language"] = "es-ES"
```

Expected:

```python
result.is_valid is False
"language must be 'es-ES'" in result.format_report()
"Latin American Spanish" not in result.format_report()
```

### 6.7. Placeholder social links test

Given SEO description:

```text
Redes adicionales: no proporcionadas.
```

Expected validator blocks promotion:

```python
result.is_valid is False
"placeholder social-link text" in result.format_report().lower()
```

### 6.8. No forbidden age phrase test

Given SEO description with:

```text
adultos mayores
```

Expected `_validate_forbidden_positioning` blocks it and suggests preferred phrases.

---

## 7. Acceptance criteria

Codex task is complete only when:

1. `configs/vida-plena-45/channel.yaml` is Spain-first:
   - `audience.language = es-ES`
   - `primary_markets = ["ES"]`
   - `seo.language = es-ES`
   - `publish_schedule.timezone = Europe/Madrid`
   - `publish_schedule.time_local = 20:00`
2. All content generation prompts use dynamic language from config instead of hard-coded `es-419`.
3. SEO prompt no longer says `Spanish/LatAm wellness search terms` when channel is Spain-first.
4. SEO prompt forbids placeholder missing-social-link text.
5. SEO validator error message no longer says `(Latin American Spanish)` when expected language is dynamic.
6. SEO validator blocks placeholder text like `Redes adicionales: no proporcionadas`.
7. Script, scenes, SEO, and QA prompts include Spain style guidance.
8. Existing tests pass.
9. New tests listed above pass.
10. No behavior is broken for other possible channels. If another channel still uses `es-419`, prompts should use that channel's configured language.

---

## 8. Suggested Codex goal

Use this as the Codex goal, not the entire spec:

```text
Implement docs/spain_first_config_prompt_spec.md.

Scope:
1. Update configs/vida-plena-45/channel.yaml to Spain-first es-ES, Europe/Madrid, ES primary market.
2. Update operator and idea generator prompts to use dynamic locale/language from channel config, not hard-coded es-419/LatAm.
3. Prevent placeholder social-link text like "Redes adicionales: no proporcionadas" in SEO output/validation.
4. Update SEO validator error wording and add locale/placeholder tests.
5. Preserve compatibility for other channels.

Run the relevant tests and fix regressions.
```

---

## 9. Notes for review after implementation

After Codex finishes, manually check one generated SEO artifact. It should produce:

- `language: "es-ES"`
- Title natural for Spain.
- Description with timestamps one per line.
- No `Redes adicionales: no proporcionadas`.
- Pinned comment natural and not too long.
- Hashtags relevant to Spain-first Spanish health/wellness.

Recommended upload schedule after config update:

```text
Monday / Wednesday / Friday — 20:00 Europe/Madrid
```

For sleep videos, also consider:

```text
Sunday — 20:00 Europe/Madrid
```

but keep the default channel cadence unless analytics later says otherwise.
