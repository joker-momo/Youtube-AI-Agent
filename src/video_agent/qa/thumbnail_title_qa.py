from __future__ import annotations

from typing import Any

from video_agent.qa.common import pass_result, revise_result


def check_thumbnail_title(seo: dict[str, Any], channel_config: dict[str, Any]) -> dict[str, Any]:
    title = seo.get("title", "").strip()
    if not title:
        return revise_result("MISSING_TITLE", "SEO title is required.", "generate_title")
    if len(title) > 90:
        return revise_result("TITLE_TOO_LONG", "SEO title must be 90 characters or fewer.", "shorten_title")
    thumbnail_words = title.replace(":", " ").split()[:8]
    max_words = channel_config.get("qa_rules", {}).get("thresholds", {}).get("max_thumbnail_words", 6)
    if len(thumbnail_words) > max_words + 2:
        return revise_result("THUMBNAIL_TEXT_DENSE", "Thumbnail text candidate is too dense.", "shorten_thumbnail_text")
    if seo.get("ai_disclosure") is not True:
        return revise_result("MISSING_AI_DISCLOSURE", "AI disclosure must be true.", "set_ai_disclosure")
    return pass_result({"title_chars": len(title)})
