"""Strict, JSON-only, Spain-first prompts for Short script / scene / QA generation."""
from __future__ import annotations

import json
from typing import Any

_OUTPUT_RULES = (
    "OUTPUT RULES:\n"
    "- Return exactly one raw JSON object.\n"
    "- No markdown fences.\n"
    "- No commentary.\n"
    "- No extra text before or after the JSON.\n"
)

_LANGUAGE_RULES = (
    "LANGUAGE RULES:\n"
    "- Spanish for Spain, es-ES.\n"
    "- Natural spoken Spanish for adults 45+.\n"
    "- Prefer Spain-native terms.\n"
    "- Do NOT call the audience ancianos, tercera edad, abuelos, elderly, seniors, or adultos mayores.\n"
)

_RETENTION_RULES = (
    "SHORTS RETENTION RULES:\n"
    "- First 2 seconds must contain pain, curiosity, a number, or a mistake.\n"
    "- No greeting. No greetings of any kind.\n"
    "- Do not start with the channel name.\n"
    "- Do not say 'en este short', 'en este vídeo', 'hoy vamos a', 'bienvenidos', or 'hola'.\n"
    "- One main idea only.\n"
    "- Give one useful payoff before the CTA.\n"
    "- CTA must be short and not exceed 8 words when possible.\n"
    "- Do not make the Short a generic recap or summary.\n"
)

_STYLE_RULES = (
    "STYLE RULES:\n"
    "- Warm, direct, calm. More concise than long-form.\n"
    "- Short and medium sentences. Important sentence on its own line.\n"
    "- No childish TikTok slang, no melodrama, no miracle promises, no aggressive medical tone.\n"
)

_SAFETY_RULES = (
    "SAFETY RULES:\n"
    "- Do not create new health claims not supported by the source scenes.\n"
    "- Do not promise cures. Do not make diagnosis or treatment claims.\n"
    "- A long medical disclaimer must NOT appear in a Short.\n"
)


def _source_block(source_artifacts: dict) -> str:
    if not source_artifacts:
        return ""
    return "SOURCE (rewrite faithfully, do not invent):\n" + json.dumps(source_artifacts, ensure_ascii=False)[:8000] + "\n\n"


def _idea_block(short_plan: dict) -> str:
    if not short_plan.get("idea_id"):
        return ""
    key_points = short_plan.get("key_points") or []
    points_text = "\n".join(
        f"- {item.get('point', '')} | source_scene_ids={item.get('source_scene_ids', [])}"
        for item in key_points
    )
    return (
        "SHORT IDEA:\n"
        f"Title: {short_plan.get('title', '')}\n"
        f"Hook text: {short_plan.get('hook_text', '')}\n"
        f"Viewer pain: {short_plan.get('viewer_pain', '')}\n"
        f"Practical payoff: {short_plan.get('practical_payoff', '')}\n"
        f"Format: {short_plan.get('format', '')}\n"
        f"Key points:\n{points_text}\n"
        f"Narration seed:\n{short_plan.get('narration_seed', '')}\n\n"
    )


