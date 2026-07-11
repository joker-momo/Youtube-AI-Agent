"""ChatGPT image-prompt builder for Shorts AI-fallback scenes.

Moved verbatim from ``video_agent.assets.service`` (T4): these helpers turn a
scene dict into the prompt sent to the browser-worker image driver. They are
Shorts-only and must not import ``video_agent.stages.*``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Anatomy/typography guard ported from the long-form graphic_images stage
# (orchestrator/stages/graphic_images.py). The generated card is shown full-bleed
# with no Remotion text overlay, so the image must carry legible baked-in text
# and must never show malformed hands/faces.
_GRAPHIC_QUALITY_GUARD = (
    "Anatomy must look natural: avoid close-up hands, fingers, or utensils held mid-air; "
    "keep hands relaxed, partially out of frame or softly out of focus; absolutely no "
    "malformed hands, extra fingers, or distorted faces. "
    "Set every word in Montserrat (or a near-identical clean geometric bold sans-serif). "
    "Lay the text out with a clear visual hierarchy; make each line LARGE and BOLD with HIGH "
    "CONTRAST so it is crisp and easy to read, never faint or washed out. "
    "Spell everything exactly with correct Spanish accents; add no other text."
)

# NEUTRAL, deliberately UN-branded fallback (mirrors the long-form stage). Real
# brand colours live in ``configs/<channel>/style-dna.json`` — the single source
# of truth; this default only applies when that file is missing/invalid.
_DEFAULT_STYLE: dict[str, Any] = {
    "palette": {
        "background": "#ECECEC",
        "primary": "#3A3A3A",
        "secondary": "#8A8A8A",
        "accent": "#B8B8B8",
        "text": "#1A1A1A",
    },
    "visual_mood": ["calm", "clean", "editorial"],
}


def _brand_style_text(style: dict[str, Any]) -> str:
    """Content-first brand directive (2026-07 graphic layout system): the scene's
    real subject chooses the visual world; the channel palette appears only as
    small accents. Replaces the earlier palette-locked soft-card treatment that
    produced the same cream/green wellness card on every graphic."""
    dp = _DEFAULT_STYLE["palette"]
    p = (style or {}).get("palette") or dp
    bg = p.get("background", dp["background"])
    primary = p.get("primary", dp["primary"])
    sec = p.get("secondary", dp["secondary"])
    accent = p.get("accent", dp["accent"])
    text = p.get("text", dp["text"])
    mood = ", ".join((style or {}).get("visual_mood") or _DEFAULT_STYLE["visual_mood"])
    return (
        f"Brand style — {mood} wellness for Spanish adults 45+ (NOT clickbait). "
        "CONTENT-FIRST art direction: the scene's real subject, action and teaching idea choose "
        "the background, lighting, materials, camera angle and dominant colours. When the content "
        "is concrete (foods, objects, labels), real, appetizing, consistently lit subject "
        "photography must dominate the frame. "
        f"The channel palette (hex) is an ACCENT ONLY, never a full-frame wash: use primary "
        f"{primary}, accent {accent} or secondary {sec} solely for small marks — a numbered badge, "
        f"a check/cross marker, one underline, or a small channel pill (text {text} on background "
        f"{bg} where a small panel is genuinely needed). "
        "Never force a recurring beige/cream/green wellness card, a repeated soft panel behind "
        "every text block, a generic icon grid, or a stock-photo collage. No neon and no harsh "
        "full-bleed gradient. Keep the composition calm, premium and editorial with a clear text "
        "hierarchy and gentle depth."
    )


def load_brand_style(channel_id: str | None) -> str:
    """Brand-style prompt directive for ``channel_id`` from its style-dna.json.

    Falls back to a neutral, un-branded palette (with a loud warning) so a
    missing style file is obvious rather than silently off-brand."""
    style: dict[str, Any] = _DEFAULT_STYLE
    if channel_id:
        sp = Path("configs") / str(channel_id) / "style-dna.json"
        try:
            if sp.exists():
                data = json.loads(sp.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("palette"):
                    style = data
                else:
                    print(
                        f"[shorts.image_prompt] WARNING: style-dna.json invalid at {sp} — "
                        "using the neutral fallback palette (cards will look UN-branded).",
                        flush=True,
                    )
            else:
                print(
                    f"[shorts.image_prompt] WARNING: style-dna.json missing at {sp} — "
                    "using the neutral fallback palette (cards will look UN-branded).",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[shorts.image_prompt] WARNING: failed to load style-dna.json ({exc}); "
                "neutral fallback palette.",
                flush=True,
            )
    return _brand_style_text(style)


def _compact_payload_text(value: Any) -> str:
    """Flatten a layout payload into prompt text without losing its labels."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(filter(None, (_compact_payload_text(item) for item in value)))
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            text = _compact_payload_text(item)
            if text:
                parts.append(f"{key}: {text}")
        return "; ".join(parts)
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _compact_list(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    return "; ".join(cleaned)


def _required_visual_evidence_text(scene: dict[str, Any]) -> str:
    evidence = scene.get("required_visual_evidence") or {}
    if not isinstance(evidence, dict):
        return ""
    sections: list[str] = []
    positive_fields = (
        ("required_actions", "actions"),
        ("required_objects", "objects"),
        ("subject_pose", "subject"),
        ("visibility", "visibility"),
    )
    negative_fields = (
        ("forbidden_pose", "forbidden pose"),
        ("forbidden_context", "avoid setting"),
        ("forbidden_mood", "forbidden mood"),
    )
    for key, label in positive_fields:
        text = _compact_list(evidence.get(key))
        if text:
            sections.append(f"{label}: {text}")
    for key, label in negative_fields:
        text = _compact_list(evidence.get(key))
        if text:
            sections.append(f"{label}: {text}")
    return "\n".join(sections)


def _graphic_layout_contract(layout: str, payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    if layout == "graphic_comparison":
        left = payload.get("left") if isinstance(payload.get("left"), dict) else {}
        right = payload.get("right") if isinstance(payload.get("right"), dict) else {}
        footer = str(payload.get("footer") or "").strip()
        lines = [
            "Layout contract: graphic_comparison.",
            "Use a clean side-by-side composition with two balanced panels over a realistic, softly blurred scene background.",
            "LEFT PANEL:",
            f"- heading exactly: {str(left.get('heading') or '').strip()}",
            f"- supporting line exactly: {str(left.get('text') or '').strip()}",
            "RIGHT PANEL:",
            f"- heading exactly: {str(right.get('heading') or '').strip()}",
            f"- supporting line exactly: {str(right.get('text') or '').strip()}",
        ]
        if footer:
            lines.append(f"Footer exactly: {footer}")
        lines.append("Do not invent extra numbers, calories, grams, medical claims, warning icons, red crosses, or good-versus-bad moral judgment.")
        return "\n".join(lines)
    if layout == "graphic_checklist":
        items = [str(item).strip() for item in (payload.get("items") or []) if str(item).strip()]
        lines = [
            "Layout contract: graphic_checklist.",
            "Create one saveable, premium checklist card integrated into a realistic editorial background.",
            "Show only these checklist items, in this order:",
        ]
        lines.extend(f"- {item}" for item in items)
        lines.append("Do not add extra checklist items, decorative icons that change meaning, tiny footnotes, or dense paragraphs.")
        return "\n".join(lines)
    if layout == "graphic_plate_ratio":
        segments = payload.get("segments") or []
        lines = [
            "Layout contract: graphic_plate_ratio.",
            "Create a clear plate/portion ratio visual with warm realistic food texture and readable segment labels.",
            "Use only these segments:",
        ]
        for segment in segments if isinstance(segments, list) else []:
            if isinstance(segment, dict):
                lines.append(
                    f"- {str(segment.get('label') or '').strip()}: {str(segment.get('value') or '').strip()}"
                )
        lines.append("Do not add calorie math, grams, scales, medical symbols, or extra ratios.")
        return "\n".join(lines)
    if layout == "graphic_label_callout":
        callouts = payload.get("callouts") or []
        lines = [
            "Layout contract: graphic_label_callout.",
            "Create a realistic product/label close-up with a few clean callouts, not a generic template.",
        ]
        product = str(payload.get("productLabel") or "").strip()
        if product:
            lines.append(f"Product label context: {product}")
        lines.append("Use only these callouts:")
        for callout in callouts if isinstance(callouts, list) else []:
            if isinstance(callout, dict):
                note = str(callout.get("note") or "").strip()
                line = (
                    f"- {str(callout.get('label') or '').strip()}: "
                    f"{str(callout.get('value') or '').strip()}"
                )
                if note:
                    line += f" ({note})"
                lines.append(line)
        lines.append("Do not add unrelated nutrition values, brand logos, warning stamps, or tiny legal-style copy.")
        return "\n".join(lines)
    if layout == "graphic_step_list":
        steps = payload.get("steps") or []
        lines = [
            "Layout contract: graphic_step_list.",
            "Create a numbered step-by-step timeline card: ordered items 1, 2, 3 connected by arrows or chevrons, NOT check-marks.",
            "Show only these steps, in this order:",
        ]
        for step in steps if isinstance(steps, list) else []:
            if isinstance(step, dict):
                lines.append(
                    f"- {str(step.get('label') or '').strip()}: {str(step.get('text') or '').strip()}"
                )
        lines.append("Do not add extra steps, check-marks, tiny footnotes, or dense paragraphs.")
        return "\n".join(lines)
    if layout == "graphic_routine_split":
        blocks = payload.get("blocks") or []
        total = str(payload.get("totalLabel") or "").strip()
        lines = [
            "Layout contract: graphic_routine_split.",
            "Create a calm time-block routine card: each block on its own row with its time budget clearly visible.",
        ]
        if total:
            lines.append(f"Total time label: {total}")
        lines.append("Use only these time blocks, in this order:")
        for block in blocks if isinstance(blocks, list) else []:
            if isinstance(block, dict):
                lines.append(
                    f"- {str(block.get('time') or '').strip()}: {str(block.get('text') or '').strip()}"
                )
        lines.append("Do not add extra blocks, alarm-clock urgency, or perfectionist wording.")
        return "\n".join(lines)
    if layout == "graphic_stat":
        title = str(payload.get("title") or "").strip()
        body = str(payload.get("body") or "").strip()
        lines = [
            "Layout contract: graphic_stat.",
            "Create a bold statistic card built around ONE ENORMOUS number as the hero, "
            "the number filling roughly half the card with only a small label — no bullet list.",
        ]
        if title:
            lines.append(f'The enormous focal number/stat, exactly: "{title}"')
        if body:
            lines.append(f'A small label beneath the number, MUCH smaller than the number: "{body}"')
        lines.append("Do not add extra numbers, percentages, charts, or invented statistics.")
        return "\n".join(lines)
    if layout == "graphic_myth":
        title = str(payload.get("title") or "").strip()
        body = str(payload.get("body") or "").strip()
        lines = [
            "Layout contract: graphic_myth.",
            "Create a myth-versus-fact card: two stacked contrasting rows — a 'Mito' row with a "
            "soft cross icon in the brand secondary colour above a 'Realidad' row with a check "
            "icon in the brand accent colour.",
        ]
        if title:
            lines.append(f'"Mito" row text, exactly: "{title}"')
        if body:
            lines.append(f'"Realidad" row text, exactly: "{body}"')
        lines.append("Do not add fear language, medical symbols, or extra claims.")
        return "\n".join(lines)
    if layout == "graphic_do_dont":
        bad = str(payload.get("bad") or "").strip()
        good = str(payload.get("good") or "").strip()
        lines = [
            "Layout contract: graphic_do_dont.",
            "Create a do-versus-don't card: TWO real photos side by side — the worse choice "
            "desaturated with a soft cross marker in the brand secondary colour, the better "
            "choice bright with a check marker in the brand accent colour.",
        ]
        if bad:
            lines.append(f'LEFT photo (the WORSE choice), label exactly: "{bad}"')
        if good:
            lines.append(f'RIGHT photo (the BETTER choice), label exactly: "{good}"')
        lines.append("Do not add moral shaming, red warning stamps, or invented claims.")
        return "\n".join(lines)
    if layout == "graphic_recipe_snapshot":
        items = [str(item).strip() for item in (payload.get("items") or []) if str(item).strip()]
        lines = [
            "Layout contract: graphic_recipe_snapshot.",
            f"Create a recipe-snapshot card: {len(items) or 2}-3 real food photos as clean "
            "side-by-side tiles, each with a short label — a practical example, not a text list.",
            "Show only these foods, in this order:",
        ]
        lines.extend(f"- {item}" for item in items)
        lines.append("Do not add calorie math, grams, extra ingredients, or medical claims.")
        return "\n".join(lines)
    if layout == "graphic_quote_portrait":
        quote = str(payload.get("title") or "").strip()
        lines = [
            "Layout contract: graphic_quote_portrait.",
            "Create a magazine-style quote-portrait card: ONE large quotation in quote marks "
            "beside a warm candid portrait of a mature adult 50+, editorial cover feel, "
            "no boxed text panel, no bullet list.",
        ]
        if quote:
            lines.append(f'The quotation, exactly: "{quote}"')
        lines.append("Do not add attribution names, extra sentences, or stocky posed smiles.")
        return "\n".join(lines)
    if layout == "graphic_evidence_nugget":
        title = str(payload.get("title") or "").strip()
        body = str(payload.get("body") or "").strip()
        lines = [
            "Layout contract: graphic_evidence_nugget.",
            "Create an evidence-nugget card: ONE number/fact as a large documentary-style "
            "lower-third over a real photo, serious and credible, minimal extra text.",
        ]
        if title:
            lines.append(f'The bold number/fact, exactly: "{title}"')
        if body:
            lines.append(f'A small context line beneath it: "{body}"')
        lines.append("Do not invent citations, journal names, percentages, or extra statistics.")
        return "\n".join(lines)
    if layout == "graphic_warning":
        items = [str(item).strip() for item in (payload.get("items") or []) if str(item).strip()]
        lines = [
            "Layout contract: graphic_warning.",
            "Create a cautionary card with an 'avoid this' tone: each item on its own row led by "
            "a soft cross / caution icon in the brand secondary colour (NOT check-marks).",
            "Show only these items, in this order:",
        ]
        lines.extend(f"- {item}" for item in items)
        lines.append(
            "Keep the tone calm and helpful, never alarmist: no red danger stamps, skulls, "
            "sirens, or fear language."
        )
        return "\n".join(lines)
    return f"Layout contract: {layout}.\nUse the payload exactly; do not invent extra teaching points."


def _surface_style_contract(surface_style: str) -> str:
    """Prompt directive for the planner-preferred surface families (2026-07
    content-first system). Legacy surface values need no extra directive."""
    contracts = {
        "hero_stat": (
            "Surface style hero_stat: ONE enormous focal number/fact dominates the frame "
            "with a much smaller label beneath it — no bullet list, no extra panels."
        ),
        "binary_split": (
            "Surface style binary_split: two contrasting halves with equal visual weight, "
            "a clear divider, and one marker per side (check on the better side, soft cross "
            "on the other when the layout implies a judgment)."
        ),
        "numbered_photo_bands": (
            "Surface style numbered_photo_bands: compose the items as 2-4 HORIZONTAL, "
            "edge-to-edge (full-bleed) photo bands stacked vertically — one band per item, "
            "each band a real photograph of that item. Each band carries ONE large solid "
            "CIRCULAR numbered badge (all badges the same accent colour) and a bold label of "
            "at most four words. Do not add paragraphs, footnotes, invented claims, or a "
            "separate card around every band."
        ),
        "annotated_object": (
            "Surface style annotated_object: one realistic hero object/label close-up with "
            "a few clean callout lines pointing at the real details — not a template card."
        ),
        "photo_tiles": (
            "Surface style photo_tiles: clean side-by-side real-photo tiles, one short label "
            "per tile, consistent lighting and scale across tiles."
        ),
    }
    return contracts.get(surface_style, "")


def build_scene_image_prompt(scene: dict[str, Any], query: str, brand_style: str = "") -> str:
    """Build the ChatGPT image prompt for AI fallback scenes.

    For former ``graphic_*`` scenes the generated image must carry the same
    teaching content as the graphic payload, but as a richer editorial visual
    instead of a rigid renderer card. ``brand_style`` (see ``load_brand_style``)
    injects the channel palette/mood directive ported from the long-form
    graphic_images stage so cards render ON-brand.
    """
    layout = str(scene.get("layout") or "")
    payload = scene.get("layout_payload") or {}
    title = (
        str(payload.get("title") or "")
        if isinstance(payload, dict)
        else ""
    ).strip()
    on_screen = str(scene.get("on_screen_text") or title or "").strip()
    caption = str(scene.get("caption") or "").strip()
    narration = str(scene.get("narration") or "").strip()
    visual_prompt = str(scene.get("visual_prompt") or query or "").strip()
    payload_text = _compact_payload_text(payload)
    evidence_text = _required_visual_evidence_text(scene)
    source_graphic_layout = str(scene.get("generated_image_source_layout") or "").strip()
    graphic_layout = (
        layout
        if layout.startswith("graphic_")
        else (
            source_graphic_layout
            if source_graphic_layout.startswith("graphic_")
            else ("graphic_generated" if str(scene.get("visual_type") or "") == "graphic" or str(scene.get("asset_strategy") or "") == "graphic_fallback" else "")
        )
    )
    is_graphic = bool(graphic_layout)
    is_cta = layout in {"short_cta", "cta"} or str(scene.get("retention_function") or "") == "cta"
    is_hook = layout in {"short_hook", "hook"} or str(scene.get("retention_function") or "") == "hook"

    if is_hook:
        # The 3-second hook decides the swipe. A clean, human, emotional first
        # frame beats an empty-room stock clip. CLEAN background only (renderer
        # overlays the hook headline) — no trigger words so the driver keeps its
        # "no text overlays" guard.
        return (
            "A vertical medium close-up photograph of one calm adult aged 50 to 60 at home "
            "(living room or kitchen), face clearly visible and centered, expression subtly "
            "overwhelmed yet composed, gently holding a phone or a warm mug of tea, soft warm "
            "natural light, shallow depth of field, photorealistic editorial photography.\n"
            f"Scene mood: {caption or narration or visual_prompt}"
        )

    if is_graphic:
        layout_contract = _graphic_layout_contract(graphic_layout, payload if isinstance(payload, dict) else {})
        brand_block = f"{brand_style.strip()}\n" if brand_style.strip() else ""
        surface_contract = (
            _surface_style_contract(str(payload.get("surface_style") or ""))
            if isinstance(payload, dict)
            else ""
        )
        surface_block = f"{surface_contract}\n" if surface_contract else ""
        return (
            "Create a premium vertical editorial image for a Spanish wellness Short for adults 45+. "
            "Replace a flat renderer card with a natural, polished visual that still carries the teaching content exactly. "
            "Follow a content-first art direction: the teaching content decides the visual world, "
            "and brand colours appear only as small accents (badge, marker, underline, pill). "
            "Use warm Mediterranean light, realistic textures, tasteful magazine-style composition, and large legible Spanish typography inside mobile safe margins. "
            "Make the first frame useful and readable without zooming. "
            "Do not use a plain beige card, generic icons, stock-photo collage, watermark, tiny text, extra claims, or English wording.\n"
            f"{brand_block}"
            f"{_GRAPHIC_QUALITY_GUARD}\n"
            f"Scene layout: {graphic_layout}\n"
            f"{surface_block}"
            f"Scene visual idea: {visual_prompt}\n"
            f"Main headline to include exactly: {on_screen or title}\n"
            f"{layout_contract}\n"
            f"Full payload reference: {payload_text}\n"
            f"Required visual evidence:\n{evidence_text or 'Use the scene visual idea and payload as the source of truth.'}\n"
            f"Narration context: {narration or caption}\n"
            "Keep every written element short, high contrast, and visually integrated with the scene."
        )
    if is_cta:
        # CLEAN background only — the renderer overlays the CTA headline itself, so
        # the image must contain NO burned-in wording (a generated image with the
        # headline plus the renderer overlay produced two overlapping texts). Phrase
        # it WITHOUT trigger words (text/title/overlay/word/logo/watermark) so the
        # driver keeps its default "no text overlays, no watermark" guard.
        return (
            "A warm, inviting vertical wellness photograph for adults 45+. "
            "A calm mature 45+ adult in a peaceful walking or at-home wellness moment, "
            "soft natural golden light, gentle shallow depth of field, serene and hopeful mood, "
            "with plenty of calm empty space in the upper third of the frame. "
            "Photorealistic editorial photography.\n"
            f"Scene mood: {caption or narration or visual_prompt}"
        )
    if str(scene.get("asset_strategy") or "") == "ai_image_preferred" or evidence_text:
        return (
            "Create a premium photorealistic lifestyle image for a Spanish wellness Short for adults 45+. "
            "Use a realistic mature adult, warm natural Mediterranean light, calm editorial composition, and a clear opening action. "
            "The image should feel like a real photographed moment, not a template, stock collage, illustration, or poster.\n"
            f"Scene visual idea: {visual_prompt or query}\n"
            f"Required visual evidence:\n{evidence_text or 'Follow the scene visual idea exactly.'}\n"
            "No readable signage, captions, UI, numbers, commercial marks, medical symbols, scales, alarmist colors, or shame/fear mood."
        )
    return visual_prompt or query
