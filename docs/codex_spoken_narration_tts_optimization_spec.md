# Codex Spec — Spoken Narration + TTS Prosody Optimization

## Goal

Improve the narration quality for `Vida Plena 45+` videos by making the Spanish script sound more like natural spoken narration and by tuning TTS pacing/prosody.

This spec focuses on three issues observed in generated videos:

1. The voice sounds too even and lacks emotional emphasis.
2. The narration pace is slightly too slow for YouTube retention.
3. Some scenes are too long, especially early disclaimers or dense explanation scenes.

The target result is a calm, trustworthy, Spain-first Spanish narration style for people over 45 that feels like a warm coach speaking directly to the viewer, not like an essay being read aloud.

---

## Non-goals

Do **not** replace the TTS engine in this task.

Do **not** migrate to ElevenLabs, Azure, Google TTS, or any external paid provider.

Do **not** redesign the whole video pipeline.

Do **not** change the channel niche, target audience, or Spain-first locale settings.

This task should improve the current Kokoro/local TTS pipeline and the prompts that generate narration.

---

## Current context

The channel is:

```yaml
channel:
  id: "vida-plena-45"
  name: "Vida Plena 45+"

audience:
  language: "es-ES"
  primary_markets: ["ES"]

locale_style:
  target_locale: "Spain"
  language_code: "es-ES"
```

The current generated narration is understandable and usable, but it has these issues:

- Sentences often sound like written Spanish instead of spoken Spanish.
- TTS reads too evenly because the input lacks natural paragraph breaks and emphasis cues.
- The real pacing feels closer to slow wellness narration than YouTube-optimized calm narration.
- Some scenes are long, including disclaimer-style scenes.
- Audio should remain calm, not salesy or hyperactive.

Target voice direction:

```text
warm
clear
calm
Spain-first Spanish
spoken, not essay-like
trustworthy
lightly emotional
not exaggerated
not too casual
```

---

## Target output quality

After this implementation:

- Narration should sound more conversational.
- Scene narration should use shorter sentence groups.
- Important emotional sentences should be placed on their own line.
- TTS should read slightly faster but remain calm.
- Long scenes should be reduced or split.
- Early disclaimer should be short and not harm retention.
- Mixed audio should target safe YouTube loudness/peak levels.

Target audio feel:

```text
speed: calm but not slow
realistic range: 115–125 words per minute perceived pace
preferred target: around 120 WPM
voice emotion: gentle emphasis on pain, relief, and action words
```

---

## Files likely to edit

Prioritize these files if they exist in the current repo:

```text
configs/vida-plena-45/channel.yaml
src/video_agent/operator.py
src/video_agent/operator_validators.py
src/video_agent/orchestrator/stages.py
src/video_agent/tts/*
src/video_agent/audio/*
tests/
```

If the actual TTS or audio mastering code is elsewhere, locate the existing Kokoro synthesis and `narration_mixed`/audio mixing path, then apply the changes there.

---

# Part 1 — Make narration more spoken, less written

## 1.1 Add spoken narration rules to script prompt

Find the ChatGPT script prompt, likely:

```python
_chatgpt_script_prompt(...)
```

Add a section like this to the prompt:

```text
SPOKEN NARRATION RULES (MANDATORY):
- Write for spoken Spanish, not essay-style Spanish.
- The narration should sound like a calm coach speaking to one person.
- Use short and medium sentences.
- Prefer direct phrases such as: "si te pasa esto", "empieza por aquí", "prueba esto", "no hace falta", "vamos paso a paso".
- Put important emotional sentences on their own line.
- Use paragraph breaks to guide natural pauses.
- Avoid long paragraphs with many commas.
- Avoid overly abstract or literary phrases.
- Avoid robotic repeated endings across sections.
- Keep a warm Spain-first tone for people over 45.
- Do not sound childish, slangy, or overly casual.
```

## 1.2 Add prosody rules to script prompt

Add another section:

```text
TTS PROSODY RULES:
- Write narration for calm Spanish TTS.
- Use paragraph breaks before emotional or important sentences.
- Every major section should include one memorable sentence of 8–14 words.
- Avoid long chains joined by commas.
- Do not overuse exclamation marks.
- Do not use SSML unless the TTS pipeline explicitly supports it.
- Use punctuation naturally to guide pauses.
```

## 1.3 Add spoken rhythm rules to scenes prompt

Find the scene-generation prompts, likely:

```python
_chatgpt_scenes_prompt(...)
_chatgpt_scenes_batch_prompt(...)
```

Add this section:

```text
SCENE NARRATION RHYTHM RULES:
- Scene narration must sound natural when read aloud.
- Prefer 1–3 short paragraphs per scene.
- Put the key emotional sentence on its own line.
- Each scene should have one clear emphasis point.
- Avoid one long paragraph with many commas.
- Avoid formal essay-style connectors when a direct spoken phrase is better.
- Keep narration clear enough for people over 45 listening on mobile.
```

## 1.4 Rewrite examples to guide Codex/prompt behavior

Add examples inside the prompt so the model understands the style shift.

### Before

```text
Para muchas personas de más de 45 años, el camino más sensato empieza con poco, bien elegido y repetido con cabeza.
```

### After

```text
Si tienes más de 45, no hace falta empezar fuerte.

Empieza con poco, elige bien, y repítelo sin prisa.
```

### Before

```text
La movilidad diaria es el pegamento de todo el plan.
```

### After

```text
La movilidad diaria ayuda a que todo encaje mejor.
```

### Before

```text
No necesitas ganar una batalla contra tu cuerpo. Necesitas construir confianza con él.
```

### After

```text
No necesitas ganar una batalla contra tu cuerpo.

Necesitas construir confianza con él.
```

## 1.5 Add a helper or validator for written-style narration

Add a lightweight validator/warning system for narration text.

Suggested helper:

```python
def detect_written_style_narration(text: str) -> list[str]:
    ...
```

It should return warnings, not hard errors, for:

- Very long sentences.
- Very long paragraphs.
- Too many comma-heavy sentences.
- Overly formal connectors.
- Repeated section endings.

Suggested thresholds:

```text
sentence > 28 words: warning
paragraph > 65 words: warning
3+ commas in one sentence: warning
same ending phrase repeated 3+ times: warning
```

Suggested warning messages:

```text
Scene scene-12: narration has a sentence longer than 28 words; consider splitting for TTS.
Scene scene-24: narration paragraph is too dense; add a paragraph break before the key idea.
Scene scene-36: narration sounds essay-like; prefer direct spoken phrasing.
```

Keep this as a warning initially so the pipeline does not become too brittle.

---

# Part 2 — Improve “voice too even / low emotional emphasis”

## 2.1 Use paragraph breaks as TTS emphasis cues

The pipeline should preserve line breaks in narration fields where practical.

Do not aggressively collapse all newlines in narration. Newlines can be useful for TTS rhythm.

For generated `script.json` and `scenes.json`, preserve paragraph breaks inside:

```text
narration
narration_text
caption
```

Only normalize excessive whitespace, not meaningful paragraph breaks.

Recommended normalization:

```python
def normalize_spoken_text(text: str) -> str:
    # Convert CRLF/CR to LF
    # Collapse 3+ newlines to 2 newlines
    # Collapse repeated spaces/tabs inside each line
    # Preserve single and double newlines
```

## 2.2 Add emphasis sentence requirement

In script/scene prompt, require one short emphasis sentence in important scenes.

Example:

```text
Each scene should include at most one emphasis sentence.
The emphasis sentence should be short, direct, and useful.
Examples:
- "No tienes que demostrar nada."
- "Empieza antes de agotarte."
- "Tu cuerpo necesita confianza, no castigo."
- "Lo importante es repetir, no hacerlo perfecto."
```

## 2.3 Avoid fake emotion

Do not ask the model to use melodrama.

Avoid:

```text
¡Transforma tu vida para siempre!
¡Nunca más sufrirás!
```

Prefer:

```text
No hace falta hacerlo perfecto.
Empieza de una forma que puedas repetir.
Si algo duele, baja el ritmo.
```

## 2.4 Optional: add emphasis metadata for future-proofing

If easy and non-breaking, add optional metadata to scenes:

```json
{
  "narration_emphasis": ["sin forzar", "confianza", "dolor agudo"]
}
```

Do not require render/TTS to use this yet.

This can help future TTS engines or subtitle highlighting, but it should not break current schema.

---

# Part 3 — Improve narration speed / retention pacing

## 3.1 Update TTS config

In `configs/vida-plena-45/channel.yaml`, update TTS pacing.

Recommended config:

```yaml
tts:
  provider: "kokoro"
  voice_id: "ef_dora"
  lang_code: "e"
  speed: 1.03
  sample_rate: 24000
  pace_wpm: 120
  scene_lead_in_sec: 0.45
  humanize:
    enabled: true
    pause_comma_ms: 230
    pause_semicolon_ms: 340
    pause_sentence_ms: 500
    pause_paragraph_ms: 700
    speed_jitter_pct: 3.5
```

If tests or listening review show it is too fast, fallback to:

```yaml
speed: 1.01
pace_wpm: 115
```

If still too slow after review, try:

```yaml
speed: 1.05
pace_wpm: 123
```

Do not exceed:

```yaml
speed: 1.08
```

for this channel unless manually approved.

## 3.2 Keep calm pacing but reduce drag

Target perceived pace:

```text
115–125 WPM
preferred: ~120 WPM
```

The voice should not sound rushed.

The goal is to remove drag, not to create a high-energy motivational tone.

## 3.3 Reduce excessive pause durations

Make sure the TTS humanization code actually uses these pause values:

```yaml
pause_comma_ms: 230
pause_semicolon_ms: 340
pause_sentence_ms: 500
pause_paragraph_ms: 700
```

If current code ignores these fields, wire them into the TTS synthesis/mixing logic.

If pause insertion is currently post-processing generated WAV clips, apply pause config there.

## 3.4 Add a pace report

After TTS generation, write a small report file such as:

```text
jobs/<job_id>/tts_report.json
```

Recommended fields:

```json
{
  "total_audio_sec": 687.9,
  "estimated_words": 1400,
  "estimated_wpm": 122.1,
  "scene_count": 48,
  "avg_scene_audio_sec": 14.3,
  "long_scenes": [
    {"scene_id": "scene-07", "duration_sec": 21.82}
  ],
  "config": {
    "speed": 1.03,
    "pause_sentence_ms": 500,
    "pause_paragraph_ms": 700
  }
}
```

Use this to debug whether pacing is improving.

---

# Part 4 — Shorten or split long scenes

## 4.1 Add scene duration targets

For YouTube retention, especially with TTS narration, scenes should usually be:

```text
ideal: 8–16 seconds
acceptable: 6–18 seconds
warning: >18 seconds
hard warning: >22 seconds
```

The video can still have rare long scenes, but not early in the video unless absolutely necessary.

## 4.2 Add validator warnings for long scenes

Use either scene duration from `scenes.json` or actual TTS durations if available.

Add warnings:

```text
Scene scene-07 duration 21.82s is long; split or shorten.
Scene scene-48 duration 18.73s is borderline long; consider trimming CTA.
```

Thresholds:

```python
LONG_SCENE_WARNING_SEC = 18.0
VERY_LONG_SCENE_WARNING_SEC = 22.0
EARLY_SCENE_STRICT_LIMIT_SEC = 16.0  # scenes 1-8
```

For scenes 1–8, warn sooner because early retention matters.

## 4.3 Shorten disclaimers

Do not put a long medical disclaimer early in the video.

Replace long early disclaimer with one concise sentence:

```text
Este contenido es informativo y no sustituye la opinión médica; si tienes dolor, mareos o una condición médica, consulta con un profesional.
```

