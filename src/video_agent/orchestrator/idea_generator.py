"""Idea generator: vidIQ keyword discovery → ChatGPT idea expansion.

Correct flow:
  1. Score seed keywords on vidIQ.
  2. Collect ``related`` keywords from each result, score those too.
  3. Merge all scored keywords, sort by score DESC.
  4. Take the top-N high-opportunity keywords (high score = high volume / low competition).
  5. Ask ChatGPT to flesh out one video idea per top keyword.

This ensures every idea is anchored to a keyword that YouTube search data confirms
has real demand — not a topic ChatGPT invented in a vacuum.

Usage::

    ideas = await generate_ideas(
        channel_path,
        chatgpt_fn=lambda msgs: client.run_session("chatgpt", msgs),
        vidiq_fn=client.run_vidiq_scores,
        seed_topics=["insomnio", "menopausia"],
        count=5,
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

IDEAS_SUBDIR = "ideas"

_REQUIRED_FIELDS = {"topic", "angle", "target_duration_sec", "key_points", "title_seed"}


# ---------------------------------------------------------------------------
# vidIQ helpers
# ---------------------------------------------------------------------------

def _extract_related_strings(score_result: dict) -> list[str]:
    """Pull related keyword strings from a vidIQ score dict (handles str or dict items)."""
    out: list[str] = []
    for r in (score_result.get("related") or []):
        if isinstance(r, str):
            out.append(r)
        elif isinstance(r, dict):
            kw = r.get("keyword") or r.get("term") or ""
            if kw:
                out.append(str(kw))
    return out


async def _discover_top_keywords(
    seeds: list[str],
    vidiq_fn: Callable[[list[str]], Awaitable[list[dict]]],
    max_related: int = 15,
    top_n: int = 8,
) -> list[dict]:
    """Score seeds + their related keywords, return top_n sorted by score DESC.

    Each returned dict: {keyword, score, volume, competition, related}
    """
    # Phase 1: score seeds
    seed_results: list[dict] = []
    if seeds:
        try:
            seed_results = await vidiq_fn(seeds)
        except Exception:
            seed_results = []

    # Collect unique related keywords from seed results
    seen: set[str] = set(s.lower() for s in seeds)
    related_pool: list[str] = []
    for r in seed_results:
        for kw in _extract_related_strings(r):
            if kw.lower() not in seen:
                seen.add(kw.lower())
                related_pool.append(kw)
            if len(related_pool) >= max_related:
                break

    # Phase 2: score related keywords
    related_results: list[dict] = []
    if related_pool:
        try:
            related_results = await vidiq_fn(related_pool[:max_related])
        except Exception:
            related_results = []

    # Merge and normalise
    all_scored: list[dict] = []
    for r in (seed_results + related_results):
        kw = r.get("keyword", "")
        score = r.get("score")
        if not kw:
            continue
        all_scored.append({
            "keyword": kw,
            "score": score if isinstance(score, (int, float)) else 0,
            "volume": r.get("volume", ""),
            "competition": r.get("competition", ""),
        })

    # Deduplicate by keyword (keep highest score)
    best: dict[str, dict] = {}
    for item in all_scored:
        key = item["keyword"].lower()
        if key not in best or item["score"] > best[key]["score"]:
            best[key] = item

    sorted_kws = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    return sorted_kws[:top_n]


# ---------------------------------------------------------------------------
# Prompt builder (keyword-anchored)
# ---------------------------------------------------------------------------

def _idea_gen_prompt(
    channel_config: dict,
    top_keywords: list[dict],
    count: int,
) -> str:
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

    avoid_block = ""
    if avoid_topics:
        avoid_block = f"\nAvoid these subjects entirely: {', '.join(avoid_topics)}."

    phrase_block = ""
    if forbidden or preferred:
        parts = []
        if forbidden:
            parts.append("Forbidden phrases (never use): " + ", ".join(f'"{p}"' for p in forbidden))
        if preferred:
            parts.append("Preferred phrases: " + ", ".join(f'"{p}"' for p in preferred))
        phrase_block = "\n" + ". ".join(parts) + "."

    kw_lines = []
    for i, kw in enumerate(top_keywords, 1):
        score = kw.get("score", "?")
        vol = kw.get("volume", "")
        comp = kw.get("competition", "")
        meta = f"score {score}/100"
        if vol:
            meta += f", volume: {vol}"
        if comp:
            meta += f", competition: {comp}"
        kw_lines.append(f"  {i}. \"{kw['keyword']}\" ({meta})")

    kw_block = "\n".join(kw_lines)

    return f"""You are a YouTube content strategist for the channel **{channel_name}**.

