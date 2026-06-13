"""Deterministic ROI/crop planning for Shorts backgrounds."""
from __future__ import annotations

import copy
from typing import Any


def plan_crop(
    scene: dict[str, Any],
    *,
    scene_index: int = 0,
    asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    first_frame_plan = scene.get("first_frame_plan") or {}
    target = str(first_frame_plan.get("roi_target") or "").strip() or "center"
    strategy = str(first_frame_plan.get("strategy") or "")
    target_low = target.lower()

    if target_low in {"ingredient label", "product", "hand", "bread package"} or strategy in {"evidence_closeup", "object_contrast"}:
        return {
            "mode": "object_dominant",
            "target": target,
            "scale": 1.2 if scene_index == 0 else 1.14,
            "anchor": "center-left" if scene_index == 0 else "center",
            "safe_area": "mobile_9_16",
            "reason": "first frame needs label/package dominance" if scene_index == 0 else "scene needs object emphasis",
        }

    return {
        "mode": "center",
        "target": target,
        "scale": 1.08 if scene_index == 0 else 1.05,
        "anchor": "center",
        "safe_area": "mobile_9_16",
        "reason": "deterministic fallback without vision",
    }


def apply_crop_plan(
    scenes_doc: dict[str, Any],
    *,
    only_with_background: bool = True,
) -> dict[str, Any]:
    out = copy.deepcopy(scenes_doc or {})
    for index, scene in enumerate(out.get("scenes") or []):
        layout = str(scene.get("layout") or "")
        if layout.startswith("graphic_"):
            continue
        if only_with_background and not ((scene.get("asset_refs") or {}).get("background")):
            continue
        scene["crop_plan"] = plan_crop(scene, scene_index=index)
    return out
