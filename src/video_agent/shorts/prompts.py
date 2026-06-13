"""Strict, JSON-only, Spain-first prompts for Short script / scene / QA generation."""
from __future__ import annotations

import json
from typing import Any
from video_agent.contracts import TopicFamily, resolve_topic_family

from video_agent.shorts.idea_preservation import derive_idea_contract, derive_idea_items


def _get_topic_family_rules(topic: TopicFamily) -> str:
    if topic == TopicFamily.NUTRITION:
        return (
            "VIDA PLENA 45+ NUTRITION/BREAD POLISHING RULES:\n"
            "If this Short discusses bread, ingredients, or food habits:\n"
            "1. Hook scene:\n"
            "   - First scene visual_prompt MUST clearly show the food in a kitchen/table context.\n"
            "   - Reject abstract close-ups or generic footage.\n"
            "2. Visual Specificity:\n"
            "   - EVERY scene must clearly show the food or eating behavior.\n"
            "   - Reject generic cooking/eating footage without the specific food visible.\n"
            "3. Graphics:\n"
            "   - Prefer exactly 1-2 graphics. Use graphic_label_callout for ingredients and graphic_comparison for choices.\n"
        )
    elif topic == TopicFamily.MOVEMENT:
        return (
            "VIDA PLENA 45+ MOVEMENT/EXERCISE POLISHING RULES:\n"
            "1. Scene Evidence:\n"
            "   - Provide structured required_visual_evidence per scene.\n"
            "   - Example required_actions: ['standing', 'gentle stretching'], required_objects: ['chair', 'trainers visible'].\n"
            "2. Visual Rejections:\n"
            "   - Add strict forbidden_pose/context (e.g., ['lying in bed', 'seated silhouette only', 'frail portrayal']).\n"
            "3. Asset Strategy:\n"
            "   - For critical exercise action scenes, set asset_strategy = 'stock_ok' and visual_importance = 'critical'.\n"
            "   - Ensure source_scene_ids mapping is strictly populated from the source support if covers_items is present. DO NOT output [] for action scenes.\n"
        )
    return ""

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
    if not any(short_plan.get(key) for key in ("idea_id", "title", "hook_text", "viewer_pain", "narration_seed", "key_points")):
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
        "Use the selected hook_text as the script hook. Do not sensationalize or rewrite it into clickbait.\n"
        f"Viewer pain: {short_plan.get('viewer_pain', '')}\n"
        f"Practical payoff: {short_plan.get('practical_payoff', '')}\n"
        f"Format: {short_plan.get('format', '')}\n"
        f"Key points:\n{points_text}\n"
        f"Narration seed:\n{short_plan.get('narration_seed', '')}\n\n"
    )


