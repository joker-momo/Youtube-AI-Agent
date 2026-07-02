"""Deterministic local pre-QA checks run before the Gemini QA round-trip.

Every check here must be mechanical and certain (schema limits, language
contract, YouTube hard limits). Anything editorial — hook strength, audience
fit, medical safety — stays with the Gemini QA stage. A failure routes the
artifact straight back to ChatGPT rework without spending a Gemini session,
so this gate must never produce speculative false positives.
"""
from __future__ import annotations

from typing import Any

from video_agent.operator_shards import _REQUIRED_SCENE_FIELDS
from video_agent.operator_validators import _looks_like_spanish_visual_prompt

# YouTube hard limits (metadata is rejected or truncated beyond these).
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESCRIPTION_MAX = 5000
YOUTUBE_TAGS_TOTAL_MAX = 500

__all__ = [
    "expected_seo_language",
    "local_artifact_issues",
    "scenes_issues",
    "script_issues",
    "seo_issues",
]


def expected_seo_language(channel_config: dict[str, Any]) -> str:
    return (
        (channel_config.get("seo") or {}).get("language")
        or (channel_config.get("audience") or {}).get("language")
        or "es-ES"
    )


def seo_issues(payload: dict[str, Any], channel_config: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    expected = expected_seo_language(channel_config)
    actual = payload.get("language")
    if actual != expected:
        issues.append(
            f"SEO language must be exactly {expected}; got {actual!r}. "
            "Regenerate the SEO artifact in the configured language."
        )

    title = str(payload.get("title") or "").strip()
    if not title:
        issues.append("SEO title is empty.")
    elif len(title) > YOUTUBE_TITLE_MAX:
        issues.append(
            f"SEO title is {len(title)} characters; YouTube allows at most "
            f"{YOUTUBE_TITLE_MAX}. Shorten it without losing the keyword."
        )

    description = str(payload.get("description") or "")
    if not description.strip():
        issues.append("SEO description is empty.")
    elif len(description) > YOUTUBE_DESCRIPTION_MAX:
        issues.append(
            f"SEO description is {len(description)} characters; YouTube allows "
            f"at most {YOUTUBE_DESCRIPTION_MAX}."
        )

    tags = payload.get("tags")
    if not isinstance(tags, list) or not tags:
        issues.append("SEO tags must be a non-empty list.")
    else:
        cleaned = [str(t).strip() for t in tags]
        if any(not t for t in cleaned):
            issues.append("SEO tags contain empty entries.")
        lowered = [t.lower() for t in cleaned if t]
        if len(set(lowered)) != len(lowered):
            issues.append("SEO tags contain duplicates; every tag must be unique.")
        total = sum(len(t) for t in cleaned)
        if total > YOUTUBE_TAGS_TOTAL_MAX:
            issues.append(
                f"SEO tags total {total} characters; YouTube allows at most "
                f"{YOUTUBE_TAGS_TOTAL_MAX} across all tags. Drop the weakest tags."
            )
    return issues


def scenes_issues(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return ["scenes.json contains no scenes."]

    spanish_offenders: list[str] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            issues.append("Every scene must be an object.")
            continue
        scene_id = str(scene.get("id") or "?")
        missing = sorted(_REQUIRED_SCENE_FIELDS - set(scene))
        if missing:
            issues.append(f"Scene {scene_id}: missing fields {missing}.")
        duration = scene.get("duration_sec")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
            issues.append(f"Scene {scene_id}: duration_sec must be a positive number.")
        if not str(scene.get("narration") or "").strip():
            issues.append(f"Scene {scene_id}: narration is empty.")
        prompt = str(scene.get("visual_prompt") or "")
        is_spanish, reason = _looks_like_spanish_visual_prompt(prompt)
        if is_spanish:
            spanish_offenders.append(f"{scene_id} ({reason or 'Spanish detected'})")

    if spanish_offenders:
        summary = ", ".join(spanish_offenders[:5])
        if len(spanish_offenders) > 5:
            summary += f", and {len(spanish_offenders) - 5} more"
        issues.append(
            f"{len(spanish_offenders)} scene visual_prompt fields are Spanish. "
            "Pexels stock search is English-keyword based; rewrite them in "
            f"English. Offenders: {summary}."
        )
    return issues


def script_issues(payload: dict[str, Any]) -> list[str]:
    # The promoter already validates the script schema; only reject the
    # degenerate empty artifact here. Editorial quality is Gemini's job.
    if not isinstance(payload, dict) or not payload:
        return ["Script artifact is empty."]
    return []


def local_artifact_issues(
    artifact: str,
    payload: dict[str, Any],
    channel_config: dict[str, Any],
) -> list[str]:
    if artifact == "seo":
        return seo_issues(payload, channel_config)
    if artifact == "scenes":
        return scenes_issues(payload)
    if artifact == "script":
        return script_issues(payload)
    return []
