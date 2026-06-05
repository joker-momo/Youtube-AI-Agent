from __future__ import annotations

import errno
import fcntl
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from video_agent.orchestrator.queue import JobQueue
from video_agent.shorts import paths as shorts_paths
from video_agent.shorts import status as shorts_status
from video_agent.shorts.idea_store import (
    read_idea_generation_run,
    read_short_ideas,
    read_studio_render_run,
)
from video_agent.web.routes._legacy import _job_idea_title, _safe_job_dir, get_jobs_root
from video_agent.web.run_all_pipeline import is_run_locked

router = APIRouter()

_REQUIRED_LONG_ARTIFACTS = ("video.mp4", "script.json", "scenes.json", "seo.json")
_SHORTS_STUDIO_HTML_PATH = Path(__file__).resolve().parents[1] / "shorts_studio.html"


@router.get("/shorts-studio", response_class=HTMLResponse)
def get_shorts_studio_page() -> HTMLResponse:
    if not _SHORTS_STUDIO_HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="shorts_studio.html not found")
    return HTMLResponse(content=_SHORTS_STUDIO_HTML_PATH.read_text(encoding="utf-8"))


class PrepareDraftsRequest(BaseModel):
    force: bool = False


class ConfirmRenderRequest(BaseModel):
    short_ids: list[str]


class GenerateIdeasRequest(BaseModel):
    target_count: int = 10
    force: bool = False


class RenderIdeasRequest(BaseModel):
    idea_ids: list[str]
    force: bool = False


def _is_file_locked(path: Path) -> bool:
    if not path.exists():
        return False
    lock_fd = path.open("a+")
    try:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return True
            raise
        finally:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        lock_fd.close()
    return False


