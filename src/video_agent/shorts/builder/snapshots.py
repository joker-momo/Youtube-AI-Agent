"""Snapshot and hash helpers extracted from short_builder."""
from __future__ import annotations

from typing import Any


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _cover_text(hook: str, max_words: int) -> str:
    words = [w for w in str(hook).strip().strip("¿?¡!.,").split() if w]
    return " ".join(words[:max_words]).upper()


def _normalized_script_hash(script: dict[str, Any]) -> str:
    from video_agent.shorts.quality_hash import stable_hash
    hook = str(script.get("hook") or "").strip().lower()
    narration = str(script.get("narration") or "").strip().lower()
    cta = str(script.get("cta") or "").strip().lower()
    idea_items = script.get("idea_items") or script.get("points") or script.get("checklist") or []
    if isinstance(idea_items, list):
        idea_items = [str(item).strip().lower() for item in idea_items]
    norm = {
        "hook": hook,
        "narration": narration,
        "cta": cta,
        "idea_items": idea_items
    }
    return stable_hash(norm)


def _normalized_scene_hash(short_scenes: dict[str, Any]) -> str:
    """Stable hash of the render-relevant scene fields, used to detect a retry
    loop where the generator keeps emitting the same output (spec §8)."""
    from video_agent.shorts.quality_hash import stable_hash

    norm = []
    for sc in (short_scenes.get("scenes") or []):
        payload = sc.get("layout_payload") or {}
        norm.append({
            "layout": sc.get("layout"),
            "narration": (sc.get("narration") or "").strip().lower(),
            "caption": (sc.get("caption") or "").strip().lower(),
            "on_screen_text": (sc.get("on_screen_text") or "").strip().lower(),
            "duration_sec": round(float(sc.get("duration_sec") or 0.0), 1),
            "title": (payload.get("title") or ""),
            "items": payload.get("items") or payload.get("bullets") or [],
        })
    return stable_hash(norm)


def _scene_duration_sum(scenes_doc: dict[str, Any]) -> float:
    return round(
        sum(float(scene.get("duration_sec") or 0.0) for scene in ((scenes_doc or {}).get("scenes") or [])),
        1,
    )


def _snapshot_scene_durations(scenes_doc: dict[str, Any]) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    for index, scene in enumerate((scenes_doc or {}).get("scenes") or []):
        scene_id = str(scene.get("id") or scene.get("scene_id") or index)
        snapshot[scene_id] = float(scene.get("duration_sec") or 0.0)
    return snapshot


def _restore_scene_durations(scenes_doc: dict[str, Any], snapshot: dict[str, float]) -> None:
    if not snapshot:
        return
    for index, scene in enumerate((scenes_doc or {}).get("scenes") or []):
        scene_id = str(scene.get("id") or scene.get("scene_id") or index)
        if scene_id in snapshot:
            scene["duration_sec"] = snapshot[scene_id]
    scenes_doc["total_duration_sec"] = _scene_duration_sum(scenes_doc)
