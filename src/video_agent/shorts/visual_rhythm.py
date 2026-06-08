from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.quality_hash import stable_hash
from video_agent.storage.atomic import atomic_write_json

_MOTIONS = ["push_in", "crop_shift", "object_reveal", "text_pop", "slow_zoom", "cutaway", "graphic_burst"]
_HOOK_MOTIONS = {"push_in", "object_reveal", "face_cut", "text_pop"}


def _scene_id(scene: dict[str, Any], index: int) -> str:
    return str(scene.get("id") or scene.get("scene_id") or f"s{index + 1:02d}")


def _duration(scene: dict[str, Any]) -> float:
    try:
        return float(scene.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _risk(scene: dict[str, Any], previous: dict[str, Any] | None) -> str:
    motion = str(scene.get("motion") or "none")
    text = str(scene.get("on_screen_text") or "").strip().lower()
    if motion in {"", "none", "static"} or _duration(scene) > 3.2:
        return "static"
    if previous and text and text == str(previous.get("on_screen_text") or "").strip().lower():
        return "repeated_visual"
    if len(text.split()) > 6:
        return "text_heavy"
    return "none"


def build_visual_rhythm_plan(
    long_job_dir: Path,
    short_id: str,
    short_scenes: dict,
    retention_plan: dict,
    channel_config: dict,
) -> dict[str, Any]:
    artifact = paths.short_json_dir(long_job_dir, short_id) / paths.SHORT_VISUAL_RHYTHM_FILE
    input_hash = stable_hash(short_scenes, retention_plan, channel_config=channel_config)
    if artifact.exists():
        cached = json.loads(artifact.read_text(encoding="utf-8"))
        if cached.get("input_hash") == input_hash:
            cached["generation_mode"] = "cached"
            return cached

    scenes = list((short_scenes or {}).get("scenes") or [])
    rhythm = []
    previous = None
    for index, scene in enumerate(scenes):
        sid = _scene_id(scene, index)
        existing = str(scene.get("motion") or "none")
        motion = existing if existing not in {"", "none", "static"} else _MOTIONS[index % len(_MOTIONS)]
        if index == 0 and motion not in _HOOK_MOTIONS:
            motion = "push_in"
        risk = _risk(scene, previous)
        beat = (retention_plan.get("retention_beats") or [{}])[min(index, max(0, len(retention_plan.get("retention_beats") or [{}]) - 1))]
        rhythm.append({
            "scene_id": sid,
            "motion": motion,
            "visual_change_reason": str(beat.get("function") or "retention"),
            "text_animation_hint": "pop" if index == 0 else "slide",
            "risk": risk,
        })
        previous = scene
    total = sum(_duration(scene) for scene in scenes)
    plan = {
        "short_id": short_id,
        "average_visual_change_sec": round(total / max(1, len(scenes)), 1),
        "max_static_hold_sec": max([_duration(s) for s in scenes if str(s.get("motion") or "none") in {"none", "static", ""}] or [0.0]),
        "scene_rhythm": rhythm,
        "qa": {"verdict": "PENDING"},
        "input_hash": input_hash,
        "generation_mode": "deterministic",
    }
    artifact.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(artifact, plan)
    return plan


def apply_visual_rhythm_to_scenes(short_scenes: dict, rhythm_plan: dict) -> dict[str, Any]:
    out = copy.deepcopy(short_scenes or {})
    by_id = {str(item.get("scene_id")): item for item in (rhythm_plan or {}).get("scene_rhythm") or []}
    interrupts = list((rhythm_plan or {}).get("pattern_interrupts") or [])
    for index, scene in enumerate(out.get("scenes") or []):
        sid = _scene_id(scene, index)
        item = by_id.get(sid)
        if not item:
            continue
        scene["motion"] = item.get("motion") or scene.get("motion") or "slow_zoom"
        scene["rhythm_tag"] = item.get("visual_change_reason") or "retention"
        scene["retention_function"] = item.get("visual_change_reason") or scene.get("retention_function") or "tension"
        scene["text_animation_hint"] = item.get("text_animation_hint") or ""
        if index < len(interrupts):
            scene["pattern_interrupt"] = interrupts[index].get("type")
        elif index == 0 and interrupts:
            scene["pattern_interrupt"] = interrupts[0].get("type")
        scene.setdefault("shorts_quality_debug", {})["visual_rhythm_risk"] = item.get("risk")
    return out