## Channel
- Description: {description}
- Primary audience: Spanish-speaking adults aged {age_range[0]}–{age_range[1]}
- Language: {language}
- Sub-niches: {", ".join(sub_niches)}
{phrase_block}{avoid_block}

## High-opportunity keywords (real YouTube search data from vidIQ)

These keywords were scored for search volume and competition. Higher score = better opportunity.
Build your video ideas directly around these keywords — each idea should target one of them.

{kw_block}

## Task

Generate exactly **{count} video ideas**, each anchored to one of the keywords above.
Use the keyword naturally in the title and topic — this is what viewers are actively searching for.

Each idea must be a JSON object with these exact keys:

```json
{{
  "topic": "one clear sentence describing what the video covers",
  "angle": "the specific educational or emotional angle — why this matters for 45+ adults",
  "target_duration_sec": {target_dur},
  "key_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "title_seed": "a natural Spanish title for the video (no clickbait, no exclamation marks)",
  "target_keyword": "the exact keyword from the list above that this idea targets"
}}
```

Rules:
- All text in Spanish ({language}).
- `target_duration_sec` must be exactly {target_dur} for every idea.
- `key_points` must have 5–7 items; each concrete and actionable.
- `title_seed` must include or closely echo the target keyword naturally.
- `title_seed` must read naturally — no ALL CAPS, no "¡¡¡", no exaggerated claims.
- Each idea targets a DIFFERENT keyword from the list.
- Do NOT include medical diagnoses, supplement recommendations, or miracle cures.

Return ONLY a valid JSON array of {count} objects — no markdown fences, no commentary.
"""


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def parse_ideas(raw: str) -> list[dict]:
    """Parse ChatGPT raw response into a list of validated idea dicts."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [_validate_idea(item) for item in parsed if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass

    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, list):
                return [_validate_idea(item) for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

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
    """Normalise and validate an idea dict."""
    missing = _REQUIRED_FIELDS - obj.keys()
    if missing:
        raise ValueError(f"Idea missing required fields: {missing}")
    if not isinstance(obj.get("key_points"), list):
        raise ValueError("key_points must be a list")
    if not isinstance(obj.get("target_duration_sec"), int):
        try:
            obj["target_duration_sec"] = int(obj["target_duration_sec"])
        except (TypeError, ValueError):
            raise ValueError("target_duration_sec must be an integer")
    result = {
        "topic": str(obj["topic"]).strip(),
        "angle": str(obj["angle"]).strip(),
        "target_duration_sec": obj["target_duration_sec"],
        "key_points": [str(kp).strip() for kp in obj["key_points"]],
        "title_seed": str(obj["title_seed"]).strip(),
    }
    if "target_keyword" in obj:
        result["target_keyword"] = str(obj["target_keyword"]).strip()
    return result


# ---------------------------------------------------------------------------
# File saver
# ---------------------------------------------------------------------------

def _slug(text: str, max_len: int = 40) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
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
        path.write_text(json.dumps(idea, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(path)

    return paths


# ---------------------------------------------------------------------------
# High-level async entrypoint
# ---------------------------------------------------------------------------

async def generate_ideas(
    channel_path: Path,
    chatgpt_fn: Callable[[list[str]], Awaitable[str]],
    vidiq_fn: Callable[[list[str]], Awaitable[list[dict]]] | None = None,
    seed_topics: list[str] | None = None,
    count: int = 10,
) -> tuple[list[dict], list[dict]]:
    """Discover top keywords via vidIQ, then ask ChatGPT to flesh out ideas.

    Returns (ideas, top_keywords) so the caller can attach scores to the response.
    ``vidiq_fn`` is optional — if None, falls back to ChatGPT-only generation with seeds.
    """
    channel_config = read_yaml(channel_path)
    seeds = seed_topics or []

    if vidiq_fn is not None:
        top_keywords = await _discover_top_keywords(seeds, vidiq_fn)
    else:
        # Fallback: no vidIQ — use seeds as pseudo-keywords
        top_keywords = [{"keyword": s, "score": None, "volume": "", "competition": ""} for s in seeds]

    if not top_keywords:
        raise ValueError("vidIQ returned no scoreable keywords from the given seeds. Try different topics.")

    prompt = _idea_gen_prompt(channel_config, top_keywords, count)
    raw = await chatgpt_fn([prompt])
    ideas = parse_ideas(raw)
    if not ideas:
        raise ValueError(f"ChatGPT returned no parseable ideas. Raw:\n{raw[:500]}")

    return ideas, top_keywords
