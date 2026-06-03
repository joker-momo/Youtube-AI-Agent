from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import manifest, paths
from video_agent.shorts.idea_generator import resolve_long_job_artifact
from video_agent.shorts.idea_store import (
    read_short_ideas,
    write_selected_ideas,
    write_studio_render_run,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _call_build_short_fn(
    build_short_fn: Callable[..., dict],
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    *,
    source_artifacts: dict,
) -> dict:
    try:
        return build_short_fn(
            long_job_dir,
            short_plan,
            channel_config,
            require_render_confirmation=False,
            source_artifacts=source_artifacts,
        ) or {}
    except TypeError:
        return build_short_fn(long_job_dir, short_plan, channel_config) or {}


def _next_short_number(long_job_dir: Path) -> int:
    highest = 0
    shorts_root = paths.shorts_dir(long_job_dir)
    if shorts_root.exists():
        for child in shorts_root.iterdir():
            if not child.is_dir():
                continue
            match = re.match(r"^short-(\d+)", child.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def _source_scenes_by_id(long_job_dir: Path) -> dict[str, dict[str, Any]]:
    scenes_doc = _read_json(resolve_long_job_artifact(long_job_dir, "scenes.json"))
    result: dict[str, dict[str, Any]] = {}
    for index, scene in enumerate(scenes_doc.get("scenes") or [], start=1):
        scene_id = str(scene.get("id") or scene.get("scene_id") or f"scene-{index:02d}")
        result[scene_id] = {
            "scene_id": scene_id,
            "index": index,
            "narration": str(scene.get("narration") or ""),
            "start_sec": float(scene.get("audio_offset_sec") or scene.get("start_sec") or 0.0),
            "end_sec": float(
                scene.get("end_sec")
                or float(scene.get("audio_offset_sec") or scene.get("start_sec") or 0.0) + float(scene.get("duration_sec") or 0.0)
            ),
        }
    return result


def _build_source_artifacts(idea: dict, long_job_dir: Path) -> dict[str, Any]:
    source_scene_index = _source_scenes_by_id(long_job_dir)
    source_scenes: list[dict[str, Any]] = []
    for scene_id in idea.get("source_scene_ids") or []:
        scene = dict(source_scene_index.get(scene_id) or {})
        if not scene:
            continue
        scene["narration"] = scene.get("narration", "")[:1200]
        source_scenes.append(scene)
    source_scenes.sort(key=lambda item: item.get("index", 0))
    return {
        "idea": {
            "idea_id": idea.get("idea_id"),
            "title": idea.get("title"),
            "hook_text": idea.get("hook_text"),
            "viewer_pain": idea.get("viewer_pain"),
            "practical_payoff": idea.get("practical_payoff"),
            "format": idea.get("format"),
            "narration_seed": idea.get("narration_seed"),
        },
        "key_points": list((idea.get("key_points") or [])[:6]),
        "source_scenes": source_scenes,
    }


def idea_to_short_plan(
    idea: dict,
    *,
    short_id: str,
    source_long_job_id: str,
    channel_config: dict,
) -> dict:
    return {
        "short_id": short_id,
        "source_long_job_id": source_long_job_id,
        "idea_id": idea.get("idea_id"),
        "idea_type": "synthesis",
        "candidate_type": "synthesis",
        "format": idea.get("format"),
        "scene_ids": list(idea.get("source_scene_ids") or []),
        "source_scene_ids": list(idea.get("source_scene_ids") or []),
        "source_start_sec": None,
        "source_end_sec": None,
        "score": ((idea.get("scores") or {}).get("overall")),
        "reason": "User-selected synthesis idea",
        "hook_angle": idea.get("title") or idea.get("hook_text"),
        "viewer_pain": idea.get("viewer_pain"),
        "practical_payoff": idea.get("practical_payoff"),
        "music_track": "shorts_nutrition_energy",
        "cover_strategy": "idea_hook_cover",
        "voice_preset": {},
        "narration_seed": idea.get("narration_seed", ""),
        "title": idea.get("title", ""),
        "hook_text": idea.get("hook_text", ""),
        "key_points": list(idea.get("key_points") or []),
        "visual_angle": idea.get("visual_angle", ""),
        "risk_level": idea.get("risk_level", "lifestyle"),
        "scores": dict(idea.get("scores") or {}),
    }


def _load_manifest(long_job_dir: Path) -> dict[str, Any]:
    try:
        return manifest.read_manifest(long_job_dir)
    except Exception:
        return {}


def _prior_short_ids_for_idea(long_job_dir: Path, idea_id: str) -> list[str]:
    found: list[str] = []
    manifest_doc = _load_manifest(long_job_dir)
    for entry in manifest_doc.get("shorts") or []:
        if entry.get("idea_id") == idea_id and entry.get("short_id") not in found:
            found.append(entry["short_id"])
    shorts_root = paths.shorts_dir(long_job_dir)
    if shorts_root.exists():
        for child in shorts_root.iterdir():
            if not child.is_dir() or not child.name.startswith("short-"):
                continue
            short_id = child.name
            if short_id in found:
                continue
            status_doc = _read_json(paths.short_status_path(long_job_dir, short_id))
            idea_doc = _read_json(paths.resolve_short_json(child, paths.SHORT_IDEA_FILE))
            if status_doc.get("idea_id") == idea_id or idea_doc.get("idea_id") == idea_id:
                found.append(short_id)
    return found


def _archive_short_dir(long_job_dir: Path, short_id: str) -> str | None:
    src = paths.short_dir(long_job_dir, short_id)
    if not src.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = paths.shorts_dir(long_job_dir) / "_archive" / f"{ts}-{short_id}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return str(Path("shorts") / "_archive" / f"{ts}-{short_id}")


def _update_manifest_for_archive(manifest_doc: dict[str, Any], idea_id: str, archive_path_by_short: dict[str, str]) -> dict[str, Any]:
    if not manifest_doc:
        manifest_doc = {"shorts": []}
    archived_entries = list(manifest_doc.get("archived_shorts") or [])
    active_entries = []
    for entry in manifest_doc.get("shorts") or []:
        short_id = entry.get("short_id")
        if entry.get("idea_id") == idea_id and short_id in archive_path_by_short:
            archived_entries.append(
                {
                    **entry,
                    "status": "archived",
                    "rendered": False,
                    "archived_at": _now(),
                    "archive_path": archive_path_by_short[short_id],
                    "video_path": None,
                    "cover_path": None,
                }
            )
            continue
        active_entries.append(entry)
    manifest_doc["shorts"] = active_entries
    if archived_entries:
        manifest_doc["archived_shorts"] = archived_entries
    return manifest_doc


def render_selected_short_ideas(
    long_job_dir: Path,
    channel_config: dict,
    idea_ids: list[str],
    *,
    build_short_fn: Callable[..., dict] | None = None,
    force: bool = False,
) -> dict:
    from video_agent.shorts.short_builder import build_short as default_build_short

    build_short_fn = build_short_fn or default_build_short
    ideas_doc = read_short_ideas(long_job_dir)
    ideas_by_id = {str(idea.get("idea_id")): idea for idea in ideas_doc.get("ideas") or []}
    selected_ideas = [ideas_by_id[idea_id] for idea_id in idea_ids if idea_id in ideas_by_id]
    if not selected_ideas:
        raise ValueError("No valid idea IDs selected")

    write_selected_ideas(
        long_job_dir,
        {
            "schema_version": "selected_short_ideas.v1",
            "source_long_job_id": long_job_dir.name,
            "selected_at": _now(),
            "generation_id": ideas_doc.get("generation_id"),
            "selected_idea_ids": [idea.get("idea_id") for idea in selected_ideas],
            "status": "selected",
        },
    )

    manifest_doc = _load_manifest(long_job_dir)
    active_entries: list[dict[str, Any]] = list(manifest_doc.get("shorts") or [])
    warnings: list[str] = []
    errors: list[str] = []
    attempted_render_count = 0
    rendered_count = 0
    needs_review_count = 0
    failed_count = 0
    skipped_count = 0

    for idea in selected_ideas:
        idea_id = str(idea.get("idea_id"))
        prior_short_ids = _prior_short_ids_for_idea(long_job_dir, idea_id)
        if prior_short_ids and not force:
            skipped_count += 1
            warnings.append(f"already_rendered_idea:{idea_id}")
            continue

        if prior_short_ids and force:
            archive_paths: dict[str, str] = {}
            for short_id in prior_short_ids:
                archive_path = _archive_short_dir(long_job_dir, short_id)
                if archive_path:
                    archive_paths[short_id] = archive_path
            warnings.append(f"archived_prior_short_for_idea:{idea_id}")
            manifest_doc = _update_manifest_for_archive(manifest_doc, idea_id, archive_paths)
            active_entries = list(manifest_doc.get("shorts") or [])

        short_number = _next_short_number(long_job_dir)
        idea_id = str(idea.get("idea_id") or "idea")
        title = idea.get("title") or idea.get("hook_text") or "short"
        from video_agent.shorts.paths import slugify
        title_slug = slugify(title)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = f"short-{short_number:02d}_{idea_id}_{ts}_{title_slug}"
        short_plan = idea_to_short_plan(
            idea,
            short_id=short_id,
            source_long_job_id=long_job_dir.name,
            channel_config=channel_config,
        )
        source_artifacts = _build_source_artifacts(idea, long_job_dir)
        attempted_render_count += 1

        # ── Write a preliminary manifest entry BEFORE build_short so the
        # ── Renders tab can display the progress card immediately. ──
        active_entries = [e for e in active_entries if e.get("idea_id") != idea_id]
        preliminary_entry = {
            "short_id": short_id,
            "idea_id": idea_id,
            "idea_type": "synthesis",
            "format": idea.get("format"),
            "status": "in_progress",
            "rendered": False,
            "qa_verdict": None,
            "source_scene_ids": list(idea.get("source_scene_ids") or []),
            "video_path": None,
            "cover_path": None,
        }
        active_entries.append(preliminary_entry)
        manifest_doc.update({
            "source_long_job_id": long_job_dir.name,
            "status": "rendering",
            "mode": "synthesis_ideas",
            "shorts": active_entries,
        })
        manifest.write_manifest(long_job_dir, manifest_doc)

        try:
            result = _call_build_short_fn(
                build_short_fn,
                long_job_dir,
                short_plan,
                channel_config,
                source_artifacts=source_artifacts,
            )
        except Exception as exc:
            result = {"short_id": short_id, "idea_id": idea_id, "status": "failed", "rendered": False, "qa_verdict": "FAIL"}
            failed_count += 1
            errors.append(str(exc))
            is_stop = False
            from fastapi import HTTPException
            if isinstance(exc, HTTPException):
                detail = exc.detail
                if isinstance(detail, dict) and detail.get("stop_requested"):
                    is_stop = True
            if is_stop:
                raise exc
        else:
            status = result.get("status")
            if status == "rendered":
                rendered_count += 1
            elif status == "needs_review":
                needs_review_count += 1
            else:
                failed_count += 1

        active_entries = [entry for entry in active_entries if entry.get("idea_id") != idea_id]
        active_entries.append(
            {
                "short_id": short_id,
                "idea_id": idea_id,
                "idea_type": "synthesis",
                "format": idea.get("format"),
                "status": result.get("status"),
                "rendered": bool(result.get("rendered")),
                "qa_verdict": result.get("qa_verdict"),
                "source_scene_ids": list(idea.get("source_scene_ids") or []),
                "video_path": result.get("video_path"),
                "cover_path": result.get("cover_path"),
            }
        )

    blocked_count = failed_count + needs_review_count
    status = "failed"
    if rendered_count > 0 and blocked_count == 0 and skipped_count == 0:
        status = "completed"
    elif rendered_count > 0:
        status = "completed_with_warnings"
    elif skipped_count == len(selected_ideas):
        errors.append("no_new_shorts_rendered")

    manifest_doc.update(
        {
            "source_long_job_id": long_job_dir.name,
            "status": status,
            "mode": "synthesis_ideas",
            "shorts": active_entries,
        }
    )
    manifest.write_manifest(long_job_dir, manifest_doc)

    run_doc = {
        "schema_version": "studio_render_run.v1",
        "source_long_job_id": long_job_dir.name,
        "mode": "synthesis_ideas",
        "generation_id": ideas_doc.get("generation_id"),
        "status": status,
        "started_at": _now(),
        "completed_at": _now(),
        "selected_idea_count": len(selected_ideas),
        "attempted_render_count": attempted_render_count,
        "rendered_count": rendered_count,
        "needs_review_count": needs_review_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "blocked_count": blocked_count,
        "warnings": warnings,
        "errors": errors,
    }
    write_studio_render_run(long_job_dir, run_doc)
    return run_doc
