"""Build ``short_seo.json`` for a Short (LLM-generated, parsed + normalized)."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable

from video_agent.shorts import paths, prompts
from video_agent.shorts.idea_preservation import validate_seo_idea_consistency
from video_agent.storage.atomic import atomic_write_json


# Generic gym/virality tags that almost never match a Spain-first wellness
# Short for 45+. Off-topic tags push the Short to the wrong audience and
# tank retention, so we strip them post-LLM as a hard safety net even when
# the prompt told the model not to emit them.
_FORBIDDEN_HASHTAGS = {
    "#gym", "#fitness", "#workout", "#crossfit", "#musculacion",
    "#musculación", "#pesas", "#cardio", "#abs", "#motivation",
    "#mindset", "#shortsviral", "#fyp", "#parati", "#viral",
    "#foryou", "#trending",
}

_DEFAULT_FALLBACK_HASHTAGS = ["#bienestar", "#vida45plus", "#saludable", "#shorts"]

_HASHTAG_ALIASES = {
    "#nutricion45": "#nutricion",
    "#nutrición45": "#nutricion",
}


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _invoke(llm_fn: Callable[..., str], kind: str, prompt: str) -> str:
    try:
        return llm_fn(prompt)
    except TypeError:
        return llm_fn(kind, prompt)


def _normalize_hashtags(raw_tags: Any) -> list[str]:
    """Lowercase, prefix '#', dedupe, drop forbidden + empty entries."""
    if not isinstance(raw_tags, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for entry in raw_tags:
        if not isinstance(entry, str):
            continue
        raw = entry.strip().lower()
        if not raw:
            continue
        raw_parts = re.findall(r"#[^#\s]+", raw) if raw.count("#") > 1 else [raw]
        for raw_part in raw_parts:
            tag = raw_part.strip().lower()
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = "#" + tag.lstrip("#")
            # Strip whitespace inside (LLM sometimes emits "# salud mental") and
            # punctuation around the token, while preserving Spanish letters.
            tag = "#" + re.sub(r"[^\wáéíóúüñ]+", "", "".join(tag[1:].split()), flags=re.IGNORECASE)
            tag = _HASHTAG_ALIASES.get(tag, tag)
            if not tag or tag == "#":
                continue
            if tag in _FORBIDDEN_HASHTAGS:
                continue
            if tag in seen:
                continue
            seen.add(tag)
            cleaned.append(tag)
    return cleaned


def _description_with_spaced_hashtags(description: str, hashtags: list[str]) -> str:
    """Ensure visible YouTube hashtags are separated and match normalized tags."""
    base = re.sub(r"(?:\s*#[^#\s]+)+\s*$", "", description.strip()).strip()
    tag_text = " ".join(hashtags)
    if not tag_text:
        return base
    return f"{base} {tag_text}".strip() if base else tag_text


def build_short_seo(
    long_job_dir: Path,
    short_id: str,
    short_plan: dict,
    short_script: dict,
    channel_config: dict,
    llm_fn: Callable[..., str],
    long_video_url: str = "",
) -> dict[str, Any]:
    prompt = prompts.short_seo_prompt(channel_config, short_plan, short_script, long_video_url)
    parsed = _parse(_invoke(llm_fn, "seo", prompt))
    funnel = (channel_config.get("shorts") or {}).get("funnel") or {}
    pinned_template = funnel.get("pinned_comment_template", "")
    pinned = parsed.get("pinned_comment") or (
        pinned_template.replace("{long_video_url}", long_video_url) if pinned_template else ""
    )
    hashtags = _normalize_hashtags(parsed.get("hashtags"))
    if not hashtags:
        hashtags = list(_DEFAULT_FALLBACK_HASHTAGS)
    # YouTube allows many tags but the spec asks for 3-5 visible hashtags.
    hashtags = hashtags[:5]
    title = (parsed.get("title") or short_script.get("hook", "")).strip()
    if len(title) > 60:
        title = title[:60].rstrip() + "…"
    description = _description_with_spaced_hashtags((parsed.get("description") or "").strip(), hashtags)
    seo = {
        "short_id": short_id,
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "pinned_comment": (pinned or "").strip(),
        "long_video_url": long_video_url,
        "language": "es-ES",
        "ai_disclosure": True,
    }
    consistency_issues = validate_seo_idea_consistency(seo, short_script)
    if any(issue.severity in {"blocking_error", "repairable_error"} for issue in consistency_issues):
        detail = "; ".join(issue.detail for issue in consistency_issues)
        raise ValueError(f"SEO idea fidelity validation failed: {detail}")
    jd = paths.short_json_dir(long_job_dir, short_id)
    jd.mkdir(parents=True, exist_ok=True)
    atomic_write_json(jd / paths.SHORT_SEO_FILE, seo)
    return seo
