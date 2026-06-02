from __future__ import annotations


def short_ideas_prompt(channel_config: dict, source_doc: dict, target_count: int = 10) -> str:
    scene_blocks = source_doc.get("full_narration", "")
    source_long_job_id = source_doc.get("source_long_job_id", "")
    source_title = source_doc.get("title", "")
    return f"""You are a YouTube Shorts strategist for Vida Plena 45+.

Analyze the full long-form video narration below and propose high-retention YouTube Shorts ideas.

IMPORTANT:
- Generate synthesis ideas only.
- Do not propose raw excerpts or contiguous scene clips.
- Each idea may combine multiple non-contiguous source scenes.
- Every idea must be grounded in source_scene_ids.
- Do not invent health claims not present in the source.
- Avoid diagnosis, cure, treatment, or miracle claims.
- Use Spanish for Spain, natural for adults 45+.
- Return exactly one raw JSON object.
- No markdown.
- No commentary.
- Produce 8-12 ideas when possible.

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
      "risk_level": "lifestyle|soft_health|medical_sensitive",
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

SOURCE LONG VIDEO:
{scene_blocks}
"""
