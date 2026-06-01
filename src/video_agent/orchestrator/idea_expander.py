from __future__ import annotations

import json
from typing import Awaitable, Callable

from video_agent.operator import extract_json_object


class IdeaExpansionError(ValueError):
    pass


def build_title_to_idea_prompt(
    *,
    channel_config: dict,
    title_seed: str,
    duration_mode: str,
    target_duration_sec: int | None,
    min_duration_sec: int,
    max_duration_sec: int,
    existing_videos: list[dict],
    duplicate_policy: str,
    notes: str | None = None,
    validation_feedback: str | None = None,
) -> str:
    channel = channel_config.get("channel", {}) if isinstance(channel_config, dict) else {}
    audience = channel_config.get("audience", {}) if isinstance(channel_config, dict) else {}
    config_summary = {
        "channel_id": channel.get("id"),
        "channel_name": channel.get("name"),
        "language": channel_config.get("language") or channel_config.get("target_language") or "Spanish",
        "audience": audience,
    }
    duration_rule = (
        f"Use exactly target_duration_sec={target_duration_sec}."
        if duration_mode == "fixed"
        else f"Choose target_duration_sec between {min_duration_sec} and {max_duration_sec} based on complexity."
    )
    feedback = f"\nPrevious attempt failed validation:\n{validation_feedback}\n" if validation_feedback else ""
    optional_notes = f"\nOperator notes:\n{notes}\n" if notes else ""
    return f"""
You are expanding a raw YouTube video idea/title into a production-ready idea.json.

Return exactly one JSON object. No markdown. No commentary.

Channel config summary:
{json.dumps(config_summary, ensure_ascii=False, indent=2)}

Input title_seed:
{title_seed}

Duration mode:
{duration_mode}
{duration_rule}

Existing published/generated videos to avoid:
{json.dumps(existing_videos[:100], ensure_ascii=False, indent=2)}

Duplicate policy:
{duplicate_policy}

Do not repeat:
- same title
- same topic with same angle
- same viewer pain with same payoff
- same key_points structure
- same thumbnail hook concept

If the input title is too similar and duplicate_policy is "rewrite_angle":
- keep the general topic only if useful
- create a different angle
- create different key_points
- explain the difference in duplicate_check.how_this_angle_is_different

If it is too similar and cannot be made meaningfully different:
- set duplicate_check.verdict = "TOO_SIMILAR"

Health safety:
- Do not invent medical claims.
- Do not promise cure/prevention/reversal.
- Avoid "cura", "garantiza", "elimina", "milagro".
- Prefer "ayuda a", "puede apoyar", "cuidar", "proteger" cautiously.
- key_points must include at least one caution about consulting a professional when appropriate.
- For sarcopenia, do not say "cura la sarcopenia" or guarantee preventing muscle loss.

Create JSON with these fields:
- topic: specific, explanatory, not just the title
- angle: specific editorial approach, not a generic summary
- target_duration_sec: integer
- duration_mode: "{duration_mode}"
- duration_reason: required when duration_mode is auto
- key_points: at least 3 practical Spanish strings
- title_seed: exactly "{title_seed}"
- target_keyword
- viewer_pain
- thumbnail_hook
- idea_format
- source: "manual_title_expansion"
- duplicate_check: object with verdict, closest_existing_title, overlap_reason, how_this_angle_is_different

{optional_notes}{feedback}
""".strip()


async def expand_title_to_idea(
    *,
    title_seed: str,
    channel_config: dict,
    session_fn: Callable[[list[str]], Awaitable[str]],
    duration_mode: str,
    target_duration_sec: int | None,
    min_duration_sec: int,
    max_duration_sec: int,
    existing_videos: list[dict],
    duplicate_policy: str,
    notes: str | None = None,
    max_attempts: int = 3,
) -> dict:
    feedback: str | None = None
    last_error = ""
    for _attempt in range(max(1, max_attempts)):
        prompt = build_title_to_idea_prompt(
            channel_config=channel_config,
            title_seed=title_seed,
            duration_mode=duration_mode,
            target_duration_sec=target_duration_sec,
            min_duration_sec=min_duration_sec,
            max_duration_sec=max_duration_sec,
            existing_videos=existing_videos,
            duplicate_policy=duplicate_policy,
            notes=notes,
            validation_feedback=feedback,
        )
        raw = await session_fn([prompt])
        try:
            idea = extract_json_object(raw)
            idea["title_seed"] = title_seed
            idea.setdefault("source", "manual_title_expansion")
            return idea
        except Exception as exc:
            last_error = str(exc)
            feedback = last_error
    raise IdeaExpansionError(last_error or "ChatGPT did not return a valid JSON object.")
