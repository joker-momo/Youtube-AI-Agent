from __future__ import annotations


def short_ideas_prompt(channel_config: dict, source_doc: dict, target_count: int = 10) -> str:
    scene_blocks = source_doc.get("full_narration", "")
    source_long_job_id = source_doc.get("source_long_job_id", "")
    source_title = source_doc.get("title", "")
    return f"""You are a YouTube Shorts strategist for Vida Plena 45+.

Analyze the full long-form video narration below and propose high-retention YouTube Shorts ideas.

The channel is Spain-first practical wellness for adults over 45:
nutrition, sleep, movement, stress, daily habits, blood sugar, circulation, memory, weight, energy, and healthy routines.

IMPORTANT:
- Generate synthesis ideas only.
- Do not propose raw excerpts or contiguous scene clips.
- Each idea should combine multiple source scenes when possible.
- Prefer 3–5 source scenes for checklist, mistake_list, warning_signs, and top_tips.
- Do not create a one-scene idea unless SOURCE LONG VIDEO contains fewer than 2 usable narrated scenes.
- Every idea must be grounded in source_scene_ids.
- Use only scene IDs that appear in SOURCE LONG VIDEO.
- Do not invent, rename, or modify scene IDs.
- Do not invent health claims not present in the source.
- Avoid diagnosis, cure, treatment, or miracle claims.
- Use Spanish for Spain, natural for adults 45+.
- Do not call the audience ancianos, tercera edad, abuelos, elderly, seniors, or adultos mayores.
- Ideas must be meaningfully different from each other.
- Avoid ideas that use the same or nearly identical source_scene_ids set.
- Return exactly one raw JSON object.
- No markdown.
- No commentary.
- Produce 8–12 ideas when possible.
- If the source does not support 8 strong, distinct, source-backed ideas, return fewer ideas and explain why in warnings.
- Never invent extra ideas just to reach 8.

Allowed formats:
checklist, mistake_list, warning_signs, myth_truth, problem_solution, top_tips, recap, pain_to_tip

Field rules:
- idea_type must be exactly "synthesis".
- risk_level must be exactly one of: "lifestyle", "soft_health", "medical_sensitive".
- hook_text must be 2–6 words, uppercase Spanish, suitable for thumbnail/on-screen hook.
- narration_seed must be 80–180 words and summarize only the selected source scenes for that idea.
- key_points must each include source_scene_ids.
- Every key_points[*].source_scene_ids must be a subset of the idea's source_scene_ids.
- scores must be integers from 0 to 100.
- Scores are first-pass estimates. Be consistent, but the system will validate and recompute overall.

Required output shape:
{{
  "source_long_job_id": "{source_long_job_id}",
  "source_title": "{source_title}",
  "ideas": [
    {{
      "idea_id": "idea-01",
      "idea_type": "synthesis",
      "format": "mistake_list",
      "title": "...",
      "hook_text": "...",
      "viewer_pain": "...",
      "practical_payoff": "...",
      "source_scene_ids": ["scene-04", "scene-11"],
      "key_points": [
        {{
          "point": "...",
          "source_scene_ids": ["scene-04"]
        }}
      ],
      "narration_seed": "...",
      "visual_angle": "...",
      "cta_angle": "long_video_channel_cta",
      "risk_level": "soft_health",
      "scores": {{
        "hook_strength": 90,
        "viewer_pain": 85,
        "practical_value": 90,
        "source_fidelity": 90,
        "visual_potential": 85,
        "safety": 95,
        "uniqueness": 80,
        "overall": 88
      }},
      "risk_flags": []
    }}
  ],
  "warnings": []
}}

Selection criteria:
- High retention in the first 2 seconds.
- Clear pain, curiosity, mistake, number, myth, or practical promise.
- One main idea per Short.
- Practical payoff before CTA.
- Strong visual potential.
- Source-backed, not invented.
- Safe wellness language.

SOURCE LONG VIDEO:
{scene_blocks}
"""
