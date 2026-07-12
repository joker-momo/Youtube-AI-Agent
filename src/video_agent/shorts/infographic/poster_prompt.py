"""Per-format 9:16 infographic poster prompts (AI-only, text baked in)."""
from __future__ import annotations

from typing import Any

from video_agent.browser_worker.drivers.chatgpt_image import build_image_gen_prompt

_BASE = (
    "Design ONE dense vertical infographic POSTER (9:16, mobile) for a Spanish "
    "wellness audience. Render ALL the Spanish text below EXACTLY as written, spelled "
    "correctly with accents, large and legible on a phone. Use simple realistic food/"
    "object photos or clean flat icons per item. Keep generous margins; do not crop any "
    "text. Add NO other text, no captions beyond what is listed, and no watermark or logo "
    "EXCEPT the channel brand mark described below (if any) — that is the only permitted "
    "extra mark on the poster. "
    "STRICT TYPOGRAPHIC HIERARCHY: render each item's short label/sub-heading LARGE and "
    "extra-BOLD, and any explanation/note text clearly smaller and lighter beneath it — "
    "a phone viewer must understand the poster from the bold labels alone, without "
    "reading the small text. "
    "VISUAL CONSISTENCY: every item photo/icon must share ONE consistent rendering "
    "style across the whole poster — same lighting, same scale, each subject cleanly "
    "isolated within its own cell (no item drawn in a different art style from the "
    "rest). Prefix each small note/benefit line with a tiny matching mini-icon "
    "(e.g., a heart for a heart benefit) so notes read as designed rows."
)