def short_script_prompt(
    channel_config: dict,
    short_plan: dict,
    source_artifacts: dict | None = None,
    *,
    retention_plan: dict | None = None,
) -> str:
    fmt = short_plan.get("format", "pain_to_tip")
    target_duration_sec = short_plan.get("target_duration_sec") or 35
    seed = short_plan.get("narration_seed", "")
    idea_block = _idea_block(short_plan).strip() or "(none)"
    source_block_text = _source_block(source_artifacts or {}).strip() or "(none)"
    retention_plan_text = json.dumps(retention_plan or {}, ensure_ascii=False, indent=2)
    idea_contract = derive_idea_contract(short_plan)
    idea_items = derive_idea_items(short_plan, idea_contract)
    contract_blob = json.dumps(
        {"idea_contract": idea_contract, "idea_items": idea_items},
        ensure_ascii=False,
        indent=2,
    )

    expected_cta = "Vídeo completo en el canal."
    if source_artifacts and source_artifacts.get("funnel", {}).get("cta"):
        expected_cta = source_artifacts["funnel"]["cta"]
    elif short_plan.get("funnel", {}).get("cta"):
        expected_cta = short_plan["funnel"]["cta"]

    orig_count_val = idea_contract.get("original_count")
    final_count_val = idea_contract.get("final_count")
    orig_count_str = "null" if orig_count_val is None else str(orig_count_val)
    final_count_str = "null" if final_count_val is None else str(final_count_val)
    preserved_str = "true" if idea_contract.get("must_preserve_count") else "false"

    schema = (
        "{\n"
        '  "short_id": "string",\n'
        '  "source_long_job_id": "string or null",\n'
        f'  "short_format": "{fmt}",\n'
        f'  "target_duration_sec": {target_duration_sec},\n'
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
        '  "hook_pattern": "string",\n'
        '  "curiosity_gap": "string",\n'
        '  "micro_tension_lines": ["string"],\n'
        '  "identity_line": "string",\n'
        '  "comment_trigger": "string",\n'
        '  "idea_contract": {\n'
        f'    "preserved": {preserved_str},\n'
        f'    "original_count": {orig_count_str},\n'
        f'    "final_count": {final_count_str},\n'
        '    "adaptation_used": false,\n'
        '    "adaptation_reason": ""\n'
        "  },\n"
        '  "idea_items": [\n'
        "    {\n"
        '      "item_id": 1,\n'
        '      "label": "string",\n'
        '      "spoken_or_visual_role": "narration | on_screen_text | caption | layout_payload | visual_action",\n'
        '      "source_support": ["key_point_1"],\n'
        '      "required": true\n'
        "    }\n"
        "  ],\n"
        '  "source_mapped_flow": [\n'
        "    {\n"
        '      "item_id": 1,\n'
        '      "source_support": ["key_point_1"],\n'
        '      "spoken_summary": "string",\n'
        '      "visual_role": "string"\n'
        "    }\n"
        "  ],\n"
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
        "Create a 20–60 second Short with ONE main idea.\n\n"
        "INPUTS:\n"
        f"SHORT IDEA block:\n{idea_block}\n\n"
        f"SOURCE block:\n{source_block_text}\n\n"
        f"RETENTION PLAN:\n{retention_plan_text}\n\n"
        f"SOURCE NARRATION SEED:\n{seed}\n\n"
        f"IDEA PRESERVATION CONTRACT:\n{contract_blob}\n\n"
        "The selected idea is a viewer promise. If the idea title/hook/format contains a number, preserve that number in the final Short.\n"
        "Do NOT silently change 5 errores -> 2 errores, 5 errores -> errores comunes, or 3 pasos -> 2 pasos unless adaptation_allowed is explicitly true.\n"
        "If the idea seems too dense: compress each item to a micro-point, move detail to on-screen text/graphic payload later, allow content-led duration, or mark SPLIT_RECOMMENDED.\n"
        "But do not reduce the count without explicit approval.\n\n"
        "OUTPUT RULES:\n"
        "Return exactly ONE raw valid JSON object.\n"
        "No markdown fences.\n"
        "No commentary.\n"
        "No trailing commas.\n"
        "All strings must be valid JSON strings.\n"
        "CRITICAL FOR REWRITES: You MUST return the ENTIRE script from start to finish. Do NOT return just a partial fragment. Do NOT duplicate the exact same narration text across multiple different source_mapped_flow items.\n\n"
        "LANGUAGE RULES:\n"
        "Use es-ES.\n"
        "Speak to adults 45+ without using words like \"ancianos\", \"abuelos\", \"seniors\", \"personas mayores\", or age-shaming language.\n\n"
        "RETENTION RULES:\n"
        "The first 2 seconds must open with pain, curiosity, a number, or a common mistake.\n"
        "Use retention_plan.hook_pattern, curiosity_gap, payoff_promise, identity_resonance, and comment_trigger when present.\n"
        "Include at least two micro_tension_lines for Shorts longer than 20 seconds.\n"
        "Include one reflective comment/save trigger, not spammy engagement bait.\n"
        "No greeting.\n"
        "Do not say \"en este short\", \"en este vídeo\", \"hoy\", \"bienvenidos\", or \"hola\".\n"
        "Keep one main idea only.\n"
        "Deliver the payoff before the CTA.\n"
        f"CTA must be 8 words or fewer. You MUST include this exact phrase in the CTA: \"{expected_cta}\"\n"
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
        f"The target duration for this Short is {target_duration_sec} seconds. Pacing must be calm and warm.\n"
        f"For this {target_duration_sec}s Short, the spoken narration must be between {int(target_duration_sec * 1.7)} and {int(target_duration_sec * 2.0)} Spanish words (approximately {int(target_duration_sec * 1.8)} words is ideal). "
        f"Ensure that the visual beats time_sec blocks span from 0s to exactly {target_duration_sec}s.\n"
        "Use the calibrated Vida Plena voice budget: estimated Spanish WPS is 2.25.\n"
        "CHECKLIST POINT COUNT POLICY:\n"
        "If idea_contract.must_preserve_count=true, preserve the promised count; exact count uses original_count, range count uses idea_count_max as the upper bound.\n"
        "Do not reduce 5 errores to 3 or 4 only to satisfy a generic checklist rule.\n"
        "If there is no locked count, 3 spoken checklist points is ideal, 4 is a normal upper target, and 5+ may be too dense unless intentionally longer or split.\n"
        "For locked-count ideas, compact each item instead of reducing the number of items.\n"
        "Move supporting detail to on-screen text or graphic payload instead of narration when possible.\n\n"
        "SOURCE-MAPPED SCRIPTING RULES:\n"
        "idea_contract is authoritative. narration_seed functions as supportive context, not a strict ordinal override.\n\n"
        "COUNT AUTHORITY:\n"
        "Do NOT infer checklist count from narration_seed ordinal words (e.g. Primero, Segundo). "
        "Use ONLY idea_contract.original_count for the required checklist count.\n"
        "The source_mapped_flow array must contain exactly as many items as defined in idea_contract.original_count unless adaptation is explicitly allowed.\n\n"
        "CREATIVE FLEXIBILITY RULES:\n"
        "You may experiment with hooks, transitions, and emotional framing to maximize retention flow.\n"
        "For checklists/explainers: maintain logical progression.\n"
        "For stories: prioritize emotional arc.\n"
        "For myth vs. fact: use sharp contrasts.\n\n"
        "STRICT PRIORITY ORDER:\n"
        "1. Safety/Source fidelity\n"
        "2. idea_contract / source_mapped_flow\n"
        "3. Audio-fit/readability\n"
        "4. Retention creativity\n"
        "5. Style polish\n\n"
        "MANDATORY PRE-OUTPUT SELF-CHECK:\n"
        "- Verify that all points from idea_contract are present in source_mapped_flow.\n"
        "- Verify audio-fit is realistic.\n"
        "- Verify no items were dropped or merged.\n\n"
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
        "- Total 25-60 seconds.\n"
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


def short_seo_prompt(
    channel_config: dict,
    short_plan: dict,
    short_script: dict,
    long_video_url: str = "",
    *,
    retention_plan: dict | None = None,
    retry_feedback: str = "",
) -> str:
    hook = str(short_script.get("hook") or "").strip()
    narration = str(short_script.get("narration") or "").strip()
    cta = str(short_script.get("cta") or "").strip()
    idea_contract = short_script.get("idea_contract") or {}
    comment_trigger = ((retention_plan or {}).get("comment_trigger") or {}).get("question", "")
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

    feedback_block = (
        f"{retry_feedback.strip()}\n\n" if str(retry_feedback or "").strip() else ""
    )

    return (
        f"{feedback_block}"
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
        f"- Retention-plan comment trigger: {comment_trigger or '(none)'}\n\n"
        f"- Idea contract: {json.dumps(idea_contract, ensure_ascii=False)}\n\n"
        f"{_OUTPUT_RULES}\n"
        "SEO IDEA FIDELITY:\n"
        "- If the original idea promised 5 errores and final script preserved 5 errores, title/description may mention 5 errores.\n"
        "- If adaptation was explicitly approved and final script changed count, SEO must match final count.\n"
        "- Never publish title \"5 errores\" if the final video only covers 2.\n"
        "- Never publish title \"2 errores\" if the selected idea required 5 and adaptation_allowed is false.\n\n"
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
        "- For bread/pan-related Shorts, the title MUST match the final script format and viewer promise. Use an \"errores\"/mistake-list title ONLY when short_format is \"mistake_list\" OR the final hook/narration explicitly promises errores, and the video actually covers them.\n"
        "- For checklist / label-reading / purchase-rule / comparison bread Shorts, do NOT use an \"errores\" title. Prefer action/topic titles such as \"Gira el paquete: regla para comprar pan\", \"Pan después de los 45: mira la etiqueta\", or \"Qué mirar al comprar pan después de los 45\".\n"
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
        "- For bread/pan-related Shorts, hashtags MUST match the actual script topic. Use 3-5 tags; base options are #alimentacionsaludable, #comerpan, #vida45plus, #shorts. Pick the remaining tag from the real content: label-reading/package/ingredient list => #panintegral, #etiquetanutricional or #comprasaludable; plate/complete meal => #platosaludable; general nutrition => #nutricion. Do NOT force #nutricion when a more specific tag better matches the Short.\n"
        "- Every hashtag MUST be semantically tied to the actual script topic (mental health, sleep, nutrition, joints, balance, etc.).\n"
        "- FORBIDDEN unless the script is genuinely about that exact topic: #gym, #fitness, #workout, #crossfit, #musculacion, #pesas, #cardio, #abs, #motivation, #mindset, #shortsviral, #fyp, #parati, #viral, #foryou, #trending.\n"
        "- Prefer specific, topical Spain-Spanish wellness tags such as #saludmental, #bienestar, #descanso, #sueño, #mindfulness, #estres, #ansiedad, #alimentacionsaludable, #platosaludable, #nutricion, #vida45plus, #saludable, #autocuidado — but ONLY if they actually match the script.\n"
        "- For nutrition Shorts, prefer broad viewer-search terms like #alimentacionsaludable or #platosaludable over invented age-number nutrition hashtags.\n"
        "- Always include #shorts ONLY as a 5th hashtag at most; never as the first.\n\n"
        "PINNED COMMENT RULES:\n"
        "- Prefer the retention-plan comment trigger when it is reflective, safe, and genuinely tied to the Short.\n"
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
            "hook_pattern": "common_mistake",
            "viewer_pain": "...",
            "curiosity_gap": "...",
            "comment_trigger_type": "personal_experience",
            "identity_angle": "sin culpa después de los 45",
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
        "- Each selected Short SHOULD include hook_pattern, viewer_pain, curiosity_gap, comment_trigger_type, and identity_angle.\n"
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


def short_scene_prompt_v6(
    channel_config: dict,
    short_plan: dict,
    short_script: dict,
    feedback: str = "",
    *,
    retention_plan: dict | None = None,
    spoken_humanization: dict | None = None,
    topic: TopicFamily = TopicFamily.GENERAL,
) -> str:
    """Spec v6 §2.4 / §9.3 — ChatGPT chooses each scene's layout."""
    scene_script_context = {
        "short_id": short_script.get("short_id"),
        "short_format": short_script.get("short_format"),
        "hook": short_script.get("hook"),
        "narration": short_script.get("narration"),
        "cta": short_script.get("cta"),
        "idea_contract": short_script.get("idea_contract"),
        "idea_items": short_script.get("idea_items"),
        "source_mapped_flow": short_script.get("source_mapped_flow"),
    }
    script_json = json.dumps(scene_script_context, ensure_ascii=False)
    retention_json = json.dumps(retention_plan or {}, ensure_ascii=False)[:2000]
    humanization_json = json.dumps(spoken_humanization or {}, ensure_ascii=False)[:1600]
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
        '      "retention_function": "hook | tension | proof | payoff | identity | cta",\n'
        '      "rhythm_tag": "push | reveal | contrast | pause | payoff | comment",\n'
        '      "pattern_interrupt": "string",\n'
        '      "layout_payload": {\n'
        '        "title": "string",\n'
        '        "items": ["string"],\n'
        '        "emphasis": "string"\n'
        "      },\n"
        "      \"transition_from_previous\": \"string\",\n"
        "      \"covers_items\": [1],\n"
        "      \"source_scene_ids\": [],\n"
        '      "visual_importance": "critical | normal | bridge",\n'
        '      "asset_strategy": "stock_ok | ai_image_preferred | graphic_fallback",\n'
        '      "required_visual_evidence": {\n'
        '        "required_actions": ["string"],\n'
        '        "required_objects": ["string"],\n'
        '        "subject_pose": ["string"],\n'
        '        "visibility": ["string"],\n'
        '        "forbidden_pose": ["string"],\n'
        '        "forbidden_context": ["string"],\n'
        '        "forbidden_mood": ["string"]\n'
        "      }\n"
        "    }\n"
        "  ],\n"
        "  \"qa\": {\n"
        "    \"verdict\": \"PENDING_SCENES_QA\"\n"
        "  }\n"
        "}"
    )

    is_food_topic = topic in (TopicFamily.NUTRITION, TopicFamily.GENERAL)
    if not is_food_topic:
        topic_directive = (
            f"TOPIC = {topic.value}. This Short is NOT about bread, food, or food labels.\n"
            "Any bread / pan / food-label / supermarket / ingredient example in the rules below is\n"
            "illustrative for NUTRITION Shorts ONLY — IGNORE it here. Do not output bread visuals,\n"
            "graphic_label_callout, food CTAs (e.g. 'MÍRALO ANTES DE COMPRAR PAN'), or supermarket\n"
            f"hooks. Apply the {topic.value} rules below instead.\n\n"
        )
    else:
        topic_directive = ""

    return (
        "Turn this approved Short script into vertical 9:16 scenes for generation.\n\n"
        f"SCRIPT:\n{script_json}\n\n"
        f"RETENTION PLAN:\n{retention_json}\n\n"
        f"SPOKEN HUMANIZATION:\n{humanization_json}\n\n"
        f"RETRY FEEDBACK:\n{feedback_block}\n\n"
        "TASK:\n"
        "Create scene-by-scene visual instructions for a vertical YouTube Short.\n"
        "Do not rewrite the core message.\n"
        "Do not add new health claims.\n\n"
        f"{topic_directive}"
        "VISUAL QUALITY FIELDS (required per scene):\n"
        "- visual_importance: 'critical' for hook/payoff/key-item scenes, 'bridge' for connective scenes, else 'normal'.\n"
        "- asset_strategy: 'stock_ok' (default), 'ai_image_preferred' for hard-to-find specific visuals, 'graphic_fallback' for data/lists.\n"
        "- required_visual_evidence: for critical scenes, fill required_actions/required_objects/subject_pose/visibility plus forbidden_pose/forbidden_context/forbidden_mood. Leave lists empty when not relevant.\n\n"
        "RETENTION / RHYTHM REQUIREMENTS:\n"
        "- Align each scene with a retention beat when possible.\n"
        "- Add optional retention_function, rhythm_tag, and pattern_interrupt fields per scene.\n"
        "- Avoid slide-deck feel; vary motion naturally.\n"
        "- Use realistic Spanish supermarket/kitchen/home scenes as the base for lifestyle and health topics.\n"
        "- Keep text overlays short and readable.\n\n"
        "SCENE FLOW COHESION RULES:\n"
        "Ensure that each major beat has scene coverage, connected naturally via visual progression or transitions.\n"
        "Define transition_from_previous:\n"
        "  - For s01: transition_from_previous = \"START\" or \"\".\n"
        "  - For s02+: transition_from_previous must explain continuity from the previous scene.\n\n"
        "CREATIVE VISUAL RULES:\n"
        "Discourage repetitive scene construction; promote dynamic visuals and cinematic storytelling.\n"
        "Ensure visuals complement the spoken claim.\n\n"
        "PRE-OUTPUT SCENE SELF-CHECK:\n"
        "- Verify transition logic.\n"
        "- Verify concrete visuals.\n"
        "- Verify no missing source bridges.\n"
        "- Verify suitability for 45+ target demographic.\n\n"
        "NON-NEGOTIABLE LAYOUT BUDGET FOR THIS SHORT:\n"
        "This is a normal checklist/explainer Short, NOT graphic-led.\n"
        "You must output:\n"
        "- 5–8 scenes total.\n"
        "- MAXIMUM 2 graphic scenes.\n"
        "- For bread/food-label Shorts, prefer EXACTLY these two graphic moments:\n"
        "  1. graphic_label_callout for \"primer ingrediente\"\n"
        "  2. graphic_comparison for \"fibra / azúcar / jarabes\"\n"
        "Do NOT use graphic_checklist for: hook, setup, \"haz esta revisión\", recap, "
        "CTA, or \"no mires solo el color\".\n"
        "Those must be realistic short_* scenes: short_hook, short_myth, short_tip, "
        "short_checklist, short_cta.\n"
        "If you output 3 or more graphic scenes, the output is invalid.\n"
        "If you use graphic_checklist as a setup/recap scene in a bread-label Short, "
        "the output is invalid.\n"
        "The video must feel like a real Spain supermarket/kitchen Short with 1–2 "
        "helpful graphics, not a slide deck.\n\n"
        "SCRIPT FIDELITY & NARRATION:\n"
        "Preserve the SCRIPT meaning and source-supported claims, but do NOT copy long "
        "script phrases verbatim.\n"
        "If SCRIPT has idea_contract.original_count/final_count, preserve that count in scenes; all promised items must appear in a readable way and do not drop items silently.\n"
        "Each scene must include covers_items: an array of idea_items.item_id values covered by that scene. A scene may cover one or two promised items; do not cover more than 2 unless it is only a quick recap after all items were introduced.\n"
        "If narration is too dense, speak the short item label and move detail to caption, on_screen_text, visual action, or layout_payload.\n"
        "You may shorten scene.narration to fit timing if:\n"
        "- the core meaning stays faithful,\n"
        "- no new claim is added,\n"
        "- supporting detail moves to caption, on_screen_text, or layout_payload.\n"
        "Scene narration must sound natural in es-ES, not like keyword fragments.\n\n"
        "OUTPUT RULES:\n"
        "Return exactly ONE raw valid JSON object.\n"
        "No markdown fences.\n"
        "No commentary.\n"
        "No trailing commas.\n"
        "All strings must be valid JSON strings.\n\n"
        "SCENE COUNT & TIMING:\n"
        "- target_duration_sec is a soft planning target, not a hard pacing requirement.\n"
        "- Retention pacing is more important than exactly matching target_duration_sec.\n"
        "- Valid final duration is 20–60 sec; ideal final duration is 28–38 sec for most Shorts.\n"
        "- If good pacing results in 26–34 sec, do not stretch scenes.\n"
        "- Create 5–12 scenes by default when the idea contract needs the extra time.\n"
        "- For checklist/explainer Shorts, create 6–12 scenes when the idea contract needs it.\n"
        "- For simple hook-tip-CTA Shorts, create 4–6 scenes.\n"
        "- Never create a 7–12 sec scene to hit the target.\n"
        "- Keep the CTA scene tight: 2.0–2.6 sec ideal, 2.8 sec hard max; a 3–5 word CTA should be 2.2–2.5 sec.\n"
        "- Keep simple one-line tip scenes 3.2–4.0 sec ideal, 4.5 sec hard max; use 4.5 sec only when the visual action needs it.\n"
        "- Normal lifestyle scenes should be short: hook/opening 1.8–2.8 sec hard max 3.0; myth/setup 2.0–3.0 hard max 3.2; tip/lifestyle reinforcement 2.2–4.2 hard max 5.0; short_checklist 3.0–4.5 hard max 5.0; CTA 1.8–2.6 hard max 2.8.\n"
        "- If a narration beat is too long for one scene, split it into two scenes with different on_screen_text.\n"
        "- Do not repeat the same idea or title for too long.\n"
        "- total_duration_sec must equal the sum of all scene duration_sec values.\n"
        "- Last scene SHOULD use layout \"short_cta\" if CTA is present.\n\n"
        "SCENE NARRATION COMPRESSION & WORD CAPS:\n"
        "- Keep the message faithful, but compress individual scene narration to fit the calibrated voice timing.\n"
        "- You may shorten scene.narration for timing if the meaning stays faithful and source-supported.\n"
        "- Move details to caption, on_screen_text, or layout_payload callouts/items.\n"
        "- Do not invent new claims, and do not remove the core payoff.\n"
        "- Do not copy long script narration beats verbatim if they exceed the word caps below.\n"
        "- SCENE NARRATION WORD CAPS:\n"
        "  - short_hook: max 5–6 spoken words (e.g., \"¿Pan marrón? No basta.\")\n"
        "  - short_myth/setup: max 6–7 spoken words\n"
        "  - short_tip: max 8–10 spoken words\n"
        "  - short_checklist: max 8–10 spoken words\n"
        "  - graphic_checklist: max 6–8 spoken words\n"
        "  - graphic_label_callout: max 7–9 spoken words\n"
        "  - graphic_comparison: max 7–9 spoken words\n"
        "  - short_quote: max 8–10 spoken words\n"
        "  - short_cta: max 4–5 spoken words\n\n"
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
        "When a scene teaches a formula, ratio, checklist, numbered steps, label-reading,\n"
        "a two-choice comparison, or a time-block routine that the\n"
        "viewer should remember visually, use ONE of these instead of generic footage:\n"
        "- graphic_plate_ratio  -> plate split formulas (1/2, 1/4, 50%, 25%).\n"
        "- graphic_checklist    -> 2-5 short action items under one instruction.\n"
        "- graphic_step_list    -> 2-4 numbered steps.\n"
        "- graphic_label_callout -> label-reading moments (fibra, azúcares, sal, proteína, por 100 g).\n"
        "- graphic_comparison   -> two choices such as MEJOR vs CUIDADO without fear language.\n"
        "- graphic_routine_split -> time blocks such as 10 min + 10 min + 10 min.\n"
        "Use a graphic only for the highest-value knowledge moment. Most scenes stay\n"
        "stock short_* layouts. Graphic scenes are explanatory bursts, not slides:\n"
        "make one idea visually clear in 2.5–5.0 seconds. If a graphic needs more\n"
        "than 5 seconds, split it into two scenes or simplify the text.\n"
        "Graphic duration targets and hard caps:\n"
        "- graphic_checklist: target 3.0–4.0 sec, hard max 4.5 sec.\n"
        "- graphic_step_list: target 3.0–4.0 sec, hard max 4.5 sec.\n"
        "- graphic_plate_ratio: target 3.0–4.5 sec, hard max 5.0 sec.\n"
        "- graphic_label_callout: target 3.5–5.0 sec, hard max 5.0 sec.\n"
        "- graphic_comparison: target 3.5–4.5 sec, hard max 5.0 sec.\n"
        "- graphic_routine_split: target 3.5–5.0 sec, hard max 5.0 sec.\n"
        "HARD RULE — GRAPHIC COUNT:\n"
        "- A normal Short uses a MAXIMUM of 2 graphic scenes. This is a hard cap, not a preference.\n"
        "- Being a checklist/explainer does NOT make a Short graphic-led. Checklists are normal Shorts: still max 2 graphics.\n"
        "- Use 3 graphics ONLY when the INPUT explicitly says the Short is graphic-led (e.g. \"graphic-led\" / \"graphic_led\" in the idea/plan). Otherwise never exceed 2.\n"
        "- Bread/food-label Shorts keep REALISTIC supermarket/kitchen footage as the base (hands holding bread, reading a label in the aisle, the shopping basket). Graphics are accents on top of real scenes, not the spine of the Short.\n"
        "- Pick the 1–2 HIGHEST-VALUE knowledge moments for graphics: prefer graphic_label_callout for \"primer ingrediente\" and graphic_comparison for \"fibra / azúcar / jarabes\". Render setup/recap beats (the checklist intro, \"haz esta revisión\") as realistic short_tip or short_myth scenes, NOT graphic_checklist.\n"
        "If 2 graphics already exist, do not add a third just because another scene contains label/checklist terms; improve the stock visual_prompt instead.\n"
        "No single graphic scene should occupy more than about 12% of a normal Short.\n"
        "For graphic scenes, the title and first meaningful content must be visible within the first 0.5 sec, and by 1.0 sec the viewer should understand the main point.\n"
        "Do not create graphic scenes where the card/frame appears first and useful text appears much later.\n"
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
        "graphic_label_callout: {\"title\": \"MIRA PRIMERO\", \"productLabel\": \"Pan integral\", "
        "\"callouts\": [{\"label\": \"Fibra\", \"value\": \"6 g\", \"note\": \"mejor saciedad\"}, "
        "{\"label\": \"Azúcares\", \"value\": \"3 g\", \"note\": \"por 100 g\"}], "
        "\"footer\": \"El color no basta. Mira la etiqueta.\"} (2-4 callouts).\n"
        "graphic_comparison: {\"title\": \"EN EL SÚPER\", \"left\": {\"heading\": \"MEJOR\", "
        "\"text\": \"Integral con buena fibra\", \"badge\": \"Más saciante\"}, "
        "\"right\": {\"heading\": \"CUIDADO\", \"text\": \"Oscuro, pero sin grano integral\", "
        "\"badge\": \"Lee etiqueta\"}, \"footer\": \"No te quedes solo con el color.\"} "
        "(avoid veneno/prohibido/nunca/milagro/cura/doctores no quieren).\n"
        "graphic_routine_split: {\"title\": \"RUTINA 30 MINUTOS\", \"totalLabel\": \"30 min\", "
        "\"blocks\": [{\"time\": \"10 min\", \"text\": \"Cerrar el día\"}, "
        "{\"time\": \"10 min\", \"text\": \"Preparar dormitorio\"}, "
        "{\"time\": \"10 min\", \"text\": \"Respirar y bajar ritmo\"}], "
        "\"footer\": \"No hace falta hacerlo perfecto.\"} (2-4 time blocks).\n"
        "Graphic layout_payload may optionally include visual fields:\n"
        "- variant: brand_default | warm_olive | soft_clay | cream_focus | evening_calm\n"
        "- visual_tone: calm | focus | warning_soft | positive | evening\n"
        "- background_mode: clean | radial | paper | video_blur\n"
        "- surface_style: none | soft_card | editorial | plate_focus\n"
        "These visual fields are optional. Use them only when they improve clarity or tone.\n"
        "If unsure, omit them and the renderer will choose safe defaults.\n"
        "Use visual variants deliberately so consecutive graphics do not look identical.\n"
        "Suggested visual mapping: nutrition/plate -> warm_olive + radial + plate_focus; "
        "ingredient or busca integral checklist -> warm_olive + paper + soft_card; "
        "label reading -> cream_focus + paper + soft_card; comparison/caution -> warning_soft "
        "+ soft_clay + radial + soft_card; sleep/routine -> evening_calm + calm + paper + editorial.\n"
        "Keep graphic title 2-5 words, es-ES. Prefer max 4 words when possible. "
        "Checklist items: 2–4 preferred, 5 max. Label callouts: 2–3 preferred, 4 max. "
        "Footer is optional and should be max 8–10 words.\n\n"
        "Use graphic_label_callout when the scene teaches label reading: etiqueta, ingredientes, fibra,\n"
        "azúcares, sal, proteína, por 100 g.\n"
        "Use graphic_comparison when the scene compares two choices: mejor/cuidado, opción A vs opción B,\n"
        "más saciante/menos saciante.\n"
        "Use graphic_routine_split when the scene splits a routine into time blocks: 10 min, 20 min,\n"
        "30 min, cerrar el día, preparar dormitorio, respirar.\n\n"
        "Do NOT use graphics for emotional setup, lifestyle mood, simple transition, generic \"tip práctico\", or short CTA.\n"
        "For bread/food-label Shorts, a good sequence is: realistic hook with bread/label in hand; short_myth or short_tip setup (NOT a graphic); graphic_label_callout for the first ingredient; realistic supermarket reinforcement; graphic_comparison for fibra/azúcar/jarabes; action CTA. That is exactly 2 graphics on a realistic base.\n\n"
        "PRODUCT ATTRACTIVENESS RULES:\n"
        "- The scene plan must be attractive for Spanish adults 45+.\n"
        "- Use concrete practical visuals, no abstract filler.\n"
        "- Use warm Spain-first lifestyle context.\n"
        "- Keep text readable and calm.\n"
        "- No shame, fear, frailty, or generic stock when a concrete supermarket/kitchen/label image is possible.\n\n"
        "TEXT RULES:\n"
        "- on_screen_text: 2–5 words.\n"
        "- layout_payload.title: 2–5 words.\n"
        "- caption: short subtitle-style text, maximum 12 words.\n"
        "- No long bottom subtitle paragraphs.\n"
        "- Do not duplicate the full narration as on-screen text.\n"
        "- Use es-ES for narration, caption, on_screen_text, and layout_payload.\n"
        "- visual_prompt must be in English.\n\n"
        "MYTH / SETUP RULES:\n"
        "- A myth/setup scene must be short and specific, max 3.0 sec.\n"
        "- Avoid a long standalone scene that only says \"MITO RÁPIDO\".\n"
        "- Prefer concrete text like MITO: SI ES MARRÓN, ES INTEGRAL, then immediately follow with REALIDAD: MIRA INGREDIENTES or transition into a graphic.\n"
        "- Do not keep the same on_screen_text for 5+ seconds.\n\n"
        "CTA RULES:\n"
        "- CTA duration: 1.8–2.6 sec.\n"
        + ("- Prefer action-oriented Spanish CTAs: GUARDA ESTA LISTA, GUÁRDALO PARA LA COMPRA, MÍRALO ANTES DE COMPRAR PAN, ÚSALO EN EL SÚPER.\n"
           if is_food_topic else
           "- Prefer action-oriented Spanish CTAs that fit the topic (GUARDA ESTA LISTA, EMPIEZA HOY, PRUÉBALO MAÑANA). Do NOT use bread/supermarket CTAs.\n") +
        "- Avoid passive/status CTAs: CHECKLIST GUARDADA, LISTA COMPLETA, FIN, CONSEJO FINAL.\n"
        "- CTA should not be a long graphic scene unless it is the whole point of the video.\n\n"
        "VISUAL RULES:\n"
        "- visual_prompt must be vertical-friendly for 9:16.\n"
        "- Prefer simple realistic scenes: close-up face, hands, kitchen, supermarket aisle, bed, yoga mat, chair, walking, daily routine.\n"
        "- The first visual must clearly show the topic object or core action/context of THIS Short.\n"
        + ("- For bread-label topics, the hook visual_prompt should clearly include bread, a bread package, ingredient label, supermarket bread shelf, hand comparing bread packages, or shopping basket with pan integral.\n"
           "- Avoid food-label hooks that are abstract close-ups, generic kitchen shots, generic person eating, unrecognizable food texture, or footage that does not immediately say bread/label/supermarket.\n"
           if is_food_topic else
           "- For movement/exercise topics, the hook must show a capable 45+ adult standing or moving (gentle stretch, chair-supported exercise, walking) with trainers/chair visible. Never frail, bedbound, or medical mobility aids (rollator/walker/wheelchair).\n") +
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
        f"{_get_topic_family_rules(topic)}\n"        "FINAL SELF-CHECK BEFORE RETURNING JSON:\n"
        "- scenes.length is between 5 and 8 for this normal checklist Short.\n"
        "- graphic scene count is 0, 1, or 2. Never 3+.\n"
        "- bread-label setup/recap scenes are realistic short_* scenes, not graphic_checklist.\n"
        "- total_duration_sec equals the sum of duration_sec.\n"
        "- first scene is short_hook.\n"
        "- last scene is short_cta.\n"
        "- no scene narration exceeds the word cap for its layout.\n"
        "- output is valid JSON and contains a non-empty scenes array.\n\n"
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
        "- duration 20-60s; first 2s = pain/curiosity/number/mistake; no greeting.\n"
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


def gemini_script_qa_prompt(
    channel_config: dict,
    short_script: dict,
    short_source_map: dict | None = None,
    original_idea: dict | None = None,
) -> str:
    """Gemini QA validates script quality, language, and safety."""
    script_json = json.dumps(short_script, ensure_ascii=False)
    source_map_json = json.dumps(short_source_map, ensure_ascii=False) if short_source_map else "(none)"
    original_idea_json = json.dumps(original_idea or {}, ensure_ascii=False) if original_idea else "(none)"

    schema = (
        "{\n"
        '  "verdict": "PASS | FAIL",\n'
        '  "issues": [\n'
        "    {\n"
        '      "type": "hook | structure | cta | source_fidelity | source_support | idea_fidelity | safety | language | schema | style",\n'
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
        '    "safety": 0,\n'
        '    "hook_specificity": 0,\n'
        '    "micro_tension": 0,\n'
        '    "human_naturalness": 0,\n'
        '    "visual_rhythm": 0,\n'
        '    "identity_resonance": 0,\n'
        '    "commentability": 0\n'
        "  }\n"
        "}"
    )

    return (
        "You are the Shorts Script QA reviewer for a Spain-first wellness channel for adults aged 45+.\n\n"
        "Review the provided Short script against retention, style, safety, source fidelity, and readiness for scene generation.\n\n"
        f"ORIGINAL IDEA:\n{original_idea_json}\n\n"
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
        "- Score hook_specificity, micro_tension, human_naturalness, visual_rhythm, identity_resonance, and commentability on a 0-100 scale.\n"
        "- If SOURCE MAP is missing, do not invent support. Add a warning.\n\n"
        "IDEA COUNT FIDELITY:\n"
        "- If the selected original idea promises a number of items, the script must preserve that number unless adaptation_allowed is true.\n"
        "- FAIL with idea_fidelity if original idea says 5 errores but script only covers 2 errores, or 3 pasos becomes 2 pasos.\n"
        "- Do not suggest reducing the count as the first repair. Suggest micro-compressing each item, moving detail to on-screen text, content-led duration, or split_recommended.\n"
        "- Source support must be explicit: each promised item needs at least one valid source_support reference and labels must be meaningfully different.\n"
        "- The source_mapped_flow array must contain exactly as many items as defined in idea_contract.original_count unless adaptation is explicitly allowed.\n"
        "- Do not trust only preserved=true; verify final_count, idea_items length, and planned narration/visual representation.\n\n"
        "COUNT AUTHORITY:\n"
        "- Use ONLY idea_contract.original_count as the required checklist count when present.\n"
        "- Do NOT infer checklist count from narration_seed ordinal wording.\n"
        "- narration_seed may contain expanded source context, not the locked final item count.\n"
        "- If narration_seed has more or fewer ordinal words (e.g. Primero, Segundo, Tercero, Cuarto, Quinto) than idea_contract.original_count, it must NOT fail QA or produce a HARD_BLOCKER. It may only produce a WARN when count_source=\"key_points\".\n"
        "- Validate source_mapped_flow against idea_items/key_points. Do not invent a missing point.\n"
        "- FAIL only if source_mapped_flow drops, merges, or splits required idea_items incorrectly.\n\n"
        "WORD-BUDGET / AUDIO-FIT RULES:\n"
        "- Estimate spoken duration using calibrated Spanish WPS 2.25 plus sentence pauses.\n"
        "- For a 35s Short, 60–70 spoken Spanish words is the normal budget.\n"
        "- For a 30s Short, 50–60 words is the normal budget.\n"
        "- For a 60s hard-max Short, 115–125 words is the upper budget unless explicitly requested.\n"
        "- Do not fail only because duration exceeds the old 38s preference; warn if the script is longer but still engaging and structurally clear.\n"
        "- FAIL if the script is rushed, repetitive, unclear, unsupported, unsafe, audio-fit impossible, or poor product quality.\n"
        "- CHECKLIST POINT COUNT QA POLICY: if idea_contract.must_preserve_count=true, exact count allows original_count and range count allows idea_count_max; fail only for rushed/unreadable/unsupported/unsafe/audio-fit impossible execution.\n"
        "- If must_preserve_count=false, 3 points is ideal, 4 is a normal upper target, and 5+ may fail if it hurts clarity, pacing, or audio-fit.\n"
        "- Repair by compacting each item, moving detail to visuals, or recommending split_recommended. Do not ask to reduce the promised count unless adaptation_allowed=true.\n\n"
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
        "- narration is audio-fit impossible or rushed beyond practical Short quality\n\n"
        "SCORING:\n"
        "Scores must be integers from 0 to 10.\n"
        "- hook: first-2-seconds strength and clarity\n"
        "- payoff: usefulness and placement before CTA\n"
        "- funnel: flow from hook to setup to payoff to CTA\n"
        "- source_fidelity: support from SOURCE MAP\n"
        "- safety: absence of overclaims or unsafe framing\n"
        "- hook_specificity, micro_tension, human_naturalness, visual_rhythm, identity_resonance, commentability: 0-100 quality upgrade scores\n\n"
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
        '      "type": "duration | scene_count | layout | text | caption | visual | safe_zone | source_fidelity | safety | language | schema | product_quality_score_low | product_quality_average_low | product_quality_scores_missing",\n'
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
        '  "product_scores": {\n'
        '    "audience_fit_45_plus": 0,\n'
        '    "hook_strength": 0,\n'
        '    "visual_specificity": 0,\n'
        '    "clarity": 0,\n'
        '    "retention_pacing": 0,\n'
        '    "natural_spanish": 0,\n'
        '    "saveability": 0\n'
        '  },\n'
        '  "quality_scores": {\n'
        '    "hook_specificity": 0,\n'
        '    "micro_tension": 0,\n'
        '    "human_naturalness": 0,\n'
        '    "visual_rhythm": 0,\n'
        '    "identity_resonance": 0,\n'
        '    "commentability": 0\n'
        '  }\n'
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
        "VISUAL QUALITY FIELDS:\n"
        "Each scene should carry visual_importance (critical|normal|bridge), asset_strategy "
        "(stock_ok|ai_image_preferred|graphic_fallback) and, for critical scenes, a structured "
        "required_visual_evidence dict (required_actions/required_objects/subject_pose/visibility/"
        "forbidden_pose/forbidden_context/forbidden_mood). Flag a 'visual' issue when a critical "
        "scene is missing usable required_visual_evidence or its visual_prompt contradicts it.\n\n"
        "PRODUCT QUALITY SCORES:\n"
        "Return product_scores with integer scores from 0 to 10:\n"
        "- audience_fit_45_plus\n"
        "- hook_strength\n"
        "- visual_specificity\n"
        "- clarity\n"
        "- retention_pacing\n"
        "- natural_spanish\n"
        "- saveability\n\n"
        "Also return quality_scores from 0 to 100: hook_specificity, micro_tension, human_naturalness, visual_rhythm, identity_resonance, and commentability.\n\n"
        "Scoring scale:\n"
        "10 = excellent for a Spain-first 45+ Shorts audience\n"
        "8 = strong and publishable\n"
        "7 = acceptable but should be improved if possible\n"
        "6 or below = not good enough for this channel\n\n"
        "If any product score is below 7, provide a concrete repair instruction.\n"
        "If average product score is below 8, provide a concrete repair instruction.\n"
        "Do not fail for layout preference alone, such as \"could be graphic\", unless the current scene is unclear, unreadable, off-topic, misleading, or unattractive for Spanish adults 45+.\n\n"
        "- You may comment on pacing quality, but deterministic validator is authoritative for numeric caps and arithmetic.\n"
        "- Do not FAIL solely for a numeric threshold if deterministic validation says it is valid.\n"
        "- Do not create major issues or required_changes for values that are inside the accepted numeric ranges. Use warnings only for render/visual verification notes such as \"acceptable but verify\".\n"
        "- Focus on product quality, visual fit, source fidelity, tone, clarity, retention, and audio_fit_risk.\n"
        "- target_duration_sec is a soft planning target; do not ask to stretch scenes to exactly 35 sec.\n"
        "- A 28–34 sec Short can be acceptable when pacing is strong and narration audio fits.\n"
        "- Scene count policy: 5–8 scenes by default, 6–9 for checklist/explainer, 4–6 for simple hook-tip-CTA.\n"
        "- Normal lifestyle scene timing guidance: hook/opening 1.8–2.8 sec; myth/setup 2.0–3.0 sec; tip/lifestyle reinforcement 2.2–4.2 sec; CTA 1.8–2.6 sec.\n"
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
        "  - graphic_label_callout (label-reading scenes)\n"
        "  - graphic_comparison (two-choice comparison scenes)\n"
        "  - graphic_routine_split (time-block routine scenes)\n"
        "- Do not allow long-form layouts. Do not allow any layout that is neither a\n"
        "  short_* layout nor one of the six allowed graphic_* layouts above.\n"
        "- For graphic_plate_ratio: layout_payload.segments must have 2-4 entries whose\n"
        "  values sum to 100.\n"
        "- For graphic_checklist: layout_payload.items must have 2-5 short entries.\n"
        "- For graphic_step_list: layout_payload.steps must have 2-4 {label,text} entries.\n"
        "- For graphic_label_callout: layout_payload.callouts must have 2-4 {label,value,note?} entries.\n"
        "- For graphic_comparison: layout_payload.left/right must be objects with heading/text and must avoid veneno, prohibido, nunca, milagro, cura, and \"doctores no quieren\".\n"
        "- For graphic_routine_split: layout_payload.blocks must have 2-4 {time,text} entries.\n"
        "- Do not reject graphic scenes for omitting variant, visual_tone, background_mode, or surface_style; these fields are optional.\n"
        "- If graphic visual fields are present, reject invalid values. Allowed variant values: brand_default, warm_olive, soft_clay, cream_focus, evening_calm. Allowed visual_tone values: calm, focus, warning_soft, positive, evening. Allowed background_mode values: clean, radial, paper, video_blur. Allowed surface_style values: none, soft_card, editorial, plate_focus.\n"
        "- Reject present visual fields only if they are invalid, unreadable, or visually inconsistent with Vida Plena 45+.\n"
        "- Graphic scenes are explanatory bursts, not slides. Fail normal Shorts with any graphic scene longer than 5.0 sec.\n"
        "- Fail graphic_checklist or graphic_step_list scenes longer than 4.5 sec.\n"
        "- Flag any single graphic scene that occupies more than 12–15% of a normal Short, unless the Short is explicitly graphic-led.\n"
        "- Use at most 2 graphic scenes per normal Short; flag 3+ graphics unless intentionally graphic-led.\n"
        "- For graphic scenes, title and first meaningful content should be visible within about 0.5 sec; by 1.0 sec the main point should be understandable.\n"
        "- Warn if payload density is too high for duration, e.g. graphic_label_callout with 4 callouts in 3 sec or graphic_checklist with 5 items in 3 sec.\n"
        "- Request revision if two consecutive scenes repeat the same idea or the same on_screen_text appears for too long.\n"
        "- Request revision if a generic setup like MITO RÁPIDO takes more than 3 sec; prefer a specific myth statement and quick transition.\n"
        "- For food-label Shorts, fail a generic hook visual if the topic object is not obvious. For bread-label topics, the hook visual_prompt must include bread, bread package, ingredient label, supermarket bread shelf, shopping basket, or similar concrete bread/label imagery.\n"
        "- Warn if CTA text is passive/status-like. Replace CHECKLIST GUARDADA with GUARDA ESTA LISTA or GUÁRDALO PARA LA COMPRA.\n"
        "- MISSING GRAPHIC is warning only.\n"
        "- Do not fail a Short for missing graphic if the Short already has 2 graphics; suggest improving the stock visual_prompt instead.\n"
        "- MISSING GRAPHIC (warning, not fail): if a scene's narration contains a\n"
        "  compact visualizable structure but uses a stock short_* layout, add a\n"
        "  warning suggesting the matching graphic layout. Trigger ONLY on real\n"
        "  structure: explicit fractions (1/2, 1/4, medio plato, un cuarto), a\n"
        "  percentage split (50%, 25%), 2+ checklist items under one instruction, a\n"
        "  numbered 2-4 step sequence, a named rule plus concrete parts (\"regla\n"
        "  del plato\"), label-reading terms (etiqueta, fibra, azúcares, sal, proteína,\n"
        "  por 100 g), two-choice comparisons (mejor/cuidado, opción A vs opción B),\n"
        "  or routine time blocks. Do NOT trigger on the bare words lista/regla/paso/minutos\n"
        "  without that structure. Suggested fixes can include convert_to_graphic_label_callout,\n"
        "  convert_to_graphic_comparison, or convert_to_graphic_routine_split.\n"
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
        "- source_scene_ids must not be invented.\n"
        "- Evaluate scene flow against source_mapped_flow from the script if available.\n"
        "- Use transition_from_previous as evidence of cohesion.\n"
        "- Distinguish 'generic lifestyle filler' from an 'intentional bridge'.\n"
        "- Do not require visual complexity if a graphic_fallback/card is clearer for the viewer.\n"
        "- Hard-fail only when flow is genuinely disconnected or key visual context is missing.\n\n"
        "VIDA PLENA 45+ BREAD/5 ERRORS SHORT POLISHING QA RULES:\n"
        "If this Short is a 5-error bread Short for Vida Plena 45+ (e.g. title/hook mentions '5 errores' or 'pan'), apply these strict quality criteria.\n"
        "Use these as polish targets, not unconditional FAIL thresholds. Actual hard block is decided by Python tiered gates:\n"
        "1. Hook scene:\n"
        "   - Must use two-line hook format: title \"NO ES EL PAN\" and subtitle \"MIRA CÓMO LO USAS\" or \"SON 5 HÁBITOS\".\n"
        "   - First visual must clearly show bread in kitchen/table context.\n"
        "2. Pacing & Durations:\n"
        "   - Total duration must be 26.0–30.0s (never under 25.5s).\n"
        "   - Error scenes: 3.2–4.0s. If s02–s06 are 3.6s, they are inside range and must not cause FAIL.\n"
        "   - Payoff scene: 4.2–5.0s.\n"
        "   - CTA: 2.4–2.8s. If s08 is 2.6s, it is inside range and must not cause FAIL.\n"
        "3. On-screen text labels:\n"
        "   - Generic \"ERROR 1/2/3...\" text is forbidden. Fail it. Must use specific uppercase labels: \"DE PIE\", \"SUMAR SIN DECIDIR\", \"BARRA A LA VISTA\", \"CANSANCIO\", \"CENA IMPROVISADA\".\n"
        "4. Payoff Scene (s07):\n"
        "   - Must use compact 'graphic_checklist' with title \"MEJOR ASÍ\" and checklist items: \"Porción visible\", \"Plato pequeño\", \"Comida completa\". Do not use short_quote, graphic_routine_split, short_checklist, or plate ratio.\n"
        "5. Visual Specificity:\n"
        "   - Every scene must clearly show bread or bread-related behavior. Reject generic cooking/eating footage without bread visible.\n"
        "6. Caption Zone & Safe Zone:\n"
        "   - Captions must be under 9 words to prevent Shorts UI overlap.\n"
        "7. CTA:\n"
        "   - Must use on_screen_text \"GUÁRDALO\" and caption \"PARA TU PRÓXIMA CENA\" (duration 2.4-2.8s).\n"
        "8. Visual Styling:\n"
        "   - Footage must feel warm and bright (cream/olive wellness tone). Reject heavy dark cinematic overlays.\n"
        "9. Strict Product Score Thresholds (Polish Targets):\n"
        "   - hook_strength: >= 9\n"
        "   - clarity: >= 9\n"
        "   - retention_pacing: >= 9\n"
        "   - visual_specificity: >= 9\n"
        "   - audience_fit_45_plus: >= 9\n"
        "   - natural_spanish: >= 9\n"
        "   - saveability: >= 8.5\n\n"
        "PASS / FAIL RULES:\n"
        "Return \"PASS\" only if the scenes are ready to render.\n"
        "Return \"FAIL\" if regeneration is needed.\n"
        "Do not return FAIL for an acceptable value that merely needs visual/render verification; put that note in warnings.\n"
        "FAIL if:\n"
        "- safety/source/layout hard errors appear\n"
        "- visuals do not match the topic\n"
        "- hook is generic enough to hurt retention\n"
        "- text is unreadable\n"
        "- pacing has a hard structural issue confirmed by validator\n"
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
        "- audience_fit: Spain-first, respectful, useful for adults 45+\n"
        "- retention_pacing: no slideshow feel, no long-held repeated idea\n"
        "- visual_specificity: concrete topic visuals instead of generic stock\n"
        "- clarity_45_plus: readable, calm, easy to follow\n"
        "- audio_fit_risk: whether narration density appears likely to overflow the planned duration\n\n"
        f"RETURN JSON SCHEMA:\n{schema}\n"
    )


def gemini_vision_qa_prompt(scene: dict) -> str:
    """Vision QA prompt: validate one asset frame against the scene's
    required_visual_evidence. Strict PASS/FAIL JSON schema (plan Task 5)."""
    evidence = scene.get("required_visual_evidence") or {}
    evidence_json = json.dumps(evidence, ensure_ascii=False, indent=2)
    schema = (
        "{\n"
        '  "verdict": "PASS | FAIL",\n'
        '  "missing_evidence": ["string"],\n'
        '  "forbidden_violations": ["string"],\n'
        '  "confidence": 0.0,\n'
        '  "reason": "string"\n'
        "}"
    )
    return (
        "You are a strict visual QA reviewer for a Spain-first wellness channel (audience 45+).\n"
        "Inspect the attached image frame, which is a candidate background for one Short scene.\n\n"
        f"SCENE visual_prompt:\n{scene.get('visual_prompt') or ''}\n\n"
        f"REQUIRED VISUAL EVIDENCE:\n{evidence_json}\n\n"
        "RULES:\n"
        "- FAIL if any required_actions / required_objects / subject_pose / visibility entry is not clearly visible.\n"
        "- FAIL if any forbidden_pose / forbidden_context / forbidden_mood entry IS visible.\n"
        "- List every missing requirement in missing_evidence and every violated forbidden entry in forbidden_violations.\n"
        "- confidence is your 0.0-1.0 certainty in the verdict.\n\n"
        "OUTPUT RULES:\n"
        "Return exactly ONE raw valid JSON object. No markdown fences. No commentary.\n\n"
        f"RETURN JSON SCHEMA:\n{schema}\n"
    )