def short_script_prompt(channel_config: dict, short_plan: dict, source_artifacts: dict | None = None) -> str:
    fmt = short_plan.get("format", "pain_to_tip")
    seed = short_plan.get("narration_seed", "")
    idea_block = _idea_block(short_plan).strip() or "(none)"
    source_block_text = _source_block(source_artifacts or {}).strip() or "(none)"

    schema = (
        "{\n"
        '  "short_id": "string",\n'
        '  "source_long_job_id": "string or null",\n'
        f'  "short_format": "{fmt}",\n'
        '  "target_duration_sec": 35,\n'
        '  "hook": "string",\n'
        '  "narration": "string",\n'
        '  "beats": [\n'
        "    {\n"
        '      "time_sec": "0-3",\n'
        '      "visual": "string",\n'
        '      "narration": "string",\n'
        '      "purpose": "hook | setup | payoff | cta"\n'
        "    }\n"
        "  ],\n"
        '  "cta": "string",\n'
        '  "qa": {\n'
        '    "verdict": "PENDING_SHORTS_QA"\n'
        "  }\n"
        "}"
    )

    return (
        "You are a YouTube Shorts writer for a Spain-first wellness channel for adults aged 45+.\n\n"
        f"Write ONE vertical YouTube Short in format \"{fmt}\".\n\n"
        "Use the selected SHORT IDEA when present, but SOURCE always has priority.\n"
        "Do not summarize the full long video.\n"
        "Use only claims clearly supported by the provided key_points, source scenes, and narration seed.\n"
        "If a claim is not supported, omit it.\n"
        "Create a 20–45 second Short with ONE main idea.\n\n"
        "INPUTS:\n"
        f"SHORT IDEA block:\n{idea_block}\n\n"
        f"SOURCE block:\n{source_block_text}\n\n"
        f"SOURCE NARRATION SEED:\n{seed}\n\n"
        "OUTPUT RULES:\n"
        "Return exactly ONE raw valid JSON object.\n"
        "No markdown fences.\n"
        "No commentary.\n"
        "No trailing commas.\n"
        "All strings must be valid JSON strings.\n\n"
        "LANGUAGE RULES:\n"
        "Use es-ES.\n"
        "Speak to adults 45+ without using words like \"ancianos\", \"abuelos\", \"seniors\", \"personas mayores\", or age-shaming language.\n\n"
        "RETENTION RULES:\n"
        "The first 2 seconds must open with pain, curiosity, a number, or a common mistake.\n"
        "No greeting.\n"
        "Do not say \"en este short\", \"en este vídeo\", \"hoy\", \"bienvenidos\", or \"hola\".\n"
        "Keep one main idea only.\n"
        "Deliver the payoff before the CTA.\n"
        "CTA must be 8 words or fewer.\n"
        "No generic recap.\n\n"
        "STYLE RULES:\n"
        "Warm, direct, calm.\n"
        "Short and medium sentences.\n"
        "No TikTok slang.\n"
        "No melodrama.\n"
        "No miracle language.\n"
        "No aggressive medical tone.\n\n"
        "SAFETY RULES:\n"
        "Do not add new health claims.\n"
        "Do not imply cures, diagnosis, or treatment.\n"
        "Do not include long disclaimers.\n"
        "Do not use fear-based medical language.\n\n"
        "DURATION RULES:\n"
        "Target duration: around 35 seconds.\n"
        "Narration should be approximately 80–105 Spanish words.\n\n"
        f"RETURN JSON SCHEMA:\n{schema}\n"
    )