def _labels(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw = plan.get("items")
    return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []


def _message_match_line(plan: dict[str, Any]) -> str:
    """Topic-first visual contract (2026-07-12, mirrors long-form bug-532).

    The poster imagery must restate the topic by itself: derive the concrete
    objects the header/items actually name (same deterministic vocabulary the
    long-form thumbnail planner uses) and demand they appear as the hero/topic
    imagery, so a 45+ viewer understands the poster without reading small text.
    """
    from video_agent.thumbnail_planner import derive_topic_props

    header = " ".join(
        str(plan.get(k) or "").strip() for k in ("title", "hook_line")
    ).strip()
    body_text = " ".join(
        [str(plan.get("subtitle") or "").strip()]
        + [str(i.get("label") or "").strip() for i in _labels(plan)]
    ).strip()
    props = derive_topic_props(body_text, header)

    line = (
        "MESSAGE MATCH (MANDATORY): from the imagery and BOLD labels ALONE — "
        "without reading any small text — a Spanish adult aged 45+ scanning a "
        "phone feed must understand what this poster is about and what to do. "
        "Every item's photo/icon must depict that item's own words, never a "
        "generic wellness image."
    )
    if props:
        line += (
            " The hero/topic imagery must visibly show: "
            + "; ".join(props)
            + " — the exact objects the poster text names."
        )
    return line


def _format_block(plan: dict[str, Any]) -> str:
    fmt = str(plan.get("poster_format") or "")
    items = _labels(plan)
    labels = [str(i.get("label") or "").strip() for i in items]
    if fmt == "category_grid":
        return (
            "Layout: a grid of "
            f"{len(labels)} labelled cells, each with an icon/photo above its label, an "
            "optional central hero image. Items: " + "; ".join(labels) + "."
        )
    if fmt == "numbered_tips":
        return (
            "Layout: a NUMBERED vertical list (1, 2, 3 …), each row an icon + a short "
            "tip. Render each number as a bold white digit inside a solid CIRCULAR "
            "badge, all badges the same accent color. Numbered items in order: "
            + "; ".join(f"{n}. {t}" for n, t in enumerate(labels, 1)) + "."
        )
    if fmt == "warning_list":
        rows = [
            f"{n}. {str(i.get('label') or '').strip()}"
            + (f" — {str(i.get('note') or '').strip()}" if i.get("note") else "")
            for n, i in enumerate(items, 1)
        ]
        return (
            "Layout: a NUMBERED warning list, each row a food photo + a red CROSS (X) mark "
            "+ a short caution. Render each number as a bold white digit inside a solid "
            "red CIRCULAR badge, all badges the same color. Rows: " + "; ".join(rows) + "."
        )
    if fmt == "myth_vs_truth":
        rows = [
            f'Row {n}: MITO {n} card = "{str(i.get("label") or "").strip()}"; '
            f'VERDAD {n} card = "{str(i.get("note") or "").strip()}"'
            for n, i in enumerate(items, 1)
        ]
        return (
            "Layout: a TWO-COLUMN grid of rounded rectangle cards — a MITO column on "
            "the left (light red/pink card backgrounds) and a VERDAD column on the "
            "right (light green card backgrounds), each numbered pair aligned on the "
            "same row. Each Mito card starts with a small numbered red circular badge "
            "and a red ribbon tag reading \"MITO n\", then a red CROSS (X) icon and the "
            "myth text. Each Verdad card starts with a small numbered green circular "
            "badge and a green ribbon tag reading \"VERDAD n\", then a green CHECK icon "
            "and the truth text. One small relevant photo/icon per card. Rows: "
            + "; ".join(rows) + "."
        )
    if fmt == "timeline_routine":
        rows = [
            f"{str(i.get('time') or '').strip()} — {str(i.get('label') or '').strip()}"
            for i in items
        ]
        return (
            "Layout: a vertical DAY TIMELINE from top (morning) to bottom (night): a "
            "connected line with a dot per moment, the clock time LARGE and bold beside "
            "each dot, an icon and the activity label next to it. Moments in order: "
            + "; ".join(rows) + "."
        )
    if fmt == "checklist_score":
        score_line = str(plan.get("score_line") or "").strip()
        return (
            "Layout: a SELF-CHECK list, each row an empty checkbox (☐) + one short "
            "criterion. Rows: " + "; ".join(labels) + "."
            + (f' At the bottom, a highlighted score band: "{score_line}".' if score_line else "")
        )
    if fmt == "comparison":
        groups_sorted = sorted({str(x.get("group") or "") for x in items})
        first_group = groups_sorted[0] if groups_sorted else ""
        left = [str(i.get("label") or "").strip() for i in items if str(i.get("group") or "") == first_group]
        right = [str(i.get("label") or "").strip() for i in items if str(i.get("label") or "").strip() not in left]
        return (
            "Layout: TWO columns side by side separated by a central divider, a green "
            "check over the good column and a red cross over the other. "
            f"Left column: {', '.join(left)}. Right column: {', '.join(right)}."
        )
    return "Layout: a clean labelled infographic. Items: " + "; ".join(labels) + "."


def _brand_identity_line(channel_config: dict[str, Any] | None) -> str:
    name = str(((channel_config or {}).get("channel") or {}).get("name") or "").strip()
    if not name:
        return ""
    return (
        f'\n\nBrand identity (creative direction): "{name}". Match a calm, '
        f"trustworthy, editorial wellness tone consistent with this identity. "
        f"Additionally, add ONE small rounded brand badge near the bottom of the "
        f'poster: a checkmark icon + the text "{name}" inside a thin bordered pill, '
        f"small and unobtrusive — this is the ONLY on-screen brand mark, do not add "
        f"any other banner, bar, or repeat of the channel name elsewhere. Any small "
        f"decorative accent near this badge should be thematically tied to the "
        f"poster's real topic (matching the header's decorative icons), never a "
        f"fixed generic motif repeated across every topic. Do NOT add any mascot "
        f"character, cartoon person, or speech bubble anywhere on the poster."
    )


def _header_style_line(subtitle: str) -> str:
    line = (
        "HEADER STYLE: split the title across two bold lines with a color change "
        "between them (e.g., first line dark navy, second line a warm accent red or "
        "orange) for strong editorial-magazine impact."
    )
    if subtitle:
        line += (
            " Render the subtitle as a small rounded PILL/badge shape in a solid "
            "brand-accent color (green) with bold white text, centered just below "
            "the title."
        )
    line += (
        " Add two small decorative icons flanking a central circular topic icon just "
        "under the header — simple, thematically related to the topic (e.g., weather "
        "icons either side of a joint for a joint-pain-and-weather topic, a moon and "
        "clock for a sleep topic) — purely decorative, no extra words. Add a thin "
        "dotted horizontal line separating the header from the content below."
    )
    return line


def build_poster_body(plan: dict[str, Any], channel_config: dict[str, Any] | None = None) -> str:
    """The raw poster body WITHOUT the driver's dimension instruction."""
    title = str(plan.get("title") or "").strip()
    subtitle = str(plan.get("subtitle") or "").strip()
    body = _BASE + "\n\n" + f'Big title at the top: "{title}".'
    if subtitle:
        body += f' Subtitle under it: "{subtitle}".'
    body += "\n\n" + _header_style_line(subtitle)
    body += "\n\n" + _message_match_line(plan)
    body += "\n\n" + _format_block(plan)
    body += _brand_identity_line(channel_config)
    return body


def build_poster_prompt(plan: dict[str, Any], channel_config: dict[str, Any] | None = None) -> str:
    """Full prompt (body + portrait dimension instruction) — for logging/inspection."""
    return build_image_gen_prompt(build_poster_body(plan, channel_config), aspect_ratio="9:16")
