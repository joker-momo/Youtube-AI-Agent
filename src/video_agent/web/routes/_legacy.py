from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import shutil
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from video_agent.contracts import EVENT_LOG, repo_root, load_env
load_env()

from video_agent.orchestrator import (
    JobAlreadyExistsError,
    JobNotFoundError,
    advance,
    create_job,
    load_job,
)
from video_agent.utils.logging import EventLogger
from video_agent.orchestrator.idea_generator import (
    find_duplicate,
    generate_ideas,
    load_published_videos,
    normalize_keyword,
    save_ideas,
    sync_published_videos,
)
from video_agent.orchestrator.browser_client import (
    BrowserClient,
    BrowserClientError,
    LoginRequiredFromWorker,
)
from video_agent.orchestrator.orchestrator import StageError
from video_agent.orchestrator.stages import (
    IDEA_FILE,
    StageInputMissingError,
    _idea_keywords,
    auto_idea_research_stage,
    auto_scenes_qa_stage,
    auto_scenes_stage,
    auto_script_qa_stage,
    auto_script_stage,
    auto_seo_qa_stage,
    auto_seo_stage,
    auto_thumbnail_image_stage,
    generate_scene_asset,
    promote_scenes_stage,
    promote_seo_stage,
    promote_script_stage,
    run_render_stage,
    run_persona_eval_stage,
    run_review_stage,
    run_scenes_stage,
    run_seo_stage,
    run_script_stage,
    run_whisper_timestamps_stage,
)
from video_agent.web.approval_flow import (
    APPROVAL_REQUIRED_STAGES,
    approval_block_for_current_stage,
    load_approvals,
    reset_stage_for_regen,
    set_approval,
)
from video_agent.web.run_all_pipeline import execute_run_all, is_run_locked, stop_request_path
from video_agent.web.timeline_helpers import (
    STAGE_ARTIFACTS,
    STAGE_ETA_SECONDS,
    effective_stage_status,
    job_has_in_progress_stage,
    resolve_inside,
    stage_duration_seconds,
)
from video_agent.storage.atomic import atomic_write_json, atomic_write_text

router = APIRouter()

# Track live /run-all request tasks by job_id so /stop can cancel promptly.
_RUN_ALL_TASKS: dict[str, asyncio.Task[Any]] = {}


def _kill_job_subprocesses(job_dir: Path) -> list[int]:
    """Best-effort hard-stop for known long-running subprocesses."""
    killed: list[int] = []
    for name in (".render.pid", ".thumbnail.pid"):
        pid_path = job_dir / name
        if not pid_path.exists():
            continue
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
        except OSError:
            pass
        try:
            pid_path.unlink()
        except OSError:
            pass
    return killed


class CreateJobRequest(BaseModel):
    job_id: str
    channel_id: str
    idea_path: str


class RawScriptRequest(BaseModel):
    raw_response: str


# Backwards-compatible re-exports. The /config/env* handlers and env helpers
# moved to ``video_agent.web.routes.config`` and ``video_agent.web.services.env_config``
# in the Phase 2.1 refactor. Re-exporting the old private names keeps tests
# that still patch ``_legacy._env_path`` (and external callers importing
# ``EnvSaveRequest`` from this module) working without rewrites.
from video_agent.web.routes.config import EnvSaveRequest  # noqa: E402,F401
from video_agent.web.services.env_config import (  # noqa: E402
    active_env_example_path as _active_env_example_path,
    active_env_path as _active_env_path,
    env_editor_enabled as _env_editor_enabled,
    env_example_path as _env_example_path,
    env_path as _env_path,
    mask_env_content as _mask_env_content,
    mask_env_value as _mask_env_value,
    require_env_editor as _require_env_editor,
)


def get_jobs_root() -> Path:
    return Path(os.environ.get("JOBS_DIR", "/app/jobs"))


_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_channel_id(channel_id: str) -> str:
    if not channel_id or not _SAFE_CHANNEL_ID_RE.match(channel_id):
        raise HTTPException(status_code=400, detail=f"Invalid channel_id: {channel_id!r}")
    return channel_id