Full disclaimer should stay in the YouTube description.

Prompt rule to add:

```text
DISCLAIMER RULE:
- Do not create a long disclaimer scene near the beginning.
- If a disclaimer is needed in narration, use one concise sentence only.
- Put the complete medical disclaimer in the SEO description, not in the first minute of the video.
```

## 4.4 Split dense scenes

If a scene has more than 45–55 spoken words, split it into two scenes or shorten it.

Suggested rule:

```text
Scene narration should usually be 18–40 words.
Warning if >50 words.
Hard warning if >65 words.
```

## 4.5 CTA scene should be short

Final CTA should not be a long paragraph.

Preferred final scene:

```text
Empieza con una versión tan posible que puedas repetirla.

Si este enfoque te ayuda, suscríbete a Vida Plena 45+ y cuéntanos qué movimiento suave probarás esta semana.
```

Avoid CTA scenes that contain multiple actions and long channel promos in one sentence.

---

# Part 5 — Audio mastering improvements

## 5.1 Keep loudness safe

After narration synthesis, master mixed narration to:

```text
Integrated loudness: -15 LUFS
True peak: -1.5 dBTP
Audio codec: AAC
Bitrate: 192 kbps
Sample rate: 48 kHz
```

Acceptable range:

```text
Integrated loudness: -14 to -16 LUFS
True peak: <= -1.0 dBTP
```

## 5.2 Avoid clipping

If current `narration_mixed.m4a` can hit or exceed 0 dBFS, add a limiter.

Suggested ffmpeg chain:

```bash
ffmpeg -i narration.wav \
  -filter:a "atempo=1.04,loudnorm=I=-15:TP=-1.5:LRA=7,alimiter=limit=-1.5dB" \
  -ar 48000 -ac 2 -c:a aac -b:a 192k narration_mixed.m4a
```

If the pipeline already applies `atempo` through TTS speed, do not double-speed the audio. In that case, use:

```bash
ffmpeg -i narration.wav \
  -filter:a "loudnorm=I=-15:TP=-1.5:LRA=7,alimiter=limit=-1.5dB" \
  -ar 48000 -ac 2 -c:a aac -b:a 192k narration_mixed.m4a
```

## 5.3 Add audio QA fields

If possible, write audio QA metadata:

```json
{
  "integrated_lufs": -15.0,
  "true_peak_dbtp": -1.5,
  "duration_sec": 687.9,
  "codec": "aac",
  "bitrate": "192k",
  "sample_rate": 48000,
  "warnings": []
}
```

Warn when:

```text
true_peak_dbtp > -1.0
integrated_lufs > -13.0
integrated_lufs < -18.0
bitrate < 128k for final mixed audio
```

---

# Part 6 — Tests to add

Add or update tests depending on existing test structure.

## 6.1 Prompt tests

Test that script prompt contains:

```text
SPOKEN NARRATION RULES
TTS PROSODY RULES
Write for spoken Spanish
Put important emotional sentences on their own line
```

Test that scenes prompt contains:

```text
SCENE NARRATION RHYTHM RULES
visual_prompt MANDATORY ENGLISH ONLY
Scene narration must sound natural when read aloud
```

## 6.2 Text normalization tests

If implementing `normalize_spoken_text`, test:

```python
def test_normalize_spoken_text_preserves_paragraph_breaks():
    text = "No necesitas demostrar nada.\n\nEmpieza con poco.   Sin prisa."
    assert normalize_spoken_text(text) == "No necesitas demostrar nada.\n\nEmpieza con poco. Sin prisa."
```

Also test it does not collapse meaningful newlines.

## 6.3 Written-style warning tests

Test:

```python
def test_written_style_warns_long_sentence():
    text = "..."  # 35+ words
    warnings = detect_written_style_narration(text)
    assert any("longer than" in w for w in warnings)
```

Test:

```python
def test_written_style_allows_short_spoken_text():
    text = "No tienes que demostrar nada.\n\nEmpieza con poco."
    warnings = detect_written_style_narration(text)
    assert not warnings
```

