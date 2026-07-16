"""Per-format 9:16 infographic poster prompts (AI-only, text baked in).

Color policy (bug-541): this module hardcodes NO color names or hex values.
Every appearance instruction binds a semantic role (canvas, headline_1,
positive, negative, …) to an exact hex resolved from the channel's style DNA.
Role assignment rotates deterministically with the poster's own content, so two
different ideas on the same brand stop converging on one fixed recipe while the
palette itself stays canonical.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any

from video_agent.browser_worker.drivers.chatgpt_image import build_image_gen_prompt
from video_agent.style_dna import DEFAULT_STYLE, is_valid_hex, load_style_dna_from_config

# Palette entries used as CHROMATIC role sources (rotated per poster). Canvas and
# body text stay pinned to background/text so legibility never rotates away.
_CHROMATIC_KEYS = ("primary", "secondary", "accent")
_NEUTRAL_KEYS = ("background", "text")
# Deterministic role permutations of the chromatic entries (6 for 3 colors).
_ROLE_PERMUTATIONS = tuple(itertools.permutations(range(len(_CHROMATIC_KEYS))))


def _validated_palette(channel_config: dict[str, Any] | None) -> dict[str, str]:
    """Canonical palette from style DNA; centralized neutral fallback per key."""
    raw = (load_style_dna_from_config(channel_config) or {}).get("palette") or {}
    fallback = DEFAULT_STYLE["palette"]
    out: dict[str, str] = {}
    for key in (*_NEUTRAL_KEYS, *_CHROMATIC_KEYS):
        value = raw.get(key)
        out[key] = value.strip() if is_valid_hex(value) else fallback[key]
    return out


def _content_fingerprint(plan: dict[str, Any]) -> str:
    """Stable digest of the CONTENT-bearing plan fields only.

    Uses sha256 over a canonical JSON projection — never Python's randomized
    ``hash()``, no timestamps and no retry counters — so the same poster keeps
    one palette across QA retries while different ideas rotate roles (R3/R8).
    """
    items = [
        {
            "label": str(i.get("label") or "").strip(),
            "note": str(i.get("note") or "").strip(),
            "time": str(i.get("time") or "").strip(),
            "group": str(i.get("group") or "").strip(),
        }
        for i in _labels(plan)
    ]
    payload = {
        "format": str(plan.get("poster_format") or "").strip(),
        "title": str(plan.get("title") or "").strip(),
        "subtitle": str(plan.get("subtitle") or "").strip(),
        "hook_line": str(plan.get("hook_line") or "").strip(),
        "items": items,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _srgb_channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of ``#RRGGBB``."""
    h = hex_color.strip().lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _srgb_channel(r) + 0.7152 * _srgb_channel(g) + 0.0722 * _srgb_channel(b)


def _contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colors (1.0 … 21.0)."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _best_foreground_on(fill: str, candidates: tuple[str, ...]) -> str:
    """Highest-contrast palette candidate for text over ``fill`` (deterministic:
    ties resolve to the first candidate in the given order) — KTD3."""
    return max(candidates, key=lambda c: _contrast_ratio(c, fill))


def build_effective_palette(
    plan: dict[str, Any], channel_config: dict[str, Any] | None = None
) -> dict[str, str]:
    """Semantic role -> exact hex, all sourced from the channel style DNA.

    The chromatic entries are permuted by a stable digest of the poster's own
    content, so role assignment varies per idea but is byte-identical for the
    same plan + palette on every retry (R3/R4/R8; KTD2).
    """
    palette = _validated_palette(channel_config)
    chromatic = [palette[k] for k in _CHROMATIC_KEYS]
    digest = _content_fingerprint(plan)
    perm = _ROLE_PERMUTATIONS[int(digest[:8], 16) % len(_ROLE_PERMUTATIONS)]
    rotated = [chromatic[i] for i in perm]
    badge_fill = rotated[2]
    neutrals = (palette["background"], palette["text"])
    return {
        "canvas": palette["background"],
        "body_text": palette["text"],
        "headline_1": rotated[0],
        "headline_2": rotated[1],
        "badge_fill": badge_fill,
        "badge_text": _best_foreground_on(badge_fill, neutrals),
        "positive": rotated[0],
        "negative": rotated[1],
        "divider_accent": rotated[2],
    }


