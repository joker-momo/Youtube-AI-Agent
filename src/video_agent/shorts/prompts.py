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
    return "SOURCE (rewrite faithfully, do not invent):\n" + json.dumps(source_artifacts, ensure_ascii=False)[:2000] + "\n\n"


def short_script_prompt(channel_config: dict, short_plan: dict, source_artifacts: dict | None = None) -> str:
    schema = {
        "short_id": short_plan.get("short_id", "short-01"),
        "source_long_job_id": short_plan.get("source_long_job_id", ""),
        "short_format": short_plan.get("format", "pain_to_tip"),
        "target_duration_sec": 35,
        "hook": "...",
        "narration": "...",
        "beats": [],
        "cta": "...",
        "qa": {"verdict": "PENDING_SHORTS_QA"},
    }
    seed = short_plan.get("narration_seed", "")
    return (
        f"You are a YouTube Shorts writer for a Spain-first wellness channel (adults 45+).\n"
        f"Write a single vertical Short in format '{short_plan.get('format', 'pain_to_tip')}'.\n\n"
        f"{_source_block(source_artifacts or {})}"
        f"SOURCE NARRATION SEED:\n{seed}\n\n"
        f"{_OUTPUT_RULES}\n{_LANGUAGE_RULES}\n{_RETENTION_RULES}\n{_STYLE_RULES}\n{_SAFETY_RULES}\n"
        f"Return JSON exactly in this shape (qa.verdict MUST stay PENDING_SHORTS_QA):\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
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
    schema = {
        "title": "...",
        "description": "...",
        "hashtags": ["#shorts"],
        "pinned_comment": "...",
        "long_video_url": long_video_url,
    }
    return (
        "Write SEO for a vertical YouTube Short, Spain Spanish es-ES.\n\n"
        f"SHORT HOOK: {short_script.get('hook', '')}\n\n"
        f"{_OUTPUT_RULES}\n"
        "- Title <= 60 chars, no clickbait. Include 3-6 hashtags. Pinned comment points to the long video.\n"
        f"Return JSON exactly:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
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
        f"SCRIPT:\n{json.dumps(short_script, ensure_ascii=False)[:1500]}\n\n"
        f"SCENES:\n{json.dumps(short_scenes, ensure_ascii=False)[:1500]}\n\n"
        f"{_OUTPUT_RULES}\nReturn JSON exactly:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )
