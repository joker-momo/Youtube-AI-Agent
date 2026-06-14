"""Idea generator: seed keyword selection → ChatGPT idea expansion.

Correct flow:
  1. Collect user-provided seeds or channel-matched trend seeds.
  2. Enrich keyword candidates with local audience, language, intent, and content signals.
  3. Select top-opportunity plus long-tail candidates for prompt input.
  4. Ask ChatGPT to flesh out one video idea per top keyword.

Usage::

    ideas = await generate_ideas(
        channel_path,
        chatgpt_fn=lambda msgs: client.run_session("chatgpt", msgs),
        seed_topics=["insomnio", "menopausia"],
        count=5,
    )
    paths = save_ideas(ideas, channel_id="vida-plena-45", out_dir=repo_root() / "inputs")
"""

from __future__ import annotations

import functools  # noqa: F401
import html  # noqa: F401
import json
import re
import unicodedata  # noqa: F401
import urllib.request
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

try:
    from defusedxml import ElementTree as ET  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - falls back when defusedxml missing
    import xml.etree.ElementTree as ET  # noqa: S405 — XXE-safe payload only via defusedxml; install defusedxml

# Facade: leaf clusters extracted to sibling modules; re-exported via * so
# existing imports keep working. Patched functions (_fetch_google_trends,
# atomic_write_json) and their callers stay here so monkeypatch on this
# module reaches them.
from video_agent.orchestrator.idea_constants import *  # noqa: F401,F403
from video_agent.orchestrator.idea_keyword_scoring import *  # noqa: F401,F403
from video_agent.orchestrator.idea_youtube_sync import *  # noqa: F401,F403
from video_agent.storage.atomic import atomic_write_json
from video_agent.utils.json_io import read_yaml


def _fetch_google_trends(geo: str, language: str = "es") -> list[str]:
    """Fetch daily trending searches from Google Trends RSS for the given geo.

    Returns a list of trend title strings. Never raises — returns [] on error.
    Geo examples: "MX", "CO", "ES", "AR"
    """
    url = f"https://trends.google.com/trending/rss?geo={geo}&hl={language}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        titles: list[str] = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())
        return titles
    except Exception as exc:
        import sys

        print(f"[idea_generator] Google Trends fetch failed ({geo}): {exc}", file=sys.stderr)
        return []


def _auto_seeds_from_trends(channel_config: dict, max_seeds: int = 10) -> list[str]:
    """Derive seed keywords from Google Trends filtered by channel niche.

    Tries each primary_market geo in order. Falls back to sub_niches as seeds
    if no trending topic matches the channel niche.
    """
    markets = channel_config.get("audience", {}).get("primary_markets", ["MX"])
    language = channel_config.get("audience", {}).get("language", "es").split("-")[0]
    niche_kws = _niche_keywords(channel_config)
    sub_niches_raw = channel_config.get("niche", {}).get("sub_niches", [])

    all_trends: list[str] = []
    for geo in markets[:2]:  # try first 2 markets max
        trends = _fetch_google_trends(geo, language)
        all_trends.extend(trends)
        if len(all_trends) >= 30:
            break

    # Filter by niche relevance
    matched = [t for t in all_trends if _trend_matches_niche(t, niche_kws)]

    if matched:
        unique = list(dict.fromkeys(matched))
        return unique[:max_seeds]

    # Fallback 1: channel-specific seed profile (Spain-first, 45+ concrete).
    explicit_seeds = [
        str(s).strip()
        for s in ((channel_config.get("keyword_research") or {}).get("fallback_seeds") or [])
        if str(s).strip()
    ]
    if explicit_seeds:
        return explicit_seeds[:max_seeds]

    # Fallback 2: use sub_niches expanded to Spanish keywords as seeds
    fallback: list[str] = []
    for sn in sub_niches_raw:
        kws = _NICHE_KW_MAP.get(sn, [])
        if kws:
            fallback.append(kws[0])  # take first representative keyword
    return fallback[:max_seeds] if fallback else ["salud", "bienestar"]


def _idea_gen_prompt(
    channel_config: dict,
    top_keywords: list[dict] | list[str],
    count: int,
    published_titles: list[str] | None = None,
) -> str:
    ch = channel_config.get("channel", {})
    audience = channel_config.get("audience", {})
    niche = channel_config.get("niche", {})
    fmt = channel_config.get("content_format", {})
    positioning = channel_config.get("positioning", {})

    channel_name = ch.get("name", ch.get("id", ""))
    description = ch.get("description", "")
    language = audience.get("language", "es-ES")
    locale_style = channel_config.get("locale_style", {}) or {}
    target_locale = locale_style.get("target_locale") or (
        "Spain" if language == "es-ES" else "Latin America"
    )
    lexical = locale_style.get("lexical_preferences", {}) or {}
    lexical_prefer = list(lexical.get("prefer") or [])
    lexical_avoid = list(lexical.get("avoid") or [])
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
        if isinstance(kw, str):
            kw_lines.append(f'  {i}. "{kw}"')
            continue
        score = kw.get("final_score", kw.get("score", "?"))
        keyword_source_score = kw.get("keyword_source_score", kw.get("score"))
        vol = kw.get("volume", "")
        comp = kw.get("competition", "")
        cluster = kw.get("intent_cluster", "")
        angle = kw.get("recommended_angle", "")
        hooks = kw.get("thumbnail_hook_options", [])
        meta = f"final score {score}/100"
        if keyword_source_score is not None:
            meta += f", external score: {keyword_source_score}/100"
        if vol:
            meta += f", volume: {vol}"
        if comp:
            meta += f", competition: {comp}"
        if cluster:
            meta += f", cluster: {cluster}"
        if angle:
            meta += f", recommended angle: {angle}"
        if hooks:
            meta += f", thumbnail hooks: {', '.join(hooks[:3])}"
        kw_lines.append(f'  {i}. "{kw["keyword"]}" ({meta})')

    kw_block = "\n".join(kw_lines)

    published_block = ""
    titles = [str(t).strip() for t in (published_titles or []) if str(t).strip()]
    titles = titles[:MAX_PUBLISHED_TITLES_IN_IDEA_PROMPT]
    if titles:
        title_lines = "\n".join(f"  {i}. {t}" for i, t in enumerate(titles, 1))
        published_block = (
            "\n## Already published videos to avoid\n\n"
            "Do not generate ideas that substantially repeat these titles or angles:\n"
            f"{title_lines}\n\n"
            "- Do not reuse the same core angle as any published title.\n"
            "- If a keyword is close to a published video, choose a clearly different angle.\n"
        )

    prefer_line = ("- Prefer: " + ", ".join(lexical_prefer)) if lexical_prefer else ""
    avoid_line = ("- Avoid: " + ", ".join(lexical_avoid)) if lexical_avoid else ""
    locale_block = "\n".join(
        line
        for line in [
            "## Locale style",
            f"- Target locale: {target_locale}",
            f"- Language code: {language}",
            f"- Use {target_locale}-natural Spanish.",
            prefer_line,
            avoid_line,
        ]
        if line
    )

    return f"""You are a YouTube content strategist for the channel **{channel_name}**.

## Channel
- Description: {description}
- Primary audience: Spanish-speaking adults aged {age_range[0]}–{age_range[1]}
- Language: {language}
- Sub-niches: {", ".join(sub_niches)}
{phrase_block}{avoid_block}

{locale_block}

## High-opportunity keywords

These keywords were scored for audience fit, search intent, language fit, and content fit. Higher score = better opportunity.
Build your video ideas directly around these keywords — each idea should target one of them.

{kw_block}
{published_block}
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
- All text must be in Spanish for {target_locale} ({language}), not Latin America Spanish unless the config says otherwise.
- Do not use Portuguese.
- Do not use forbidden age-positioning phrases from channel_config.positioning.forbidden_phrases.
- `target_duration_sec` must be exactly {target_dur} for every idea.
- `key_points` must have 5–7 items; each concrete and actionable.
- `title_seed` must include or closely echo the target keyword naturally.
- `title_seed` must read naturally — no ALL CAPS, no "¡¡¡", no exaggerated claims.
- Each idea targets a DIFFERENT keyword from the list.
- Do NOT include medical diagnoses, supplement recommendations, or miracle cures.

## Variation rules (make each idea distinct)
- Avoid making every idea about the same night/sleep angle.
- Prefer a specific viewer pain + practical promise.
- Each idea should use a DIFFERENT viewer intent when possible: symptom/pain, mistake to avoid, simple routine, checklist, explanation.
- Do not generate generic "wellness" topics — be concrete.
- The title should be specific enough to support a strong thumbnail.
- Prefer concrete {target_locale}-first wording for people over 45.

You MAY also include these optional fields when helpful (they are accepted but not required):
- `"thumbnail_hook"`: 1–3 word punchy thumbnail text (e.g. "NO DESCANSAS")
- `"viewer_pain"`: the concrete pain the viewer feels (e.g. "duerme pero se levanta cansado")
- `"idea_format"`: one of symptom_pain, mistake_to_avoid, simple_routine, mistake_checklist, explanation

Return ONLY a valid JSON array of {count} objects — no markdown fences, no commentary.
"""


def _select_keywords_for_prompt(keyword_result: list[dict] | dict, count: int) -> list[dict]:
    if isinstance(keyword_result, list):
        return keyword_result[: max(count, 8)]
    selected = keyword_result.get("top_opportunity_keywords", []) + keyword_result.get(
        "long_tail_test_keywords", []
    )
    if selected:
        return selected[: max(count, 8)]
    legacy = keyword_result.get("all_scored_keywords", [])
    hard_rejections = {"language_mismatch", "audience_mismatch", "content_mismatch"}
    safe_fallback = [
        item
        for item in legacy
        if item.get("language_fit", 0) >= 80
        and item.get("content_fit", 0) >= 60
        and not hard_rejections.intersection(set(item.get("rejection_reasons") or []))
    ]
    return safe_fallback[: max(count, 8)]


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
        except (TypeError, ValueError) as exc:
            raise ValueError("target_duration_sec must be an integer") from exc
    result = {
        "topic": str(obj["topic"]).strip(),
        "angle": str(obj["angle"]).strip(),
        "target_duration_sec": obj["target_duration_sec"],
        "key_points": [str(kp).strip() for kp in obj["key_points"]],
        "title_seed": str(obj["title_seed"]).strip(),
    }
    if "target_keyword" in obj:
        result["target_keyword"] = str(obj["target_keyword"]).strip()
    # Preserve known optional fields when present (backward compatible: absent
    # fields are simply omitted, never injected).
    for opt in ("thumbnail_hook", "viewer_pain", "idea_format"):
        if obj.get(opt) is not None:
            result[opt] = str(obj[opt]).strip()
    return result


