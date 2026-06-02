"""Build ``short_source_map.json`` linking a Short back to long-video scenes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _long_scene_index(long_job_dir: Path) -> dict[str, dict]:
    p = long_job_dir / "scenes.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {str(s.get("id")): s for s in (data.get("scenes") or [])}


def _long_title(long_job_dir: Path) -> str:
    p = long_job_dir / "seo.json"
    if p.exists():
        try:
            return str(json.loads(p.read_text(encoding="utf-8")).get("title", ""))
        except Exception:
            return ""
    return ""


def build_source_map(
    long_job_dir: Path,
    short_plan: dict,
    short_script: dict,
    channel_config: dict,
    long_video_url: str = "",
) -> dict[str, Any]:
    idx = _long_scene_index(long_job_dir)
    used = []
    for sid in short_plan.get("scene_ids") or []:
        scene = idx.get(str(sid), {})
        used.append(
            {
                "scene_id": sid,
                "source_start_sec": scene.get("audio_offset_sec", short_plan.get("source_start_sec")),
                "source_end_sec": (
                    float(scene.get("audio_offset_sec", 0.0)) + float(scene.get("duration_sec", 0.0))
                    if scene
                    else short_plan.get("source_end_sec")
                ),
                "reason": short_plan.get("reason", "Selected for standalone value"),
                "original_narration": scene.get("narration", ""),
                "short_rewrite": short_script.get("narration", ""),
            }
        )
    funnel_cfg = (channel_config.get("shorts") or {}).get("funnel") or {}
    cta = short_script.get("cta") or funnel_cfg.get("default_cta_without_url", "Vídeo completo en el canal.")
    return {
        "short_id": short_plan.get("short_id"),
        "idea_id": short_plan.get("idea_id"),
        "idea_type": short_plan.get("idea_type") or short_plan.get("candidate_type"),
        "key_points": list(short_plan.get("key_points") or []),
        "source_long_job_id": long_job_dir.name,
        "source_video_title": _long_title(long_job_dir),
        "source_video_url": long_video_url,
        "source_files": {
            "script": "../../script.json",
            "scenes": "../../scenes.json",
            "seo": "../../seo.json",
            "whisper_timestamps": "../../whisper_timestamps.json",
        },
        "used_source_scenes": used,
        "funnel": {"cta": cta, "long_video_url": long_video_url},
    }
