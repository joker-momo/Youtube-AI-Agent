"""Extract Short candidate windows from a long-form job's scenes.

A candidate is a single strong scene or 2–5 consecutive scenes that could stand
alone as a Short. Source timestamps come from each scene's ``audio_offset_sec``
and ``duration_sec`` so the source map can point back into the long video.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_WINDOW = 3  # consecutive scenes per candidate window (MVP)


def _load_scenes(long_job_dir: Path) -> list[dict[str, Any]]:
    p = long_job_dir / "scenes.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return list(data.get("scenes") or [])


def _scene_start(scene: dict) -> float:
    return float(scene.get("audio_offset_sec") or 0.0)


def _scene_end(scene: dict) -> float:
    return _scene_start(scene) + float(scene.get("duration_sec") or 0.0)


def _candidate(scenes: list[dict]) -> dict[str, Any]:
    ids = [s.get("id") for s in scenes]
    narration = "\n".join(str(s.get("narration") or "").strip() for s in scenes).strip()
    layouts = [str(s.get("layout") or "") for s in scenes]
    visual = next((str(s.get("visual_prompt") or "") for s in scenes if s.get("visual_prompt")), "")
    return {
        "candidate_id": "cand-" + "_".join(str(i) for i in ids),
        "scene_ids": ids,
        "narration": narration,
        "layouts": layouts,
        "visual_prompt": visual,
        "source_start_sec": _scene_start(scenes[0]),
        "source_end_sec": _scene_end(scenes[-1]),
        "duration_sec": round(_scene_end(scenes[-1]) - _scene_start(scenes[0]), 2),
    }


def extract_candidates(long_job_dir: Path) -> list[dict[str, Any]]:
    """Return single-scene and 2..MAX_WINDOW consecutive-scene candidates."""
    scenes = _load_scenes(long_job_dir)
    out: list[dict[str, Any]] = []
    n = len(scenes)
    # single scenes
    for s in scenes:
        out.append(_candidate([s]))
    # consecutive windows 2..MAX_WINDOW
    for size in range(2, MAX_WINDOW + 1):
        for start in range(0, n - size + 1):
            out.append(_candidate(scenes[start : start + size]))
    return out
