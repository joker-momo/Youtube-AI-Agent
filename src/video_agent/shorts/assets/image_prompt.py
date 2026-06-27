"""ChatGPT image-prompt builder for Shorts AI-fallback scenes.

Moved verbatim from ``video_agent.assets.service`` (T4): these helpers turn a
scene dict into the prompt sent to the browser-worker image driver. They are
Shorts-only and must not import ``video_agent.stages.*``.
"""

from __future__ import annotations

import json
from typing import Any


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
    return f"Layout contract: {layout}.\nUse the payload exactly; do not invent extra teaching points."


def build_scene_image_prompt(scene: dict[str, Any], query: str) -> str:
    """Build the ChatGPT image prompt for AI fallback scenes.

    For former ``graphic_*`` scenes the generated image must carry the same
    teaching content as the graphic payload, but as a richer editorial visual
    instead of a rigid renderer card.
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
        return (
            "Create a premium vertical editorial image for a Spanish wellness Short for adults 45+. "
            "Replace a flat renderer card with a natural, polished visual that still carries the teaching content exactly. "
            "Use warm Mediterranean light, realistic textures, tasteful magazine-style composition, and large legible Spanish typography inside mobile safe margins. "
            "Make the first frame useful and readable without zooming. "
            "Do not use a plain beige card, generic icons, stock-photo collage, watermark, tiny text, extra claims, or English wording.\n"
            f"Scene layout: {graphic_layout}\n"
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