## 6.4 Long scene tests

Test long scene warning:

```python
def test_long_scene_warning():
    scene = {"id": "scene-07", "duration_sec": 21.82, "narration": "..."}
    warnings = validate_scene_duration(scene, scene_index=7)
    assert warnings
```

Test stricter early scene warning:

```python
def test_early_scene_strict_warning():
    scene = {"id": "scene-03", "duration_sec": 17.5, "narration": "..."}
    warnings = validate_scene_duration(scene, scene_index=3)
    assert warnings
```

## 6.5 TTS config test

Test that channel config loads:

```yaml
speed: 1.03
pace_wpm: 120
scene_lead_in_sec: 0.45
pause_sentence_ms: 500
pause_paragraph_ms: 700
```

## 6.6 Audio QA tests

If audio QA functions are added, unit test threshold logic without requiring ffmpeg.

Example:

```python
def test_audio_qa_warns_clipping():
    report = audio_qa_report(integrated_lufs=-14.5, true_peak_dbtp=-0.2, bitrate_kbps=192)
    assert any("true peak" in w.lower() for w in report["warnings"])
```

---

# Part 7 — Acceptance criteria

This task is complete when:

1. Script prompt explicitly instructs ChatGPT to write spoken Spanish, not essay-style Spanish.
2. Scenes prompt explicitly instructs ChatGPT to write narration with natural TTS rhythm.
3. Important emotional sentences are encouraged to be placed on their own line.
4. TTS config is updated to a slightly faster but still calm pace.
5. Pause values are reduced and actually used by the TTS/mixing pipeline if applicable.
6. Long scene warnings exist for scenes over 18 seconds.
7. Early scenes 1–8 warn above 16 seconds.
8. Long disclaimer early in the video is discouraged by prompt.
9. Audio mastering target is -15 LUFS and true peak no higher than -1.0 dBTP.
10. Tests cover prompt rules, spoken text normalization, long scene warnings, and/or audio QA threshold logic.
11. Existing tests continue to pass.

---

# Part 8 — Suggested implementation order

1. Update `channel.yaml` TTS config.
2. Add spoken narration/prosody rules to script prompt.
3. Add scene rhythm rules to scene prompts.
4. Add disclaimer-shortening rule.
5. Add or update text normalization to preserve meaningful narration line breaks.
6. Add long scene duration validator warnings.
7. Add TTS/audio report if feasible.
8. Update audio mastering/limiter if current pipeline allows it.
9. Add tests.
10. Run the test suite.

---

# Part 9 — Example before/after narration style

## Written-style narration

```text
Para muchas personas de más de 45 años, el camino más sensato empieza con poco, bien elegido y repetido con cabeza. El primer cambio es mental.
```

## Preferred spoken narration

```text
Si tienes más de 45, no hace falta empezar fuerte.

Empieza con poco.
Elige bien.
Y repítelo sin prisa.

El primer cambio empieza en cómo miras el movimiento.
```

## Written-style narration

```text
La clave está en no llenar esos diez minutos de exigencia. Al caminar, no busques el máximo ritmo. Busca un paso vivo pero conversable.
```

## Preferred spoken narration

```text
No llenes esos diez minutos de exigencia.

Si caminas, no busques el máximo ritmo.
Busca un paso vivo, pero cómodo.

Un ritmo con el que todavía puedas hablar.
```

## Written-style disclaimer

```text
Antes de seguir, una nota importante. Este contenido es general y no sustituye una valoración profesional. Si tienes dolor fuerte, mareos, falta de aire inusual, una lesión reciente o una condición médica, consulta con un profesional sanitario.
```

## Preferred spoken disclaimer

```text
Este contenido es informativo y no sustituye la opinión médica.

Si tienes dolor, mareos o una condición médica, consulta con un profesional.
```

For early video retention, prefer even shorter:

```text
Este contenido es informativo; si tienes dolor, mareos o una condición médica, consulta con un profesional.
```
