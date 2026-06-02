from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts.idea_prompts import short_ideas_prompt
from video_agent.shorts.idea_scorer import validate_and_score_ideas
from video_agent.shorts.idea_store import (
    read_studio_render_run,
    write_idea_generation_run,
    write_short_ideas,
    write_studio_render_run,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_long_job_artifact(job_dir: Path, logical_name: str) -> Path | None:
    if logical_name.endswith(".json"):
        candidates = [job_dir / logical_name, job_dir / "json" / logical_name]
    elif logical_name == "video.mp4":
        candidates = [job_dir / logical_name, job_dir / "outputs" / logical_name]
    else:
        candidates = [job_dir / logical_name]
    for path in candidates:
        if path.exists():
            return path
    return None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_long_narration_source(long_job_dir: Path, *, max_chars: int = 24000) -> dict:
    scenes_doc = _read_json(resolve_long_job_artifact(long_job_dir, "scenes.json"))
    seo_doc = _read_json(resolve_long_job_artifact(long_job_dir, "seo.json"))
    kept_scenes: list[dict[str, Any]] = []
    blocks: list[str] = []
    truncated = False

    for index, scene in enumerate(scenes_doc.get("scenes") or [], start=1):
        narration = str(scene.get("narration") or "").strip()
        if not narration:
            continue
        scene_id = str(scene.get("id") or scene.get("scene_id") or f"scene-{index:02d}")
        start_sec = float(scene.get("audio_offset_sec") or scene.get("start_sec") or 0.0)
        duration_sec = float(scene.get("duration_sec") or 0.0)
        end_sec = float(scene.get("end_sec") or (start_sec + duration_sec))
        block = f"SCENE {scene_id} [{start_sec:.1f}s-{end_sec:.1f}s]\n{narration}\n"
        candidate = ("\n".join(blocks + [block])).strip()
        if blocks and len(candidate) > max_chars:
            truncated = True
            break
        blocks.append(block)
        kept_scenes.append(
            {
                "scene_id": scene_id,
                "index": index,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "duration_sec": duration_sec,
                "narration": narration,
                "visual_prompt": scene.get("visual_prompt", ""),
                "layout": scene.get("layout", ""),
            }
        )

    full_narration = "\n".join(blocks).strip()
    return {
        "source_long_job_id": long_job_dir.name,
        "title": seo_doc.get("title", ""),
        "scenes": kept_scenes,
        "full_narration": full_narration,
        "truncated": truncated,
        "narration_chars": len(full_narration),
    }


def _parse_json_object(raw: str) -> dict[str, Any]:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def generate_short_ideas(
    long_job_dir: Path,
    channel_config: dict,
    *,
    llm_fn: Callable[[str], str],
    target_count: int = 10,
) -> dict[str, Any]:
    generation_id = f"ideas-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    source_doc = build_long_narration_source(long_job_dir)
    warnings = list(source_doc.get("warnings") or [])
    if source_doc.get("truncated"):
        warnings.append("source_truncated_for_idea_generation")
    run_started = {
        "schema_version": "idea_generation_run.v1",
        "source_long_job_id": long_job_dir.name,
        "status": "ideas_generating",
        "started_at": _now(),
        "provider": "chatgpt",
        "idea_count": 0,
        "generation_id": generation_id,
        "invalidates_prior_render_run": True,
        "errors": [],
        "warnings": warnings,
    }
    prior_render_run = read_studio_render_run(long_job_dir)
    if prior_render_run:
        write_studio_render_run(
            long_job_dir,
            {
                **prior_render_run,
                "status": "stale",
                "stale_reason": "new_ideas_generation_started",
                "superseded_by_generation_id": generation_id,
            },
        )
    write_idea_generation_run(long_job_dir, run_started)
    raw_doc = _parse_json_object(llm_fn(short_ideas_prompt(channel_config, source_doc, target_count)))
    ideas_doc = validate_and_score_ideas(raw_doc, source_doc, target_count=target_count)
    ideas_doc.update(
        {
            "schema_version": "short_ideas.v1",
            "source_long_job_id": long_job_dir.name,
            "source_title": source_doc.get("title", ""),
            "generated_at": _now(),
            "generation_id": generation_id,
            "provider": "chatgpt",
            "input_source": {
                "scenes_count": len(source_doc.get("scenes") or []),
                "narration_chars": source_doc.get("narration_chars", 0),
                "truncated": bool(source_doc.get("truncated")),
            },
        }
    )
    ideas_doc["warnings"] = sorted(set(list(ideas_doc.get("warnings") or []) + warnings))
    write_short_ideas(long_job_dir, ideas_doc)
    write_idea_generation_run(
        long_job_dir,
        {
            **run_started,
            "status": "ideas_ready",
            "completed_at": _now(),
            "idea_count": len(ideas_doc.get("ideas") or []),
            "warnings": ideas_doc.get("warnings") or [],
        },
    )
    return ideas_doc
