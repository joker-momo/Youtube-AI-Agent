from __future__ import annotations

from typing import Any

from video_agent.qa.common import pass_result, revise_result


def check_thumbnail_title(seo: dict[str, Any], channel_config: dict[str, Any]) -> dict[str, Any]:
    title = seo.get("title", "").strip()
    if not title:
        return revise_result("MISSING_TITLE", "SEO title is required.", "generate_title")
    if len(title) > 90:
        return revise_result("TITLE_TOO_LONG", "SEO title must be 90 characters or fewer.", "shorten_title")
    thresholds = channel_config.get("qa_rules", {}).get("thresholds", {})
    min_words = thresholds.get("min_thumbnail_words", 3)
    max_words = thresholds.get("max_thumbnail_words", 6)
    candidates: list[tuple[str, str]] = [
        ("selected thumbnail_text", str(seo.get("thumbnail_text") or "").strip())
    ]
    for index, variant in enumerate(seo.get("title_variants") or [], start=1):
        if isinstance(variant, dict):
            candidates.append(
                (f"variant {index}", str(variant.get("thumbnail_text") or "").strip())
            )

    for label, thumbnail_text in candidates:
        if not thumbnail_text:
            return revise_result(
                "MISSING_THUMBNAIL_TEXT",
                f"Thumbnail text is required for {label}.",
                "generate_thumbnail_text",
            )
        word_count = len(thumbnail_text.replace(":", " ").split())
        if word_count < min_words:
            return revise_result(
                "THUMBNAIL_TEXT_SPARSE",
                f"Thumbnail text for {label} has {word_count} words; minimum is {min_words}.",
                "expand_thumbnail_text",
            )
        if word_count > max_words:
            return revise_result(
                "THUMBNAIL_TEXT_DENSE",
                f"Thumbnail text for {label} has {word_count} words; maximum is {max_words}.",
                "shorten_thumbnail_text",
            )
    if seo.get("ai_disclosure") is not True:
        return revise_result("MISSING_AI_DISCLOSURE", "AI disclosure must be true.", "set_ai_disclosure")
    return pass_result({"title_chars": len(title)})