def short_scene_prompt(channel_config: dict, short_plan: dict, short_script: dict) -> str:
    schema = {
        "channel_id": (channel_config.get("channel") or {}).get("id", ""),
        "job_id": short_plan.get("source_long_job_id", ""),
        "short_id": short_plan.get("short_id", "short-01"),
        "total_duration_sec": short_script.get("target_duration_sec", 35),
        "scenes": [],
        "qa": {"verdict": "PENDING_SHORTS_QA"},
    }
    scene_rules = (
        "SCENE RULES:\n"
        "- 5-12 short scenes total.\n"
        "- First scene 1.5-3.0 seconds. Normal scenes 2.0-5.0 seconds. CTA scene 3.0-6.0 seconds.\n"
        "- Total 25-45 seconds.\n"
        "- on_screen_text 2-5 words; must NOT duplicate the caption exactly.\n"
        "- visual_prompt MUST be English only and describe a vertical-friendly shot.\n"
        "- layouts allowed: short_hook, short_pain, short_tip, short_checklist, short_quote, short_cta.\n"
        "- No long paragraphs, no cluttered overlays.\n"
    )
    return (
        f"Turn this Short script into vertical (9:16) scenes.\n\n"
        f"SCRIPT:\n{json.dumps(short_script, ensure_ascii=False)[:2000]}\n\n"
        f"{_OUTPUT_RULES}\n{scene_rules}\n"
        f"Return JSON exactly in this shape:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )


def short_seo_prompt(channel_config: dict, short_plan: dict, short_script: dict, long_video_url: str = "") -> str:
    hook = str(short_script.get("hook") or "").strip()
    narration = str(short_script.get("narration") or "").strip()
    cta = str(short_script.get("cta") or "").strip()
    short_format = str(short_plan.get("format") or "").strip()
    viewer_pain = str(short_plan.get("viewer_pain") or "").strip()
    payoff = str(short_plan.get("practical_payoff") or "").strip()
    title_seed = str(short_plan.get("title") or "").strip()
    pillar = str(
        short_plan.get("pillar")
        or short_plan.get("detected_pillar")
        or (channel_config.get("channel") or {}).get("pillar")
        or ""
    ).strip()

    schema = (
        "{\n"
        '  "title": "string, <= 60 chars, includes the core pain and the 45+ frame",\n'
        '  "description": "string, 1-3 sentences in es-ES, echoes the script payoff, ends with 3-5 hashtags",\n'
        '  "hashtags": ["#string", "#string", "#string", "#string", "#string"],\n'
        '  "pinned_comment": "string, opens a real conversation about the script\'s pain, ends with a question to the viewer",\n'
        f'  "long_video_url": "{long_video_url}"\n'
        "}"
    )

    return (
        "You are the SEO copywriter for a Spain-first wellness channel for adults aged 45+.\n\n"
        "Write the YouTube Short metadata in Spain Spanish (es-ES). Every field MUST match the actual content of the script below. "
        "Off-topic hashtags will get the Short shown to the wrong audience, kill retention, and stop YouTube from recommending the channel — so accuracy beats keyword volume.\n\n"
        "SCRIPT CONTEXT:\n"
        f"- Pillar / topic family: {pillar or '(infer from the script)'}\n"
        f"- Short format: {short_format or '(unspecified)'}\n"
        f"- Idea title seed: {title_seed or '(none)'}\n"
        f"- Viewer pain: {viewer_pain or '(infer from hook)'}\n"
        f"- Practical payoff: {payoff or '(infer from narration)'}\n"
        f"- HOOK: {hook}\n"
        f"- NARRATION: {narration[:1200]}\n"
        f"- CTA: {cta}\n\n"
        f"{_OUTPUT_RULES}\n"
        "LANGUAGE RULES:\n"
        "- es-ES, adults 45+ register.\n"
        "- Do NOT use \"ancianos\", \"abuelos\", \"seniors\", \"personas mayores\", or any age-shaming wording.\n\n"
        "SEO KEYWORD STRATEGY:\n"
        "- Use one high-volume broad search keyword when it honestly matches the script, then narrow it with Spain-first 45+ intent.\n"
        "- Combine one broad search keyword with the actual payoff/pain and the 45+ frame; do not publish a generic title that could target any Spanish-speaking market.\n"
        "- For nutrition/pan/plato Shorts, prefer natural title/description keywords such as \"alimentación saludable\", \"plato saludable\", \"el pan engorda\", \"comer pan saludable\", or \"qué pan es mejor\" when they match the script.\n"
        "- Description must reuse the chosen broad keyword naturally in the first sentence, then add the practical 45+ Spain/es-ES angle.\n"
        "- Keep Spain intent subtle and natural: Spain Spanish vocabulary, adults 45+, and everyday Spain-compatible phrasing; do not add fake geography if the script does not mention it.\n\n"
        "TITLE RULES:\n"
        "- Maximum 60 characters including spaces.\n"
        "- Name the actual pain or payoff from the script (e.g. \"carga mental\", \"insomnio\", \"sarcopenia\") AND the 45+ frame.\n"
        "- Put the chosen broad keyword near the beginning when it reads naturally, then qualify it with the specific pain/payoff or 45+ frame.\n"
        "- No clickbait (\"increíble\", \"NADIE te dijo\", \"el truco que…\"), no all-caps screaming.\n"
        "- At most one tasteful emoji at the end if it reinforces the topic; never multiple.\n\n"
        "DESCRIPTION RULES:\n"
        "- 1 to 3 short sentences in es-ES that echo the script payoff (do NOT just repeat the title).\n"
        "- End the description with the same 3-5 hashtags returned in the hashtags array.\n"
        "- No links to other channels, no sponsor text, no calls to subscribe.\n\n"
        "HASHTAG RULES:\n"
        "- 3 to 5 hashtags, all lowercase, no spaces, each starting with '#'.\n"
        "- Every hashtag MUST be semantically tied to the actual script topic (mental health, sleep, nutrition, joints, balance, etc.).\n"
        "- FORBIDDEN unless the script is genuinely about that exact topic: #gym, #fitness, #workout, #crossfit, #musculacion, #pesas, #cardio, #abs, #motivation, #mindset, #shortsviral, #fyp, #parati, #viral, #foryou, #trending.\n"
        "- Prefer specific, topical Spain-Spanish wellness tags such as #saludmental, #bienestar, #descanso, #sueño, #mindfulness, #estres, #ansiedad, #alimentacionsaludable, #platosaludable, #nutricion, #vida45plus, #saludable, #autocuidado — but ONLY if they actually match the script.\n"
        "- For nutrition Shorts, prefer broad viewer-search terms like #alimentacionsaludable or #platosaludable over invented age-number nutrition hashtags.\n"
        "- Always include #shorts ONLY as a 5th hashtag at most; never as the first.\n\n"
        "PINNED COMMENT RULES:\n"
        "- 1 to 2 sentences in es-ES that reflect the real pain from the script (carga mental, insomnio, ansiedad, etc.).\n"
        "- End with one open question that invites the viewer to share their own experience (no yes/no).\n"
        "- Do NOT shove the long-video URL into the pinned comment unless the long_video_url field is non-empty AND the question naturally invites watching more.\n\n"
        "SAFETY RULES:\n"
        "- No medical claims, no cures, no diagnosis, no \"this solves your X\".\n"
        "- No fear language. No miracle promises.\n\n"
        f"RETURN JSON SCHEMA:\n{schema}\n"
    )


def short_qa_prompt(channel_config: dict, short_script: dict, short_scenes: dict) -> str:
    schema = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "warnings": [],
        "scores": {"hook": 90, "payoff": 85, "funnel": 80, "source_fidelity": 90, "safety": 95, "mobile_readability": 90},
    }
    return (
        "Review this Short for hook strength, retention in the first 2 seconds, one main idea, standalone "
        "value, funnel CTA, source fidelity, safety/no overclaim, Spain-first language, mobile readability, "
        "and vertical scene logic.\n\n"
        f"SCRIPT:\n{json.dumps(short_script, ensure_ascii=False)}\n\n"
        f"SCENES:\n{json.dumps(short_scenes, ensure_ascii=False)}\n\n"
        f"{_OUTPUT_RULES}\nReturn JSON exactly:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )


# ---------------------------------------------------------------------------
# Spec v6 §9 — additional prompts (planner, scene v6, gemini_qa)
# ---------------------------------------------------------------------------

_SHORT_LAYOUT_NAMES = (
    "short_hook", "short_pain", "short_tip", "short_checklist",
    "short_myth", "short_quote", "short_cta",
)


def planner_prompt(channel_config: dict, candidates: list[dict],
                   long_summary: dict, formats: list[str]) -> str:
    """ChatGPT planner: pick 1-3 Shorts from provided candidates only."""
    schema = {
        "source_long_job_id": long_summary.get("job_id", ""),
        "source_title": long_summary.get("title", ""),
        "detected_pillar": long_summary.get("pillar", "routine"),
        "target_count": 3,
        "selected_shorts": [{
            "short_id": "short-01",
            "format": "pain_to_tip",
            "candidate_id": "candidate-XX",
            "source_scene_ids": ["scene-NN"],
            "hook_angle": "...",
            "viewer_pain": "...",
            "practical_payoff": "...",
            "music_track": "shorts_sleep_stress",
            "cta_type": "long_video_channel_cta",
            "reason": "...",
        }],
        "warnings": [],
    }
    rules = (
        "PLANNER RULES (spec v6 §2.2):\n"
        "- Choose 1-3 Shorts (max 5).\n"
        "- Only select candidate_id values present in CANDIDATES below.\n"
        "- Do NOT invent source scenes. source_scene_ids must come from the candidate.\n"
        "- Each selected Short MUST include a 'reason'.\n"
        "- Allowed formats: " + ", ".join(formats) + ".\n"
        "- Do not force weak Shorts; lower target_count if candidates are weak.\n"
    )
    cand_blob = json.dumps(
        [{"candidate_id": c.get("candidate_id"),
          "scene_ids": c.get("scene_ids"),
          "tier": c.get("tier"),
          "final_score": c.get("final_score"),
          "narration": str(c.get("narration", ""))[:280]}
         for c in candidates[:20]],
        ensure_ascii=False)[:6000]
    return (
        "You are the Shorts planner for a Spain-first wellness channel (45+).\n\n"
        f"LONG VIDEO TITLE: {long_summary.get('title', '')}\n"
        f"DETECTED PILLAR: {long_summary.get('pillar', '')}\n\n"
        f"CANDIDATES (only choose from these candidate_id values):\n{cand_blob}\n\n"
        f"{_OUTPUT_RULES}\n{rules}\n"
        f"Return JSON exactly:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )


def short_scene_prompt_v6(channel_config: dict, short_plan: dict,
                          short_script: dict, feedback: str = "") -> str:
    """Spec v6 §2.4 / §9.3 — ChatGPT chooses each scene's layout."""
    script_json = json.dumps(short_script, ensure_ascii=False)[:2000]
    feedback_block = feedback.strip() if feedback else "(none)"

    schema = (
        "{\n"
        '  "channel_id": "string or null",\n'
        '  "job_id": "string or null",\n'
        '  "short_id": "string",\n'
        '  "total_duration_sec": 35,\n'
        '  "scenes": [\n'
        "    {\n"
        '      "id": "s01",\n'
        '      "duration_sec": 5,\n'
        '      "layout": "short_hook",\n'
        '      "narration": "string",\n'
        '      "caption": "string",\n'
        '      "on_screen_text": "string",\n'
        '      "visual_prompt": "English visual generation prompt, vertical 9:16",\n'
        '      "motion": "string",\n'
        '      "layout_payload": {\n'
        '        "title": "string",\n'
        '        "items": ["string"],\n'
        '        "emphasis": "string"\n'
        "      },\n"
        '      "source_scene_ids": []\n'
        "    }\n"
        "  ],\n"
        '  "qa": {\n'
        '    "verdict": "PENDING_SCENES_QA"\n'
        "  }\n"
        "}"
    )

    return (
        "Turn this approved Short script into vertical 9:16 scenes for generation.\n\n"
        f"SCRIPT:\n{script_json}\n\n"
        f"RETRY FEEDBACK:\n{feedback_block}\n\n"
        "TASK:\n"
        "Create scene-by-scene visual instructions for a vertical YouTube Short.\n"
        "Do not rewrite the core message.\n"
        "Do not add new health claims.\n"
        "Keep the narration faithful to the SCRIPT.\n\n"
        "OUTPUT RULES:\n"
        "Return exactly ONE raw valid JSON object.\n"
        "No markdown fences.\n"
        "No commentary.\n"
        "No trailing commas.\n"
        "All strings must be valid JSON strings.\n\n"
        "SCENE COUNT & TIMING:\n"
        "- Create 4–7 scenes.\n"
        "- Each scene should be 3–8 seconds.\n"
        "- total_duration_sec must equal the sum of all scene duration_sec values.\n"
        "- Preserve the script's CTA if present.\n"
        "- First scene MUST use layout \"short_hook\".\n"
        "- Last scene SHOULD use layout \"short_cta\" if CTA is present.\n\n"
        "ALLOWED SHORT LAYOUTS ONLY:\n"
        "- short_hook\n"
        "- short_pain\n"
        "- short_tip\n"
        "- short_checklist\n"
        "- short_myth\n"
        "- short_quote\n"
        "- short_cta\n\n"
        "Do NOT use long-form layouts.\n"
        "Do NOT use layouts without the \"short_\" prefix, EXCEPT the graphic layouts below.\n\n"
        "GRAPHIC SCENE LAYOUTS (use sparingly, 0-2 per Short):\n"
        "When a scene teaches a formula, ratio, checklist, or numbered steps that the\n"
        "viewer should remember visually, use ONE of these instead of generic footage:\n"
        "- graphic_plate_ratio  -> plate split formulas (1/2, 1/4, 50%, 25%).\n"
        "- graphic_checklist    -> 2-5 short action items under one instruction.\n"
        "- graphic_step_list    -> 2-4 numbered steps.\n"
        "Use a graphic only for the highest-value knowledge moment. Most scenes stay\n"
        "stock short_* layouts. A graphic scene should last 2.5-4.0 seconds.\n"
        "Do NOT use any other graphic_* layout. Do NOT use scene_id, voiceover_text,\n"
        "or voiceover_start_sec.\n\n"
        "GRAPHIC layout_payload SHAPES:\n"
        "graphic_plate_ratio: {\"title\": \"LA REGLA DEL PLATO\", \"segments\": ["
        "{\"label\": \"1/2 verduras\", \"value\": 50}, {\"label\": \"1/4 proteína\", \"value\": 25}, "
        "{\"label\": \"1/4 pan, arroz o patata\", \"value\": 25}], \"footer\": \"...\"} "
        "(2-4 segments, values MUST sum to 100).\n"
        "graphic_checklist: {\"title\": \"ANTES DE DORMIR\", \"items\": [\"Móvil lejos\", "
        "\"Luz baja\", \"Lista de pendientes\"], \"footer\": \"...\"} (2-5 items).\n"
        "graphic_step_list: {\"title\": \"EN 3 PASOS\", \"steps\": [{\"label\": \"1\", \"text\": "
        "\"Mira la etiqueta\"}, {\"label\": \"2\", \"text\": \"Busca fibra\"}], \"footer\": \"...\"} "
        "(2-4 steps).\n"
        "Keep graphic title 2-5 words, es-ES. Labels/items short and readable.\n\n"
        "TEXT RULES:\n"
        "- on_screen_text: 2–5 words.\n"
        "- layout_payload.title: 2–5 words.\n"
        "- caption: short subtitle-style text, maximum 12 words.\n"
        "- No long bottom subtitle paragraphs.\n"
        "- Do not duplicate the full narration as on-screen text.\n"
        "- Use es-ES for narration, caption, on_screen_text, and layout_payload.\n"
        "- visual_prompt must be in English.\n\n"
        "VISUAL RULES:\n"
        "- visual_prompt must be vertical-friendly for 9:16.\n"
        "- Prefer simple realistic scenes: close-up face, hands, kitchen, supermarket aisle, bed, yoga mat, chair, walking, daily routine.\n"
        "- Show adults aged 45+ naturally and respectfully.\n"
        "- Do not make people look frail, sick, helpless, ashamed, or mocked.\n"
        "- No scary medical imagery.\n"
        "- No hospital, pills, needles, organs, scans, or diagnosis visuals unless explicitly supported by the script.\n"
        "- Do not introduce unsupported before/after transformations.\n"
        "- Keep visuals practical, warm, calm, and Spain-first.\n\n"
        "LAYOUT DECISION RULES:\n"
        "- ChatGPT decides the best layout for each scene.\n"
        "- Use short_hook for the opening curiosity/pain/mistake.\n"
        "- Use short_pain for the relatable problem.\n"
        "- Use short_tip for one clear action.\n"
        "- Use short_checklist only when there are 2–3 simple checks.\n"
        "- Use short_myth only when correcting a misconception.\n"
        "- Use short_quote only for a short memorable line from the script.\n"
        "- Use short_cta only for the final CTA.\n\n"
        "SOURCE RULES:\n"
        "- source_scene_ids must only contain IDs already present in the SCRIPT or source references.\n"
        "- If no source_scene_ids are available, use [].\n"
        "- Do not invent source_scene_ids.\n\n"
        f"RETURN JSON SCHEMA:\n{schema}\n"
    )


def gemini_qa_prompt(channel_config: dict, short_script: dict,
                     short_scenes: dict, short_source_map: dict | None = None) -> str:
    """Spec v6 §2.5 / §9.4 — Gemini QA validates layout + safety + funnel."""
    schema = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "warnings": [],
        "scores": {
            "hook": 90, "payoff": 85, "funnel": 80,
            "source_fidelity": 90, "safety": 95,
            "mobile_readability": 90, "layout": 90,
        },
    }
    rules = (
        "GEMINI QA RULES (spec v6 §13):\n"
        "- duration 20-45s; first 2s = pain/curiosity/number/mistake; no greeting.\n"
        "- one main idea; payoff before CTA; CTA short; CTA <= 20% of duration.\n"
        "- on_screen_text 2-5 words; captions <= 2 lines.\n"
        "- visuals match the pain/topic; source_map exists; source fidelity OK.\n"
        "- no medical overclaim, no miracle promise, no long disclaimer.\n"
        "- music selected; cover text valid.\n"
        "- layout choices correct; first scene is short_hook; only short_* layouts used.\n"
        "- primary text not too low.\n"
        "- PASS only if ready to render. FAIL with specific required_changes "
        "when regeneration is needed.\n"
    )
    body = (
        f"SCRIPT:\n{json.dumps(short_script, ensure_ascii=False)}\n\n"
        f"SCENES:\n{json.dumps(short_scenes, ensure_ascii=False)}\n\n"
    )
    if short_source_map:
        body += f"SOURCE MAP:\n{json.dumps(short_source_map, ensure_ascii=False)}\n\n"
    return (
        "You are the Shorts QA reviewer for a Spain-first wellness channel (45+).\n\n"
        f"{body}{_OUTPUT_RULES}\n{rules}\n"
        f"Return JSON exactly:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )


def gemini_script_qa_prompt(channel_config: dict, short_script: dict, short_source_map: dict | None = None) -> str:
    """Gemini QA validates script quality, language, and safety."""
    script_json = json.dumps(short_script, ensure_ascii=False)
    source_map_json = json.dumps(short_source_map, ensure_ascii=False) if short_source_map else "(none)"

    schema = (
        "{\n"
        '  "verdict": "PASS | FAIL",\n'
        '  "issues": [\n'
        "    {\n"
        '      "type": "hook | structure | cta | source_fidelity | safety | language | schema | style",\n'
        '      "severity": "major | minor",\n'
        '      "detail": "string"\n'
        "    }\n"
        "  ],\n"
        '  "required_changes": [\n'
        '    "string"\n'
        "  ],\n"
        '  "warnings": [\n'
        '    "string"\n'
        "  ],\n"
        '  "scores": {\n'
        '    "hook": 0,\n'
        '    "payoff": 0,\n'
        '    "funnel": 0,\n'
        '    "source_fidelity": 0,\n'
        '    "safety": 0\n'
        "  }\n"
        "}"
    )

    return (
        "You are the Shorts Script QA reviewer for a Spain-first wellness channel for adults aged 45+.\n\n"
        "Review the provided Short script against retention, style, safety, source fidelity, and readiness for scene generation.\n\n"
        f"SCRIPT:\n{script_json}\n\n"
        f"SOURCE MAP:\n{source_map_json}\n\n"
        "OUTPUT RULES:\n"
        "Return exactly ONE raw valid JSON object.\n"
        "No markdown fences.\n"
        "No commentary.\n"
        "No trailing commas.\n"
        "All strings must be valid JSON strings.\n\n"
        "QA RULES:\n"
        "- First 2 seconds must open with pain, curiosity, a number, or a common mistake.\n"
        "- No greeting.\n"
        "- No banned intro phrases such as \"hola\", \"bienvenidos\", \"en este short\", \"en este vídeo\", or \"hoy\".\n"
        "- Script must keep ONE main idea.\n"
        "- Payoff must appear before the CTA.\n"
        "- CTA must be short and 8 words or fewer.\n"
        "- No generic recap.\n"
        "- No medical overclaim.\n"
        "- No miracle promise.\n"
        "- No diagnosis, cure, or treatment claims.\n"
        "- No long disclaimer.\n"
        "- Spanish must be Spain-first es-ES.\n"
        "- Tone must be warm, direct, calm, and suitable for adults 45+.\n"
        "- Do not use \"ancianos\", \"abuelos\", \"seniors\", \"personas mayores\", or age-shaming language.\n"
        "- Source fidelity must be checked against SOURCE MAP when present.\n"
        "- If SOURCE MAP is missing, do not invent support. Add a warning.\n\n"
        "SCHEMA CHECK:\n"
        "The script should include:\n"
        "- short_id\n"
        "- source_long_job_id\n"
        "- short_format\n"
        "- target_duration_sec\n"
        "- hook\n"
        "- narration\n"
        "- beats\n"
        "- cta\n"
        "- qa.verdict\n\n"
        "PASS / FAIL RULES:\n"
        "Return \"PASS\" only if the script is ready to generate scenes.\n"
        "Return \"FAIL\" if regeneration is needed.\n"
        "FAIL if:\n"
        "- unsupported health claims appear\n"
        "- hook fails the first-2-seconds rule\n"
        "- script has more than one main idea\n"
        "- payoff is missing or appears only after CTA\n"
        "- CTA is longer than 8 words\n"
        "- Spanish is not es-ES\n"
        "- required JSON fields are missing\n"
        "- source fidelity cannot be confirmed for important claims when SOURCE MAP is present\n\n"
        "SCORING:\n"
        "Scores must be integers from 0 to 10.\n"
        "- hook: first-2-seconds strength and clarity\n"
        "- payoff: usefulness and placement before CTA\n"
        "- funnel: flow from hook to setup to payoff to CTA\n"
        "- source_fidelity: support from SOURCE MAP\n"
        "- safety: absence of overclaims or unsafe framing\n\n"
        f"RETURN JSON SCHEMA:\n{schema}\n"
    )


def gemini_scenes_qa_prompt(channel_config: dict, short_script: dict, short_scenes: dict) -> str:
    """Gemini QA validates vertical scene layout, mobile readability, and visuals."""
    script_json = json.dumps(short_script, ensure_ascii=False)
    scenes_json = json.dumps(short_scenes, ensure_ascii=False)

    schema = (
        "{\n"
        '  "verdict": "PASS | FAIL",\n'
        '  "issues": [\n'
        "    {\n"
        '      "type": "duration | scene_count | layout | text | caption | visual | safe_zone | source_fidelity | safety | language | schema",\n'
        '      "scene_id": "string or null",\n'
        '      "severity": "major | minor",\n'
        '      "detail": "string"\n'
        "    }\n"
        "  ],\n"
        '  "required_changes": [\n'
        '    "string"\n'
        "  ],\n"
        '  "warnings": [\n'
        '    "string"\n'
        "  ],\n"
        '  "scores": {\n'
        '    "funnel": 0,\n'
        '    "mobile_readability": 0,\n'
        '    "layout": 0\n'
        "  }\n"
        "}"
    )

    return (
        "You are the Shorts Scenes QA reviewer for a Spain-first wellness channel for adults aged 45+.\n\n"
        "Review whether the generated scenes are ready for vertical 9:16 rendering.\n\n"
        f"SCRIPT:\n{script_json}\n\n"
        f"SCENES:\n{scenes_json}\n\n"
        "OUTPUT RULES:\n"
        "Return exactly ONE raw valid JSON object.\n"
        "No markdown fences.\n"
        "No commentary.\n"
        "No trailing commas.\n"
        "All strings must be valid JSON strings.\n\n"
        "QA RULES:\n"
        "- total_duration_sec must be 20–45 seconds.\n"
        "- total_duration_sec must equal the sum of all scene duration_sec values.\n"
        "- Create 4–7 scenes total.\n"
        "- Each scene should be 3–8 seconds.\n"
        "- First scene layout must be \"short_hook\".\n"
        "- Last scene must be \"short_cta\" if the script has a CTA.\n"
        "- Allowed layouts:\n"
        "  - short_hook\n"
        "  - short_pain\n"
        "  - short_tip\n"
        "  - short_checklist\n"
        "  - short_myth\n"
        "  - short_quote\n"
        "  - short_cta\n"
        "  - graphic_plate_ratio (formula/ratio scenes)\n"
        "  - graphic_checklist (2-5 action items)\n"
        "  - graphic_step_list (2-4 numbered steps)\n"
        "- Do not allow long-form layouts. Do not allow any layout that is neither a\n"
        "  short_* layout nor one of the three allowed graphic_* layouts above.\n"
        "- For graphic_plate_ratio: layout_payload.segments must have 2-4 entries whose\n"
        "  values sum to 100.\n"
        "- For graphic_checklist: layout_payload.items must have 2-5 short entries.\n"
        "- For graphic_step_list: layout_payload.steps must have 2-4 {label,text} entries.\n"
        "- Use at most 2 graphic scenes per Short.\n"
        "- MISSING GRAPHIC (warning, not fail): if a scene's narration contains a\n"
        "  compact visualizable structure but uses a stock short_* layout, add a\n"
        "  warning suggesting the matching graphic layout. Trigger ONLY on real\n"
        "  structure: explicit fractions (1/2, 1/4, medio plato, un cuarto), a\n"
        "  percentage split (50%, 25%), 2+ checklist items under one instruction, a\n"
        "  numbered 2-4 step sequence, or a named rule plus concrete parts (\"regla\n"
        "  del plato\"). Do NOT trigger on the bare words lista/regla/paso/minutos\n"
        "  without that structure.\n"
        "- on_screen_text must be 2–5 words.\n"
        "- layout_payload.title must be 2–5 words.\n"
        "- captions must be short and fit within 2 lines.\n"
        "- caption must not duplicate long narration.\n"
        "- visual_prompt must be in English.\n"
        "- narration, caption, on_screen_text, and layout_payload must use es-ES.\n"
        "- visuals must match the script topic and scene narration.\n"
        "- visuals must be vertical-friendly for 9:16.\n"
        "- Primary text must stay in a mobile safe zone, not too low, not near the bottom UI area.\n"
        "- Visuals must be respectful toward adults 45+.\n"
        "- No frail, sick, helpless, mocked, or ashamed portrayal.\n"
        "- No scary medical imagery.\n"
        "- No hospital, pills, needles, organs, scans, or diagnosis visuals unless explicitly supported by the script.\n"
        "- Scenes must not add new health claims beyond the SCRIPT.\n"
        "- Scene narration must remain faithful to the SCRIPT.\n"
        "- source_scene_ids must not be invented.\n\n"
        "PASS / FAIL RULES:\n"
        "Return \"PASS\" only if the scenes are ready to render.\n"
        "Return \"FAIL\" if regeneration is needed.\n"
        "FAIL if:\n"
        "- total duration is outside 20–45 seconds\n"
        "- total_duration_sec does not equal the sum of scenes\n"
        "- scene count is outside the required range\n"
        "- first scene is not short_hook\n"
        "- CTA exists but last scene is not short_cta\n"
        "- any layout is not allowed\n"
        "- on_screen_text or title length violates rules\n"
        "- visual_prompt is not English\n"
        "- captions are too long\n"
        "- visuals do not match the topic\n"
        "- primary text is likely too low for Shorts UI\n"
        "- scenes add unsupported health claims\n"
        "- any scene contains unsafe or age-shaming visual framing\n\n"
        "SCORING:\n"
        "Scores must be integers from 0 to 10.\n"
        "- funnel: scene flow from hook to pain/setup to payoff to CTA\n"
        "- mobile_readability: text length, safe-zone placement, caption readability\n"
        "- layout: correct use of short layouts and visual-scene fit\n\n"
        f"RETURN JSON SCHEMA:\n{schema}\n"
    )