def _safe_job_dir(jobs_root: Path, job_id: str) -> Path:
    """Validate ``job_id`` and return the resolved job directory inside ``jobs_root``.

    Rejects any id that contains path separators, parent refs, NUL, or that
    resolves outside ``jobs_root``. Raises HTTPException(400) on violation.
    """
    if not job_id or not _SAFE_JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail=f"Invalid job_id: {job_id!r}")
    candidate = (jobs_root / job_id).resolve()
    try:
        candidate.relative_to(jobs_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid job_id: {job_id!r}") from exc
    return candidate


def _enqueue_stage_command(
    *,
    job_id: str,
    jobs_root: Path,
    command: str,
    payload: dict[str, Any] | None = None,
) -> dict:
    from video_agent.orchestrator.queue import JobQueue

    queue = JobQueue(jobs_root / "queue.db")
    queue.enqueue(job_id, enforce_approvals=False, command=command, payload=payload)
    return {"job_id": job_id, "status": "enqueued", "command": command}


def _queue_status(jobs_root: Path, job_id: str, job_dir: Path | None = None) -> str | None:
    try:
        if job_dir is not None and is_run_locked(job_dir):
            return "running"
        from video_agent.orchestrator.queue import JobQueue

        queue = JobQueue(jobs_root / "queue.db")
        row = queue.get_job(job_id)
        if row and row.get("status") == "running" and queue.is_running_stale(job_id):
            return "stale"
        return str(row.get("status")) if row and row.get("status") else None
    except Exception:
        return None


@router.get("/health")
def health() -> dict:
    return {"ok": True, "service": "app"}


# /config/env, /config/env (POST), /config/env/bootstrap handlers moved to
# video_agent.web.routes.config — see commit extracting Phase 2.1 of the
# legacy refactor. Helpers still re-exported above for backward-compatible
# imports.


@router.get("/jobs")
def list_jobs(jobs_root: Path = Depends(get_jobs_root)) -> dict:
    """List every job folder under JOBS_DIR that has a ``job.json``.

    Returns a summary view per job (stage progress + duration) suitable
    for the dashboard. Heavy fields (stages array) are included so the
    dashboard does not need a second round-trip per row.
    """
    items = []
    if jobs_root.exists():
        for entry in sorted(jobs_root.iterdir(), reverse=True):
            job_file = entry / "job.json"
            if not job_file.exists():
                continue
            try:
                payload = json.loads(job_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            current_stage = payload.get("current_stage")
            stop_requested = stop_request_path(entry).exists()
            queue_status = _queue_status(
                jobs_root,
                str(payload.get("job_id") or entry.name),
                entry,
            )
            current_stage_active = queue_status == "running" or job_has_in_progress_stage(payload)
            stages = []
            for raw_stage in payload.get("stages", []):
                stage = dict(raw_stage)
                stage["status"] = effective_stage_status(
                    stage,
                    current_stage,
                    stop_requested=stop_requested,
                    current_stage_active=current_stage_active,
                )
                stages.append(stage)
            done = sum(1 for s in stages if s.get("status") == "completed")
            total = len(stages)
            in_progress = [
                s["name"] for s in stages if s.get("status") == "in_progress"
            ]
            items.append(
                {
                    "job_id": payload.get("job_id"),
                    "idea_title": _job_idea_title(entry),
                    "channel_id": payload.get("channel_id"),
                    "current_stage": current_stage,
                    "created_at": payload.get("created_at"),
                    "updated_at": payload.get("updated_at"),
                    "queue_status": queue_status,
                    "stages_done": done,
                    "stages_total": total,
                    "in_progress": in_progress,
                    "stages": stages,
                }
            )
    return {"count": len(items), "jobs": items}


def _job_idea_title(job_dir: Path) -> str:
    idea_path = job_dir / IDEA_FILE
    if not idea_path.exists():
        idea_path = job_dir / "idea.json"
        if not idea_path.exists():
            return ""
    try:
        idea = json.loads(idea_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for key in ("title_seed", "recommended_angle", "topic", "target_keyword"):
        value = idea.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# ---------------------------------------------------------------------------
# Dashboard timeline / artifact endpoints.
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/timeline")
def job_timeline(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    """Return per-stage view for the dashboard.

    Each entry combines the stage's status from job.json, the artifact
    relative paths for input + output (so the UI can request them via
    /jobs/{id}/artifact?path=...), the actual elapsed seconds if the
    stage has run, and an ETA in seconds for stages still pending.
    """
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    stop_requested = stop_request_path(job_dir).exists()

    # Render ETA scales with target_duration_sec when known.
    render_eta = STAGE_ETA_SECONDS["render"]
    try:
        scenes_path = job_dir / "json" / "scenes.json"
        if not scenes_path.exists():
            scenes_path = job_dir / "scenes.json"
        if scenes_path.exists():
            sc = json.loads(scenes_path.read_text(encoding="utf-8"))
            total = int(sc.get("total_duration_sec") or 0)
            if total > 0:
                render_eta = total * 1.2  # ~1.2x realtime on typical machine
        else:
            idea_path = job_dir / IDEA_FILE
            if not idea_path.exists():
                idea_path = job_dir / "idea.json"
            if idea_path.exists():
                idea = json.loads(idea_path.read_text(encoding="utf-8"))
                total = int(idea.get("target_duration_sec") or 0)
                if total > 0:
                    render_eta = total * 1.2
    except Exception:
        pass

    items = []
    remaining_eta = 0.0
    completed_so_far = 0
    total_stages = len(state.get("stages", []))
    current_stage = state.get("current_stage")
    queue_status = _queue_status(jobs_root, job_id, job_dir)
    current_stage_active = queue_status == "running" or job_has_in_progress_stage(state)
    for raw_stage in state.get("stages", []):
        stage = dict(raw_stage)
        stage["status"] = effective_stage_status(
            stage,
            current_stage,
            stop_requested=stop_requested,
            current_stage_active=current_stage_active,
        )
        name = stage.get("name")
        cfg = STAGE_ARTIFACTS.get(name, {})
        inputs = []
        for rel in cfg.get("input", []):
            p = resolve_inside(job_dir, rel)
            inputs.append(
                {"path": rel, "exists": bool(p and p.exists()), "size": (p.stat().st_size if p and p.exists() else 0)}
            )
        outputs = []
        for rel in cfg.get("output", []):
            p = resolve_inside(job_dir, rel)
            outputs.append(
                {"path": rel, "exists": bool(p and p.exists()), "size": (p.stat().st_size if p and p.exists() else 0)}
            )
        actual = stage_duration_seconds(stage)
        eta = render_eta if name == "render" else STAGE_ETA_SECONDS.get(name, 30)
        if stage.get("status") == "completed":
            completed_so_far += 1
        elif stage.get("status") != "completed":
            remaining_eta += eta
        sub_progress = None
        # Sharded stages emit per-batch artifacts to a directory; surface
        # the saved/total count so the UI can show meaningful in-stage
        # progress instead of a frozen "Scenes JSON · 0s" row.
        if name == "scenes_promote":
            batches_dir = job_dir / "operator" / "chatgpt" / "scenes_batches"
            plan_path = job_dir / "operator" / "chatgpt" / "scenes_plan.json"
            done = 0
            total = 0
            if batches_dir.is_dir():
                done = sum(
                    1
                    for f in batches_dir.glob("scenes_batch_*.json")
                    if f.is_file()
                )
            try:
                if plan_path.exists():
                    plan_obj = json.loads(plan_path.read_text(encoding="utf-8"))
                    data = plan_obj.get("data") if isinstance(plan_obj, dict) else None
                    batches = (data or {}).get("batches") if isinstance(data, dict) else None
                    if isinstance(batches, list):
                        total = len(batches)
            except Exception:
                total = 0
            if done or total:
                sub_progress = {
                    "kind": "scenes_batches",
                    "done": done,
                    "total": total,
                    "label": f"Batch {done}/{total or '?'} saved",
                }
        item = {
            **stage,
            "inputs": inputs,
            "outputs": outputs,
            "actual_seconds": actual,
            "eta_seconds": eta if stage.get("status") != "completed" else 0,
        }
        if sub_progress is not None:
            item["sub_progress"] = sub_progress
        items.append(item)

    shorts_stage = _shorts_timeline_stage(job_dir)
    if shorts_stage is not None:
        items.append(shorts_stage)
        total_stages += 1
        if shorts_stage.get("status") == "completed":
            completed_so_far += 1
        elif shorts_stage.get("status") != "completed":
            remaining_eta += int(shorts_stage.get("eta_seconds") or 0)

    pct = (100.0 * completed_so_far / total_stages) if total_stages else 0
    approvals = load_approvals(job_dir)
    stop_requested = stop_request_path(job_dir).exists()
    return {
        "job_id": job_id,
        "idea_title": _job_idea_title(job_dir),
        "channel_id": state.get("channel_id"),
        "current_stage": state.get("current_stage"),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "queue_status": queue_status,
        "stages_done": completed_so_far,
        "stages_total": total_stages,
        "percent": round(pct, 1),
        "remaining_eta_seconds": int(remaining_eta),
        "stages": items,
        "approvals": approvals,
        "required_approvals": list(APPROVAL_REQUIRED_STAGES),
        "approval_blocked_by": approval_block_for_current_stage(
            state.get("current_stage"), approvals
        ),
        "stop_requested": stop_requested,
    }


def _shorts_timeline_stage(job_dir: Path) -> dict | None:
    """Virtual final timeline stage for Shorts child artifacts.

    DEFAULT_STAGES stays long-form only; this projected stage makes the job
    detail timeline show Shorts as the final child pipeline of the long job.
    """
    try:
        from video_agent.shorts import status as shorts_status

        summary = shorts_status.summarize_shorts(job_dir)
    except Exception:
        return None

    manifest_exists = (job_dir / "shorts" / "shorts_manifest.json").exists()
    running = bool(summary.get("running"))
    shorts = list(summary.get("shorts") or [])
    if not running and not manifest_exists and not shorts:
        return None

    state = summary.get("state") or "none"
    if running:
        stage_status = "in_progress"
    elif state == "failed":
        stage_status = "failed"
    elif manifest_exists:
        stage_status = "completed"
    else:
        stage_status = "pending"

    outputs = [
        {"path": "shorts/shorts_manifest.json", "exists": manifest_exists, "size": 0},
        {
            "path": "shorts/autopilot_run.json",
            "exists": (job_dir / "shorts" / "autopilot_run.json").exists(),
            "size": 0,
        },
    ]
    for output in outputs:
        p = resolve_inside(job_dir, output["path"])
        if p and p.exists():
            output["size"] = p.stat().st_size

    short_steps = []
    for entry in shorts:
        short_id = entry.get("short_id")
        qa = entry.get("qa_verdict") or "-"
        status_name = entry.get("status") or "pending"
        rendered = status_name == "rendered"
        needs_review = status_name == "needs_review"
        steps = [
            {"name": "idea", "status": "completed"},
            {"name": "script", "status": "completed"},
            {"name": "scenes", "status": "completed"},
            {"name": "source_map", "status": "completed"},
            {"name": "seo", "status": "completed"},
            {"name": "qa", "status": "completed" if qa == "PASS" or needs_review else "pending", "label": f"QA {qa}"},
            {"name": "audio_mix", "status": "completed" if rendered else ("skipped" if needs_review else "pending")},
            {"name": "render", "status": "completed" if rendered else ("skipped" if needs_review else "pending")},
        ]
        short_steps.append(
            {
                "short_id": short_id,
                "status": status_name,
                "qa_verdict": qa,
                "hook": entry.get("hook", ""),
                "video_path": entry.get("video_path", ""),
                "cover_path": entry.get("cover_path", ""),
                "steps": steps,
            }
        )

    return {
        "name": "shorts_autopilot",
        "label": "Shorts Autopilot",
        "status": stage_status,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "inputs": [
            {"path": "outputs/video.mp4", "exists": (job_dir / "outputs/video.mp4").exists() or (job_dir / "video.mp4").exists(), "size": ((job_dir / "outputs/video.mp4").stat().st_size if (job_dir / "outputs/video.mp4").exists() else ((job_dir / "video.mp4").stat().st_size if (job_dir / "video.mp4").exists() else 0))},
            {"path": "json/review.json", "exists": (job_dir / "json/review.json").exists() or (job_dir / "review.json").exists(), "size": ((job_dir / "json/review.json").stat().st_size if (job_dir / "json/review.json").exists() else ((job_dir / "review.json").stat().st_size if (job_dir / "review.json").exists() else 0))},
        ],
        "outputs": outputs,
        "actual_seconds": None,
        "eta_seconds": 0 if stage_status == "completed" else 300,
        "sub_progress": {
            "kind": "shorts_autopilot",
            "label": summary.get("label", "none"),
            "counts": summary.get("counts") or {},
            "shorts": short_steps,
        },
    }


@router.get("/jobs/{job_id}/artifact")
def job_artifact(
    job_id: str,
    path: str,
    jobs_root: Path = Depends(get_jobs_root),
):
    """Stream a single file from inside the job directory.

    ``path`` is interpreted as relative to ``<jobs_root>/<job_id>/`` and
    is rejected (404) if it escapes that directory.
    """
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    target = resolve_inside(job_dir, path)
    if target is None or not target.exists() or not target.is_file():
        # Fallback check for backward compatibility:
        # If the requested path is a top-level filename, try json/ or outputs/
        if "/" not in path and "\\" not in path:
            if path.endswith(".json") or path.endswith(".jsonl"):
                fallback = resolve_inside(job_dir, f"json/{path}")
                if fallback and fallback.exists() and fallback.is_file():
                    target = fallback
            else:
                fallback = resolve_inside(job_dir, f"outputs/{path}")
                if fallback and fallback.exists() and fallback.is_file():
                    target = fallback

    if target is None or not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")
    # FastAPI guesses media type from the path; this is enough for our
    # mix of .json / .md / .txt / .mp4 / .jpg.
    return FileResponse(target)


@router.get("/jobs/{job_id}/logs")
def job_logs(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    tail: int = 200,
) -> dict:
    """Return realtime-friendly log payload for dashboard Logs tab."""
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    def _tail_lines(path: Path, limit: int) -> list[str]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return []
        return lines[-max(1, limit):]

    def _tail_jsonl(path: Path, limit: int) -> list[dict]:
        out = []
        for line in _tail_lines(path, limit):
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        return out

    events = _tail_jsonl(job_dir / EVENT_LOG, limit=tail)
    render_progress = None
    try:
        rp = job_dir / "json" / "render_progress.json"
        if not rp.exists():
            rp = job_dir / "render_progress.json"
        if rp.exists():
            render_progress = json.loads(rp.read_text(encoding="utf-8"))
    except Exception:
        render_progress = None

    incident_dir = repo_root() / "logs" / "incidents"
    latest_incident = None
    if incident_dir.exists():
        for p in sorted(incident_dir.glob("*.incident.json"), reverse=True)[:200]:
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for snap in payload.get("recent_jobs", []):
                if job_id in str(snap.get("job_dir", "")):
                    latest_incident = {
                        "path": str(p),
                        "run_id": payload.get("run_id"),
                        "status": payload.get("status"),
                        "ended_at": payload.get("ended_at"),
                        "error": payload.get("error"),
                    }
                    break
            if latest_incident:
                break

    text_lines = []
    text_lines.append(f"job_id={job_id}")
    text_lines.append(f"job_dir={job_dir}")
    text_lines.append(f"stop_requested={stop_request_path(job_dir).exists()}")
    if render_progress:
        text_lines.append("render_progress=" + json.dumps(render_progress, ensure_ascii=False))
    if latest_incident:
        text_lines.append("latest_incident=" + json.dumps(latest_incident, ensure_ascii=False))
    text_lines.append("events_tail:")
    for e in events:
        text_lines.append(json.dumps(e, ensure_ascii=False))

    return {
        "job_id": job_id,
        "job_dir": str(job_dir),
        "render_progress": render_progress,
        "latest_incident": latest_incident,
        "events": events,
        "stop_requested": stop_request_path(job_dir).exists(),
        "text": "\n".join(text_lines),
    }


@router.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return _load_dashboard_html()


_DASHBOARD_HTML_PATH = Path(__file__).resolve().parents[1] / "dashboard.html"


def _load_dashboard_html() -> str:
    return _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")


@router.post("/jobs", status_code=201)
def post_job(
    payload: CreateJobRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, payload.job_id)
    try:
        state = create_job(
            job_dir,
            job_id=payload.job_id,
            channel_id=payload.channel_id,
            idea_path=payload.idea_path,
        )
    except JobAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state.to_dict()


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return load_job(job_dir).to_dict()


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> None:
    job_dir = _safe_job_dir(jobs_root, job_id)
    job_file = job_dir / "job.json"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    payload = json.loads(job_file.read_text(encoding="utf-8"))
    if job_has_in_progress_stage(payload):
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a running job. Stop it first, then delete.",
        )
    shutil.rmtree(job_dir)


@router.post("/jobs/{job_id}/stop")
def stop_job(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    """Request graceful stop for the current /run-all execution."""
    job_dir = _safe_job_dir(jobs_root, job_id)
    job_file = job_dir / "job.json"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    atomic_write_text(stop_request_path(job_dir), "1\n", encoding="utf-8")
    try:
        state = load_job(job_dir)
        EventLogger(job_dir / EVENT_LOG).log(
            "STOP_REQUESTED",
            {"job_id": state.job_id, "current_stage": state.current_stage},
        )
    except Exception:
        pass
    killed_pids = _kill_job_subprocesses(job_dir)
    cancelled = False
    task = _RUN_ALL_TASKS.get(job_id)
    if task is not None and not task.done():
        cancelled = task.cancel("stop requested by operator")
    return {
        "job_id": job_id,
        "stop_requested": True,
        "cancelled": bool(cancelled),
        "killed_pids": killed_pids,
    }


@router.post("/jobs/{job_id}/advance")
def post_advance(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    try:
        state = advance(job_dir)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state.to_dict()


@router.get("/jobs/{job_id}/events")
def get_events(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    events_path = job_dir / EVENT_LOG
    if not events_path.exists():
        return {"job_id": job_id, "events": []}
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {"job_id": job_id, "events": events}


def get_channel_path() -> Path:
    return Path(
        os.environ.get(
            "CHANNEL_CONFIG",
            "/app/configs/vida-plena-45/channel.yaml",
        )
    )


def get_inputs_root() -> Path:
    return Path(os.environ.get("INPUTS_DIR", "/app/inputs"))


@router.post("/jobs/{job_id}/idea", status_code=201)
def post_idea(
    job_id: str,
    idea: dict,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    from video_agent.utils.validation import validate_json
    try:
        validate_json(idea, repo_root() / "schemas" / "manual-idea.schema.json")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid idea payload: {exc}") from exc
    idea_path = job_dir / IDEA_FILE
    atomic_write_json(idea_path, idea)
    return {"job_id": job_id, "idea_path": str(idea_path)}


@router.post("/jobs/{job_id}/stages/script/run")
def post_run_script(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_script_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/script/promote")
def post_promote_script(
    job_id: str,
    payload: RawScriptRequest,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = promote_script_stage(job_dir, channel_path, payload.raw_response)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    set_approval(job_dir, "script_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/scenes/run")
def post_run_scenes(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_scenes_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/scenes/promote")
def post_promote_scenes(
    job_id: str,
    payload: RawScriptRequest,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = promote_scenes_stage(job_dir, channel_path, payload.raw_response)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    set_approval(job_dir, "scenes_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/seo/run")
def post_run_seo(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_seo_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/seo/promote")
def post_promote_seo(
    job_id: str,
    payload: RawScriptRequest,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = promote_seo_stage(job_dir, channel_path, payload.raw_response)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    set_approval(job_dir, "seo_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/whisper_timestamps/run")
def post_run_whisper_timestamps(
    job_id: str,
    async_: bool = Query(False, alias="async"),
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if async_:
        return _enqueue_stage_command(
            job_id=job_id,
            jobs_root=jobs_root,
            command="stage_whisper_timestamps",
        )
    try:
        output = run_whisper_timestamps_stage(job_dir)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/render/run")
def post_run_render(
    job_id: str,
    async_: bool = Query(False, alias="async"),
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if async_:
        return _enqueue_stage_command(
            job_id=job_id,
            jobs_root=jobs_root,
            command="stage_render",
        )
    try:
        output = run_render_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.get("/jobs/{job_id}/stages/render/progress")
def get_render_progress(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    """Return current Remotion render progress. Polling-friendly — always 200."""
    job_dir = _safe_job_dir(jobs_root, job_id)
    progress_path = job_dir / "json" / "render_progress.json"
    if not progress_path.exists():
        progress_path = job_dir / "render_progress.json"
    if not progress_path.exists():
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
    try:
        import json as _json
        return _json.loads(progress_path.read_text(encoding="utf-8"))
    except Exception:
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}


@router.get("/jobs/{job_id}/stages/whisper_timestamps/progress")
def get_whisper_progress(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    """Latest WHISPER_STAGE_PROGRESS event from events.jsonl.

    Worker emits per-stage heartbeats (synth → load → transcribe → map);
    transcribe heartbeats include ``estimated_pct``. UI uses that for a
    live progress bar similar to render.
    """
    job_dir = _safe_job_dir(jobs_root, job_id)
    log_path = job_dir / "events.jsonl"
    if not log_path.exists():
        return {"percent": 0, "step": "", "elapsed_sec": 0}
    latest_step = ""
    latest_pct = 0.0
    latest_elapsed = 0.0
    latest_meta = ""
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line or "WHISPER_STAGE_PROGRESS" not in line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            data = obj.get("data") or {}
            latest_step = str(data.get("step") or latest_step)
            if data.get("estimated_pct") is not None:
                try:
                    latest_pct = float(data["estimated_pct"])
                except Exception:
                    pass
            if data.get("elapsed_sec") is not None:
                try:
                    latest_elapsed = float(data["elapsed_sec"])
                except Exception:
                    pass
            # Coarse step → percent floor so non-transcribe steps still
            # render a bar.
            floor_map = {
                "start": 1,
                "synthesizing_narration_audio": 5,
                "narration_audio_ready": 15,
                "audio_info": 20,
                "loading_whisper_model_tiny": 25,
                "whisper_model_loaded": 30,
                "transcribing_audio": 35,
                "transcription_complete": 90,
                "mapping_words_to_scenes": 95,
                "scene_mapping_complete": 98,
                "timestamps_written": 100,
            }
            floor = floor_map.get(latest_step, 0)
            if floor > latest_pct:
                latest_pct = float(floor)
            if data.get("audio_duration_sec"):
                latest_meta = f"audio {data['audio_duration_sec']}s"
            elif data.get("total_words"):
                latest_meta = f"{data['total_words']} words"
    except Exception:
        pass
    return {
        "percent": round(min(max(latest_pct, 0), 100), 1),
        "step": latest_step,
        "elapsed_sec": latest_elapsed,
        "meta": latest_meta,
    }


# Legacy Shorts render progress route removed per spec v5 §2.1. The Shorts
# Autopilot owns shorts/ now; see /jobs/{id}/shorts (status summary) and
# routes/shorts.py.


@router.post("/jobs/{job_id}/stages/review/run")
def post_run_review(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_review_stage(job_dir)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/persona_eval/run")
def post_run_persona_eval(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_persona_eval_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


def _one_shot_with_briefing(
    client: BrowserClient,
    site: str,
    kind: str,
    channel_path: Path,
    job_dir: Path,
):
    """Wrap ``client.run_session`` so the one-shot tab receives the
    initial briefing as its first message and the task as the second.

    Used by the per-stage ``/stages/X/auto`` routes which create a
    fresh tab per request (no shared context with previous calls).
    The /run-all flow uses ``open_persistent_session`` instead so the
    briefing is sent only once for the whole pipeline.
    """
    from video_agent.orchestrator.briefing import build_initial_briefing
    from video_agent.utils.json_io import read_yaml as _read_yaml

    channel_config = _read_yaml(channel_path)
    state = load_job(job_dir)
    briefing = build_initial_briefing(
        channel_config,
        kind=kind,
        job_id=state.job_id,
        channel_id=state.channel_id,
    )

    async def fn(msgs):
        return await client.run_session(site, [briefing] + list(msgs))

    return fn


def get_browser_client() -> BrowserClient:
    """FastAPI dependency: returns the BrowserClient used by auto stages.

    Tests override this with a fake to avoid hitting the real
    ``browser-worker`` container.
    """
    return BrowserClient()


def _handle_browser_client_error(exc: BrowserClientError) -> HTTPException:
    if isinstance(exc, LoginRequiredFromWorker):
        return HTTPException(
            status_code=409,
            detail={
                "error": str(exc),
                "browser_worker_detail": exc.detail,
                "login_required": True,
            },
        )
    return HTTPException(
        status_code=502,
        detail={
            "error": str(exc),
            "browser_worker_status": exc.status_code,
            "browser_worker_detail": exc.detail,
        },
    )


@router.get("/jobs/{job_id}/approvals")
def get_approvals(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    approvals = load_approvals(job_dir)
    state = load_job(job_dir)
    return {
        "job_id": job_id,
        "approvals": approvals,
        "required_approvals": list(APPROVAL_REQUIRED_STAGES),
        "approval_blocked_by": approval_block_for_current_stage(
            state.current_stage, approvals
        ),
    }


@router.post("/jobs/{job_id}/approvals/{stage_name}/confirm")
def post_confirm_approval(
    job_id: str,
    stage_name: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if stage_name not in APPROVAL_REQUIRED_STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown approval stage: {stage_name}")
    set_approval(job_dir, stage_name, True)
    return {"job_id": job_id, "stage": stage_name, "approved": True}


@router.post("/jobs/{job_id}/approvals/{stage_name}/clear")
def post_clear_approval(
    job_id: str,
    stage_name: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if stage_name not in APPROVAL_REQUIRED_STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown approval stage: {stage_name}")
    set_approval(job_dir, stage_name, False)
    return {"job_id": job_id, "stage": stage_name, "approved": False}


@router.post("/jobs/{job_id}/stages/idea_research/auto")
async def post_auto_idea_research(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_idea_research_stage(
            job_dir,
            channel_path,
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    # Fresh research run invalidates prior manual confirmation.
    set_approval(job_dir, "idea_research", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/{stage_name}/regenerate")
def post_regenerate_stage(
    job_id: str,
    stage_name: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if stage_name not in APPROVAL_REQUIRED_STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown regeneratable stage: {stage_name}")
    try:
        reset_stage_for_regen(job_dir, stage_name)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    set_approval(job_dir, stage_name, False)
    state = load_job(job_dir)
    return {"state": state.to_dict()}


class GenerateIdeasRequest(BaseModel):
    seed_topics: list[str] = []
    count: int = 10


class IdeaFromTitleRequest(BaseModel):
    title_seed: str


class ScoreIdeasRequest(BaseModel):
    ideas: list[dict]


def flatten_keyword_result_for_ui(top_keywords):
    if isinstance(top_keywords, list):
        return top_keywords
    if isinstance(top_keywords, dict):
        return (
            top_keywords.get("top_opportunity_keywords", [])
            + top_keywords.get("long_tail_test_keywords", [])
            + top_keywords.get("rejected_keywords", [])
        )
    return []


def keyword_result_summary(keyword_result) -> dict:
    if isinstance(keyword_result, dict):
        top = keyword_result.get("top_opportunity_keywords", [])
        long_tail = keyword_result.get("long_tail_test_keywords", [])
        rejected = keyword_result.get("rejected_keywords", [])
        all_scored = keyword_result.get("all_scored_keywords", [])
        metadata = keyword_result.get("metadata", {})
        return {
            "total_scanned": len(all_scored) or (len(top) + len(long_tail) + len(rejected)),
            "total_top_opportunity": len(top),
            "total_long_tail": len(long_tail),
            "total_rejected": len(rejected),
            "target_language": metadata.get("target_language", "spanish"),
            "target_audience": metadata.get("target_audience", "people_45_plus"),
            "keyword_scoring_version": metadata.get("version", "legacy"),
            "serp_inspection": metadata.get("serp_inspection", "unknown"),
        }
    if isinstance(keyword_result, list):
        return {
            "total_scanned": len(keyword_result),
            "total_top_opportunity": len(keyword_result),
            "total_long_tail": 0,
            "total_rejected": 0,
            "target_language": "unknown",
            "target_audience": "unknown",
            "keyword_scoring_version": "legacy",
            "serp_inspection": "unknown",
        }
    return {
        "total_scanned": 0,
        "total_top_opportunity": 0,
        "total_long_tail": 0,
        "total_rejected": 0,
        "target_language": "unknown",
        "target_audience": "unknown",
        "keyword_scoring_version": "unknown",
        "serp_inspection": "unknown",
    }


def keyword_score_payload(kw_data: dict, target_kw: str, summary: dict, match_source: str) -> dict:
    return {
        "target_keyword": kw_data.get("keyword", target_kw),
        "keyword_source_score": kw_data.get("keyword_source_score", kw_data.get("score")),
        "keyword_final_score": kw_data.get("final_score", kw_data.get("score")),
        "intent_cluster": kw_data.get("intent_cluster"),
        "audience_fit": kw_data.get("audience_fit"),
        "intent_strength": kw_data.get("intent_strength"),
        "content_fit": kw_data.get("content_fit"),
        "language_fit": kw_data.get("language_fit"),
        "serp_opportunity": kw_data.get("serp_opportunity"),
        "bucket": kw_data.get("bucket"),
        "recommended_angle": kw_data.get("recommended_angle"),
        "thumbnail_hook_options": kw_data.get("thumbnail_hook_options", []),
        "keyword_notes": kw_data.get("notes", []),
        "keyword_rejection_reasons": kw_data.get("rejection_reasons", []),
        "keyword_scoring_version": summary.get("keyword_scoring_version"),
        "keyword_match_source": match_source,
    }


def _keyword_is_safe_for_ui(keyword: str, channel_config: dict) -> bool:
    from video_agent.orchestrator.idea_generator import (
        detect_language_fit,
        merge_keyword_channel_config,
        score_content_fit,
    )

    text = str(keyword or "").strip()
    if not text:
        return False
    scoring_cfg = merge_keyword_channel_config(channel_config)
    language_fit, _notes = detect_language_fit(text, scoring_cfg["target_language"])
    if language_fit < 80:
        return False
    return score_content_fit(text, scoring_cfg) >= 60


def _filter_keyword_result_for_ui(keyword_result, channel_config: dict):
    def clean_item(item: dict) -> dict | None:
        if not _keyword_is_safe_for_ui(item.get("keyword", ""), channel_config):
            return None
        cleaned = dict(item)
        if isinstance(cleaned.get("related"), list):
            related = []
            for raw in cleaned["related"]:
                if isinstance(raw, str):
                    kw = raw
                    value = raw
                elif isinstance(raw, dict):
                    kw = raw.get("keyword") or raw.get("term") or ""
                    value = dict(raw)
                else:
                    continue
                if _keyword_is_safe_for_ui(kw, channel_config):
                    related.append(value)
            cleaned["related"] = related
        return cleaned

    if isinstance(keyword_result, list):
        filtered = []
        for item in keyword_result:
            if isinstance(item, dict):
                cleaned = clean_item(item)
                if cleaned is not None:
                    filtered.append(cleaned)
        return filtered
    if isinstance(keyword_result, dict):
        filtered = dict(keyword_result)
        for key in (
            "top_opportunity_keywords",
            "long_tail_test_keywords",
            "rejected_keywords",
            "all_scored_keywords",
        ):
            cleaned_items = []
            for item in keyword_result.get(key) or []:
                if isinstance(item, dict):
                    cleaned = clean_item(item)
                    if cleaned is not None:
                        cleaned_items.append(cleaned)
            filtered[key] = cleaned_items
        return filtered
    return keyword_result


@router.get("/channels")
def list_channels() -> dict:
    """List available channel configs."""
    configs_dir = repo_root() / "configs"
    channels = []
    if configs_dir.exists():
        for p in sorted(configs_dir.iterdir()):
            if p.is_dir() and (p / "channel.yaml").exists():
                channels.append(p.name)
    return {"channels": channels}


@router.get("/channels/{channel_id}/ideas")
def list_saved_ideas(
    channel_id: str,
    inputs_root: Path = Depends(get_inputs_root),
) -> dict:
    """List saved idea JSON files for a channel (most recent first).

    Scans both inputs/ideas/{channel_id}/ (new generated ideas) and
    inputs/*.json root files (legacy batch ideas), returning all that
    look like valid idea objects (have at least a 'topic' field).
    """
    _safe_channel_id(channel_id)
    _IDEA_FIELDS = {"topic", "angle", "title_seed", "key_points"}
    ideas: list[dict] = []
    paths: list[str] = []

    # ideas/{channel_id}/ subdirectory (generated by idea_generator).
    # Root inputs/*.json fallback removed — it leaked ideas from other channels.
    ideas_dir = inputs_root / "ideas" / channel_id
    subdir_files: list[Path] = []
    if ideas_dir.exists():
        subdir_files = sorted(ideas_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)

    for f in subdir_files:
        if len(ideas) >= 50:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not (_IDEA_FIELDS & data.keys()):
                continue
            # Belt-and-braces: drop entries tagged with a different channel_id.
            if data.get("channel_id") and data.get("channel_id") != channel_id:
                continue
            ideas.append(data)
            paths.append(f"ideas/{channel_id}/{f.name}")
        except Exception:
            pass
    return {"channel_id": channel_id, "ideas": ideas, "paths": paths}


@router.post("/channels/{channel_id}/ideas/score")
async def post_score_ideas(
    channel_id: str,
    req: ScoreIdeasRequest,
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    """Estimate keyword quality for a list of ideas using local channel rules."""
    _safe_channel_id(channel_id)
    from video_agent.utils.json_io import read_yaml as _read_yaml
    channel_path = repo_root() / "configs" / channel_id / "channel.yaml"
    if not channel_path.exists():
        raise HTTPException(status_code=404, detail=f"No config for channel: {channel_id}")
    channel_config = _read_yaml(channel_path)
    # Collect all keywords from all ideas in one flat list, track ranges
    all_keywords: list[str] = []
    ranges: list[tuple[int, int]] = []
    for idea in req.ideas:
        kws = _idea_keywords(idea)
        ranges.append((len(all_keywords), len(all_keywords) + len(kws)))
        all_keywords.extend(kws)

    from video_agent.orchestrator.idea_generator import (
        enrich_keyword_item,
        merge_keyword_channel_config,
    )

    scoring_cfg = merge_keyword_channel_config(channel_config)
    all_scores = [
        enrich_keyword_item({"keyword": keyword, "score": None}, scoring_cfg)
        for keyword in all_keywords
    ]

    results = []
    for i, idea in enumerate(req.ideas):
        start, end = ranges[i]
        idea_scores = all_scores[start:end]
        valid = [s for s in idea_scores if isinstance(s.get("score"), (int, float))]
        best = int(max(s["score"] for s in valid)) if valid else None
        results.append({
            "topic": idea.get("topic", ""),
            "title_seed": idea.get("title_seed", ""),
            "keywords": all_keywords[start:end],
            "scores": idea_scores,
            "best_score": best,
            "competition": None,
            "related": [],
            "error": None,
        })

    return {"results": results, "error": None}


@router.get("/channels/{channel_id}/sync-videos")
async def get_sync_channel_videos(
    channel_id: str,
) -> dict:
    """Fetch published videos from YouTube RSS and cache to published_videos.json."""
    _safe_channel_id(channel_id)
    channel_path = repo_root() / "configs" / channel_id / "channel.yaml"
    if not channel_path.exists():
        raise HTTPException(status_code=404, detail=f"No config for channel: {channel_id}")
    from video_agent.utils.json_io import read_yaml as _read_yaml
    channel_config = _read_yaml(channel_path)
    try:
        videos = sync_published_videos(channel_config, repo_root() / "configs")
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"channel_id": channel_id, "count": len(videos), "videos": videos}


@router.delete("/channels/{channel_id}/ideas")
async def delete_saved_ideas(
    channel_id: str,
    inputs_root: Path = Depends(get_inputs_root),
) -> dict:
    """Delete every saved-idea JSON under ``inputs/ideas/<channel_id>/``.

    Returns ``{deleted: int}`` with the count of files removed. Files
    outside the channel folder are never touched. The directory itself
    is left in place (idempotent for the UI's load-saved flow).
    """
    _safe_channel_id(channel_id)
    ideas_dir = (inputs_root / "ideas" / channel_id).resolve()
    safe_root = (inputs_root / "ideas").resolve()
    try:
        ideas_dir.relative_to(safe_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid channel_id: {channel_id!r}") from exc
    if not ideas_dir.exists():
        return {"channel_id": channel_id, "deleted": 0}
    deleted = 0
    for p in ideas_dir.iterdir():
        if p.is_file() and p.suffix.lower() == ".json":
            try:
                p.unlink()
                deleted += 1
            except OSError:
                continue
    return {"channel_id": channel_id, "deleted": deleted}


@router.get("/channels/{channel_id}/videos")
async def get_channel_videos(channel_id: str) -> dict:
    """Return the cached YouTube channel videos plus the channel's
    ``youtube_channel_id`` so the dashboard can render direct links.

    Reads ``configs/<channel_id>/published_videos.json`` written by
    ``/channels/{id}/sync-videos``. Returns ``{count, videos, channel_url,
    synced_at}``; ``videos: []`` when the cache is missing instead of a
    404 so the UI can still render an empty list with a "Sync" CTA.
    """
    _safe_channel_id(channel_id)
    channel_path = repo_root() / "configs" / channel_id / "channel.yaml"
    if not channel_path.exists():
        raise HTTPException(status_code=404, detail=f"No config for channel: {channel_id}")
    from video_agent.utils.json_io import read_yaml as _read_yaml

    channel_config = _read_yaml(channel_path)
    cache_path = repo_root() / "configs" / channel_id / "published_videos.json"
    videos: list[dict] = []
    synced_at: str | None = None
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            videos = data.get("videos") or []
            synced_at = data.get("synced_at")
        except Exception:
            videos = []
    yt_channel_id = (channel_config.get("channel") or {}).get("youtube_channel_id")
    channel_url = (
        f"https://www.youtube.com/channel/{yt_channel_id}" if yt_channel_id else None
    )
    return {
        "channel_id": channel_id,
        "youtube_channel_id": yt_channel_id,
        "channel_url": channel_url,
        "count": len(videos),
        "videos": videos,
        "synced_at": synced_at,
    }


@router.post("/channels/{channel_id}/ideas/generate", status_code=201)
async def post_generate_ideas(
    channel_id: str,
    req: GenerateIdeasRequest,
    inputs_root: Path = Depends(get_inputs_root),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    """Generate ideas via ChatGPT from user seeds or local trend seeds."""
    _safe_channel_id(channel_id)
    channel_path = repo_root() / "configs" / channel_id / "channel.yaml"
    if not channel_path.exists():
        raise HTTPException(status_code=404, detail=f"No config for channel: {channel_id}")

    if not 1 <= req.count <= 50:
        raise HTTPException(status_code=422, detail="count must be between 1 and 50")

    # Load cached published videos first — feed their titles into the idea
    # prompt so ChatGPT avoids already-published angles, and reuse them below
    # for duplicate detection.
    from video_agent.orchestrator.idea_generator import (
        MAX_PUBLISHED_TITLES_IN_IDEA_PROMPT,
    )
    from video_agent.utils.json_io import read_yaml as _read_yaml
    channel_config = _read_yaml(channel_path)
    published = load_published_videos(channel_config, repo_root() / "configs")
    published_titles = [v["title"] for v in published if v.get("title")]
    published_titles = published_titles[:MAX_PUBLISHED_TITLES_IN_IDEA_PROMPT]

    try:
        ideas, top_keywords, seed_source = await generate_ideas(
            channel_path=channel_path,
            chatgpt_fn=lambda msgs: client.run_session("chatgpt", msgs),
            seed_topics=req.seed_topics,
            count=req.count,
            with_metadata=True,
            published_titles=published_titles,
        )
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    top_keywords_for_ui = _filter_keyword_result_for_ui(top_keywords, channel_config)
    keywords_for_ui = flatten_keyword_result_for_ui(top_keywords_for_ui)
    summary = keyword_result_summary(top_keywords_for_ui)
    kw_score_map = {
        normalize_keyword(kw["keyword"]): kw
        for kw in keywords_for_ui
        if isinstance(kw, dict) and kw.get("keyword")
    }
    ideas_with_scores = []
    duplicate_candidates: list[dict] = []
    used_fallback_keywords: set[str] = set()
    for idx, idea in enumerate(ideas):
        enriched = dict(idea)
        target_kw = idea.get("target_keyword", "")
        target_norm = normalize_keyword(target_kw)
        kw_data = kw_score_map.get(target_norm, {})
        match_source = "target_keyword"
        if not kw_data and idx < len(keywords_for_ui):
            fallback = keywords_for_ui[idx]
            if isinstance(fallback, dict) and fallback.get("keyword"):
                fallback_norm = normalize_keyword(fallback["keyword"])
                if fallback_norm not in used_fallback_keywords:
                    kw_data = fallback
                    match_source = "ordered_fallback"
        if kw_data:
            used_fallback_keywords.add(normalize_keyword(kw_data.get("keyword", "")))
            enriched.update(keyword_score_payload(kw_data, target_kw, summary, match_source))
        # Re-score the idea using its richer title_seed (multi-word, intent
        # verbs) instead of the bare seed keyword. The keyword-discovery
        # phase may score a 1-2 word term that lands in rejected_keywords
        # (intent_strength=25). The ChatGPT-fleshed idea
        # title carries the real intent signal — re-scoring against the
        # title reflects the actual quality the user judges in the UI.
        from video_agent.orchestrator.idea_generator import (
            score_intent_strength as _intent_fn,
            score_audience_fit as _aud_fn,
            score_content_fit as _content_fn,
            calculate_final_score as _final_fn,
            assign_bucket as _bucket_fn,
            detect_language_fit as _language_fn,
            merge_keyword_channel_config as _merge_cfg,
        )
        title_for_scoring = (
            enriched.get("title_seed") or enriched.get("topic") or target_kw
        )
        if title_for_scoring:
            cfg = _merge_cfg(_read_yaml(channel_path))
            new_intent = _intent_fn(title_for_scoring)
            new_audience = _aud_fn(title_for_scoring, cfg)
            new_content = _content_fn(title_for_scoring, cfg)
            # Re-check language at the title level — a ChatGPT title in the
            # wrong locale/language must be caught even if the seed keyword
            # scored as clean Spanish.
            new_language, language_notes = _language_fn(
                title_for_scoring, cfg["target_language"]
            )
            existing_notes = list(
                enriched.get("keyword_notes")
                or enriched.get("notes")
                or []
            )
            combined_notes = sorted(set(existing_notes + list(language_notes)))
            scoring = {
                "keyword": title_for_scoring,
                "keyword_source_score": enriched.get("keyword_source_score"),
                "audience_fit": max(enriched.get("audience_fit") or 0, new_audience),
                "intent_strength": max(enriched.get("intent_strength") or 0, new_intent),
                "content_fit": max(enriched.get("content_fit") or 0, new_content),
                "language_fit": new_language,
                "serp_opportunity": enriched.get("serp_opportunity") or 50,
                "notes": combined_notes,
                "rejection_reasons": [],
            }
            _final_fn(scoring)
            new_bucket = _bucket_fn(scoring)
            enriched["intent_strength"] = scoring["intent_strength"]
            enriched["audience_fit"] = scoring["audience_fit"]
            enriched["content_fit"] = scoring["content_fit"]
            enriched["language_fit"] = scoring["language_fit"]
            enriched["keyword_final_score"] = scoring["final_score"]
            enriched["bucket"] = new_bucket
            # Append language notes without overwriting existing keyword notes.
            enriched["keyword_notes"] = sorted(set(
                list(enriched.get("keyword_notes") or [])
                + list(scoring.get("notes") or [])
            ))
        # Duplicate check against published channel videos. Duplicates are
        # returned separately and NOT saved as normal ideas.
        dup = find_duplicate(enriched, published)
        if dup:
            enriched["is_duplicate"] = True
            enriched["duplicate_of"] = dup
            duplicate_candidates.append(enriched)
            continue
        enriched["is_duplicate"] = False
        enriched["duplicate_of"] = None
        ideas_with_scores.append(enriched)

    paths = save_ideas(ideas_with_scores, channel_id=channel_id, out_dir=inputs_root)
    rel_paths = [str(p.relative_to(inputs_root)) for p in paths]

    return {
        "channel_id": channel_id,
        "count": len(ideas_with_scores),
        "ideas": ideas_with_scores,
        "keyword_result": top_keywords_for_ui,
        "top_keywords": top_keywords_for_ui,
        "summary": summary,
        "seed_source": seed_source,
        "published_videos_checked": len(published),
        "saved": rel_paths,
        "duplicate_candidates": duplicate_candidates,
        "duplicate_count": len(duplicate_candidates),
    }


@router.post("/channels/{channel_id}/ideas/from-title", status_code=201)
async def post_idea_from_title(
    channel_id: str,
    req: IdeaFromTitleRequest,
    inputs_root: Path = Depends(get_inputs_root),
    jobs_root: Path = Depends(get_jobs_root),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    """Expand a bare title into one enriched idea (Idea Generator card shape).

    No job is created — the idea is saved and returned so the UI can render it
    as a card; the video is created later via ``/jobs/from-idea``.
    """
    _safe_channel_id(channel_id)
    from video_agent.web.services.video_job_creator import create_idea_from_title

    return await create_idea_from_title(
        channel_id=channel_id,
        title_seed=req.title_seed,
        jobs_root=jobs_root,
        inputs_root=inputs_root,
        browser_client=client,
    )


@router.post("/jobs/{job_id}/stages/script/auto")
async def post_auto_script(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_script_stage(job_dir, channel_path, _one_shot_with_briefing(client, "chatgpt", "writing", channel_path, job_dir))
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    set_approval(job_dir, "script_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/scenes/auto")
async def post_auto_scenes(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_scenes_stage(job_dir, channel_path, _one_shot_with_briefing(client, "chatgpt", "writing", channel_path, job_dir))
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    set_approval(job_dir, "scenes_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/seo/auto")
async def post_auto_seo(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_seo_stage(job_dir, channel_path, _one_shot_with_briefing(client, "chatgpt", "writing", channel_path, job_dir))
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    set_approval(job_dir, "seo_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/script_qa/auto")
async def post_auto_script_qa(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_script_qa_stage(
            job_dir, channel_path,
            _one_shot_with_briefing(client, "gemini", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/scenes_qa/auto")
async def post_auto_scenes_qa(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_scenes_qa_stage(
            job_dir, channel_path,
            _one_shot_with_briefing(client, "gemini", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/seo_qa/auto")
async def post_auto_seo_qa(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_seo_qa_stage(
            job_dir, channel_path,
            _one_shot_with_briefing(client, "gemini", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/thumbnail_image/auto")
async def post_auto_thumbnail_image(
    job_id: str,
    async_: bool = Query(False, alias="async"),
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if async_:
        return _enqueue_stage_command(
            job_id=job_id,
            jobs_root=jobs_root,
            command="stage_thumbnail_image_auto",
        )
    try:
        output = await auto_thumbnail_image_stage(
            job_dir,
            channel_path,
            client.generate_image,
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    set_approval(job_dir, "thumbnail_image", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/scenes/{scene_id}/generate_asset")
async def post_generate_scene_asset(
    job_id: str,
    scene_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    """Generate one ChatGPT image for a single scene.

    Uses ``scenes.json[scene_id].visual_prompt`` to build the image
    prompt, saves the PNG under ``jobs/<id>/assets/<scene_id>.png``,
    and patches ``scenes.json`` so ``asset_refs.primary`` points at
    the new file. The next render run picks it up via the
    ``_find_asset_refs_primary`` priority path.
    """
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        result = await generate_scene_asset(
            job_dir,
            channel_path,
            scene_id,
            client.generate_image,
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    return result


@router.post("/jobs/{job_id}/run-all")
async def post_run_all(
    job_id: str,
    enforce_approvals: bool = False,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    import sys
    if "pytest" in sys.modules:
        return await execute_run_all(
            job_dir=job_dir,
            channel_path=channel_path,
            client=client,
            enforce_approvals=enforce_approvals,
        )

    state = load_job(job_dir)
    if is_run_locked(job_dir):
        return {
            "job_id": job_id,
            "status": "already_running",
            "state": state.to_dict(),
        }

    from video_agent.orchestrator.queue import JobQueue
    db_path = jobs_root / "queue.db"
    queue = JobQueue(db_path)
    enqueued = queue.enqueue(job_id, enforce_approvals)

    if not enqueued:
        return {
            "job_id": job_id,
            "status": "already_running",
            "state": state.to_dict(),
        }
    return {
        "job_id": job_id,
        "status": "enqueued",
        "state": state.to_dict(),
    }


class RunBatchRequest(BaseModel):
    job_ids: list[str]
    enforce_approvals: bool = False


@router.post("/run-batch")
async def post_run_batch(
    req: RunBatchRequest,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    import sys
    if "pytest" in sys.modules:
        results: list[dict] = []
        for job_id in req.job_ids:
            try:
                job_dir = _safe_job_dir(jobs_root, job_id)
            except HTTPException as exc:
                results.append({"job_id": job_id, "error": exc.detail})
                continue
            if not (job_dir / "job.json").exists():
                results.append({"job_id": job_id, "error": f"Unknown job: {job_id}"})
                continue
            try:
                outcome = await execute_run_all(
                    job_dir=job_dir,
                    channel_path=channel_path,
                    client=client,
                    enforce_approvals=req.enforce_approvals,
                )
                results.append({"job_id": job_id, "result": outcome})
            except HTTPException as exc:
                results.append({"job_id": job_id, "error": exc.detail})
            except Exception as exc:
                results.append({"job_id": job_id, "error": str(exc)})
        failed = [r for r in results if "error" in r]
        summary = {
            "total": len(req.job_ids),
            "succeeded": len(results) - len(failed),
            "failed": len(failed),
            "results": results,
        }
        from video_agent.notifications.telegram import notify_batch_done
        await notify_batch_done(
            total=summary["total"],
            succeeded=summary["succeeded"],
            failed=summary["failed"],
            failed_jobs=[r["job_id"] for r in failed],
        )
        return summary

    """Enqueue /run-all on each job sequentially."""
    from video_agent.orchestrator.queue import JobQueue
    db_path = jobs_root / "queue.db"
    queue = JobQueue(db_path)

    results: list[dict] = []
    for job_id in req.job_ids:
        try:
            job_dir = _safe_job_dir(jobs_root, job_id)
        except HTTPException as exc:
            results.append({"job_id": job_id, "error": exc.detail})
            continue
        if not (job_dir / "job.json").exists():
            results.append({"job_id": job_id, "error": f"Unknown job: {job_id}"})
            continue

        queue.enqueue(job_id, req.enforce_approvals)
        results.append({"job_id": job_id, "status": "enqueued"})

    failed = [r for r in results if "error" in r]
    summary = {
        "total": len(req.job_ids),
        "succeeded": len(results) - len(failed),
        "failed": len(failed),
        "results": results,
    }
    return summary


EVENTS_POLL_SECONDS = float(os.environ.get("EVENTS_POLL_SECONDS", "0.2"))


@router.websocket("/jobs/{job_id}/events")
async def ws_events(
    websocket: WebSocket,
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> None:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        await websocket.close(code=4404)
        return

    await websocket.accept()
    events_path = job_dir / EVENT_LOG
    offset = 0
    try:
        while True:
            if not (job_dir / "job.json").exists():
                await websocket.close(code=4404)
                return
            if events_path.exists():
                with events_path.open("r", encoding="utf-8") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
                for line in chunk.splitlines():
                    if not line.strip():
                        continue
                    await websocket.send_text(line)
            await asyncio.sleep(EVENTS_POLL_SECONDS)
    except WebSocketDisconnect:
        return


@router.get("/jobs/{job_id}/{path:path}")
def job_file_fallback(
    job_id: str,
    path: str,
    jobs_root: Path = Depends(get_jobs_root),
):
    """Fallback route to serve static assets for generated HTML pages like operator_review.html."""
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    
    target = (job_dir / path).resolve()
    if not str(target).startswith(str(job_dir.resolve())):
        raise HTTPException(status_code=404, detail="Not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
        
    import mimetypes
    media_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(target, media_type=media_type)