def _resolve_artifact(job_dir: Path, name: str) -> Path | None:
    candidates = [job_dir / name]
    if name.endswith(".json"):
        candidates.append(job_dir / "json" / name)
    elif name.endswith(".mp4"):
        candidates.append(job_dir / "outputs" / name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _job_missing_artifacts(job_dir: Path) -> list[str]:
    missing: list[str] = []
    for name in _REQUIRED_LONG_ARTIFACTS:
        if _resolve_artifact(job_dir, name) is None:
            missing.append(name)
    return missing


def system_has_active_job(jobs_root: Path) -> tuple[bool, list[dict[str, Any]]]:
    active_jobs: list[dict[str, Any]] = []
    queue = JobQueue(jobs_root / "queue.db")
    for row in queue.active_jobs():
        active_jobs.append(
            {
                "kind": "queue",
                "job_id": row.get("job_id"),
                "status": row.get("status"),
                "command": row.get("command") or "run_all",
            }
        )

    for job_dir in sorted(jobs_root.iterdir()) if jobs_root.exists() else []:
        if not job_dir.is_dir() or not (job_dir / "job.json").exists():
            continue
        if is_run_locked(job_dir):
            active_jobs.append({"kind": "run_lock", "job_id": job_dir.name, "status": "running"})
        if _is_file_locked(shorts_paths.autopilot_lock_path(job_dir)):
            active_jobs.append({"kind": "shorts_autopilot_lock", "job_id": job_dir.name, "status": "running"})
        if _is_file_locked(shorts_paths.idea_generation_lock_path(job_dir)):
            active_jobs.append({"kind": "idea_generation_lock", "job_id": job_dir.name, "status": "running"})
        if _is_file_locked(shorts_paths.render_selected_lock_path(job_dir)):
            active_jobs.append({"kind": "render_selected_lock", "job_id": job_dir.name, "status": "running"})
        shorts_root = shorts_paths.shorts_dir(job_dir)
        if not shorts_root.exists():
            continue
        for short_dir in sorted(shorts_root.iterdir()):
            if not short_dir.is_dir():
                continue
            if _is_file_locked(short_dir / shorts_paths.SHORT_LOCK_FILE):
                active_jobs.append(
                    {
                        "kind": "short_lock",
                        "job_id": job_dir.name,
                        "short_id": short_dir.name,
                        "status": "running",
                    }
                )
    return bool(active_jobs), active_jobs


def _queued_or_running_synthesis_state(job_dir: Path, active_jobs: list[dict[str, Any]]) -> str | None:
    job_id = job_dir.name
    for item in active_jobs:
        if item.get("job_id") != job_id:
            continue
        command = str(item.get("command") or "")
        if command in ("shorts_generate_ideas", "shorts_prepare_drafts", "shorts_autopilot"):
            return "ideas_generating"
        if command in ("shorts_render_selected_ideas", "shorts_confirm_render", "shorts_render_one"):
            return "rendering_selected"
    if _is_file_locked(shorts_paths.render_selected_lock_path(job_dir)):
        return "rendering_selected"
    if _is_file_locked(shorts_paths.idea_generation_lock_path(job_dir)):
        return "ideas_generating"
    if _is_file_locked(shorts_paths.autopilot_lock_path(job_dir)):
        return "ideas_generating"
    return None


def _derive_synthesis_shorts_status(job_dir: Path, active_jobs: list[dict[str, Any]]) -> str:
    active_state = _queued_or_running_synthesis_state(job_dir, active_jobs)
    if active_state:
        return active_state

    ideas = read_short_ideas(job_dir) or {}
    idea_run = read_idea_generation_run(job_dir) or {}
    if idea_run:
        idea_status = idea_run.get("status")
        idea_generation_id = idea_run.get("generation_id")
        ideas_generation_id = ideas.get("generation_id")
        if idea_status in ("queued", "running", "ideas_generating"):
            return "ideas_generating"
        if idea_status == "failed":
            return "failed"
        if idea_status == "ideas_ready" and (
            not ideas or idea_generation_id != ideas_generation_id or not ideas.get("ideas")
        ):
            return "failed"

    current_generation_id = ideas.get("generation_id")
    render_run = read_studio_render_run(job_dir) or {}
    if (
        render_run
        and render_run.get("status") != "stale"
        and render_run.get("generation_id") == current_generation_id
    ):
        status = render_run.get("status")
        if status in ("completed", "completed_with_warnings", "failed"):
            return status

    if ideas and ideas.get("ideas"):
        return "ideas_ready"

    manifest = _read_json(shorts_paths.manifest_path(job_dir))
    if manifest and manifest.get("mode") == "synthesis_ideas":
        status = manifest.get("status")
        if status == "rendered":
            return "completed"
        if status in ("completed", "completed_with_warnings", "failed"):
            return status
    return "none"


def _studio_job_state(job_dir: Path, active_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = shorts_status.summarize_shorts(job_dir)
    ideas = read_short_ideas(job_dir) or {}
    manifest = _read_json(shorts_paths.manifest_path(job_dir))
    missing = _job_missing_artifacts(job_dir)
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    rendered_short_count = sum(
        1
        for entry in (manifest.get("shorts") or [])
        if entry.get("rendered") is True and entry.get("status") != "archived"
    )
    return {
        "job_id": job.get("job_id") or job_dir.name,
        "title": _job_idea_title(job_dir),
        "channel_id": job.get("channel_id", "vida-plena-45"),
        "created_at": job.get("created_at"),
        "eligible": not missing,
        "missing": missing,
        "video_exists": _resolve_artifact(job_dir, "video.mp4") is not None,
        "shorts_status": _derive_synthesis_shorts_status(job_dir, active_jobs),
        "idea_count": len(ideas.get("ideas") or []),
        "rendered_short_count": rendered_short_count,
        "legacy_shorts_status": summary.get("state") or "none",
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.get("/shorts-studio/state")
def get_shorts_studio_state(jobs_root: Path = Depends(get_jobs_root)) -> dict[str, Any]:
    busy, active_jobs = system_has_active_job(jobs_root)
    jobs: list[dict[str, Any]] = []
    for job_dir in sorted(jobs_root.iterdir(), reverse=True) if jobs_root.exists() else []:
        if not job_dir.is_dir() or not (job_dir / "job.json").exists():
            continue
        jobs.append(_studio_job_state(job_dir, active_jobs))
    return {
        "can_start": not busy,
        "active_jobs": active_jobs,
        "jobs": jobs,
        "eligible_jobs": [job for job in jobs if job["eligible"]],
    }


@router.get("/shorts-studio/jobs/{job_id}/drafts")
def get_shorts_studio_drafts(job_id: str, jobs_root: Path = Depends(get_jobs_root)) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    # Liveness probe (lock + queue heartbeat) computed once per request so we can
    # recover orphaned "generating" shorts whose owning worker died on restart.
    try:
        _queue = JobQueue(jobs_root / "queue.db")
    except Exception:
        _queue = None
    owner_alive = shorts_status.short_owner_is_alive(job_dir, queue=_queue)

    manifest = _read_json(shorts_paths.manifest_path(job_dir))
    manifest_short_ids: set[str] = set()
    drafts: list[dict[str, Any]] = []
    for entry in manifest.get("shorts") or []:
        short_id = entry.get("short_id")
        if not short_id:
            continue
        manifest_short_ids.add(short_id)
        short_dir = shorts_paths.short_dir(job_dir, short_id)
        status_doc = _read_json(shorts_paths.short_status_path(job_dir, short_id))
        _recovered = shorts_status.recover_stale_short(
            job_dir, short_id, status_doc, owner_alive=owner_alive
        )
        if _recovered is not None:
            status_doc = _recovered
        qa_doc = _read_json(short_dir / shorts_paths.SHORT_QA_FILE)
        source_map = _read_json(short_dir / shorts_paths.SHORT_SOURCE_MAP_FILE)
        seo_doc = _read_json(short_dir / shorts_paths.SHORT_SEO_FILE)

        merged = dict(entry)
        merged.update(status_doc)
        if "qa_verdict" not in merged and qa_doc.get("qa_verdict"):
            merged["qa_verdict"] = qa_doc.get("qa_verdict")
        merged["title"] = seo_doc.get("title") or entry.get("hook") or status_doc.get("hook") or ""
        merged["source_scene_ids"] = (
            merged.get("source_scene_ids")
            or source_map.get("source_scene_ids")
            or source_map.get("scene_ids")
            or []
        )
        # Surface the full SEO doc so the dashboard can render the
        # "post-render SEO" insight panel on the Short card without an
        # extra artifact fetch round-trip.
        if seo_doc:
            merged["seo"] = {
                "short_id": seo_doc.get("short_id") or short_id,
                "title": seo_doc.get("title", ""),
                "description": seo_doc.get("description", ""),
                "hashtags": seo_doc.get("hashtags") or [],
                "pinned_comment": seo_doc.get("pinned_comment", ""),
                "long_video_url": seo_doc.get("long_video_url", ""),
                "language": seo_doc.get("language", "es-ES"),
                "ai_disclosure": bool(seo_doc.get("ai_disclosure", True)),
            }
        drafts.append(merged)

    # ── Discover in-flight shorts on disk that aren't in the manifest yet ──
    # This handles the case where the worker is actively building a short
    # but hasn't written the final manifest entry (e.g. old worker code or
    # the preliminary manifest write hasn't happened yet).
    shorts_root = shorts_paths.shorts_dir(job_dir)
    if shorts_root.exists():
        import re
        _SHORT_DIR_RE = re.compile(r"^short-\d+(?:_.*)?$")
        for child in sorted(shorts_root.iterdir()):
            if not child.is_dir() or not _SHORT_DIR_RE.match(child.name):
                continue
            short_id = child.name
            if short_id in manifest_short_ids:
                continue
            status_path = shorts_paths.short_status_path(job_dir, short_id)
            status_doc = _read_json(status_path)
            if not status_doc:
                continue
            _recovered = shorts_status.recover_stale_short(
                job_dir, short_id, status_doc, owner_alive=owner_alive
            )
            if _recovered is not None:
                status_doc = _recovered
            # Build a draft entry from the on-disk status file
            short_dir = shorts_paths.short_dir(job_dir, short_id)
            qa_doc = _read_json(short_dir / shorts_paths.SHORT_QA_FILE)
            source_map = _read_json(short_dir / shorts_paths.SHORT_SOURCE_MAP_FILE)
            seo_doc = _read_json(short_dir / shorts_paths.SHORT_SEO_FILE)
            merged = {
                "short_id": short_id,
                "idea_id": status_doc.get("idea_id"),
                "idea_type": "synthesis",
                "format": status_doc.get("format"),
                "status": status_doc.get("status", "in_progress"),
                "rendered": bool(status_doc.get("rendered")),
                "qa_verdict": status_doc.get("qa_verdict") or (qa_doc.get("qa_verdict") if qa_doc else None),
                "source_scene_ids": (
                    status_doc.get("source_scene_ids")
                    or (source_map.get("source_scene_ids") if source_map else None)
                    or (source_map.get("scene_ids") if source_map else None)
                    or []
                ),
                "video_path": status_doc.get("video_path"),
                "cover_path": status_doc.get("cover_path"),
            }
            merged.update(status_doc)
            merged["title"] = (seo_doc.get("title") if seo_doc else None) or status_doc.get("hook") or ""
            if seo_doc:
                merged["seo"] = {
                    "short_id": seo_doc.get("short_id") or short_id,
                    "title": seo_doc.get("title", ""),
                    "description": seo_doc.get("description", ""),
                    "hashtags": seo_doc.get("hashtags") or [],
                    "pinned_comment": seo_doc.get("pinned_comment", ""),
                    "long_video_url": seo_doc.get("long_video_url", ""),
                    "language": seo_doc.get("language", "es-ES"),
                    "ai_disclosure": bool(seo_doc.get("ai_disclosure", True)),
                }
            drafts.append(merged)

    return {"job_id": job_id, "drafts": drafts}


@router.get("/shorts-studio/jobs/{job_id}/shorts/{short_id}/render/progress")
def get_short_render_progress(
    job_id: str,
    short_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    short_dir = shorts_paths.short_dir(job_dir, short_id)
    progress_path = short_dir / "json" / "render_progress.json"
    if not progress_path.exists():
        progress_path = short_dir / "render_progress.json"
    if not progress_path.exists():
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
    try:
        import json as _json
        return _json.loads(progress_path.read_text(encoding="utf-8"))
    except Exception:
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}


@router.get("/shorts-studio/jobs/{job_id}/shorts/{short_id}/audio/progress")
def get_short_audio_progress(
    job_id: str,
    short_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    short_dir = shorts_paths.short_dir(job_dir, short_id)
    progress_path = short_dir / "json" / "audio_progress.json"
    if not progress_path.exists():
        progress_path = short_dir / "audio_progress.json"
    if not progress_path.exists():
        return {"percent": 0, "stage": "Preparing"}
    try:
        import json as _json
        return _json.loads(progress_path.read_text(encoding="utf-8"))
    except Exception:
        return {"percent": 0, "stage": "Preparing"}


@router.get("/shorts-studio/jobs/{job_id}/ideas")
def get_shorts_studio_ideas(job_id: str, jobs_root: Path = Depends(get_jobs_root)) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    ideas = read_short_ideas(job_dir)
    if not ideas:
        raise HTTPException(status_code=404, detail={"error": "ideas_not_found"})

    # Map idea_id to active short details to mark rendered ideas
    idea_to_short = {}
    manifest = _read_json(shorts_paths.manifest_path(job_dir))
    for entry in manifest.get("shorts") or []:
        idea_id = entry.get("idea_id")
        if idea_id:
            short_id = entry.get("short_id")
            status_doc = _read_json(shorts_paths.short_status_path(job_dir, short_id))
            idea_to_short[idea_id] = {
                "short_id": short_id,
                "status": status_doc.get("status") or entry.get("status") or "pending",
                "rendered": bool(status_doc.get("rendered") or entry.get("rendered")),
            }

    shorts_root = shorts_paths.shorts_dir(job_dir)
    if shorts_root.exists():
        import re
        _SHORT_DIR_RE = re.compile(r"^short-\d+(?:_.*)?$")
        for child in sorted(shorts_root.iterdir()):
            if not child.is_dir() or not _SHORT_DIR_RE.match(child.name):
                continue
            short_id = child.name
            status_doc = _read_json(shorts_paths.short_status_path(job_dir, short_id))
            idea_id = status_doc.get("idea_id")
            if not idea_id:
                idea_doc = _read_json(child / shorts_paths.SHORT_IDEA_FILE)
                idea_id = idea_doc.get("idea_id")
            if idea_id and idea_id not in idea_to_short:
                idea_to_short[idea_id] = {
                    "short_id": short_id,
                    "status": status_doc.get("status") or "in_progress",
                    "rendered": bool(status_doc.get("rendered")),
                }

    # Decorate ideas with active short info
    for idea in ideas.get("ideas") or []:
        idea_id = idea.get("idea_id")
        if idea_id in idea_to_short:
            idea["associated_short"] = idea_to_short[idea_id]

    return ideas


@router.delete("/shorts-studio/jobs/{job_id}/ideas")
def delete_shorts_studio_ideas(job_id: str, jobs_root: Path = Depends(get_jobs_root)) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    busy, _active = system_has_active_job(jobs_root)
    if busy:
        raise HTTPException(status_code=409, detail={"error": "system_busy"})
    deleted: list[str] = []
    for path_fn in (
        shorts_paths.short_ideas_path,
        shorts_paths.idea_generation_run_path,
        shorts_paths.selected_short_ideas_path,
    ):
        p = path_fn(job_dir)
        if p.exists():
            p.unlink()
            deleted.append(p.name)
    return {"status": "deleted", "job_id": job_id, "deleted_files": deleted}


@router.get("/shorts-studio/jobs/{job_id}/shorts/{short_id}/detail")
def get_short_detail(job_id: str, short_id: str, jobs_root: Path = Depends(get_jobs_root)) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    short_dir_path = shorts_paths.short_dir(job_dir, short_id)
    
    # Check if short is in manifest or folder exists
    manifest_path = shorts_paths.manifest_path(job_dir)
    has_short = short_dir_path.exists()
    manifest_entry = {}
    if not has_short and manifest_path.exists():
        try:
            manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
            shorts_list = manifest_doc.get("shorts") or []
            for s in shorts_list:
                if s.get("short_id") == short_id:
                    has_short = True
                    manifest_entry = s
                    break
        except Exception:
            pass
            
    if not has_short:
        raise HTTPException(status_code=404, detail=f"Unknown short: {short_id}")
        
    status_doc = _read_json(shorts_paths.short_status_path(job_dir, short_id))
    # Fallback to manifest status/hook if status_doc is empty
    if not status_doc:
        status_doc = {
            "short_id": short_id,
            "status": manifest_entry.get("status") or "pending",
            "hook": manifest_entry.get("hook") or "",
        }
    qa_doc = _read_json(short_dir_path / shorts_paths.SHORT_QA_FILE)
    qa_script_doc = _read_json(short_dir_path / shorts_paths.SHORT_SCRIPT_QA_FILE)
    qa_scenes_doc = _read_json(short_dir_path / shorts_paths.SHORT_SCENES_QA_FILE)
    script_doc = _read_json(short_dir_path / shorts_paths.SHORT_SCRIPT_FILE)
    scenes_doc = _read_json(short_dir_path / shorts_paths.SHORT_SCENES_FILE)
    seo_doc = _read_json(short_dir_path / shorts_paths.SHORT_SEO_FILE)
    idea_doc = _read_json(short_dir_path / shorts_paths.SHORT_IDEA_FILE)
    video_exists = (short_dir_path / shorts_paths.SHORT_VIDEO_FILE).exists()
    cover_exists = (short_dir_path / shorts_paths.SHORT_COVER_FILE).exists()
    return {
        "short_id": short_id,
        "job_id": job_id,
        "status": status_doc,
        "qa": qa_doc,
        "qa_script": qa_script_doc,
        "qa_scenes": qa_scenes_doc,

        "script_summary": {
            "title": script_doc.get("title", ""),
            "hook": script_doc.get("hook", ""),
            "scene_count": len(script_doc.get("scenes") or []),
        },
        "scenes_count": len(scenes_doc.get("scenes") or []),
        "seo": {
            "title": seo_doc.get("title", ""),
            "description": seo_doc.get("description", "")[:200],
        },
        "idea": idea_doc,
        "video_exists": video_exists,
        "cover_exists": cover_exists,
        "video_path": f"/jobs/{job_id}/shorts/{short_id}/{shorts_paths.SHORT_VIDEO_FILE}" if video_exists else None,
        "cover_path": f"/jobs/{job_id}/shorts/{short_id}/{shorts_paths.SHORT_COVER_FILE}" if cover_exists else None,
    }


@router.delete("/shorts-studio/jobs/{job_id}/shorts/{short_id}")
def delete_short(job_id: str, short_id: str, jobs_root: Path = Depends(get_jobs_root)) -> dict[str, Any]:
    import shutil
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    short_dir_path = shorts_paths.short_dir(job_dir, short_id)
    
    # Check if short is in manifest or folder exists
    manifest_path = shorts_paths.manifest_path(job_dir)
    has_short = short_dir_path.exists()
    if not has_short and manifest_path.exists():
        try:
            manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
            shorts_list = manifest_doc.get("shorts") or []
            if any(s.get("short_id") == short_id for s in shorts_list):
                has_short = True
        except Exception:
            pass
            
    if not has_short:
        raise HTTPException(status_code=404, detail=f"Unknown short: {short_id}")

    # Check if currently active/running
    busy, active_jobs = system_has_active_job(jobs_root)
    is_active = False
    for job in active_jobs:
        if job.get("job_id") == job_id and job.get("short_id") == short_id:
            is_active = True
            break
    lock_file = short_dir_path / shorts_paths.SHORT_LOCK_FILE
    if is_active or _is_file_locked(lock_file):
        raise HTTPException(status_code=409, detail={"error": "short_busy"})

    # 1. Delete the directories
    shutil.rmtree(short_dir_path, ignore_errors=True)
    shutil.rmtree(shorts_paths.short_tmp_dir(job_dir, short_id), ignore_errors=True)
    # Also delete from remotion/public/jobs/<short_id>
    from video_agent.contracts import repo_root
    public_short_dir = repo_root() / "remotion" / "public" / "jobs" / short_id
    if public_short_dir.exists():
        shutil.rmtree(public_short_dir, ignore_errors=True)

    # 2. Delete short_status file
    status_file = shorts_paths.short_status_path(job_dir, short_id)
    if status_file.exists():
        status_file.unlink(missing_ok=True)

    # 3. Update manifest.json
    manifest_path = shorts_paths.manifest_path(job_dir)
    deleted_from_manifest = False
    if manifest_path.exists():
        try:
            manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
            shorts_list = manifest_doc.get("shorts") or []
            new_shorts = [s for s in shorts_list if s.get("short_id") != short_id]
            if len(new_shorts) != len(shorts_list):
                manifest_doc["shorts"] = new_shorts
                from video_agent.shorts import manifest as manifest_mod
                manifest_mod.write_manifest(job_dir, manifest_doc)
                deleted_from_manifest = True
        except Exception:
            pass

    return {
        "status": "deleted",
        "job_id": job_id,
        "short_id": short_id,
        "deleted_from_manifest": deleted_from_manifest,
    }


def _clear_stop_requested(job_dir: Path) -> None:
    stop_file = job_dir / ".stop_requested"
    if stop_file.exists():
        stop_file.unlink(missing_ok=True)


@router.post("/shorts-studio/jobs/{job_id}/ideas/generate", status_code=202)
def post_shorts_studio_generate_ideas(
    job_id: str,
    req: GenerateIdeasRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    missing = _job_missing_artifacts(job_dir)
    if missing:
        raise HTTPException(status_code=400, detail={"error": "missing_artifacts", "missing": missing})
    busy, _active = system_has_active_job(jobs_root)
    if busy:
        raise HTTPException(status_code=409, detail={"error": "system_busy"})
    _clear_stop_requested(job_dir)
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(
        job_id,
        enforce_approvals=False,
        command="shorts_generate_ideas",
        payload={"target_count": int(req.target_count), "force": bool(req.force)},
    )
    return {"status": "enqueued", "command": "shorts_generate_ideas", "job_id": job_id}


@router.post("/shorts-studio/jobs/{job_id}/ideas/render", status_code=202)
def post_shorts_studio_render_ideas(
    job_id: str,
    req: RenderIdeasRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    busy, _active = system_has_active_job(jobs_root)
    if busy:
        raise HTTPException(status_code=409, detail={"error": "system_busy"})
    ideas = read_short_ideas(job_dir)
    if not ideas:
        raise HTTPException(status_code=400, detail={"error": "ideas_not_found"})
    valid_ids = {str(idea.get("idea_id")) for idea in ideas.get("ideas") or []}
    idea_ids = [idea_id for idea_id in req.idea_ids if idea_id]
    if not idea_ids:
        raise HTTPException(status_code=400, detail={"error": "missing_idea_ids"})
    invalid = [idea_id for idea_id in idea_ids if idea_id not in valid_ids]
    if invalid:
        raise HTTPException(status_code=400, detail={"error": "invalid_idea_ids", "idea_ids": invalid})

    # Prevent rendering if already rendered (unless force=True, but we want to honor user request)
    if not req.force:
        manifest = _read_json(shorts_paths.manifest_path(job_dir))
        active_idea_ids = set()
        for entry in manifest.get("shorts") or []:
            if entry.get("idea_id"):
                active_idea_ids.add(entry.get("idea_id"))

        shorts_root = shorts_paths.shorts_dir(job_dir)
        if shorts_root.exists():
            import re
            _SHORT_DIR_RE = re.compile(r"^short-\d+(?:_.*)?$")
            for child in sorted(shorts_root.iterdir()):
                if not child.is_dir() or not _SHORT_DIR_RE.match(child.name):
                    continue
                status_doc = _read_json(shorts_paths.short_status_path(job_dir, child.name))
                idea_id = status_doc.get("idea_id")
                if not idea_id:
                    idea_doc = _read_json(child / shorts_paths.SHORT_IDEA_FILE)
                    idea_id = idea_doc.get("idea_id")
                if idea_id:
                    active_idea_ids.add(idea_id)

        already_rendered = [idea_id for idea_id in idea_ids if idea_id in active_idea_ids]
        if already_rendered:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "already_rendered",
                    "message": f"Idea(s) {', '.join(already_rendered)} have already been rendered. Delete their existing shorts/jobs first.",
                }
            )

    _clear_stop_requested(job_dir)
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(
        job_id,
        enforce_approvals=False,
        command="shorts_render_selected_ideas",
        payload={"idea_ids": idea_ids, "force": bool(req.force)},
    )
    return {
        "status": "enqueued",
        "command": "shorts_render_selected_ideas",
        "job_id": job_id,
        "idea_ids": idea_ids,
    }


@router.post("/shorts-studio/jobs/{job_id}/prepare", status_code=202)
def post_shorts_studio_prepare(
    job_id: str,
    req: PrepareDraftsRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    missing = _job_missing_artifacts(job_dir)
    if missing:
        raise HTTPException(status_code=400, detail={"error": "missing_artifacts", "missing": missing})
    busy, _active = system_has_active_job(jobs_root)
    if busy:
        raise HTTPException(status_code=409, detail={"error": "system_busy"})
    _clear_stop_requested(job_dir)
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(
        job_id,
        enforce_approvals=False,
        command="shorts_prepare_drafts",
        payload={"force": bool(req.force)},
    )
    return {"status": "enqueued", "command": "shorts_prepare_drafts", "job_id": job_id}


@router.post("/shorts-studio/jobs/{job_id}/confirm-render", status_code=202)
def post_shorts_studio_confirm_render(
    job_id: str,
    req: ConfirmRenderRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict[str, Any]:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    busy, _active = system_has_active_job(jobs_root)
    if busy:
        raise HTTPException(status_code=409, detail={"error": "system_busy"})
    short_ids = [short_id for short_id in req.short_ids if short_id]
    if not short_ids:
        raise HTTPException(status_code=400, detail={"error": "missing_short_ids"})
    for short_id in short_ids:
        short_dir = shorts_paths.short_dir(job_dir, short_id)
        status_doc = _read_json(shorts_paths.short_status_path(job_dir, short_id))
        if status_doc.get("status") != "ready_for_render" or status_doc.get("qa_verdict") != "PASS":
            raise HTTPException(status_code=400, detail={"error": "short_not_ready", "short_id": short_id})
        for required in (
            shorts_paths.SHORT_SCRIPT_FILE,
            shorts_paths.SHORT_SCENES_FILE,
            shorts_paths.SHORT_SEO_FILE,
            shorts_paths.SHORT_QA_FILE,
        ):
            if not (short_dir / required).exists():
                raise HTTPException(
                    status_code=400,
                    detail={"error": "missing_short_artifacts", "short_id": short_id, "missing": required},
                )
    _clear_stop_requested(job_dir)
    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(
        job_id,
        enforce_approvals=False,
        command="shorts_confirm_render",
        payload={"short_ids": short_ids},
    )
    return {
        "status": "enqueued",
        "command": "shorts_confirm_render",
        "job_id": job_id,
        "short_ids": short_ids,
    }
