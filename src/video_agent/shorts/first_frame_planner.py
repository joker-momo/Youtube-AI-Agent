"""Deterministic first-frame planning for Shorts scene 1."""
from __future__ import annotations

import copy
import re
from typing import Any

FIRST_FRAME_STRATEGIES = [
    "evidence_closeup",
    "object_contrast",
    "graphic_proof",
    "human_reaction",
    "symbolic_ai_image",
]


def _topic_text(short_plan: dict[str, Any], scene: dict[str, Any] | None = None) -> str:
    parts = [
        str((short_plan or {}).get(key) or "")
        for key in ("title", "topic_family", "hook_text", "hook_angle", "viewer_pain", "curiosity_gap")
    ]
    if scene:
        parts.extend(str(scene.get(key) or "") for key in ("visual_prompt", "on_screen_text", "narration", "layout"))
    return " ".join(parts).lower()


def _topic_tokens(short_plan: dict[str, Any], scene: dict[str, Any] | None = None) -> set[str]:
    return set(re.findall(r"[a-záéíóúüñ]+", _topic_text(short_plan, scene)))


def _is_nutrition_label(short_plan: dict[str, Any], scene: dict[str, Any] | None = None) -> bool:
    tokens = _topic_tokens(short_plan, scene)
    object_terms = {
        "pan",
        "bread",
        "integral",
        "yogur",
        "azucar",
        "azúcar",
    }
    label_terms = {
        "ingrediente",
        "ingredient",
        "label",
        "etiqueta",
    }
    if tokens & object_terms:
        return True
    if "nutrition" in tokens and tokens & label_terms:
        return True
    return False


def _is_lifestyle(short_plan: dict[str, Any], scene: dict[str, Any] | None = None) -> bool:
    text = _topic_text(short_plan, scene)
    return any(term in text for term in ("sleep", "sueño", "dormir", "mental", "estrés", "estres", "carga"))


def _is_graphic(scene: dict[str, Any]) -> bool:
    return str(scene.get("layout") or "").startswith("graphic_")


def plan_first_frame(
    short_plan: dict[str, Any],
    scene: dict[str, Any],
    source_artifacts: dict | None = None,
    channel_config: dict | None = None,
) -> dict[str, Any]:
    """Return first-frame metadata for scene 1 without mutating claims."""
    if _is_graphic(scene):
        return {
            "strategy": "graphic_proof",
            "goal": "clarity",
            "must_show": ["proof graphic", "key label", "comparison"],
            "must_avoid": ["generic stock background", "decorative-only graphic"],
            "preferred_source": "graphic",
            "overlay_text": _overlay_text(short_plan, scene),
            "callout_text": _callout_text(short_plan, scene),
            "roi_target": "proof graphic",
        }

    if _is_nutrition_label(short_plan, scene):
        return {
            "strategy": "evidence_closeup",
            "goal": "suspicion",
            "must_show": ["bread package", "ingredient label", "hand"],
            "must_avoid": [
                "smiling person holding food",
                "wide supermarket aisle",
                "generic breakfast table",
                "perfect stock kitchen",
                "centered stock pose",
            ],
            "preferred_source": "pexels_photo",
            "overlay_text": _overlay_text(short_plan, scene),
            "callout_text": "mira el primer ingrediente",
            "roi_target": "ingredient label",
        }

    if _is_lifestyle(short_plan, scene):
        return {
            "strategy": "human_reaction",
            "goal": "recognition",
            "must_show": ["everyday adult", "micro-action", "real home context"],
            "must_avoid": ["fake smile", "over-clean home", "generic wellness stock", "empty aesthetic room"],
            "preferred_source": "pexels_photo",
            "overlay_text": _overlay_text(short_plan, scene),
            "callout_text": _callout_text(short_plan, scene),
            "roi_target": "face reaction",
        }

    return {
        "strategy": "object_contrast",
        "goal": "clarity",
        "must_show": ["specific object", "micro-action"],
        "must_avoid": ["generic wellness stock", "wide shot", "centered stock pose"],
        "preferred_source": "pexels_photo",
        "overlay_text": _overlay_text(short_plan, scene),
        "callout_text": _callout_text(short_plan, scene),
        "roi_target": "specific object",
    }


def _overlay_text(short_plan: dict[str, Any], scene: dict[str, Any]) -> str:
    hook = str((short_plan or {}).get("hook_text") or "").strip()
    if _is_nutrition_label(short_plan, scene):
        if "color" in hook.lower() or "marr" in hook.lower() or "pan" in _topic_tokens(short_plan, scene):
            return "MARRON NO ES INTEGRAL"
    text = str(scene.get("on_screen_text") or hook or (short_plan or {}).get("title") or "").strip()
    words = text.split()
    return " ".join(words[:5]).upper()


def _callout_text(short_plan: dict[str, Any], scene: dict[str, Any]) -> str:
    hook = str((short_plan or {}).get("hook_text") or "").strip()
    if hook:
        return hook[:80]
    return str(scene.get("caption") or scene.get("on_screen_text") or "").strip()[:80]


def apply_first_frame_plan(
    scenes_doc: dict[str, Any],
    short_plan: dict[str, Any],
    channel_config: dict[str, Any] | None = None,
    source_artifacts: dict | None = None,
) -> dict[str, Any]:
    out = copy.deepcopy(scenes_doc or {})
    scenes = list(out.get("scenes") or [])
    if not scenes:
        out["scenes"] = scenes
        return out
    first = scenes[0]
    plan = plan_first_frame(short_plan or {}, first, source_artifacts or {}, channel_config or {})
    first["first_frame_plan"] = plan
    if plan.get("overlay_text"):
        first["on_screen_text"] = str(plan["overlay_text"])
        first.setdefault("shorts_quality_debug", {})["first_frame_overlay_source"] = "first_frame_plan.overlay_text"
    out["scenes"] = scenes
    return out
