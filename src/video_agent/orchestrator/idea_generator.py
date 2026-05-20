"""Idea generator: ask ChatGPT to produce N idea JSON objects for a channel.

Usage (from FastAPI route or CLI):

    ideas = await generate_ideas(
        channel_path,
        chatgpt_fn=lambda msgs: client.run_session("chatgpt", msgs),
        seed_topics=["sueño", "caminata diaria"],
        count=10,
    )
    paths = save_ideas(ideas, channel_id="vida-plena-45", out_dir=repo_root() / "inputs")
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from video_agent.utils.json_io import read_yaml

# Default sub-dir inside ``inputs/`` (or any given out_dir) where ideas land.
IDEAS_SUBDIR = "ideas"

_REQUIRED_FIELDS = {"topic", "angle", "target_duration_sec", "key_points", "title_seed"}


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _idea_gen_prompt(channel_config: dict, seed_topics: list[str], count: int) -> str:
    ch = channel_config.get("channel", {})
    audience = channel_config.get("audience", {})
    niche = channel_config.get("niche", {})
    fmt = channel_config.get("content_format", {})
    positioning = channel_config.get("positioning", {})

    channel_name = ch.get("name", ch.get("id", ""))
    description = ch.get("description", "")
    language = audience.get("language", "es-419")
    age_range = audience.get("age_range", [45, 75])
    sub_niches = niche.get("sub_niches", [])
    avoid_topics = niche.get("avoid_topics", [])
    target_dur = fmt.get("target_duration_sec", 54)
    forbidden = positioning.get("forbidden_phrases", [])
    preferred = positioning.get("preferred_phrases", [])

    seed_block = ""
    if seed_topics:
        seeds_str = "\n".join(f"  - {t}" for t in seed_topics)
        seed_block = f"""
## Seed topics (use as inspiration, not literally)
{seeds_str}
"""

    avoid_block = ""
    if avoid_topics:
        avoid_str = ", ".join(avoid_topics)
        avoid_block = f"\nAvoid these subjects entirely: {avoid_str}."

    phrase_block = ""
    if forbidden or preferred:
        parts = []
        if forbidden:
            parts.append("Forbidden phrases (never use): " + ", ".join(f'"{p}"' for p in forbidden))
        if preferred:
            parts.append("Preferred phrases: " + ", ".join(f'"{p}"' for p in preferred))
        phrase_block = "\n" + ". ".join(parts) + "."

    return f"""You are a YouTube content strategist for the channel **{channel_name}**.

## Channel
- Description: {description}
- Primary audience: Spanish-speaking adults aged {age_range[0]}–{age_range[1]}
- Language: {language}
- Sub-niches: {", ".join(sub_niches)}
{phrase_block}{avoid_block}
{seed_block}
## Task

Generate exactly **{count} original video ideas** for this channel.

Each idea must be a JSON object with these exact keys:

```json
{{
  "topic": "one clear sentence describing what the video covers",
  "angle": "the specific educational or emotional angle — why this matters for 45+ adults",
  "target_duration_sec": {target_dur},
  "key_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "title_seed": "a natural Spanish title for the video (no clickbait, no exclamation marks)"
}}
```

Rules:
- All text in Spanish ({language}).
- `target_duration_sec` must be exactly {target_dur} for every idea.
- `key_points` must have 5–7 items; each one is a concrete, actionable sub-topic.
- `title_seed` must read naturally — no ALL CAPS, no "¡¡¡", no exaggerated claims.
- Ideas must be distinct from each other (different topics or angles).
- Do NOT include medical diagnoses, supplement recommendations, or miracle cures.

Return ONLY a valid JSON array of {count} objects — no markdown fences, no commentary.
"""


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


def parse_ideas(raw: str) -> list[dict]:
    """Parse ChatGPT raw response into a list of validated idea dicts.

    Tries to extract a JSON array; falls back to extracting individual
    JSON objects if the model wrapped them in prose.
    """
    text = raw.strip()

    # Strip common markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # Try parsing as a JSON array directly
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [_validate_idea(item) for item in parsed if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass

    # Fall back: find the outer [...] array in the text
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, list):
                return [_validate_idea(item) for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

    # Last resort: extract individual objects
    ideas = []
    for obj_text in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(obj_text.group(0))
            if isinstance(obj, dict) and _required_keys_present(obj):
                ideas.append(_validate_idea(obj))
        except json.JSONDecodeError:
            continue

    return ideas


def _required_keys_present(obj: dict) -> bool:
    return _REQUIRED_FIELDS.issubset(obj.keys())


def _validate_idea(obj: dict) -> dict:
    """Normalise and validate an idea dict. Raises ValueError on bad schema."""
    missing = _REQUIRED_FIELDS - obj.keys()
    if missing:
        raise ValueError(f"Idea missing required fields: {missing}")
    if not isinstance(obj.get("key_points"), list):
        raise ValueError("key_points must be a list")
    if not isinstance(obj.get("target_duration_sec"), int):
        # Try coercing float → int
        try:
            obj["target_duration_sec"] = int(obj["target_duration_sec"])
        except (TypeError, ValueError):
            raise ValueError("target_duration_sec must be an integer")
    return {
        "topic": str(obj["topic"]).strip(),
        "angle": str(obj["angle"]).strip(),
        "target_duration_sec": obj["target_duration_sec"],
        "key_points": [str(kp).strip() for kp in obj["key_points"]],
        "title_seed": str(obj["title_seed"]).strip(),
    }


# ---------------------------------------------------------------------------
# File saver
# ---------------------------------------------------------------------------


def _slug(text: str, max_len: int = 40) -> str:
    """Convert text to a safe ASCII slug."""
    # Normalise unicode (é → e, ñ → n, etc.)
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    # Keep alphanum and spaces, collapse to hyphens
    slug = re.sub(r"[^a-zA-Z0-9 ]", "", ascii_text).strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    return slug[:max_len].rstrip("-") or "idea"


def save_ideas(ideas: list[dict], channel_id: str, out_dir: Path) -> list[Path]:
    """Write each idea to ``out_dir/ideas/<channel_id>/<timestamp>-<slug>.json``."""
    dest = out_dir / IDEAS_SUBDIR / channel_id
    dest.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    paths: list[Path] = []

    for i, idea in enumerate(ideas):
        slug = _slug(idea.get("topic", "idea"))
        filename = f"{now}-{i:02d}-{slug}.json"
        path = dest / filename
        path.write_text(
            json.dumps(idea, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths.append(path)

    return paths


# ---------------------------------------------------------------------------
# High-level async entrypoint
# ---------------------------------------------------------------------------


async def generate_ideas(
    channel_path: Path,
    chatgpt_fn: Callable[[list[str]], Awaitable[str]],
    seed_topics: list[str] | None = None,
    count: int = 10,
) -> list[dict]:
    """Call ChatGPT and return a list of validated idea dicts.

    ``chatgpt_fn(messages)`` is typically ``BrowserClient.run_session``
    pre-bound to ``"chatgpt"``.  Raises ``ValueError`` if ChatGPT returns
    zero parseable ideas.
    """
    channel_config = read_yaml(channel_path)
    prompt = _idea_gen_prompt(channel_config, seed_topics or [], count)
    raw = await chatgpt_fn([prompt])
    ideas = parse_ideas(raw)
    if not ideas:
        raise ValueError(f"ChatGPT returned no parseable ideas. Raw:\n{raw[:500]}")
    return ideas