def _slug(text: str, max_len: int = 40) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9 ]", "", ascii_text).strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    return slug[:max_len].rstrip("-") or "idea"


def save_ideas(ideas: list[dict], channel_id: str, out_dir: Path) -> list[Path]:
    """Write each idea to ``out_dir/ideas/<channel_id>/<timestamp>-<slug>.json``.

    ``channel_id`` is validated against a safe-id regex and the resolved
    ``dest`` is asserted to stay inside ``out_dir`` to prevent traversal
    when this helper is called from CLI/batch entrypoints.
    """
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", channel_id or ""):
        raise ValueError(f"Invalid channel_id: {channel_id!r}")

    out_root = out_dir.resolve()
    dest = (out_dir / IDEAS_SUBDIR / channel_id).resolve()
    try:
        dest.relative_to(out_root)
    except ValueError as exc:
        raise ValueError(f"channel_id escapes out_dir: {channel_id!r}") from exc
    dest.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    paths: list[Path] = []

    for i, idea in enumerate(ideas):
        slug = _slug(idea.get("topic", "idea"))
        filename = f"{now}-{i:02d}-{slug}.json"
        path = dest / filename
        atomic_write_json(path, idea)
        paths.append(path)

    return paths


async def generate_ideas(
    channel_path: Path,
    chatgpt_fn: Callable[[list[str]], Awaitable[str]],
    seed_topics: list[str] | None = None,
    count: int = 10,
    with_metadata: bool = False,
    published_titles: list[str] | None = None,
) -> list[dict] | tuple[list[dict], list[dict], str]:
    """Select top keywords, then ask ChatGPT to flesh out ideas.

    Returns ideas list by default.
    If ``with_metadata=True``, returns (ideas, top_keywords, seed_source) where seed_source is one of:
      "user"    — caller provided seed_topics
      "trend"   — auto-discovered from Google Trends matching channel niche
      "fallback" — trends didn't match niche; used channel sub_niches as seeds
    """
    import sys

    channel_config = read_yaml(channel_path)
    seeds = list(seed_topics) if seed_topics else []
    seed_source = "user"

    # If no seeds provided, auto-discover from Google Trends filtered by channel niche
    if not seeds:
        print("[idea_generator] No seeds given — fetching from Google Trends…", file=sys.stderr)
        seeds = _auto_seeds_from_trends(channel_config)
        # Detect whether we got real trends or fell back to sub_niches
        niche_kws = _niche_keywords(channel_config)
        seed_source = (
            "trend" if any(_trend_matches_niche(s, niche_kws) for s in seeds) else "fallback"
        )
        print(f"[idea_generator] Seed source={seed_source}, seeds={seeds}", file=sys.stderr)

    cfg = merge_keyword_channel_config(channel_config)
    raw_keywords = [{"keyword": s, "score": None, "volume": "", "competition": ""} for s in seeds]
    enriched = [enrich_keyword_item(item, cfg) for item in raw_keywords if item["keyword"]]
    top_keywords = {
        "top_opportunity_keywords": select_by_cluster_limit(
            [item for item in enriched if item.get("bucket") == "top_opportunity_keywords"],
            int(cfg.get("max_keywords_per_intent_cluster", 3)),
        ),
        "long_tail_test_keywords": select_by_cluster_limit(
            [item for item in enriched if item.get("bucket") == "long_tail_test_keywords"],
            int(cfg.get("max_keywords_per_intent_cluster", 3)),
        ),
        "rejected_keywords": [
            item for item in enriched if item.get("bucket") == "rejected_keywords"
        ],
        "all_scored_keywords": enriched,
        "metadata": {
            "version": "local_keyword_scoring_v1",
            "target_language": cfg.get("target_language", "spanish"),
            "target_audience": cfg.get("target_audience", "people_45_plus"),
            "serp_inspection": "disabled",
        },
    }

    selected_keywords = _select_keywords_for_prompt(top_keywords, count)
    if not selected_keywords:
        raise ValueError(
            "No scoreable keywords were found from the given seeds. Try different topics."
        )

    prompt = _idea_gen_prompt(
        channel_config, selected_keywords, count, published_titles=published_titles
    )
    raw = await chatgpt_fn([prompt])
    ideas = parse_ideas(raw)
    if not ideas:
        raise ValueError(f"ChatGPT returned no parseable ideas. Raw:\n{raw[:500]}")

    if with_metadata:
        return ideas, top_keywords, seed_source
    return ideas