def _palette_contract_line(roles: dict[str, str]) -> str:
    """The single mandatory role->hex contract every block references (KTD4)."""
    listing = "; ".join(f"{role} = {value}" for role, value in roles.items())
    return (
        "PALETTE CONTRACT (MANDATORY — use these EXACT hex values and nothing "
        f"else): {listing}. Every surface, text, badge, icon, divider and state "
        "color on this poster must come from this list. Do NOT substitute or "
        "replace it with your habitual infographic scheme (navy headline, red/"
        "orange accent, green pill) or any color outside the list. Keep strong "
        "readable contrast: body and headline text must stay clearly legible on "
        "the canvas, and text on a filled badge must use the badge text color "
        "given above."
    )

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


def _format_block(plan: dict[str, Any], roles: dict[str, str]) -> str:
    fmt = str(plan.get("poster_format") or "")
    items = _labels(plan)
    labels = [str(i.get("label") or "").strip() for i in items]
    if fmt == "category_grid":
        return (
            "Layout: a grid of "
            f"{len(labels)} labelled cells separated by thin gridlines in the divider/"
            f"accent color ({roles['divider_accent']}), each with an icon/photo above "
            f"its label lettered in the body text color ({roles['body_text']}), an "
            "optional central hero image. Items: " + "; ".join(labels) + "."
        )
    if fmt == "numbered_tips":
        return (
            "Layout: a NUMBERED vertical list (1, 2, 3 …), each row an icon + a short "
            "tip. Render each number as a bold digit in the badge text color "
            f"({roles['badge_text']}) inside a solid CIRCULAR badge filled with the "
            f"badge fill color ({roles['badge_fill']}); all badges use that same "
            "fill. Numbered items in order: "
            + "; ".join(f"{n}. {t}" for n, t in enumerate(labels, 1)) + "."
        )
    if fmt == "warning_list":
        rows = [
            f"{n}. {str(i.get('label') or '').strip()}"
            + (f" — {str(i.get('note') or '').strip()}" if i.get("note") else "")
            for n, i in enumerate(items, 1)
        ]
        return (
            "Layout: a NUMBERED warning list, each row a food photo + a CROSS (X) mark "
            f"drawn in the negative/warning color ({roles['negative']}) + a short "
            "caution. Render each number as a bold digit in the badge text color "
            f"({roles['badge_text']}) inside a solid CIRCULAR badge filled with the "
            f"negative/warning color ({roles['negative']}); all badges use that same "
            "fill. Rows: " + "; ".join(rows) + "."
        )
    if fmt == "myth_vs_truth":
        rows = [
            f'Row {n}: MITO {n} card = "{str(i.get("label") or "").strip()}"; '
            f'VERDAD {n} card = "{str(i.get("note") or "").strip()}"'
            for n, i in enumerate(items, 1)
        ]
        return (
            "Layout: a TWO-COLUMN grid of rounded rectangle cards — a MITO column on "
            "the left and a VERDAD column on the right, each numbered pair aligned on "
            "the same row. Tint the MITO cards with a soft, low-opacity wash of the "
            f"negative/warning color ({roles['negative']}) and the VERDAD cards with a "
            f"soft, low-opacity wash of the positive color ({roles['positive']}), both "
            f"still readable under body text ({roles['body_text']}). Each Mito card "
            "starts with a small numbered circular badge and a ribbon tag reading "
            f"\"MITO n\" both filled with the negative/warning color ({roles['negative']}) "
            f"and lettered in the badge text color ({roles['badge_text']}), then a CROSS "
            f"(X) icon in the negative/warning color ({roles['negative']}) and the myth "
            "text. Each Verdad card starts with a small numbered circular badge and a "
            f"ribbon tag reading \"VERDAD n\" both filled with the positive color "
            f"({roles['positive']}) and lettered in the badge text color "
            f"({roles['badge_text']}), then a CHECK icon in the positive color "
            f"({roles['positive']}) and the truth text. One small relevant photo/icon per "
            "card. Rows: " + "; ".join(rows) + "."
        )
    if fmt == "timeline_routine":
        rows = [
            f"{str(i.get('time') or '').strip()} — {str(i.get('label') or '').strip()}"
            for i in items
        ]
        return (
            "Layout: a vertical DAY TIMELINE from top (morning) to bottom (night): a "
            f"connected line drawn in the divider/accent color ({roles['divider_accent']}) "
            f"with a dot per moment filled in the badge fill color ({roles['badge_fill']}), "
            "the clock time LARGE and bold beside each dot, an icon and the activity "
            "label next to it. Moments in order: " + "; ".join(rows) + "."
        )
    if fmt == "checklist_score":
        score_line = str(plan.get("score_line") or "").strip()
        return (
            "Layout: a SELF-CHECK list, each row an empty checkbox (☐) outlined in the "
            f"divider/accent color ({roles['divider_accent']}) + one short criterion. "
            "Rows: " + "; ".join(labels) + "."
            + (
                " At the bottom, a highlighted score band filled with the badge fill "
                f"color ({roles['badge_fill']}) and lettered in the badge text color "
                f'({roles["badge_text"]}): "{score_line}".'
                if score_line
                else ""
            )
        )
    if fmt == "comparison":
        groups_sorted = sorted({str(x.get("group") or "") for x in items})
        first_group = groups_sorted[0] if groups_sorted else ""
        left = [str(i.get("label") or "").strip() for i in items if str(i.get("group") or "") == first_group]
        right = [str(i.get("label") or "").strip() for i in items if str(i.get("label") or "").strip() not in left]
        return (
            "Layout: TWO columns side by side separated by a central divider drawn in "
            f"the divider/accent color ({roles['divider_accent']}), a CHECK mark in the "
            f"positive color ({roles['positive']}) over the recommended column and a "
            f"CROSS mark in the negative/warning color ({roles['negative']}) over the "
            f"other. Left column: {', '.join(left)}. Right column: {', '.join(right)}."
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


def _header_style_line(subtitle: str, roles: dict[str, str]) -> str:
    line = (
        "HEADER STYLE: split the title across two bold lines with a color change "
        f"between them — first line in the headline 1 color ({roles['headline_1']}), "
        f"second line in the headline 2 color ({roles['headline_2']}) — for strong "
        "editorial-magazine impact. Both lines must stay clearly readable on the "
        f"canvas ({roles['canvas']})."
    )
    if subtitle:
        line += (
            " Render the subtitle as a small rounded PILL/badge shape filled with the "
            f"badge fill color ({roles['badge_fill']}) and bold lettering in the badge "
            f"text color ({roles['badge_text']}), centered just below the title."
        )
    line += (
        " Add two small decorative icons flanking a central circular topic icon just "
        "under the header — simple, thematically related to the topic (e.g., weather "
        "icons either side of a joint for a joint-pain-and-weather topic, a moon and "
        "clock for a sleep topic) — purely decorative, no extra words. Add a thin "
        "dotted horizontal line in the divider/accent color "
        f"({roles['divider_accent']}) separating the header from the content below."
    )
    return line


def build_poster_body(plan: dict[str, Any], channel_config: dict[str, Any] | None = None) -> str:
    """The raw poster body WITHOUT the driver's dimension instruction."""
    title = str(plan.get("title") or "").strip()
    subtitle = str(plan.get("subtitle") or "").strip()
    # ONE effective mapping per call feeds the contract, header and format block,
    # so the logged prompt and the sent body can never diverge (KTD4).
    roles = build_effective_palette(plan, channel_config)
    body = _BASE + "\n\n" + f'Big title at the top: "{title}".'
    if subtitle:
        body += f' Subtitle under it: "{subtitle}".'
    body += "\n\n" + _palette_contract_line(roles)
    body += "\n\n" + _header_style_line(subtitle, roles)
    body += "\n\n" + _message_match_line(plan)
    body += "\n\n" + _format_block(plan, roles)
    body += _brand_identity_line(channel_config)
    return body


def wrap_poster_body(body: str) -> str:
    """Wrap an ALREADY-BUILT body with the portrait dimension instruction.

    Callers that must log and send the same prompt build the body once and wrap
    that exact string, so the audit log can never show a different effective
    palette from what ``image_fn`` received (KTD4/R7)."""
    return build_image_gen_prompt(body, aspect_ratio="9:16")


def build_poster_prompt(plan: dict[str, Any], channel_config: dict[str, Any] | None = None) -> str:
    """Full prompt (body + portrait dimension instruction) — for logging/inspection."""
    return wrap_poster_body(build_poster_body(plan, channel_config))
