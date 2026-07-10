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
    "reading the small text."
)


def _labels(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw = plan.get("items")
    return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []


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
            "tip. Numbered items in order: "
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
            "+ a short caution. Rows: " + "; ".join(rows) + "."
        )
    if fmt == "myth_vs_truth":
        rows = [
            f'"Mito: {str(i.get("label") or "").strip()}" -> "Verdad: {str(i.get("note") or "").strip()}"'
            for i in items
        ]
        return (
            "Layout: stacked MYTH-vs-TRUTH rows. Each row has TWO lines: the myth line "
            "with a red CROSS (X) and the word \"Mito:\", then directly under it the truth "
            "line with a green CHECK and the word \"Verdad:\". Rows in order: "
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
    # A full "Name: Tagline" channel name is too long for a corner tag; use just
    # the part before the colon (short, still recognizable) for the on-screen mark.
    short_name = name.split(":", 1)[0].strip() or name
    return (
        f'\n\nBrand identity (creative direction): "{name}". Match a calm, '
        f"trustworthy, editorial wellness tone consistent with this identity. "
        f"Additionally, add a BOTTOM BANNER: a solid-color bar spanning the full "
        f'width at the very bottom of the poster, with the channel name "{short_name}" '
        f"in bold white or cream text centered inside it — clearly legible at a "
        f"glance, like a YouTube channel watermark bar. Keep the banner short "
        f"(one line, ~5-8% of the poster height) so it never covers or crowds any "
        f"item's text, icon, or the numbered list above it."
    )


def build_poster_body(plan: dict[str, Any], channel_config: dict[str, Any] | None = None) -> str:
    """The raw poster body WITHOUT the driver's dimension instruction."""
    title = str(plan.get("title") or "").strip()
    subtitle = str(plan.get("subtitle") or "").strip()
    body = _BASE + "\n\n" + f'Big title at the top: "{title}".'
    if subtitle:
        body += f' Subtitle under it: "{subtitle}".'
    body += "\n\n" + _format_block(plan)
    body += _brand_identity_line(channel_config)
    return body


def build_poster_prompt(plan: dict[str, Any], channel_config: dict[str, Any] | None = None) -> str:
    """Full prompt (body + portrait dimension instruction) — for logging/inspection."""
    return build_image_gen_prompt(build_poster_body(plan, channel_config), aspect_ratio="9:16")
