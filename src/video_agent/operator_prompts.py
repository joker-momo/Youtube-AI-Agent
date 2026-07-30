"""Prompt template builders for operator ChatGPT/Gemini workflows."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_agent.audience_age import resolve_target_min_age
from video_agent.operator_json import _json_block, _json_file_directive
from video_agent.utils.json_io import read_json


def _resolve_existing_qa_path(job_dir: Path, artifact: str) -> Path:
    return job_dir / "operator" / "gemini" / f"{artifact}_qa.json"


def _idea_min_age(channel_config: dict[str, Any], idea: dict[str, Any]) -> int:
    """Target floor age for THIS video, from the idea (else channel floor).

    The channel is branded 45+, but an idea can target another age
    ("si tienes MÁS DE 60 AÑOS ..."); the whole video must then speak to that
    age. Falls back to ``audience.age_range`` floor for generic ideas.
    """
    return resolve_target_min_age(
        channel_config,
        str(idea.get("topic") or ""),
        str(idea.get("title_seed") or ""),
        str(idea.get("target_keyword") or ""),
        str(idea.get("thumbnail_hook") or ""),
        str(idea.get("angle") or ""),
    )


def _script_min_age(channel_config: dict[str, Any], script: dict[str, Any]) -> int:
    """Target floor age carried by an approved script (title/hook/narration)."""
    return resolve_target_min_age(
        channel_config,
        str(script.get("title") or ""),
        str(script.get("hook") or ""),
        str(script.get("narration") or "")[:600],
    )


def _locale_guidance(channel_config: dict[str, Any]) -> dict[str, Any]:
    """Resolve locale/language/lexical preferences from channel config.

    Resolution order for language: seo.language → audience.language → es-ES (default).
    target_locale defaults to ``Spain`` when language is es-ES, else ``Latin America``.
    """
    audience = (channel_config or {}).get("audience") or {}
    seo_cfg = (channel_config or {}).get("seo") or {}
    locale_style = (channel_config or {}).get("locale_style") or {}
    language = str(seo_cfg.get("language") or audience.get("language") or "es-ES")
    default_locale = "Spain" if language == "es-ES" else "Latin America"
    target_locale = str(locale_style.get("target_locale") or default_locale)
    lexical = locale_style.get("lexical_preferences") or {}
    prefer = list(lexical.get("prefer") or [])
    avoid = list(lexical.get("avoid") or [])
    return {
        "language": language,
        "target_locale": target_locale,
        "prefer": prefer,
        "avoid": avoid,
    }


def _locale_block_lines(
    channel_config: dict[str, Any],
    *,
    header: str = "LOCALE AND LANGUAGE RULES (MANDATORY):",
    min_age: int | None = None,
) -> list[str]:
    """Return prompt lines describing locale-specific writing rules from channel config.

    ``min_age`` is the per-video target floor age (from the idea/script); when
    omitted it falls back to the channel's configured audience floor.
    """
    locale = _locale_guidance(channel_config)
    age = min_age if min_age is not None else resolve_target_min_age(channel_config)
    lines = [
        header,
        f"• Write in Spanish for {locale['target_locale']}, language code {locale['language']}.",
        f"• Use a natural {locale['target_locale']}-first tone for adults {age}+.",
    ]
    if locale["prefer"]:
        lines.append("• Prefer these terms when natural: " + ", ".join(locale["prefer"]) + ".")
    if locale["avoid"]:
        lines.append("• Avoid these terms: " + ", ".join(locale["avoid"]) + ".")
    lines.append("• Never use forbidden age-positioning terms from channel_config.positioning.forbidden_phrases.")
    lines.append("• Avoid calling the audience senior, elderly, ancianos, tercera edad, abuelos, or adultos mayores.")
    return lines


def _script_channel_context(channel_config: dict[str, Any]) -> dict[str, Any]:
    """Small, script-relevant channel context for ChatGPT.

    Do not dump the full channel config into script generation: render settings,
    thumbnail/persona paths, style-DNA, visual-DNA, and operational wiring can
    bias the spoken content or leak irrelevant implementation details. The script
    model only needs audience, locale, topic boundaries, length, voice/tone, and
    safety positioning.
    """
    cfg = channel_config or {}
    channel = cfg.get("channel") if isinstance(cfg.get("channel"), dict) else {}
    audience = cfg.get("audience") if isinstance(cfg.get("audience"), dict) else {}
    locale_style = cfg.get("locale_style") if isinstance(cfg.get("locale_style"), dict) else {}
    niche = cfg.get("niche") if isinstance(cfg.get("niche"), dict) else {}
    content_format = cfg.get("content_format") if isinstance(cfg.get("content_format"), dict) else {}
    positioning = cfg.get("positioning") if isinstance(cfg.get("positioning"), dict) else {}
    seo = cfg.get("seo") if isinstance(cfg.get("seo"), dict) else {}
    tts = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}

    return {
        "channel": {
            "id": channel.get("id"),
            "name": channel.get("name"),
            "description": channel.get("description"),
        },
        "audience": {
            "language": audience.get("language"),
            "age_range": audience.get("age_range"),
            "primary_markets": audience.get("primary_markets"),
            "secondary_markets": audience.get("secondary_markets"),
        },
        "locale_style": {
            "target_locale": locale_style.get("target_locale"),
            "language_code": locale_style.get("language_code"),
            "lexical_preferences": locale_style.get("lexical_preferences"),
        },
        "niche": {
            "category": niche.get("category"),
            "sub_niches": niche.get("sub_niches"),
            "avoid_topics": niche.get("avoid_topics"),
        },
        "content_format": {
            "duration_sec_min": content_format.get("duration_sec_min"),
            "target_duration_sec": content_format.get("target_duration_sec"),
            "scenes_count_min": content_format.get("scenes_count_min"),
            "scenes_count_max": content_format.get("scenes_count_max"),
        },
        "tts": {
            "pace_wpm": tts.get("pace_wpm"),
        },
        "positioning": {
            "forbidden_phrases": positioning.get("forbidden_phrases"),
            "preferred_phrases": positioning.get("preferred_phrases"),
        },
        "seo": {
            "language": seo.get("language"),
            "min_tags": seo.get("min_tags"),
            "max_tags": seo.get("max_tags"),
        },
    }


def _chatgpt_script_prompt(channel_config: dict[str, Any], idea: dict[str, Any]) -> str:
    cf = channel_config.get("content_format", {})
    pace_wpm = channel_config.get("tts", {}).get("pace_wpm", 120)
    min_age = _idea_min_age(channel_config, idea)
    # Quality-first length: a hard MINIMUM only, NO upper cap. The script IS the
    # spoken narration — the scenes stage preserves it (splits into more scenes)
    # rather than condensing — so the floor is the real ~11-min content_format
    # minimum, not an inflated draft target. Develop every idea fully; never trim
    # to hit a time budget. (See bug-402: the old 1.43 multiplier assumed the LLM
    # under-produced; current models over-produce, so the surplus was discarded.)
    floor_sec = int(cf.get("duration_sec_min", 660))
    floor_min = round(floor_sec / 60)
    min_words = int(round(floor_sec / 60 * pace_wpm))
    return "\n".join(
        [
            "You are exporting a SCRIPT artifact as a JSON file for a YouTube channel pipeline.",
            "",
            _json_file_directive("script.json"),
            "",
            "Required JSON schema:",
            "- channel_id, job_id, hook, sections, narration, cta, qa",
            "- sections: array of 6-10 objects, each with: title, key_points (list), narration_text",
            f"- narration: natural Spanish, AT LEAST a {floor_min}-minute video (~{min_words}+ spoken words). There is NO maximum — a longer script is welcome whenever it adds real value.",
            f"- ⚠️ WORD-COUNT FLOOR (MANDATORY): the combined narration across ALL sections MUST contain AT LEAST {min_words} spoken Spanish words. There is NO upper limit. Count every word; if you are under {min_words}, expand with more concrete steps, examples, mini-stories, or sensory detail.",
            "- QUALITY OVER BREVITY: develop every idea fully. Never cut, compress, or skip a useful point to save time — completeness and depth win. Never pad with filler, slogans, or repeated phrases.",
            "- Each of the 6-10 sections must be fully developed (practical how-to, amounts, timing, one short relatable example where it helps); do not leave any section thin just to balance length.",
            f"- hook: opening sentence ≤28 words. Pattern: [relatable symptom] + [implicit promise].",
            "  Example: 'Si después de los 45 te cuesta conciliar el sueño o despiertas a las 3 de la mañana, esto es exactamente para ti.'",
            "- cta: closing call-to-action sentence",
            "- qa.verdict: set to PASS when you believe the script is ready",
            "",
            "⚠️ OPENING RETENTION RULES (FIRST 30 SECONDS — HIGHEST PRIORITY):",
            "• The render skips logo intro/outro entirely. The first frame the viewer sees is your narration hook, so the first ~12 words MUST hook them.",
            "• Do NOT start with the channel name, a greeting, 'En este video', 'Hoy', 'Bienvenidos', 'Hola', or any meta-introduction. Start IN the problem.",
            "• Open with one of these 4 retention patterns, picked to fit the idea:",
            "  1. Specific pain symptom: 'Si después de los 45 te despiertas a las 3 de la mañana mirando el techo, no es solo casualidad.'",
            "  2. Contradiction / pattern interrupt: 'Cenar pronto NO siempre te ayuda a dormir. Y la mayoría de personas de más de 45 no lo sabe.'",
            "  3. Concrete number + promise: 'En los próximos 7 minutos vas a ver 3 ajustes de la tarde que cambian la noche entera.'",
            "  4. Vivid micro-scene: 'Son las 22:30. La luz baja, el cuerpo cansado, pero la cabeza sigue corriendo. Esto es lo que está pasando y cómo cortarlo.'",
            "• The hook sentence ≤ 28 words. NO subordinate filler. Punch first, explain after.",
            "• Section 1 (first ~30 s of narration, roughly the first 70 words) MUST deliver the first concrete payoff. Do not save value for later sections.",
            "• Tease — do not summarise. Hint at the surprise, the contradiction, or the 3-step plan; do NOT list every section upfront.",
            "• Avoid promising 'al final del video' anything inside Section 1. Promise something the viewer gets in the NEXT 2 minutes.",
            "",
            "HOOK AND VALUE RULES (MANDATORY):",
            "• Do NOT open with generic teaching phrases such as 'En este video aprenderás', 'Hoy vamos a hablar de', or 'Te voy a enseñar'.",
            "• Open with a specific pain after 45: a concrete symptom, frustration, or hidden daily mistake the viewer recognizes immediately.",
            "• Make the hook feel like: pain + possible misunderstanding + gentle promise. Example: 'Si después de los 45 comes \"saludable\" pero sigues sin energía, quizá el problema no es tu fuerza de voluntad, sino cómo estás armando tu plato.'",
            "• Sections must give actions the viewer can apply today, not vague wellness slogans.",
            "• For nutrition topics, give topic-specific guidance with concrete timing, amounts, food swaps, label cues, hunger triggers, or plate structure ONLY when the idea actually needs it; do not default to the same plate formula across videos.",
            "• Do not leave advice as generic slogans like 'come más verduras', 'bebe más agua', 'duerme mejor', or 'haz ejercicio' unless each one includes a specific how-to, amount, timing, or trigger.",
            "• Use this core narrative format for the viewer experience: pain after 45 -> common misunderstanding -> simple explanation -> 3-5 practical steps -> relief close.",
            "• This is a story framework, not a topic restriction. You can apply it across sleep, nutrition, movement, menopause, stress, energy, weight, digestion, and daily habits.",
            "• Choose ONE distinct angle for this video, based on the idea. Example angles: cena ligera, despertar cansada, hambre aunque ya comiste, rodillas, ansiedad, metabolismo cambió, rutina más simple.",
            "• Across different videos, do not reuse the same pain, misunderstanding, and steps. Keep the channel consistent in experience but varied in angle.",
            "",
            "CONTENT DEPTH & TRUST RULES (from competitor study — apply to body sections):",
            "• MECHANISM: for each main point, briefly explain WHY it happens in plain physiology. The best videos teach the cause, not just the action. Keep it simple, no jargon.",
            "  ⚠️ VARY THE OPENER: do NOT begin every mechanism the same way. NEVER open more than ONE section's explanation with 'Esto ocurre porque…'. Rotate natural alternatives — 'La razón es…', 'Lo que pasa por dentro es…', 'Detrás de esto hay…', 'El cuerpo hace esto porque…', 'Aquí entra en juego…', or just state the cause directly without a formula lead-in.",
            "• ANALOGY: give ONE vivid everyday metaphor per mechanism to make biology graspable (e.g. inflammation like 'un fuego pequeño encendido', the body like 'un jardín que necesita riego'). At most one per section.",
            "• HONEST OBSERVED-PATTERN OPENER: ground the hook and sections in a real, recognizable pattern ('Mucha gente después de los 60 nota que…'). ⚠️ NEVER invent credentials or clinical authority ('soy médico', 'lo que veo en mi consulta', 'cardiólogo revela') — this channel is not a doctor and must not pretend to be.",
            "• REASSURANCE: weave brief validation so the viewer never feels blamed ('no es tu culpa', 'no tienes que hacerlo perfecto', 'puedes empezar con poco'). At least one per video, never preachy.",
            "• NUANCE OVER ABSOLUTES: prefer 'depende de…' and context over one-size rules ('no es lo mismo para una persona activa que para otra'). Avoid medical certainty; never promise cures.",
            "• SIGNATURE CLOSE: end with ONE short, memorable sentence that captures the idea (aphorism-style), e.g. 'El cuerpo rara vez grita; aprende a escucharlo antes.' One line, not melodramatic.",
            "",
            "STYLE ANTI-REPETITION RULES (MANDATORY):",
            "• Do NOT reuse a repetitive tail sentence pattern across sections.",
            "• Do NOT repeat phrases like 'hazlo simple y con calma' or close variants more than once.",
            "• Each section narration_text must end differently (different verb + image + rhythm).",
            "• Keep tone warm and natural, but avoid formulaic copy-paste cadence.",
            "",
            "CONTENT ANTI-REPETITION & PACING (MANDATORY — fixes 'too long / repetitive / slow'):",
            "• NO IDEA TWICE: never re-explain a point, mechanism, or step already covered in an earlier section. Every section must add NEW information — a new cause, step, example, amount, or angle — not a restatement of something already said.",
            "• NO RECAP FILLER mid-video: do not use 'como vimos', 'como ya dijimos', 'recuerda que', 'volviendo a lo anterior' to repeat earlier content. ONE short synthesis is allowed only in the closing.",
            "• EXPAND WITH DEPTH, NOT REPETITION: to reach the word floor, add a NEW sub-point, concrete example, amount, timing, or mini-story — NEVER pad by rephrasing something already said. If the topic genuinely runs out of new value, keep it tight rather than stretching with repetition.",
            "• FORWARD MOMENTUM: every ~30–45 seconds must move the viewer to something new. Cut throat-clearing lead-ins ('bueno', 'entonces', 'como te decía', 'pues bien') and delete any sentence that does not add information, emotion, or a step.",
            "• ONE BEAT PER SECTION: each section lands ONE clear point plus ONE emphasis line, then moves on. Do not circle back to over-elaborate a point that is already clear.",
            "• VARY RHYTHM (kills the 'slow / monotone' feel): alternate short punchy lines with an occasional longer one; never stack several same-length sentences in a row.",
            "",
            "SPOKEN NARRATION RULES (MANDATORY):",
            "• Write for spoken Spanish, not essay-style Spanish.",
            "• The narration should sound like a calm coach speaking to one person.",
            "• Use short and medium sentences.",
            "• Prefer direct phrases such as: 'si te pasa esto', 'empieza por aquí', 'prueba esto', 'no hace falta', 'vamos paso a paso'.",
            "• Put important emotional sentences on their own line.",
            "• Use paragraph breaks (blank line, i.e. \\n\\n) inside narration_text to guide natural TTS pauses.",
            "• Avoid long paragraphs with many commas.",
            "• Avoid overly abstract or literary phrases.",
            "• Avoid robotic repeated endings across sections.",
            "• Keep a warm Spain-first tone for people over 45.",
            "• Do not sound childish, slangy, or overly casual.",
            "",
            "TTS PROSODY RULES:",
            "• Write narration for calm Spanish TTS.",
            "• Use paragraph breaks before emotional or important sentences.",
            "• Every major section should include one memorable sentence of 8–14 words on its own line.",
            "• Avoid long chains joined by commas.",
            "• Do not overuse exclamation marks.",
            "• Do not use SSML tags (the TTS pipeline does not parse SSML).",
            "• Use punctuation naturally to guide pauses.",
            "",
            "EMPHASIS SENTENCE RULE:",
            "• Each section should include at most one short emphasis sentence (≤14 words), on its own line.",
            "• The emphasis sentence must be direct and useful, not melodramatic.",
            "• Examples: 'No tienes que demostrar nada.' / 'Empieza antes de agotarte.' / 'Tu cuerpo necesita confianza, no castigo.'",
            "• Avoid fake emotion such as '¡Transforma tu vida para siempre!' or '¡Nunca más sufrirás!'.",
            "",
            "DISCLAIMER RULE:",
            "• Do NOT create a long disclaimer scene near the beginning.",
            "• If a disclaimer is needed in narration, use one concise sentence only.",
            "• Preferred form: 'Este contenido es informativo; si tienes dolor, mareos o una condición médica, consulta con un profesional.'",
            "• Put the complete medical disclaimer in the SEO description, not in the first minute of the video.",
            "",
            "WRITTEN-vs-SPOKEN EXAMPLES (style guide):",
            "• AVOID: 'Para muchas personas de más de 45 años, el camino más sensato empieza con poco, bien elegido y repetido con cabeza.'",
            "• PREFER: 'Si tienes más de 45, no hace falta empezar fuerte.\\n\\nEmpieza con poco, elige bien, y repítelo sin prisa.'",
            "• AVOID: 'No necesitas ganar una batalla contra tu cuerpo. Necesitas construir confianza con él.'",
            "• PREFER: 'No necesitas ganar una batalla contra tu cuerpo.\\n\\nNecesitas construir confianza con él.'",
            "",
            *_locale_block_lines(channel_config, min_age=min_age),
            "",
            "Script context (filtered from channel config; excludes render, thumbnail, persona, style-DNA, visual-DNA, and operational settings):",
            _json_block(_script_channel_context(channel_config)),
            "",
            "Video idea:",
            _json_block(idea),
            "",
            "⚠️ REMINDER: Output ONLY the raw JSON object. No markdown. No commentary. Start with { and end with }.",
        ]
    )


def get_scenes_qa_feedback(job_dir: Path) -> str | None:
    """Helper to extract QA issues and required changes if the verdict is NEEDS_REWORK."""
    try:
        p = _resolve_existing_qa_path(job_dir, "scenes")
        if p.exists():
            qa_data = read_json(p)
            verdict = str(qa_data.get("verdict", "")).upper()
            if verdict == "NEEDS_REWORK":
                issues = qa_data.get("issues") or []
                changes = qa_data.get("required_changes") or []
                
                feedback_lines = []
                if issues:
                    feedback_lines.append("Issues found in previous version:")
                    for issue in issues:
                        feedback_lines.append(f"- {issue}")
                if changes:
                    feedback_lines.append("Required changes for this revision:")
                    for change in changes:
                        feedback_lines.append(f"- {change}")
                
                if feedback_lines:
                    return "\n".join(feedback_lines)
    except Exception:
        pass
    return None


_SCENE_RHYTHM_RULES = [
    "SCENE NARRATION RHYTHM RULES (MANDATORY):",
    "• Scene narration must sound natural when read aloud by Spanish TTS.",
    "• Prefer 1–3 short paragraphs per scene, separated by a blank line (\\n\\n).",
    "• Put the key emotional sentence on its own line so TTS pauses around it.",
    "• Each scene should have ONE clear emphasis point (≤14 words, direct, useful).",
    "• Avoid one long paragraph with many commas.",
    "• Avoid formal essay-style connectors when a direct spoken phrase is better.",
    "• Keep narration clear enough for people over 45 listening on mobile.",
    "• Scene narration should usually be 35–60 spoken words; warn above 75; never exceed 90.",
    "• Avoid melodrama: no '¡Transforma tu vida para siempre!', no '¡Nunca más sufrirás!'.",
    "• Final CTA scene must be short: do not stuff multiple actions and channel promo into one paragraph.",
    "DISCLAIMER RULE:",
    "• Do NOT place a long disclaimer in early scenes.",
    "• If narration requires a disclaimer, use ONE concise sentence such as: 'Este contenido es informativo; si tienes dolor, mareos o una condición médica, consulta con un profesional.'",
]


_LAYOUT_SELECTION_RULES = [
    "- Use layout=\"subtitle\" for normal explanation scenes.",
    "- Use layout=\"checklist\" only when the narration contains 2-4 concrete steps/items; bullets must come from narration/caption/on_screen_text.",
    "- Use layout=\"warning\" only when the narration describes a mistake, risk, or something to avoid.",
    "- Use layout=\"quote\" only for a short emotional or memorable sentence supported by the narration.",
    "- Use layout=\"stat\" when the narration centers on ONE memorable number/quantity (e.g. \"3 pasos\", \"2 veces al día\", \"80%\"): put that number or short phrase in title, and a short label in body. No bullets.",
    "- Use layout=\"steps\" when the narration describes an ORDERED sequence/process/schedule (do A, then B, then C): put 2-4 ordered steps in bullets, supported by the narration.",
    "- Use layout=\"comparison\" when the narration contrasts TWO options/choices (bien vs mal, esto vs aquello): put the two sides in bullets[0] and bullets[1] (both supported by narration).",
    "- Use layout=\"myth\" when the narration corrects a misconception: put the mistaken belief in title and the correction/reality in body (both supported by narration).",
    "- Use layout=\"plate_map\" ONLY when the narration explicitly describes a real meal/plate structure with visible food components; put the 2-4 plate components in bullets (supported by narration). This is not a generic nutrition checklist.",
    "- Use layout=\"recipe_snapshot\" ONLY when the narration gives 2-3 named foods as a concrete practical meal/snack example; put those real foods in bullets (supported by narration). This is not a generic nutrition checklist.",
    "- Prefer stat, steps, comparison, myth, or do_dont for nutrition advice about timing, habits, labels, portions, swaps, hunger, digestion, or recovery when the narration is not literally a plate/recipe example.",
    "- Use layout=\"quote_portrait\" for the most emotional/transitional sentence you want to feature magazine-style: put the sentence in body (a stronger variant of quote).",
    "- Use layout=\"evidence_nugget\" for a single credible number/fact tied to age/health ('después de los 60', 'masa muscular'): put the number/fact in title and short context in body (a documentary variant of stat).",
    "- Use layout=\"do_dont\" when the narration contrasts a WORSE choice vs a clearly BETTER one ('esto no, mejor esto'): put the worse option in bullets[0], the better in bullets[1] (both supported). Use comparison instead when the two options are NEUTRAL.",
    "- Prefer variety: across the whole script, do NOT make every card a checklist — pick the layout that matches the content shape (a number → stat, a sequence → steps, a contrast → comparison, a myth → myth). Never reuse the same title on two different graphic scenes.",
]


def _visual_context_line(channel_config: dict[str, Any], min_age: int | None = None) -> str:
    """Visual-context guidance for ``visual_prompt``, derived from the channel
    niche instead of hardcoded to one topic.

    An explicit ``niche.visual_context`` string overrides everything. Otherwise a
    generic line keyed to the niche category + audience age tells the model to
    DERIVE each scene's setting from its own narration — so a nutrition / memory /
    exercise video no longer gets pushed toward a bedroom/sleep setting.
    """
    niche = channel_config.get("niche") or {}
    override = str(niche.get("visual_context") or "").strip()
    if override:
        return f"- visual_prompt must match: {override}"
    category = str(niche.get("category") or "health and wellness").replace("_", " ")
    age = min_age if min_age is not None else resolve_target_min_age(channel_config)
    return (
        f"- visual_prompt must match THIS video's specific topic AND the channel context "
        f"({category} for adults {age}+): real everyday domestic settings, authentic mature "
        f"people, calm natural light. DERIVE the exact setting/action from the scene's own "
        f"narration: choose meal prep, supermarket labels, dining table, or real food only "
        f"when the narration is actually about food; choose gentle movement for exercise; "
        f"choose a calm bedroom only when the narration is about sleep. Give every scene a "
        f"distinct visual signature: vary location, person type, action, prop/object, time "
        f"of day, and camera distance. Do NOT reuse generic wellness filler such as sofa, "
        f"tea, kitchen, phone, or smiling portrait unless that exact object or setting is "
        f"supported by the narration."
    )


def _chatgpt_scenes_prompt(
    channel_config: dict[str, Any],
    script: dict[str, Any],
    qa_feedback: str | None = None,
) -> str:
    cf = channel_config.get("content_format", {})
    target_sec = cf.get("target_duration_sec", 840)
    scenes_min = cf.get("scenes_count_min", 40)
    scenes_max = cf.get("scenes_count_max", 55)
    scene_dur_target = round(target_sec / ((scenes_min + scenes_max) / 2))
    min_age = _script_min_age(channel_config, script)

    prompt_parts = [
        "You are exporting a SCENES artifact as a JSON file for a YouTube channel pipeline.",
        "",
        "⚠️ OUTPUT RULES — READ CAREFULLY:",
        "• Your ENTIRE response must be ONE raw JSON object — nothing else.",
        "• Do NOT write any text before or after the JSON.",
        "• Do NOT use markdown code fences (no ```json, no ```).",
        "• Do NOT add explanations, comments, or apologies.",
        "• Imagine you are writing directly to a .json file on disk.",
        f"• This JSON will be large ({scenes_min}-{scenes_max} scenes). That is fine — write the complete JSON until the final }}.",
        "",
        "Required JSON schema:",
        "- channel_id, job_id, scenes (array), total_duration_sec, qa",
        "- each scene object: id, duration_sec, narration, on_screen_text, caption, visual_prompt, motion, asset_refs, layout, layout_payload, layout_reason, section",
        "- section: the EXACT script section title this scene narrates (copy it verbatim from the approved script's sections list; use \"Hook\" for the hook and \"CTA\" for the closing call to action). Scenes must progress through sections in script order — this field drives the video's YouTube chapter timestamps, so a wrong section label produces wrong chapters.",
        f"- create AT LEAST {scenes_min} scenes (more when the script is longer — do not cap at {scenes_max}); each scene duration_sec should be {scene_dur_target-3}–{scene_dur_target+3} seconds",
        "- total_duration_sec is the sum of all scene durations and is DRIVEN BY THE SCRIPT length; preserve the full narration rather than trimming to a fixed total.",
        "- ⚠️ PRESERVE CONTENT: distribute the FULL script narration across scenes; do not condense or omit the script's details, examples, or steps.",
        "- scene ids: sequential scene-01, scene-02, ...",
        "- HOOK RULE: scene-01 narration must match the script hook word-for-word.",
        "  scene-01 on_screen_text: bold 3-6 word Spanish question or statement that hooks the viewer on THIS video's topic.",
        "- ⚠️ OPENING RETENTION: scene-01 is the first CONTENT frame after the replaceable intro/disclaimer clips. Keep scene-01 duration_sec between 8 and 12 — short enough to feel snappy but long enough to land the hook.",
        "- scenes 01-03 (first ~30 s) must deliver the first concrete payoff promised by the script hook. Do NOT use them for channel name, greetings, or 'today we will talk about'. Open IN the pain or contradiction.",
        "- scenes 01-03 visual_prompt must show the pain/situation of THIS video's topic directly (a real mature person in the relevant everyday setting the narration describes), not a generic logo card or wide establishing shot.",
        "- asset_refs: must be an object {}, never an array",
        "- on_screen_text MUST be 2-4 words (keyword hook), and MUST NOT duplicate caption text.",
        "- caption should be natural spoken sentence(s); never copy on_screen_text verbatim.",
        "- visual_prompt: ⚠️ MANDATORY ENGLISH ONLY. NEVER Spanish. visual_prompt is fed directly to Pexels stock search, which is English-keyword based. Spanish prompts produce off-topic stock footage (e.g. 'Bellagio fountains' for a 'rutina nocturna' scene). Required style: a concrete, scene-specific stock query with subject + setting + action + relevant prop/object + lighting/time + camera framing. Use THIS scene's narration as the source of truth. Do NOT reuse generic wellness filler such as sofa, tea, kitchen, phone, or smiling portrait unless that exact object/setting is in the narration. ALL OTHER FIELDS may be Spanish, but visual_prompt MUST be English.",
        _visual_context_line(channel_config, min_age),
        "- avoid off-topic visuals (cars, highways, random city traffic, tech gadgets unless explicitly in narration).",
        "- motion: 'slow_zoom' / 'pan_right' / 'pan_left'; never repeat same motion 3x in a row",
        "- layout: one of [\"hook\", \"subtitle\", \"checklist\", \"warning\", \"quote\", \"cta\", \"stat\", \"steps\", \"comparison\", \"myth\", \"plate_map\", \"recipe_snapshot\", \"quote_portrait\", \"evidence_nugget\", \"do_dont\"].",
        "- layout_payload: object with exactly these fields: {\"title\": string, \"body\": string, \"bullets\": array of strings, \"cta\": string}. The new layouts REUSE these fields (see each rule below).",
        "- layout_reason: short English reason explaining why the layout fits the narration.",
        "- scene-01 MUST use layout=\"hook\"; this is the mandatory opening graphic and must never be subtitle.",
        "- scene-01 layout_payload.title MUST contain a supported 2-8 word Spanish title copied exactly from on_screen_text or a contiguous phrase in narration/caption.",
        "- final scene should use layout=\"cta\" only if it contains a clear final action.",
        *_LAYOUT_SELECTION_RULES,
        "- ⚠️ VISUAL RHYTHM (critical for retention): the graphic layouts (hook/checklist/warning/quote/cta/stat/steps/comparison/myth/plate_map/recipe_snapshot/quote_portrait/evidence_nugget/do_dont) render as full design cards. Two or more cards back-to-back feel like a static slideshow and lose viewers. NEVER place two graphic-layout scenes consecutively — separate EVERY graphic scene with at least one (ideally two) layout=\"subtitle\" narrative scene(s) that play over moving video. This applies right after the scene-01 hook too: scene-02 onward must be \"subtitle\" until the next genuine card moment. Most scenes should be \"subtitle\"; spread the graphic cards sparingly across the whole script.",
        "- Every non-subtitle layout must include enough layout_payload for rendering.",
        "- All card payload text (title/body/bullets) must be COPIED from the narration/caption using the SAME words (you may shorten to a short phrase, but do NOT paraphrase or invent) — Python downgrades any layout whose payload text is not found in the scene's narration/caption/on_screen_text.",
        "- qa.verdict: must be PENDING_GEMINI_QA — never mark your own scenes as PASS",
        "",
        *_locale_block_lines(channel_config, header="LOCALE RULES:", min_age=min_age),
        "• All Spanish scene fields (narration, caption, on_screen_text, layout_payload) must use the configured language.",
        "• on_screen_text must sound natural in the configured locale and remain 2-4 words.",
        "• visual_prompt must remain English (stock search/generation works better in English).",
        "",
        *_SCENE_RHYTHM_RULES,
        "",
    ]

    if qa_feedback:
        prompt_parts.extend([
            "⚠️ CRITICAL REWORK FEEDBACK FROM PREVIOUS QA REVIEW:",
            "The previous version of scenes was rejected by the QA reviewer with verdict NEEDS_REWORK.",
            "You MUST revise and improve the scenes to address the following issues:",
            qa_feedback,
            "",
        ])
        
    prompt_parts.extend([
        "Channel config:",
        _json_block(channel_config),
        "",
        "Approved script:",
        _json_block(script),
        "",
        "⚠️ CRITICAL OUTPUT RULES:",
        "• Your response is ONLY a raw JSON object. No markdown, no commentary, no ```json fences.",
        "• Start your response with the character { and end with the character }.",
        f"• Expected response size: approximately {int(scenes_min) * 300}-{int(scenes_max) * 350} characters.",
        "• You MUST complete the entire JSON in this single response. Do NOT truncate.",
        "• If you run low on space, ADD more scenes — never shorten, summarize, or drop the script's content to fit.",
        "• Double-check: every { has a matching } before you finish.",
    ])
    
    return "\n".join(prompt_parts)


def _chatgpt_scenes_plan_prompt(channel_config: dict[str, Any], script: dict[str, Any]) -> str:
    cf = channel_config.get("content_format", {})
    scenes_min = int(cf.get("scenes_count_min", 40))
    pace_wpm = int(channel_config.get("tts", {}).get("pace_wpm", 120))
    floor_sec = int(cf.get("duration_sec_min", 660))
    # Content-driven scene count: ~45 spoken words per scene, floored at
    # scenes_min and at the ~11-min minimum. NO upper cap — the count grows with
    # the script so this stage PRESERVES the full narration instead of condensing
    # it to a fixed length. (See bug-402.)
    words_per_scene = 45
    narration_words = len(str(script.get("narration") or "").split())
    floor_scenes = max(scenes_min, round(floor_sec / 60 * pace_wpm / words_per_scene))
    target_scene_count = max(floor_scenes, -(-narration_words // words_per_scene))
    target_sec = (
        max(floor_sec, round(narration_words / pace_wpm * 60))
        if narration_words
        else int(cf.get("target_duration_sec", 840))
    )
    channel_id = (
        channel_config.get("channel", {}).get("id")
        or script.get("channel_id")
        or "vida-plena-45"
    )
    job_id = script.get("job_id", "")
    return "\n".join(
        [
            "You are planning sharded SCENES generation for a YouTube channel pipeline.",
            _json_file_directive("scenes_plan.json"),
            "",
            "Required envelope shape:",
            "{",
            '  "artifact_type": "scenes_plan",',
            '  "schema_version": "2026-05-json-shards-v1",',
            f'  "job_id": "{job_id}",',
            f'  "channel_id": "{channel_id}",',
            '  "status": "complete",',
            '  "batch_index": null,',
            '  "batch_total": null,',
            '  "data": {',
            f'    "target_scene_count": {target_scene_count},',
            f'    "target_total_duration_sec": {target_sec},',
            '    "batch_size": 6,',
            '    "batches": [',
            '      {',
            '        "batch_index": 1,',
            '        "scene_start": "scene-01",',
            '        "scene_end": "scene-06",',
            '        "purpose": "Opening hook",',
            '        "script_sections": ["Section Title"]',
            '      }',
            '    ]',
            "  },",
            '  "warnings": []',
            "}",
            "",
            "Plan rules:",
            "- batch_size must be between 6 and 8 scenes.",
            "- scene ranges must cover the full target_scene_count.",
            "- scene IDs must be sequential: scene-01, scene-02, ...",
            "- final batch must include the final scene.",
            "- ⚠️ COVER THE ENTIRE SCRIPT: create enough batches/scenes that the full approved narration is preserved across scenes. Do not compress, summarize, or drop any section — the scene count scales with script length.",
            "",
            *_locale_block_lines(
                channel_config, header="Locale rules:", min_age=_script_min_age(channel_config, script)
            ),
            "- Spanish text fields must use the configured language for the configured locale.",
            "- Prefer Spain-native terms from channel_config.locale_style.lexical_preferences.prefer.",
            "- Avoid terms from channel_config.locale_style.lexical_preferences.avoid.",
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Approved script:",
            _json_block(script),
        ]
    )


def _chatgpt_scenes_batch_prompt(
    channel_config: dict[str, Any],
    script: dict[str, Any],
    plan: dict[str, Any],
    batch: dict[str, Any],
    previous_batch_summary: str | None = None,
) -> str:
    channel_id = (
        channel_config.get("channel", {}).get("id")
        or script.get("channel_id")
        or "vida-plena-45"
    )
    job_id = script.get("job_id", "")
    batch_index = int(batch.get("batch_index") or 1)
    batch_total = len((plan.get("data") or {}).get("batches") or []) or int(batch.get("batch_total") or 1)
    scene_start = batch.get("scene_start", "scene-01")
    scene_end = batch.get("scene_end", scene_start)
    parts = [
        "You are exporting one small SCENES batch for a YouTube channel pipeline.",
        _json_file_directive(f"scenes_batch_{batch_index:02d}.json"),
        "",
        "Required envelope:",
        "{",
        '  "artifact_type": "scenes_batch",',
        '  "schema_version": "2026-05-json-shards-v1",',
        f'  "job_id": "{job_id}",',
        f'  "channel_id": "{channel_id}",',
        '  "status": "complete",',
        f'  "batch_index": {batch_index},',
        f'  "batch_total": {batch_total},',
        '  "data": {',
        f'    "scene_start": "{scene_start}",',
        f'    "scene_end": "{scene_end}",',
        '    "scenes": []',
        "  },",
        '  "warnings": []',
        "}",
        "",
        "Batch rules:",
        f"- Generate only scenes {scene_start} through {scene_end}.",
        "- Scene IDs must exactly match the requested range.",
        "- Every scene must include: id, duration_sec, narration, on_screen_text, caption, visual_prompt, motion, asset_refs, layout, layout_payload, layout_reason, section.",
        "- section: the EXACT script section title this scene narrates, copied VERBATIM from this batch's script_sections list (use \"Hook\" for hook scenes and \"CTA\" for the closing call to action). Scenes must move through the batch's sections in order — this field drives the video's YouTube chapter timestamps, so a wrong label produces wrong chapters.",
        "- asset_refs must be {}.",
        "- All card payload text (title/body/bullets) must be COPIED from the narration/caption using the SAME words (you may shorten to a short phrase, but do NOT paraphrase or invent) — Python downgrades any layout whose payload text is not found in the scene's narration/caption/on_screen_text.",
        "- ⚠️ visual_prompt MANDATORY ENGLISH ONLY. NEVER Spanish. Fed directly to Pexels (English keyword search). Spanish visual_prompt = rejected, you will be asked to regenerate. Write a concrete, scene-specific stock query with subject + setting + action + relevant prop/object + lighting/time + camera framing. Give every scene a distinct visual signature; do NOT reuse generic wellness filler such as sofa, tea, kitchen, phone, or smiling portrait unless that exact object/setting is in the narration.",
        "- narration must reproduce the approved script content for this scene range FAITHFULLY: keep every concrete detail, example, step, and explanation from the matching script sections. Do NOT summarize, shorten, or drop content.",
        "- If a script section is long, split it across MORE scenes (35–60 words each) rather than cutting content.",
        "- layout must be one of: hook, subtitle, checklist, warning, quote, cta, stat, steps, comparison, myth, plate_map, recipe_snapshot, quote_portrait, evidence_nugget, do_dont.",
        "- layout_payload must be an object with {title, body, bullets, cta}; use empty strings/[] for unused fields.",
        "- layout_reason must be a short English reason explaining why the layout fits the narration.",
        "- scene-01 MUST use layout=\"hook\"; this is the mandatory opening graphic and must never be subtitle.",
        "- scene-01 layout_payload.title MUST contain a supported 2-8 word Spanish title copied exactly from on_screen_text or a contiguous phrase in narration/caption.",
        "- final scene should use layout=\"cta\" only if it contains a clear final action.",
        *_LAYOUT_SELECTION_RULES,
        "- ⚠️ VISUAL RHYTHM (critical for retention): the graphic layouts (hook/checklist/warning/quote/cta/stat/steps/comparison/myth/plate_map/recipe_snapshot/quote_portrait/evidence_nugget/do_dont) render as full design cards. Two or more cards back-to-back feel like a static slideshow and lose viewers. NEVER place two graphic-layout scenes consecutively — separate EVERY graphic scene with at least one (ideally two) layout=\"subtitle\" narrative scene(s) that play over moving video. This applies right after the scene-01 hook too: scene-02 onward must be \"subtitle\" until the next genuine card moment. Most scenes should be \"subtitle\"; spread the graphic cards sparingly across the whole script.",
        "- Every non-subtitle layout must include enough layout_payload for rendering.",
        "- Do not invent overlay facts that are not supported by narration/caption/on_screen_text.",
        "- Do not return more than one JSON object.",
        "",
        *_locale_block_lines(
            channel_config, header="Locale rules:", min_age=_script_min_age(channel_config, script)
        ),
        "- Spanish text fields must use the configured language for the configured locale.",
        "- Prefer terms from channel_config.locale_style.lexical_preferences.prefer.",
        "- Avoid terms from channel_config.locale_style.lexical_preferences.avoid.",
        "- visual_prompt must stay English regardless of locale.",
        "",
        *_SCENE_RHYTHM_RULES,
        "",
    ]
    if previous_batch_summary:
        parts.extend(["Previous batch summary:", previous_batch_summary, ""])
    parts.extend(
        [
            "Channel config:",
            _json_block(channel_config),
            "",
            "Approved script:",
            _json_block(script),
            "",
            "Scenes plan:",
            _json_block(plan),
            "",
            "Requested batch:",
            _json_block(batch),
        ]
    )
    return "\n".join(parts)


def _gemini_scenes_qa_batch_prompt(
    channel_config: dict[str, Any],
    scenes_batch: dict[str, Any],
    batch_index: int,
    batch_total: int,
) -> str:
    channel_id = channel_config.get("channel", {}).get("id", "vida-plena-45")
    job_id = scenes_batch.get("job_id", "")
    return "\n".join(
        [
            "You are QA reviewer for one SCENES batch of a Spanish-language YouTube health channel.",
            _json_file_directive(f"scenes_qa_batch_{batch_index:02d}.json"),
            "",
            "Required envelope:",
            "{",
            '  "artifact_type": "scenes_qa_batch",',
            '  "schema_version": "2026-05-json-shards-v1",',
            f'  "job_id": "{job_id}",',
            f'  "channel_id": "{channel_id}",',
            '  "status": "complete",',
            f'  "batch_index": {batch_index},',
            f'  "batch_total": {batch_total},',
            '  "data": {',
            '    "verdict": "PASS",',
            '    "youtube_policy": {"compliant": true, "risk_level": "none", "violations": []},',
            '    "scene_checks": [],',
            '    "issues": [],',
            '    "required_changes": [],',
            '    "scores": {"schema_fit": 5, "channel_fit": 5, "safety": 5, "clarity": 5, "youtube_policy": 5}',
            "  },",
            '  "warnings": []',
            "}",
            "",
            "QA rules:",
            "- Review only this batch.",
            "- Include scene_checks for every scene in the batch.",
            "- If any scene has policy, safety, or schema issue, verdict must be NEEDS_REWORK.",
            "- youtube_policy.compliant must be false if there is any concern.",
            "",
            "SCENE-01 GRAPHIC HOOK IS A HARD GATE:",
            "- If this batch contains scene-01, it MUST use layout=\"hook\" with a supported 2-8 word layout_payload.title.",
            "- If scene-01 is subtitle, has no valid title, or cannot render as a graphic hook, verdict MUST be NEEDS_REWORK with a specific required_change.",
            "",
            "LAYOUT & VISUAL RHYTHM QA — except for the scene-01 hard gate above, add these to warnings for visibility, but do NOT set NEEDS_REWORK for grounding/rhythm/semantic issues alone: the Python planner already downgrades ungrounded cards, clears their payload, and enforces rhythm. ONLY set NEEDS_REWORK here if a layout value is literally not renderable:",
            "- Renderable layout (this one CAN block): every non-subtitle layout MUST be one of hook, checklist, warning, quote, cta, stat, steps, comparison, myth, plate_map, recipe_snapshot, quote_portrait, evidence_nugget, do_dont. Flag any other value.",
            "- Rhythm (warning only): note two graphic-layout scenes back-to-back, or very long runs (6+) of consecutive subtitle scenes with no card moment.",
            "- Grounding (warning only): note card payload text (title/body/bullets) that does not appear in the scene's narration/caption — the planner downgrades these automatically, so a warning is enough.",
            "- Semantic fit (warning only): note mismatches — warning = a mistake/risk to AVOID (not a to-do list); quote/quote_portrait = one memorable sentence (not bullets); stat/evidence_nugget = a real number/fact; comparison = two neutral options, do_dont = worse vs better.",
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Scenes batch:",
            _json_block(scenes_batch),
        ]
    )


def _chatgpt_seo_prompt(
    channel_config: dict[str, Any],
    script: dict[str, Any],
    scenes: dict[str, Any],
    brand_palette: dict[str, Any] | None = None,
) -> str:
    locale = _locale_guidance(channel_config)
    seo_language = locale["language"]
    is_spain = seo_language == "es-ES"
    min_age = _script_min_age(channel_config, script)
    tags_line = (
        "- tags: 5-8 concise Spain-first Spanish wellness search terms"
        if is_spain
        else "- tags: 5-8 concise Spanish wellness search terms matching the configured audience locale"
    )
    palette = (brand_palette or {}).get("palette") or {}
    brand_palette_line = (
        "Brand palette (hex) — background {bg}, primary {primary}, secondary {secondary}, "
        "default accent {accent}, text {text}."
    ).format(
        bg=palette.get("background", "#F6F1E8"),
        primary=palette.get("primary", "#2F6B57"),
        secondary=palette.get("secondary", "#D98C5F"),
        accent=palette.get("accent", "#F5C24B"),
        text=palette.get("text", "#26332F"),
    )
    return "\n".join(
        [
            "You are exporting an SEO artifact as a JSON file for a YouTube channel pipeline.",
            "",
            _json_file_directive("seo.json"),
            "",
            "Required JSON schema:",
            "- job_id, title, description, tags, language, ai_disclosure, thumbnail_path, thumbnail_text, suggested_pinned_comments, topic_accent_color",
            "- title_variants: array of EXACTLY 3 objects, each: {title, thumbnail_text}",
            "  • title: 7-12 words, CTR-FIRST — OPEN with a curiosity or contrarian HOOK, then the topic keyword after a colon or comma. The searchable keyword may come second but MUST appear somewhere. Spanish.",
            "    - Allowed CTR devices (use them, but keep honest): curiosity-gap (a hidden mistake/step tied to THIS video's actual topic, e.g. patterns like 'el [X] que casi nadie [verbo]', 'lo que pasa cuando/después de [Y]', 'por qué [Z] no funciona como crees'), contrarian (a specific myth from THIS topic framed as '(no es [culprit del video])' or 'no es lo que piensas sobre [tema]'), a concrete number tied to the actual content ('N errores', 'N señales', 'N hábitos'), or ONE power word (silencioso, sorprendente, sencillo, rápido, oculto, inesperado, decisivo).",
            "    - ⚠️ VARIETY: these are PATTERNS, not scripts — invent the specific wording from THIS video's script/topic each time. NEVER reuse a previous video's exact hook phrase; if you default to a generic filler word (nadie, siempre, nunca), swap it for something concrete drawn from the script content.",
            "    - The 3 title_variants MUST each use a DIFFERENT device: variant 1 = curiosity-gap (most CTR, hook first); variant 2 = contrarian '(no es…)'; variant 3 = keyword-first searchable (SEO safety net).",
            "    - ⚠️ HONEST CTR ONLY: NO fake authority/credentials ('cardiólogo advierte', 'enfermera revela'), NO fear or death claims ('detiene tu corazón', 'te está matando'), NO 'milagro/cura/garantiza'. The curiosity MUST be payable by the actual video content.",
            "  • thumbnail_text: 4-7 words ALL-CAPS Spanish. It is a STANDALONE MICRO-PROMISE: a viewer aged 45-75 must understand, without reading the YouTube title, what familiar problem/object the video concerns and what practical value they get.",
            "    - SEMANTIC PAYLOAD: every candidate must carry at least TWO of these signal classes: concrete topic/object (CAFÉ, PARTIDO, ACEITE DE OLIVA, ALIMENTOS), familiar pain/problem (SUEÑO, CANSANCIO, DOLOR), practical outcome (DORMIR MEJOR, CUIDAR TUS MÚSCULOS), action/decision (CUÁNDO TOMARLO, QUÉ ELEGIR, EVITA), or honest specificity (a real number, timing, or age frame).",
            "    - BAN context-free curiosity fragments that only make sense next to the title. Real repairs from this channel:",
            "      BAD: '5 GESTOS CLAVE'          -> GOOD: 'DUERME MEJOR TRAS EL PARTIDO'",
            "      BAD: '¿DUERMES PEOR DESPUÉS?'  -> GOOD: '¿TU CAFÉ EMPEORA EL SUEÑO?'",
            "      BAD: 'TU SEMANA TIENE HUECOS'  -> GOOD: '5 ALIMENTOS PARA CUIDAR TUS MÚSCULOS'",
            "      BAD: 'NO ES POR LA HORA'       -> GOOD: 'ACEITE DE OLIVA: CUÁNDO TOMARLO'",
            "    - The 3 variants take three DIFFERENT audience-fit angles: variant 1 = pain-led clarity (name the familiar problem plainly); variant 2 = outcome-led practical hope (the realistic improvement); variant 3 = action/decision-led specificity (what to do, when, or what to choose).",
            "    - Age or a number may strengthen a candidate but is NOT MANDATORY when topic and value are already explicit; do not force an imperative + age template when it makes the copy generic or unnatural.",
            "    - Respect the viewer's dignity and autonomy: practical agency (CÓMO, CUÁNDO, QUÉ ELEGIR, PARA CUIDAR, PUEDE AFECTAR), never frail/helpless framing, no degrading age labels.",
            "    - Still honest: no fake authority, no fear/death, no miracle claims, no unsupported certainty (write 'PUEDE AFECTAR TU SUEÑO', never 'ARRUINA TU SALUD' when the content only says it may influence wellbeing).",
            "    - RULE 'COMPLEMENTARY, NOT REPETITIVE': the thumbnail is complementary but complete — it selects the title's strongest pain/action/outcome angle while remaining a self-contained micro-promise (never a fragment that only works next to the title). Do not duplicate or paraphrase the full title.",
            "    - RULE 'SAME PAIN ANGLE': title and thumbnail_text must point to the same specific pain angle. If thumbnail_text points to one pain, the title must support that same pain clearly instead of switching to a generic wellness promise.",
            "    - Example alignment: thumbnail_text='TU PLATO TE HABLA' pairs with a title like 'Cómo saber si tu plato te está quitando energía después de los 45'. Do not pair it with a generic title like 'Cómo comer mejor después de los 45'.",
            "  • Make 3 variants MEANINGFULLY DIFFERENT — vary angle, emotion, or specificity",
            "  • Do NOT repeat the same hook with minor word swaps",
            "- title: copy from the best title_variants entry",
            "- thumbnail_text: copy from the best title_variants entry",
            "- topic_accent_color: ONE hex color (#RRGGBB) that is this SPECIFIC video's topic accent — not the channel's generic default.",
            f"    - {brand_palette_line}",
            "    - HARMONY RULE: the color must stay believably part of this brand — similar warm/earthy, muted-editorial family, comparable saturation and lightness to the brand palette above. Do NOT pick a jarring, neon, or unrelated hue.",
            "    - TOPIC RULE: within that harmony, shift hue/tone to fit THIS video's specific theme/emotion (e.g. a sleep/calm topic can lean toward a muted indigo or deep teal within the brand's tonal range; a food/energy topic can lean toward a warmer ochre/terracotta; a movement/stiffness topic can lean toward a grounded olive or clay). Base the choice on the actual script content, not a fixed rule.",
            "    - Must be DIFFERENT from the channel's default accent hex above (do not just copy it) unless the topic genuinely calls for the same tone.",
            "- description: YouTube video description in Spanish. It MUST follow this Golden Structure (structured into 6 distinct sections/paragraphs separated by blank lines):",
            "  1. Section 1 (Hook & SEO): 2-3 short sentences. Start with the primary keyword within the first 25 characters (e.g. 'Si después de los 45...').",
            "  2. Section 2 (Detailed Summary): 2-3 short paragraphs detailing what the video covers and what the viewer will learn, incorporating secondary/LSI keywords naturally.",
            "  3. Section 3 (Chapters / Timestamps): a YouTube chapter list derived from the approved scenes narration and durations. These render as clickable chapters, so they MUST satisfy YouTube's auto-chapter contract or YouTube ignores them entirely:",
            "    - The FIRST line MUST be '00:00 - <intro/hook label>'. YouTube requires the first chapter at exactly 00:00.",
            "    - Provide AT LEAST 3 chapters, in ascending time order, each at least 10 seconds after the previous one.",
            "    - Timestamps MUST be one timestamp per line, never combined on a single line.",
            "    - Each timestamp line MUST use 'MM:SS - Section title' exactly (two-digit minutes, two-digit seconds, dash with spaces).",
            "    - Chapter titles must be SHORT and SPECIFIC (3-6 words) describing the payoff of that part (e.g. '02:10 - Por qué te despiertas a las 3am'), NOT vague labels like 'Introducción', 'Punto 2', 'Consejos', or 'Conclusión'.",
            "    - IMPORTANT: Do not include any primary or external links in this section.",
            "  4. Section 4 (CTA & Subscription Link): A call-to-action asking viewers to subscribe, accompanied by the subscription link 'https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1'. Do NOT mention social links unless they are explicitly provided in channel_config.upload.social_links or channel_config.channel.social_links. Never write placeholder text such as 'Redes adicionales: no proporcionadas', 'no proporcionadas', 'not provided', or 'sin enlaces'.",
            "  5. Section 5 (Channel Info, Disclaimer & AI Disclosure): A short blurb about the channel's mission (Vida Plena 45+), the medical disclaimer (e.g., 'Aviso: El contenido es de carácter informativo y no sustituye la opinión médica.'), and the AI disclosure statement (disclosing that the video uses AI voice/visual assist).",
            "  6. Section 6 (Hashtags): 3-5 relevant hashtags at the very bottom (e.g., #vidasana #bienestar45).",
            "- suggested_pinned_comments: a single suggested pinned comment in Spanish (containing warm/engaging emojis) that combines two strategies: start with an engaging question to boost audience interaction (e.g. asking for opinions or experiences), and follow with a clear call-to-action to subscribe to the channel with the exact link: https://www.youtube.com/channel/UCKUswqsAaLsEkcsgzTuKAmw?sub_confirmation=1",
            f"- language: must be {seo_language}",
            tags_line,
            "- ai_disclosure: must be true",
            "- thumbnail_path: leave as empty string ''",
            "",
            "SEO LOCALE RULES:",
            f"• Optimize title, description, tags, and pinned comment for {locale['target_locale']}-first Spanish ({seo_language}).",
            "• Prefer 'móvil' over 'celular', 'ordenador' over 'computadora', 'por la tarde' over LatAm phrasing when natural." if is_spain else "• Use vocabulary natural to the configured audience locale.",
            f"• Use 'personas de más de {min_age} años' or 'adultos {min_age}+'; avoid 'adultos mayores', 'tercera edad', 'ancianos'.",
            "• Do not use LatAm label text like 'Spanish/LatAm' in output.",
            "• For thumbnail_text, use 3-7 words, all caps, Spain-natural Spanish, strong but not exaggerated." if is_spain else "• For thumbnail_text, use 3-7 words, all caps, natural Spanish for the configured locale, strong but not exaggerated.",
            "• Title and thumbnail_text must share the same pain angle.",
            "• Avoid medical certainty claims. Use 'puede ayudarte', 'hábitos sencillos', 'rutina realista'.",
            "",
            "MISSING-RESOURCE RULES (MANDATORY):",
            "Never mention missing resources. If social links, website, Instagram, Facebook, or other links are not explicitly provided in channel_config, omit them entirely. Do not write placeholders like 'no proporcionadas', 'not provided', 'sin enlaces', or 'redes adicionales'.",
            "",
            "Channel config:",
            _json_block(channel_config),
            "",
            "Approved script:",
            _json_block(script),
            "",
            "Approved scenes (summary + key visuals):",
            json.dumps(
                {
                    "total_duration_sec": scenes.get("total_duration_sec"),
                    "scene_count": len(scenes.get("scenes", [])),
                    "visual_prompts_sample": [
                        str(scene.get("visual_prompt") or "")
                        for scene in (scenes.get("scenes") or [])[:5]
                    ],
                },
                ensure_ascii=False,
            ),
            "",
            "⚠️ REMINDER: Output ONLY the raw JSON object. No markdown. No commentary. Start with { and end with }.",
        ]
    )


def _gemini_qa_prompt(
    artifact_name: str,
    artifact: dict[str, Any] | None,
    channel_config: dict[str, Any] | None = None,
) -> str:
    artifact_text = _json_block(artifact) if artifact is not None else "<paste ChatGPT JSON artifact here>"
    locale = _locale_guidance(channel_config or {})
    _age_signals = [
        str(artifact.get(k) or "")
        for k in ("title", "narration", "hook", "thumbnail_text", "topic")
        if isinstance(artifact, dict)
    ]
    min_age = resolve_target_min_age(channel_config or {}, *_age_signals)
    locale_qa_lines = [
        "",
        "════════════════════════════════════════",
        "LOCALE QA (mandatory when channel_config is available)",
        "════════════════════════════════════════",
        "• Check that the artifact uses the configured language from channel_config.seo.language or channel_config.audience.language.",
        f"• For this channel, expected language is {locale['language']} unless config says otherwise.",
        "• If the artifact has a language field and it is not EXACTLY the expected language, verdict MUST be NEEDS_REWORK.",
        f"• Expected target locale: {locale['target_locale']}.",
    ]
    if locale["avoid"]:
        locale_qa_lines.append(
            "• Flag locale lexical mismatches if these terms appear repeatedly when a configured-locale equivalent is expected: "
            + ", ".join(locale["avoid"])
            + "."
        )
    locale_qa_lines.append(
        "• Flag forbidden age-positioning terms from channel_config.positioning.forbidden_phrases (senior, ancianos, tercera edad, abuelos, adultos mayores, abuelitos)."
    )
    locale_qa_lines.append(
        "• Flag placeholder missing-resource text such as 'no proporcionadas', 'redes adicionales', 'not provided', or 'sin enlaces' in any SEO field."
    )
    artifact_qa_lines: list[str] = []
    if artifact_name.lower() == "scenes":
        artifact_qa_lines = [
            "",
            "SCENE-01 GRAPHIC HOOK IS A HARD GATE:",
            "• scene-01 MUST use layout=\"hook\" with a supported 2-8 word layout_payload.title.",
            "• If scene-01 is subtitle, has no valid title, or cannot render as a graphic hook, verdict MUST be NEEDS_REWORK with a specific required_change.",
        ]
    return "\n".join(
        [
            f"You are QA reviewer for the {artifact_name.upper()} artifact of a Spanish-language YouTube health channel.",
            "",
            "⚠️ OUTPUT RULES:",
            "• Return exactly ONE raw JSON object. No markdown. No commentary.",
            "• Start with { and end with }.",
            *locale_qa_lines,
            "",
            "═══════════════════════════════════════════",
            "MANDATORY CHECK 1 — YouTube Policy & Terms",
            "═══════════════════════════════════════════",
            "YouTube's policies are ZERO-TOLERANCE here. Even the SLIGHTEST suspicion = NEEDS_REWORK.",
            "Check every piece of content against ALL of the following:",
            "",
            "• MEDICAL MISINFORMATION: Any unproven health claims, cures, treatments, or medical advice",
            "  that contradicts established scientific consensus. Example: 'X cures diabetes'.",
            "• DANGEROUS HEALTH CONTENT: Content that encourages harmful behaviour, extreme diets,",
            "  unsafe supplements, or anything that could cause physical harm.",
            "• MISLEADING / CLICKBAIT: Title, thumbnail_text, or hook promises something the content",
            "  does not fully deliver. Exaggerated outcomes ('lose 20kg in a week').",
            "• SPAM OR DECEPTIVE PRACTICES: Repetitive content, fake engagement, misleading metadata.",
            "• HATE SPEECH OR DISCRIMINATION: Any content targeting groups by age, gender, race, etc.",
            "• PRIVACY VIOLATIONS: References to real people without consent, doxxing.",
            "• COPYRIGHT: Song lyrics, verbatim quotes from copyrighted works in narration.",
            "• CHILD SAFETY: Content inappropriate for minors that could reach them.",
            "• REGULATED PRODUCTS: Supplement promotion, pharmaceutical recommendations.",
            "• SENSATIONALISM ABOUT DEATH / DISEASE: Content designed to cause fear or panic.",
            "",
            "RULE: If ANY of the above applies — even weakly or by implication — set:",
            "  youtube_policy.compliant = false",
            "  youtube_policy.risk_level = 'medium' or 'high'",
            "  verdict = NEEDS_REWORK",
            "  required_changes must explain exactly what to fix.",
            "",
            "Only set youtube_policy.compliant = true AND risk_level = 'none' when you are",
            "100% certain NO policy concern exists.",
            "",
            "════════════════════════════════════════",
            "MANDATORY CHECK 2 — Schema & Content Quality",
            "════════════════════════════════════════",
            "• Schema fit: all required fields present, correct types, no nulls where strings expected",
            f"• Channel fit: content matches {locale['language']} Spanish health channel ({locale['target_locale']}-first) for adults {min_age}+",
            "• Safety: no specific medical diagnoses, no supplement promotion, no miracle cures",
            "• Clarity: language is natural, readable, appropriate pace",
            f"• Duration accuracy (for scenes): total_duration_sec must match sum of scene durations",
            *artifact_qa_lines,
            "",
            "════════════════════════════════════════",
            "REQUIRED JSON OUTPUT SCHEMA",
            "════════════════════════════════════════",
            "{",
            '  "verdict": "PASS" | "NEEDS_REWORK",',
            '  "youtube_policy": {',
            '    "compliant": true | false,',
            '    "risk_level": "none" | "low" | "medium" | "high",',
            '    "violations": ["exact quote or description of policy concern"]',
            '  },',
            '  "scores": {',
            '    "schema_fit": 1-5,',
            '    "channel_fit": 1-5,',
            '    "safety": 1-5,',
            '    "clarity": 1-5,',
            '    "youtube_policy": 1-5',
            '  },',
            '  "issues": ["list of problems found"],',
            '  "required_changes": ["specific actionable fix for each issue"]',
            "}",
            "",
            "VERDICT RULE: verdict = PASS only when:",
            "  • youtube_policy.compliant = true AND risk_level = 'none'",
            "  • All scores ≥ 4",
            "  • issues list is empty",
            "  • required_changes list is empty",
            "",
            f"Artifact to review ({artifact_name.upper()}):",
            artifact_text,
            "",
            "⚠️ REMINDER: Output ONLY the raw JSON. No markdown. No text before or after.",
        ]
    )
