from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import shutil
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
    auto_seo_vidiq_stage,
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
from video_agent.web.run_all_pipeline import execute_run_all, stop_request_path
from video_agent.web.timeline_helpers import (
    STAGE_ARTIFACTS,
    STAGE_ETA_SECONDS,
    effective_stage_status,
    job_has_in_progress_stage,
    resolve_inside,
    stage_duration_seconds,
)

app = FastAPI(title="video-agent-web", version="0.1.0")

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


class EnvSaveRequest(BaseModel):
    content: str


def get_jobs_root() -> Path:
    return Path(os.environ.get("JOBS_DIR", "/app/jobs"))


def _env_path() -> Path:
    return repo_root() / ".env"


def _env_example_path() -> Path:
    return repo_root() / ".env.example"


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


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "app"}


@app.get("/config/env")
def get_env_config() -> dict:
    env_path = _env_path()
    example_path = _env_example_path()
    content = ""
    exists = env_path.exists()
    if exists:
        content = env_path.read_text(encoding="utf-8")
    return {
        "path": str(env_path),
        "exists": exists,
        "example_exists": example_path.exists(),
        "content": content,
    }


@app.post("/config/env")
def save_env_config(payload: EnvSaveRequest) -> dict:
    env_path = _env_path()
    env_path.write_text(payload.content, encoding="utf-8")
    return {"ok": True, "path": str(env_path)}


@app.post("/config/env/bootstrap")
def bootstrap_env_config() -> dict:
    env_path = _env_path()
    example_path = _env_example_path()
    if env_path.exists():
        return {"ok": True, "created": False, "reason": ".env already exists"}
    if not example_path.exists():
        raise HTTPException(status_code=404, detail=".env.example not found")
    env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {"ok": True, "created": True, "path": str(env_path)}


@app.get("/jobs")
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
            stages = []
            for raw_stage in payload.get("stages", []):
                stage = dict(raw_stage)
                stage["status"] = effective_stage_status(
                    stage, current_stage, stop_requested=stop_requested
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
                    "channel_id": payload.get("channel_id"),
                    "current_stage": current_stage,
                    "created_at": payload.get("created_at"),
                    "updated_at": payload.get("updated_at"),
                    "stages_done": done,
                    "stages_total": total,
                    "in_progress": in_progress,
                    "stages": stages,
                }
            )
    return {"count": len(items), "jobs": items}


# ---------------------------------------------------------------------------
# Dashboard timeline / artifact endpoints.
# ---------------------------------------------------------------------------

@app.get("/jobs/{job_id}/timeline")
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
        scenes_path = job_dir / "scenes.json"
        if scenes_path.exists():
            sc = json.loads(scenes_path.read_text(encoding="utf-8"))
            total = int(sc.get("total_duration_sec") or 0)
            if total > 0:
                render_eta = total * 1.2  # ~1.2x realtime on typical machine
        else:
            idea_path = job_dir / IDEA_FILE
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
    for raw_stage in state.get("stages", []):
        stage = dict(raw_stage)
        stage["status"] = effective_stage_status(
            stage, current_stage, stop_requested=stop_requested
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
        items.append(
            {
                **stage,
                "inputs": inputs,
                "outputs": outputs,
                "actual_seconds": actual,
                "eta_seconds": eta if stage.get("status") != "completed" else 0,
            }
        )

    pct = (100.0 * completed_so_far / total_stages) if total_stages else 0
    approvals = load_approvals(job_dir)
    stop_requested = stop_request_path(job_dir).exists()
    return {
        "job_id": job_id,
        "channel_id": state.get("channel_id"),
        "current_stage": state.get("current_stage"),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
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


@app.get("/jobs/{job_id}/artifact")
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
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")
    # FastAPI guesses media type from the path; this is enough for our
    # mix of .json / .md / .txt / .mp4 / .jpg.
    return FileResponse(target)


@app.get("/jobs/{job_id}/logs")
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


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return _DASHBOARD_HTML


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Video Agent Dashboard</title>
<style>
  :root {
    --bg: #f6f7f9;
    --panel: #ffffff;
    --ink: #0f0f0f;
    --muted: #606060;
    --muted-2: #8c8f94;
    --line: #e5e7eb;
    --line-strong: #d6d9de;
    --rail: #0f0f0f;
    --red: #cc0000;
    --blue: #1c62b9;
    --blue-soft: #e7f1fc;
    --green: #2da14c;
    --green-soft: #e9f7ee;
    --amber: #b66a00;
    --amber-soft: #fff6e6;
    --shadow: 0 1px 2px rgba(15,15,15,.04), 0 10px 28px rgba(15,15,15,.06);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    letter-spacing: 0;
  }
  button { font: inherit; }
  .app-shell { min-height: 100vh; display: grid; grid-template-columns: 96px minmax(0, 1fr); }
  .rail {
    background: var(--rail);
    color: #fff;
    padding: 28px 18px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 28px;
  }
  .logo {
    width: 52px;
    height: 36px;
    border-radius: 8px;
    background: var(--red);
    position: relative;
    box-shadow: inset 0 -8px 14px rgba(0,0,0,.16);
  }
  .logo:after {
    content: "";
    position: absolute;
    left: 21px;
    top: 10px;
    border-left: 14px solid #fff;
    border-top: 8px solid transparent;
    border-bottom: 8px solid transparent;
  }
  .rail-nav { width: 100%; display: grid; gap: 12px; margin-top: 10px; }
  .rail-pill {
    height: 34px;
    border-radius: 8px;
    display: grid;
    place-items: center;
    color: #a7a7a7;
    font-size: 12px;
    border: 1px solid #262626;
    background: transparent;
    transition: all 140ms ease;
  }
  .rail-pill:hover { color: #e6e6e6; border-color: #3a3a3a; background: #1b1b1b; }
  .rail-pill.active {
    color: #fff;
    background: linear-gradient(180deg, #2f2f2f, #232323);
    border-color: #4a4a4a;
    box-shadow: inset 0 0 0 1px #5a5a5a;
    font-weight: 700;
  }
  .rail-pill:focus-visible {
    outline: 2px solid #7db7ff;
    outline-offset: 2px;
  }
  .workspace { min-width: 0; display: grid; grid-template-rows: 76px minmax(0, 1fr); }
  .topbar {
    background: var(--panel);
    border-bottom: 1px solid var(--line);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 34px;
    gap: 20px;
  }
  .brand h1 { margin: 0; font-size: 16px; line-height: 1.2; font-weight: 750; }
  .brand p { margin: 5px 0 0; font-size: 12px; color: var(--muted); }
  .top-meta { display: flex; align-items: center; gap: 14px; font-size: 12px; color: var(--muted); }
  .ws-dot { width: 8px; height: 8px; border-radius: 999px; display: inline-block; background: #b8bcc2; }
  .ws-dot.live { background: var(--green); box-shadow: 0 0 0 4px rgba(45,161,76,.12); }
  .ws-dot.off { background: #b8bcc2; }
  .refresh-btn {
    border: 1px solid var(--line-strong);
    background: #fff;
    color: var(--ink);
    border-radius: 8px;
    height: 34px;
    padding: 0 12px;
    cursor: pointer;
  }
  .refresh-btn:hover { border-color: #b9bec6; background: #fafafa; }
  .page {
    min-height: 0;
    overflow: auto;
    padding: 30px 34px 40px;
  }
  .page-head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 24px;
    margin-bottom: 22px;
  }
  .page-title h2 { margin: 0; font-size: 25px; font-weight: 780; }
  .page-title p { margin: 7px 0 0; color: var(--muted); font-size: 13px; }
  .primary-action {
    border: 0;
    background: var(--red);
    color: #fff;
    border-radius: 8px;
    height: 36px;
    padding: 0 16px;
    font-weight: 650;
    box-shadow: 0 8px 18px rgba(204,0,0,.18);
  }
  /* ---- New Job Modal ---- */
  .modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,.45); z-index:1000; display:flex; align-items:center; justify-content:center; }
  .modal-overlay.hidden { display:none; }
  .modal { background:#fff; border-radius:12px; padding:28px 30px; width:500px; max-width:95vw; box-shadow:0 24px 64px rgba(0,0,0,.28); }
  .modal-title { font-size:16px; font-weight:750; margin:0 0 20px; }
  .modal-field { margin-bottom:14px; }
  .modal-field label { display:block; font-size:12px; font-weight:600; color:var(--muted); margin-bottom:5px; text-transform:uppercase; letter-spacing:.04em; }
  .modal-field input, .modal-field select, .modal-field textarea { width:100%; padding:9px 12px; border:1px solid var(--line); border-radius:7px; font-size:13px; box-sizing:border-box; font-family:inherit; }
  .modal-field textarea { font-family:ui-monospace,monospace; min-height:120px; resize:vertical; font-size:11px; }
  .modal-field select { background:#fff; }
  .modal-radio-row { display:flex; gap:16px; }
  .modal-radio-row label { display:flex; align-items:center; gap:6px; font-size:13px; font-weight:500; text-transform:none; letter-spacing:0; color:#333; cursor:pointer; }
  .modal-actions { display:flex; gap:10px; justify-content:flex-end; margin-top:22px; }
  .modal-cancel { padding:9px 18px; border:1px solid var(--line); border-radius:7px; background:#fff; font-size:13px; cursor:pointer; }
  .modal-submit { padding:9px 20px; border:0; border-radius:7px; background:var(--red); color:#fff; font-size:13px; font-weight:700; cursor:pointer; }
  .modal-submit:disabled { opacity:.5; cursor:default; }
  /* ---- end New Job Modal ---- */
  /* ---- Idea Generator ---- */
  .ideas-section { background:var(--panel); border:1px solid var(--line); border-radius:8px; box-shadow:var(--shadow); margin-bottom:18px; overflow:hidden; }
  .ideas-section .panel-head { cursor:pointer; user-select:none; display:flex; justify-content:space-between; align-items:center; padding:14px 18px; }
  .ideas-section .panel-head:hover { background:var(--hover,#f7f8fa); }
  .ideas-body { padding:14px 18px 18px; border-top:1px solid var(--line); }
  .gen-form { display:flex; gap:10px; align-items:flex-start; flex-wrap:wrap; margin-bottom:14px; }
  .gen-form label { font-size:12px; color:var(--muted); font-weight:600; white-space:nowrap; align-self:center; }
  .gen-form input[type=number] { width:72px; padding:7px 10px; border:1px solid var(--line); border-radius:6px; font-size:13px; }
  .gen-form select { padding:7px 10px; border:1px solid var(--line); border-radius:6px; font-size:13px; background:#fff; }
  .gen-form textarea { flex:1; min-width:200px; padding:7px 10px; border:1px solid var(--line); border-radius:6px; font-size:12px; font-family:inherit; resize:vertical; min-height:40px; }
  .gen-form .gen-actions { display:flex; gap:8px; flex-shrink:0; }
  .ideas-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:12px; margin-top:4px; }
  .idea-card { border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; display:flex; flex-direction:column; gap:6px; }
  .idea-card-title { font-size:14px; font-weight:700; color:#1a1a1a; line-height:1.4; }
  .idea-card-angle { font-size:12px; color:var(--muted); line-height:1.5; }
  .idea-card-meta { display:flex; gap:8px; flex-wrap:wrap; }
  .idea-card-dur { font-size:11px; background:#f3f4f6; border-radius:4px; padding:2px 8px; color:#374151; }
  .idea-card-points { font-size:12px; color:#374151; padding-left:16px; margin:0; }
  .idea-card-points li { margin-bottom:2px; line-height:1.4; }
  .idea-card-actions { display:flex; gap:8px; margin-top:4px; }
  .ideas-empty { font-size:13px; color:var(--muted); padding:16px 0; text-align:center; }
  .spinner-inline { display:inline-block; width:14px; height:14px; border:2px solid #d1d5db; border-top-color:#374151; border-radius:50%; animation:spin .7s linear infinite; vertical-align:middle; margin-right:4px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .idea-duplicate-badge { font-size:11px; font-weight:600; padding:2px 8px; border-radius:10px; background:#fef2f2; color:#b91c1c; border:1px solid #fca5a5; margin-bottom:4px; display:inline-block; }
  .idea-score-row { display:flex; align-items:center; gap:8px; margin-top:4px; }
  .idea-score-badge { font-size:12px; font-weight:700; padding:2px 10px; border-radius:12px; }
  .idea-score-badge.high { background:#d1fae5; color:#065f46; }
  .idea-score-badge.mid { background:#fef3c7; color:#92400e; }
  .idea-score-badge.low { background:#fee2e2; color:#991b1b; }
  .idea-score-badge.none { background:#f3f4f6; color:#6b7280; }
  .idea-comp-badge { font-size:11px; padding:2px 8px; border-radius:10px; background:#f3f4f6; color:#374151; }
  .idea-related { font-size:11px; color:var(--muted); margin-top:3px; line-height:1.6; }
  .idea-score-bar { flex:1; height:6px; border-radius:3px; background:#e5e7eb; overflow:hidden; }
  .idea-score-bar-fill { height:100%; border-radius:3px; transition:width .4s; }
  /* ---- end Idea Generator ---- */
  .kpis {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin-bottom: 22px;
  }
  .kpi {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 17px 18px;
    min-height: 92px;
    box-shadow: var(--shadow);
  }
  .kpi-label { font-size: 11px; color: var(--muted); font-weight: 650; text-transform: uppercase; letter-spacing: .04em; }
  .kpi-value { margin-top: 9px; font-size: 25px; font-weight: 780; }
  .kpi-sub { margin-top: 5px; color: var(--muted-2); font-size: 12px; }
  .content-grid { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 12px; align-items: start; }
  .panel {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    box-shadow: var(--shadow);
  }
  .jobs-panel { min-height: 560px; overflow: hidden; }
  .panel-head {
    padding: 18px 18px 12px;
    border-bottom: 1px solid var(--line);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .panel-title { font-size: 13px; font-weight: 750; }
  .panel-count { font-size: 11px; color: var(--muted); }
  #jobs-list { padding: 12px; display: grid; gap: 10px; max-height: 660px; overflow: auto; }
  .vnc-box {
    border-top: 1px solid var(--line);
    background: #fafafa;
    padding: 10px 12px 12px;
  }
  .vnc-title { font-size: 11px; color: var(--muted); font-weight: 700; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 8px; }
  .vnc-note { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .vnc-controls { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
  .vnc-mode-btn {
    border: 1px solid var(--line-strong);
    background: #fff;
    color: #374151;
    border-radius: 6px;
    height: 26px;
    padding: 0 10px;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
  }
  .vnc-mode-btn.active { background: var(--blue-soft); border-color: #b9d7f7; color: var(--blue); }
  .vnc-frame {
    width: 100%;
    aspect-ratio: 16 / 9;
    max-height: calc(100vh - 290px);
    border: 1px solid var(--line);
    border-radius: 8px;
    background: #111;
    display: block;
    margin: 10px 0;
  }
  .vnc-tab-box {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 16px;
    margin-top: 14px;
    box-shadow: var(--shadow);
  }
  .config-wrap { display: none; }
  .config-wrap.active { display: block; }
  .config-panel {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 18px;
    box-shadow: var(--shadow);
  }
  .config-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
  .cfg-btn {
    border: 1px solid var(--line-strong);
    background: #fff;
    color: #1f2937;
    border-radius: 8px;
    height: 34px;
    padding: 0 12px;
    font-size: 12px;
    font-weight: 650;
    cursor: pointer;
    transition: all 140ms ease;
  }
  .cfg-btn:hover { border-color: #b8bec8; background: #f8fafc; }
  .cfg-btn:disabled { opacity: .55; cursor: not-allowed; }
  .cfg-btn.primary {
    background: var(--blue);
    border-color: #1b4f95;
    color: #fff;
    box-shadow: 0 8px 18px rgba(28,98,185,.18);
  }
  .cfg-btn.primary:hover { background: #19589f; border-color: #174d8b; }
  .cfg-btn.warn {
    border-color: #e6ceb0;
    background: #fff7ed;
    color: #9a4f00;
  }
  .cfg-btn.warn:hover { background: #ffedd5; border-color: #ddb78d; }
  .config-textarea {
    width: 100%;
    min-height: 420px;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 12px;
    font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    background: #fff;
    color: #111;
  }
  .job-card {
    border: 1px solid var(--line);
    border-radius: 8px;
    background: #fff;
    padding: 13px 14px;
    cursor: pointer;
    transition: border-color 150ms, background 150ms, transform 150ms;
  }
  .job-card:hover { border-color: #c9cdd3; transform: translateY(-1px); }
  .job-card.active { border-color: var(--red); background: #fffbfb; box-shadow: inset 3px 0 0 var(--red); }
  .job-row { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
  .job-main { min-width: 0; flex: 1 1 auto; }
  .job-tools { display: inline-flex; align-items: center; gap: 8px; }
  .job-stop {
    height: 24px;
    min-width: 46px;
    border: 1px solid #f2c2c2;
    border-radius: 6px;
    background: #fff7f7;
    color: var(--red);
    cursor: pointer;
    font-size: 11px;
    line-height: 1;
    padding: 0 8px;
    font-weight: 700;
  }
  .job-stop:disabled { opacity: .5; cursor: not-allowed; }
  .job-del {
    height: 24px;
    min-width: 24px;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: #fff;
    color: var(--muted-2);
    cursor: pointer;
    font-size: 14px;
    line-height: 1;
    padding: 0 6px;
  }
  .job-del:hover { border-color: #e0b7b7; color: var(--red); background: #fff6f6; }
  .job-del:disabled { opacity: .5; cursor: not-allowed; color: var(--muted-2); }
  .job-id { font-size: 12px; font-weight: 750; line-height: 1.35; word-break: break-word; }
  .job-count { color: var(--muted); font-size: 12px; white-space: nowrap; }
  .job-stage { margin-top: 8px; color: var(--muted); font-size: 12px; display: flex; align-items: center; gap: 7px; }
  .state-dot { width: 7px; height: 7px; border-radius: 999px; background: #b8bcc2; flex: 0 0 auto; }
  .state-dot.completed { background: var(--green); }
  .state-dot.in_progress { background: var(--blue); }
  .state-dot.failed { background: var(--red); }
  .job-meta { margin-top: 10px; color: var(--muted-2); font-size: 11px; display: flex; justify-content: space-between; gap: 10px; }
  .job-bar { height: 5px; background: #edf0f3; border-radius: 999px; overflow: hidden; margin-top: 11px; }
  .job-bar div { height: 100%; background: var(--blue); border-radius: inherit; transition: width 200ms; }
  .job-bar.completed div { background: var(--green); }
  .job-bar.failed div { background: var(--red); }
  .detail-panel { min-height: 560px; padding: 24px; }
  .empty {
    min-height: 520px;
    display: grid;
    place-items: center;
    text-align: center;
    color: var(--muted);
    border: 1px dashed var(--line-strong);
    border-radius: 8px;
    background: #fafafa;
  }
  .summary-card { border: 1px solid var(--line); border-radius: 8px; padding: 18px; background: #fff; }
  .sum-row { display: flex; justify-content: space-between; gap: 24px; align-items: start; }
  .sum-id { font-size: 20px; font-weight: 780; line-height: 1.2; word-break: break-word; }
  .sum-meta { margin-top: 9px; color: var(--muted); font-size: 12px; display: flex; flex-wrap: wrap; gap: 9px; align-items: center; }
  .sum-actions { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
  .channel-pill { display: inline-flex; align-items: center; gap: 7px; }
  .avatar {
    width: 22px;
    height: 22px;
    border-radius: 999px;
    background: #111;
    color: #fff;
    display: inline-grid;
    place-items: center;
    font-size: 10px;
    font-weight: 750;
  }
  .sum-pct { font-size: 34px; font-weight: 800; text-align: right; line-height: 1; }
  .pct-unit { font-size: 17px; color: var(--muted); margin-left: 2px; }
  .sum-eta { margin-top: 8px; text-align: right; color: var(--muted); font-size: 12px; }
  .sum-bar { margin-top: 18px; height: 9px; border-radius: 999px; background: #edf0f3; overflow: hidden; }
  .sum-bar div { height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--blue), #3ea6ff); transition: width 240ms; }
  .sum-bar.completed div { background: var(--green); }
  .sum-bar.failed div { background: var(--red); }
  .stage-strip { margin-top: 16px; display: grid; gap: 6px; }
  .stage-seg { height: 7px; background: #edf0f3; border-radius: 999px; position: relative; }
  .stage-seg.completed { background: var(--green); }
  .stage-seg.in_progress { background: #3ea6ff; }
  .stage-seg.failed { background: var(--red); }
  .stage-seg .tip {
    display: none;
    position: absolute;
    bottom: 14px;
    left: 50%;
    transform: translateX(-50%);
    white-space: nowrap;
    background: #111;
    color: #fff;
    font-size: 11px;
    padding: 5px 7px;
    border-radius: 6px;
    z-index: 4;
  }
  .stage-seg:hover .tip { display: block; }
  .sum-stagerow { margin-top: 13px; color: var(--muted); font-size: 12px; display: flex; justify-content: space-between; gap: 14px; }
  .detail-tabs { margin-top: 18px; display: flex; gap: 8px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
  .detail-tab-btn {
    border: 1px solid var(--line);
    border-radius: 8px;
    background: #fff;
    color: var(--muted);
    font-size: 12px;
    font-weight: 700;
    height: 30px;
    padding: 0 12px;
    cursor: pointer;
  }
  .detail-tab-btn.active {
    border-color: #b9d7f7;
    color: var(--blue);
    background: var(--blue-soft);
  }
  .detail-tab-content { margin-top: 12px; }
  .detail-tab-content.hidden { display: none; }
  .section-title {
    margin: 22px 0 10px;
    display: flex;
    justify-content: space-between;
    color: var(--muted);
    font-size: 12px;
    font-weight: 650;
  }
  .timeline { display: grid; gap: 10px; }
  .step { border: 1px solid var(--line); border-radius: 8px; background: #fff; overflow: hidden; }
  .step.completed { border-color: #d6eadb; }
  .step.in_progress { border-color: #b9d7f7; background: #fbfdff; }
  .step.failed { border-color: #f0c9c9; background: #fffafa; }
  .step-head {
    min-height: 58px;
    display: grid;
    grid-template-columns: 28px minmax(0, 1fr) auto;
    gap: 13px;
    align-items: center;
    padding: 12px 14px;
    cursor: pointer;
  }
  .step-head:hover { background: #fafafa; }
  .step-num {
    width: 28px;
    height: 28px;
    border-radius: 999px;
    display: grid;
    place-items: center;
    background: #f1f2f4;
    color: var(--muted);
    font-size: 12px;
    font-weight: 750;
  }
  .step.completed .step-num { background: var(--green); color: #fff; }
  .step.in_progress .step-num { background: var(--blue); color: #fff; }
  .step.failed .step-num { background: var(--red); color: #fff; }
  .step-label .name { display: block; font-size: 13px; font-weight: 750; }
  .step-label .code { display: block; margin-top: 4px; font-size: 11px; color: var(--muted-2); }
  .step-meta { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 12px; }
  .pill { border-radius: 999px; padding: 4px 9px; background: #f1f2f4; color: var(--muted); font-size: 11px; font-weight: 700; }
  .pill.completed { background: var(--green-soft); color: var(--green); }
  .pill.in_progress { background: var(--blue-soft); color: var(--blue); }
  .pill.failed { background: #ffe8e8; color: var(--red); }
  .run-btn { border: none; border-radius: 6px; background: var(--blue); color: #fff; font-size: 11px; font-weight: 700; padding: 4px 10px; cursor: pointer; transition: opacity 150ms; }
  .run-btn:hover { opacity: 0.85; }
  .run-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .caret { color: var(--muted-2); font-size: 20px; line-height: 1; transition: transform 150ms; }
  .step.open .caret { transform: rotate(90deg); }
  .step-body { display: none; padding: 0 14px 14px 55px; }
  .step.open .step-body { display: block; }
  .io-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .io-box { border: 1px solid var(--line); border-radius: 8px; background: #fafafa; padding: 10px; min-height: 68px; }
  .io-title { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .05em; font-weight: 750; margin-bottom: 7px; }
  .stage-actions { margin: 10px 0; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .action-btn {
    border: 1px solid var(--line-strong);
    background: #fff;
    border-radius: 6px;
    height: 28px;
    padding: 0 10px;
    font-size: 12px;
    cursor: pointer;
  }
  .action-btn.primary { border-color: #b6d8c2; background: #f0fbf4; color: #1d6b3b; }
  .action-btn.warn { border-color: #e9cfb0; background: #fff9f0; color: #8a5a12; }
  .gate-note { font-size: 12px; color: #8a5a12; background: #fff7eb; border: 1px solid #f1d6af; border-radius: 6px; padding: 6px 8px; }
  .insight { margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 14px; }
  .insight-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .insight-kv { font-size: 12px; color: #222; }
  .insight-kv b { color: var(--muted); font-weight: 650; margin-right: 6px; }
  .insight-list { margin: 8px 0 0; padding-left: 18px; font-size: 12px; color: #333; }
  .insight-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
  .insight-table th, .insight-table td { border-bottom: 1px solid var(--line); text-align: left; padding: 6px 4px; vertical-align: top; }
  .insight-table th { color: var(--muted); font-weight: 650; }
  .insight-section { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px; color: var(--muted); margin: 14px 0 6px; }
  .vb { display:inline-flex; align-items:center; gap:6px; padding:5px 14px; border-radius:20px; font-weight:700; font-size:13px; margin-bottom:10px; }
  .vb-pass { background:#d1fae5; color:#065f46; }
  .vb-fail,.vb-block { background:#fee2e2; color:#991b1b; }
  .vb-skip { background:#f3f4f6; color:#6b7280; }
  .score-row { display:flex; align-items:center; gap:8px; margin:5px 0; font-size:12px; }
  .score-kw { min-width:140px; max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#374151; }
  .score-bar-outer { flex:1; height:8px; border-radius:4px; background:#e5e7eb; overflow:hidden; }
  .score-bar-inner { height:100%; border-radius:4px; transition:width .4s; }
  .score-num { font-weight:700; min-width:28px; text-align:right; font-size:12px; }
  .score-meta { font-size:11px; color:var(--muted-2); min-width:80px; }
  .narration-box { background:#f9fafb; border:1px solid var(--line); border-radius:6px; padding:12px 14px; font-size:13px; line-height:1.7; margin-top:8px; max-height:220px; overflow-y:auto; white-space:pre-wrap; color:#374151; }
  .hook-box { background:#fffbeb; border:1px solid #fcd34d; border-radius:6px; padding:12px 14px; font-size:16px; font-weight:700; color:#92400e; margin-bottom:10px; line-height:1.4; }
  .cta-box { background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; padding:10px 14px; font-size:13px; font-weight:600; color:#1e40af; margin-top:8px; }
  .tag-chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
  .tag-chip { padding:4px 12px; border-radius:14px; background:#f3f4f6; border:1px solid #e5e7eb; font-size:12px; color:#374151; }
  .scene-card { border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin-top:10px; }
  .scene-card-head { font-size:11px; font-weight:700; color:var(--muted); display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; text-transform:uppercase; letter-spacing:.5px; }
  .scene-dur-badge { background:#f3f4f6; border-radius:8px; padding:2px 8px; font-size:11px; font-weight:700; color:#374151; }
  .scene-narration { font-size:13px; color:#374151; line-height:1.6; }
  .scene-prompt { font-size:11px; color:var(--muted-2); margin-top:6px; font-style:italic; border-top:1px solid var(--line); padding-top:6px; }
  .scene-text { font-size:12px; color:#6b7280; font-weight:600; margin-top:4px; }
  .variant-card { border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin-top:10px; position:relative; }
  .variant-card.winner { border-color:#d97706; background:#fffbeb; }
  .variant-score { position:absolute; top:10px; right:12px; font-size:11px; font-weight:700; background:#f59e0b; color:#fff; padding:2px 9px; border-radius:10px; }
  .variant-winner-badge { position:absolute; top:10px; right:70px; font-size:11px; font-weight:700; background:#065f46; color:#fff; padding:2px 9px; border-radius:10px; }
  .variant-title { font-size:15px; font-weight:700; margin-bottom:6px; color:#111; padding-right:80px; }
  .variant-hook { font-size:13px; color:#d97706; font-weight:600; }
  .description-box { background:#f9fafb; border:1px solid var(--line); border-radius:6px; padding:12px 14px; font-size:13px; line-height:1.7; margin-top:8px; max-height:160px; overflow-y:auto; color:#374151; }
  .qa-scores { display:grid; grid-template-columns:repeat(2,1fr); gap:8px; margin-top:10px; }
  .qa-score-item { font-size:12px; }
  .qa-score-label { color:var(--muted); font-weight:650; margin-bottom:3px; }
  .thumb-preview { width:100%; max-height:200px; object-fit:cover; border-radius:6px; margin-top:8px; }
  .section-item { border-bottom:1px solid var(--line); padding:8px 0; }
  .section-item:last-child { border-bottom:none; }
  .section-title { font-size:12px; font-weight:700; color:#374151; }
  .section-text { font-size:12px; color:#6b7280; margin-top:3px; line-height:1.5; }
  .file-row {
    height: 30px;
    border-radius: 6px;
    display: grid;
    grid-template-columns: 18px minmax(0, 1fr) auto;
    gap: 8px;
    align-items: center;
    color: var(--blue);
    font-size: 12px;
    cursor: pointer;
    padding: 0 7px;
  }
  .file-row:hover { background: #edf4ff; }
  .file-row.missing { color: var(--muted-2); cursor: default; }
  .file-row.missing:hover { background: transparent; }
  .file-icon { width: 16px; height: 16px; color: currentColor; }
  .file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .file-size { color: var(--muted-2); font-size: 11px; }
  .preview { margin-top: 12px; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: #fff; }
  .preview-head { min-height: 36px; display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 9px 11px; border-bottom: 1px solid var(--line); color: var(--muted); font-size: 12px; }
  .preview-close { width: 26px; height: 26px; border-radius: 6px; border: 1px solid var(--line); background: #fff; cursor: pointer; color: var(--muted); }
  .preview pre, pre.dump {
    margin: 0;
    padding: 13px;
    max-height: 360px;
    overflow: auto;
    background: #0f1115;
    color: #d7dce2;
    font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .preview img, .preview video { width: 100%; display: block; background: #000; }
  .final { margin-top: 22px; border: 1px solid #cfe6d5; border-radius: 8px; background: #fbfffc; padding: 18px; }
  .final-title { display: flex; align-items: center; gap: 10px; font-weight: 780; margin-bottom: 14px; }
  .badge { font-size: 11px; padding: 3px 8px; border-radius: 999px; background: var(--green-soft); color: var(--green); }
  .final video { width: 100%; max-height: 500px; background: #000; border-radius: 8px; display: block; }
  .final-cols { margin-top: 16px; display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 16px; align-items: start; }
  .thumb-img { width: 100%; border-radius: 8px; border: 1px solid var(--line); background: #111; display: block; }
  .copy-row { border: 1px solid var(--line); border-radius: 8px; background: #fff; margin-bottom: 10px; overflow: hidden; }
  .crh { min-height: 37px; padding: 8px 10px; display: flex; align-items: center; justify-content: space-between; gap: 10px; border-bottom: 1px solid var(--line); }
  .cr-label { font-size: 12px; font-weight: 750; }
  .count { color: var(--muted-2); font-weight: 500; }
  .copy-btn {
    border: 1px solid var(--line-strong);
    background: #fff;
    border-radius: 6px;
    height: 26px;
    padding: 0 9px;
    cursor: pointer;
    color: var(--muted);
    font-size: 11px;
  }
  .copy-btn.copied { border-color: #cfe6d5; color: var(--green); background: var(--green-soft); }
  .copy-content { padding: 10px; color: #222; font-size: 13px; line-height: 1.5; white-space: pre-wrap; }
  .copy-content.scroll { max-height: 190px; overflow: auto; }
  .tag-chips { padding: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
  .tag-chip { border-radius: 999px; background: #f1f6fd; color: var(--blue); padding: 4px 8px; font-size: 12px; }
  .download-link { display: inline-flex; align-items: center; gap: 8px; color: var(--blue); font-size: 12px; text-decoration: none; margin-top: 3px; font-weight: 650; }
  .download-link:hover { text-decoration: underline; }
  .ab-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 16px 0; }
  .ab-card { border: 2px solid var(--line); border-radius: 10px; padding: 12px; position: relative; background: #fff; }
  .ab-winner { border-color: #F2C94C; box-shadow: 0 0 0 3px rgba(242,201,76,0.18); }
  .ab-badge { display: inline-block; font-size: 11px; font-weight: 750; padding: 3px 8px; border-radius: 4px; background: var(--border, #e5e5e5); color: var(--muted); margin-bottom: 8px; }
  .ab-badge.winner { background: #F2C94C; color: #1a1a1a; }
  .ab-card .thumb-img { width: 100%; border-radius: 6px; display: block; margin-bottom: 8px; aspect-ratio: 16/9; object-fit: cover; background: #111; }
  .ab-title { font-size: 12px; font-weight: 700; line-height: 1.4; margin-bottom: 4px; color: #222; }
  .ab-hook { font-size: 11px; color: var(--muted); font-family: monospace; margin-bottom: 8px; letter-spacing: 0.03em; }
  .events { margin-top: 22px; border: 1px solid var(--line); border-radius: 8px; background: #fff; overflow: hidden; }
  .events-head { min-height: 42px; padding: 0 14px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); cursor: pointer; }
  .events-head h3 { margin: 0; font-size: 13px; }
  .events-count { color: var(--muted); font-size: 12px; }
  .events-body { display: none; max-height: 240px; overflow: auto; padding: 8px 12px; }
  .events.open .events-body { display: block; }
  .events.open .caret { transform: rotate(90deg); }
  .ev-row { min-height: 28px; display: grid; grid-template-columns: 72px 160px minmax(0, 1fr); gap: 10px; align-items: center; font-size: 12px; border-bottom: 1px dotted var(--line); }
  .ev-row:last-child { border-bottom: 0; }
  .ts { color: var(--muted-2); font-variant-numeric: tabular-nums; }
  .ev-kind { font-weight: 750; color: var(--blue); }
  .ev-kind.JOB_COMPLETED { color: var(--green); }
  .ev-kind.STAGE_FAILED, .ev-kind.STAGE_NEEDS_REWORK { color: var(--red); }
  .ev-stage { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .logs-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 10px; }
  .logs-sub { font-size: 12px; color: var(--muted); }
  .log-card { border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 12px; margin-bottom: 10px; }
  .log-title { font-size: 12px; font-weight: 750; color: var(--muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: .04em; }
  .log-pre {
    margin: 0;
    padding: 10px;
    border: 1px solid var(--line);
    border-radius: 6px;
    max-height: 280px;
    overflow: auto;
    background: #0f1115;
    color: #d7dce2;
    font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .toast {
    position: fixed;
    right: 24px;
    bottom: 24px;
    background: #111;
    color: #fff;
    border-radius: 8px;
    padding: 10px 13px;
    font-size: 12px;
    opacity: 0;
    transform: translateY(8px);
    pointer-events: none;
    transition: opacity 160ms, transform 160ms;
  }
  .toast.show { opacity: 1; transform: translateY(0); }
  @media (max-width: 980px) {
    .app-shell { grid-template-columns: 1fr; }
    .rail { display: none; }
    .workspace { grid-template-rows: auto minmax(0, 1fr); }
    .topbar { padding: 18px; align-items: flex-start; }
    .page { padding: 18px; }
    .page-head { align-items: flex-start; flex-direction: column; }
    .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .content-grid { grid-template-columns: 1fr; }
    .final-cols, .io-grid { grid-template-columns: 1fr; }
    .step-body { padding-left: 14px; }
  }
  @media (max-width: 620px) {
    .kpis { grid-template-columns: 1fr; }
    .topbar { flex-direction: column; gap: 12px; }
    .sum-row, .sum-stagerow { flex-direction: column; }
    .sum-pct, .sum-eta { text-align: left; }
    .step-head { grid-template-columns: 28px minmax(0, 1fr); }
    .step-meta { grid-column: 2; }
  }
</style>
</head>
<body>
<div class="app-shell">
  <aside class="rail">
    <div class="logo" aria-label="Video Agent"></div>
    <nav class="rail-nav" aria-label="Dashboard sections">
      <button type="button" class="rail-pill active" id="rail-jobs" onclick="switchPage('jobs')">Jobs</button>
      <button type="button" class="rail-pill" id="rail-config" onclick="switchPage('config')">Config</button>
      <div class="rail-pill">AI</div>
      <div class="rail-pill">Files</div>
      <button type="button" class="rail-pill" id="rail-logs" onclick="openLogsTabFromRail()">Logs</button>
    </nav>
  </aside>
  <div class="workspace">
    <header class="topbar">
      <div class="brand">
        <h1>Video Agent</h1>
        <p>Vida Plena 45+ production dashboard</p>
      </div>
      <div class="top-meta">
        <span><span class="ws-dot off" id="ws-dot"></span><span id="ws-label">disconnected</span></span>
        <span id="job-count">0</span>
        <button class="refresh-btn" id="refresh-btn" type="button">Refresh</button>
      </div>
    </header>
    <main class="page">
      <div class="page-head">
        <div class="page-title">
          <h2>Production Jobs</h2>
          <p>Track generation, QA, render artifacts, and final YouTube metadata.</p>
        </div>
        <div style="display:flex;gap:10px;align-items:center">
          <button class="primary-action" type="button" onclick="openNewJobModal()">＋ New Job</button>
          <button style="padding:0 14px;height:36px;border:1px solid var(--line);border-radius:8px;background:#fff;font-size:13px;cursor:pointer" type="button" onclick="fetchJobs()">Refresh</button>
        </div>
      </div>
      <!-- New Job Modal -->
      <div class="modal-overlay hidden" id="new-job-overlay" onclick="if(event.target===this)closeNewJobModal()">
        <div class="modal">
          <div class="modal-title">＋ New Job</div>
          <div class="modal-field">
            <label>Job ID</label>
            <input type="text" id="nj-job-id" placeholder="auto-generated">
          </div>
          <div class="modal-field">
            <label>Channel</label>
            <select id="nj-channel" onchange="loadModalIdeas()">
              <option value="vida-plena-45">vida-plena-45</option>
            </select>
          </div>
          <div class="modal-field">
            <label>Idea source</label>
            <div class="modal-radio-row">
              <label><input type="radio" name="nj-src" value="saved" checked onchange="njSourceChange()"> Saved idea</label>
              <label><input type="radio" name="nj-src" value="paste" onchange="njSourceChange()"> Paste JSON</label>
              <label><input type="radio" name="nj-src" value="none" onchange="njSourceChange()"> No idea yet</label>
            </div>
          </div>
          <div class="modal-field" id="nj-saved-wrap">
            <label>Select idea</label>
            <select id="nj-idea-select" style="max-width:100%">
              <option value="">Loading…</option>
            </select>
          </div>
          <div class="modal-field hidden" id="nj-paste-wrap">
            <label>Idea JSON</label>
            <textarea id="nj-idea-json" placeholder='{"topic":"...","angle":"...","title_seed":"...","key_points":[],"target_duration_sec":1500}'></textarea>
          </div>
          <div id="nj-error" style="font-size:12px;color:var(--red);margin-top:-6px;display:none"></div>
          <div class="modal-actions">
            <button class="modal-cancel" onclick="closeNewJobModal()">Cancel</button>
            <button class="modal-submit" id="nj-submit" onclick="submitNewJob()">Create Job</button>
          </div>
        </div>
      </div>
      <div id="jobs-page">
      <section class="kpis" id="kpis"></section>
      <section class="ideas-section" id="ideas-section">
        <div class="panel-head" onclick="toggleIdeasPanel()">
          <div class="panel-title">💡 Idea Generator</div>
          <span style="font-size:12px;color:var(--muted)" id="ideas-caret">› expand</span>
        </div>
        <div class="ideas-body" id="ideas-body" style="display:none">
          <div class="gen-form">
            <label>Channel</label>
            <select id="idea-channel" onchange="loadSavedIdeas()">
              <option value="vida-plena-45">vida-plena-45</option>
            </select>
            <label>Count</label>
            <input type="number" id="idea-count" value="5" min="1" max="20">
            <label>Seed topics</label>
            <textarea id="seed-topics" placeholder="One topic per line (optional)…" rows="1"></textarea>
            <div class="gen-actions">
              <button class="action-btn primary" id="gen-btn" onclick="generateAndScore()">✨ Generate + Score</button>
              <button class="action-btn" onclick="loadSavedIdeas()">📂 Load saved</button>
              <button class="action-btn" id="sync-btn" onclick="syncChannelVideos()" title="Sync published videos from YouTube to check for duplicates">🔄 Sync channel</button>
            </div>
          </div>
          <div id="ideas-status" style="font-size:12px;color:var(--muted);margin-bottom:6px"></div>
          <div class="ideas-grid" id="ideas-grid"></div>
        </div>
      </section>
      <section class="content-grid">
        <div class="panel jobs-panel">
          <div class="panel-head">
            <div class="panel-title">Job queue</div>
            <div class="panel-count" id="panel-count">0 jobs</div>
          </div>
          <div id="jobs-list"></div>
          <div id="vnc-sidebar-box" class="vnc-box"></div>
        </div>
        <div class="panel detail-panel" id="detail-panel">
          <div class="empty">Chọn một job bên trái để xem timeline, artifact và output.</div>
        </div>
      </section>
      </div>
      <div id="config-page" class="config-wrap">
          <div class="config-panel">
          <div class="panel-title" style="margin-bottom:6px">Environment Config (.env)</div>
          <div class="config-row">
            <button class="cfg-btn warn" type="button" onclick="bootstrapEnvFromExample()">Initialize from .env.example</button>
            <button class="cfg-btn" type="button" onclick="loadEnvConfig()">Reload</button>
            <button class="cfg-btn primary" type="button" onclick="saveEnvConfig()">Save .env</button>
          </div>
          <div class="config-row" id="env-meta" style="font-size:12px;color:var(--muted)">Loading...</div>
          <textarea id="env-editor" class="config-textarea" placeholder="KEY=value"></textarea>
        </div>
      </div>
    </main>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
let SELECTED_ID = null;
let WS = null;
let OPEN_STAGES = new Set();
let EVENTS_OPEN = false;
let EVENTS_PAUSED = false;
let EVENT_ROWS_CACHE = [];
const EVENT_ROWS_LIMIT = 400;
let LAST_TIMELINE = null;
let LAST_TIMELINE_JSON = '';
let LAST_JOBS_JSON = '';
let TIMELINE_POLL_TIMER = null;
let TIMELINE_REFRESH_TIMER = null;
let LAST_TIMELINE_REFRESH_AT = 0;
let LOGS_POLL_TIMER = null;
let WS_RETRY_TIMER = null;
let WS_RETRY_MS = 1000;
let ACTIVE_DETAIL_TAB = 'timeline';
let LAST_LOGS_PAYLOAD = null;
let JOBS_BY_ID = {};
let ACTIVE_PAGE = 'jobs';
let VNC_MOUNTED_JOB_ID = null;
let VNC_FIT_MODE = true;
const VNC_IDLE_SENTINEL = '__idle__';
// Cache for render progress — survives DOM rebuilds on each fetchTimeline poll
let RENDER_PROGRESS_CACHE = { pct: 0, meta: '', eta: '' };

const STAGE_LABEL = {
  idea_research: 'Idea research',
  script: 'Script prompt',
  script_promote: 'Script JSON',
  script_qa: 'Script QA',
  scenes: 'Scenes prompt',
  scenes_promote: 'Scenes JSON',
  scenes_qa: 'Scenes QA',
  seo: 'SEO prompt',
  seo_promote: 'SEO JSON',
  seo_qa: 'SEO QA',
  seo_vidiq: 'SEO vidIQ',
  thumbnail_image: 'Thumbnail image',
  assets_chatgpt: 'Asset generation',
  whisper_timestamps: 'Whisper timestamps',
  render: 'Render video',
  review: 'Review page',
  persona_eval: 'Persona eval',
};

function fmtSec(s) {
  if (s === null || s === undefined) return '';
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + 's';
  return Math.floor(s/60) + 'm ' + (s%60) + 's';
}

function fmtSize(n) {
  n = Number(n || 0);
  if (n < 1024) return n + 'B';
  if (n < 1024 * 1024) return Math.round(n / 1024) + 'KB';
  return (n / 1024 / 1024).toFixed(1) + 'MB';
}

function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(d);
}

function jobState(job) {
  const stages = job.stages || [];
  if (stages.some(s => s.status === 'failed')) return 'failed';
  if (job.stages_total > 0 && job.stages_done === job.stages_total) return 'completed';
  if (stages.some(s => s.status === 'in_progress')) return 'in_progress';
  return 'pending';
}

async function fetchJobs() {
  try {
    const r = await fetch('/jobs');
    const d = await r.json();
    const jobs = d.jobs || [];
    JOBS_BY_ID = {};
    for (const j of jobs) JOBS_BY_ID[j.job_id] = j;
    document.getElementById('job-count').textContent = d.count + ' jobs';
    document.getElementById('panel-count').textContent = d.count + ' jobs';
    // Only re-render jobs list when data actually changed — prevents flicker from polling.
    const jobsJson = JSON.stringify(jobs);
    if (jobsJson !== LAST_JOBS_JSON) {
      LAST_JOBS_JSON = jobsJson;
      renderKpis(jobs);
      renderJobsList(jobs);
    }

    // If the selected job was deleted, automatically switch to the
    // newest available job so timeline + websocket stay live.
    const hasSelected = SELECTED_ID && jobs.some(j => j.job_id === SELECTED_ID);
    if (!hasSelected) {
      SELECTED_ID = null;
      if (jobs.length) {
        const runningJob = jobs.find((j) => Array.isArray(j.stages) && j.stages.some((s) => s.status === 'in_progress'));
        selectJob((runningJob || jobs[0]).job_id);
        return;
      }
      if (WS) { try { WS.close(); } catch (e) {} WS = null; }
      const dot = document.getElementById('ws-dot');
      const label = document.getElementById('ws-label');
      dot.className = 'ws-dot off';
      label.textContent = 'disconnected';
      updateVncPanel();
      return;
    }
    updateVncPanel();
    fetchTimeline(SELECTED_ID);
  } catch (e) { console.error(e); }
}

function renderKpis(jobs) {
  const total = jobs.length;
  const completed = jobs.filter(j => jobState(j) === 'completed').length;
  const active = jobs.filter(j => jobState(j) === 'in_progress').length;
  const failed = jobs.filter(j => jobState(j) === 'failed').length;
  const avg = total ? Math.round(jobs.reduce((sum, j) => sum + (j.stages_total ? 100 * j.stages_done / j.stages_total : 0), 0) / total) : 0;
  document.getElementById('kpis').innerHTML = `
    <div class="kpi"><div class="kpi-label">Total jobs</div><div class="kpi-value">${total}</div><div class="kpi-sub">folders in jobs/</div></div>
    <div class="kpi"><div class="kpi-label">Active</div><div class="kpi-value">${active}</div><div class="kpi-sub">currently running</div></div>
    <div class="kpi"><div class="kpi-label">Completed</div><div class="kpi-value">${completed}</div><div class="kpi-sub">ready or reviewed</div></div>
    <div class="kpi"><div class="kpi-label">Avg progress</div><div class="kpi-value">${avg}%</div><div class="kpi-sub">${failed} failed job${failed === 1 ? '' : 's'}</div></div>
  `;
}

function renderJobsList(jobs) {
  const root = document.getElementById('jobs-list');
  root.innerHTML = '';
  for (const j of jobs) {
    const pct = j.stages_total ? (100 * j.stages_done / j.stages_total) : 0;
    const state = jobState(j);
    const fillCls = state === 'completed' ? 'completed' : (state === 'failed' ? 'failed' : '');
    const stopDisabled = state !== 'in_progress' ? 'disabled' : '';
    const stopTitle = state === 'in_progress' ? 'Request stop' : 'Job is not running';
    const deleteDisabled = state === 'in_progress' ? 'disabled' : '';
    const deleteTitle = state === 'in_progress'
      ? 'Stop job first'
      : 'Delete job';
    const card = document.createElement('div');
    card.className = 'job-card' + (SELECTED_ID === j.job_id ? ' active' : '');
    card.innerHTML = `
      <div class="job-row">
        <div class="job-main"><div class="job-id">${escapeHtml(j.job_id)}</div></div>
        <div class="job-tools">
          <div class="job-count"><b>${j.stages_done}</b>/${j.stages_total}</div>
          <button class="job-stop" data-action="stop-job" data-job="${escapeHtml(j.job_id)}" ${stopDisabled} title="${escapeHtml(stopTitle)}">Stop</button>
          <button class="job-del" data-action="delete-job" data-job="${escapeHtml(j.job_id)}" ${deleteDisabled} title="${escapeHtml(deleteTitle)}">×</button>
        </div>
      </div>
      <div class="job-stage">
        <span class="state-dot ${state}"></span>
        <span>${escapeHtml(STAGE_LABEL[j.current_stage] || j.current_stage || 'pending')}</span>
      </div>
      <div class="job-meta">
        <span>${escapeHtml(j.channel_id || '-')}</span>
        <span>${fmtTime(j.updated_at)}</span>
      </div>
      <div class="job-bar ${fillCls}"><div style="width:${pct}%"></div></div>
    `;
    const stopBtn = card.querySelector('button[data-action="stop-job"]');
    if (stopBtn) {
      stopBtn.onclick = (ev) => {
        ev.stopPropagation();
        if (state !== 'in_progress') return;
        stopJob(j.job_id);
      };
    }
    const delBtn = card.querySelector('button[data-action="delete-job"]');
    if (delBtn) {
      delBtn.onclick = (ev) => {
        ev.stopPropagation();
        if (state === 'in_progress') return;
        deleteJob(j.job_id);
      };
    }
    card.onclick = () => selectJob(j.job_id);
    root.appendChild(card);
  }
  if (!jobs.length) root.innerHTML = '<div class="empty" style="min-height:320px">Chưa có job nào.</div>';
}

async function stopJob(jobId) {
  if (!jobId) return;
  if (!confirm(`Stop running job "${jobId}"?`)) return;
  try {
    const r = await fetch('/jobs/' + encodeURIComponent(jobId) + '/stop', { method: 'POST' });
    if (!r.ok) {
      let msg = 'Stop request failed';
      try {
        const body = await r.json();
        msg = body.detail || msg;
      } catch (e) {}
      showToast(msg);
      return;
    }
    showToast('Stop requested');
    LAST_TIMELINE_JSON = '';
    if (SELECTED_ID === jobId) fetchTimeline(jobId);
    fetchJobs();
  } catch (e) {
    showToast('Stop request failed');
  }
}

async function deleteJob(jobId) {
  if (!jobId) return;
  if (!confirm(`Delete job "${jobId}"?`)) return;
  try {
    const r = await fetch('/jobs/' + encodeURIComponent(jobId), { method: 'DELETE' });
    if (!r.ok) {
      let msg = 'Delete failed';
      try {
        const body = await r.json();
        msg = body.detail || msg;
      } catch (e) {}
      showToast(msg);
      return;
    }
    if (SELECTED_ID === jobId) {
      SELECTED_ID = null;
      LAST_TIMELINE_JSON = '';
    }
    showToast('Job deleted');
    fetchJobs();
  } catch (e) {
    showToast('Delete failed');
  }
}

async function resumeJob(jobId) {
  if (!jobId) return;
  try {
    const r = await fetch('/jobs/' + encodeURIComponent(jobId) + '/run-all?enforce_approvals=false', { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      const msg = (d && (d.error || d.detail?.error || d.detail)) || ('HTTP ' + r.status);
      showToast('Resume failed: ' + String(msg));
      return;
    }
    showToast('Resume started');
    LAST_TIMELINE_JSON = '';
    if (SELECTED_ID === jobId) fetchTimeline(jobId);
    fetchJobs();
  } catch (e) {
    showToast('Resume failed');
  }
}

function selectJob(jobId) {
  if (!jobId) return;
  SELECTED_ID = jobId;
  OPEN_STAGES = new Set();
  EVENT_ROWS_CACHE = [];
  EVENTS_PAUSED = false;
  LAST_TIMELINE_JSON = ''; // force re-render on job switch
  LAST_LOGS_PAYLOAD = null;
  document.querySelectorAll('.job-card').forEach(c => c.classList.remove('active'));
  updateVncPanel();
  fetchTimeline(jobId);
  if (ACTIVE_DETAIL_TAB === 'logs') {
    fetchJobLogs(jobId);
    startLogsPolling();
  } else {
    stopLogsPolling();
  }
  reopenWs(jobId);
}

function updateVncPanel() {
  const panel = document.getElementById('vnc-sidebar-box');
  if (!panel) return;
  const vncUrl = 'http://localhost:7900/vnc.html?autoconnect=1&resize=scale&view_only=false';
  const selected = SELECTED_ID ? JOBS_BY_ID[SELECTED_ID] : null;
  const selectedRunning = selected && Array.isArray(selected.stages) && selected.stages.some(s => s.status === 'in_progress');
  const runningFallback = Object.values(JOBS_BY_ID).find((job) => Array.isArray(job.stages) && job.stages.some((s) => s.status === 'in_progress'));
  const j = selectedRunning ? selected : runningFallback;
  if (!j) {
    if (VNC_MOUNTED_JOB_ID === VNC_IDLE_SENTINEL && panel.querySelector('iframe.vnc-frame')) {
      return;
    }
    VNC_MOUNTED_JOB_ID = VNC_IDLE_SENTINEL;
    panel.innerHTML = `
      <div class="vnc-title">Live VNC</div>
      <div class="vnc-note">Chưa có job chạy. Đang mở browser runtime mặc định.</div>
      <iframe class="vnc-frame" src="${vncUrl}" title="VNC Runtime" allow="fullscreen" style="margin:0;"></iframe>
    `;
    return;
  }
  if (VNC_MOUNTED_JOB_ID === j.job_id && panel.querySelector('iframe.vnc-frame')) {
    const note = panel.querySelector('.vnc-note');
    if (note) note.innerHTML = `Đang theo dõi: <b>${escapeHtml(j.job_id)}</b>`;
    return;
  }
  VNC_MOUNTED_JOB_ID = j.job_id;
  panel.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
      <div>
        <div class="vnc-title" style="margin:0;">Live VNC</div>
        <div class="vnc-note" style="margin:2px 0 0;font-size:11px;">Đang theo dõi: <b>${escapeHtml(j.job_id)}</b></div>
      </div>
      <button class="action-btn" onclick="VNC_MOUNTED_JOB_ID=null;updateVncPanel()" style="height:22px;font-size:10px;padding:0 6px;line-height:1;">🔄 Reconnect</button>
    </div>
    <iframe class="vnc-frame" src="${vncUrl}" title="VNC Runtime" allow="fullscreen" style="margin:0;"></iframe>
  `;
}

function stableTimelineKey(t) {
  // Strip file sizes from in_progress stage outputs — they grow continuously
  // (e.g. video.mp4 written by ffmpeg) and would cause spurious re-renders
  // every poll tick even though nothing meaningful has changed.
  const copy = JSON.parse(JSON.stringify(t));
  for (const s of copy.stages || []) {
    if (s.status === 'in_progress') {
      for (const o of s.outputs || []) o.size = 0;
    }
  }
  // updated_at churn causes unnecessary full re-renders and event panel flicker.
  delete copy.updated_at;
  return JSON.stringify(copy);
}

function scheduleTimelineRefresh(jobId, minIntervalMs = 1200) {
  const now = Date.now();
  const elapsed = now - LAST_TIMELINE_REFRESH_AT;
  const run = () => {
    LAST_TIMELINE_REFRESH_AT = Date.now();
    fetchTimeline(jobId);
  };
  if (elapsed >= minIntervalMs) {
    if (TIMELINE_REFRESH_TIMER) {
      clearTimeout(TIMELINE_REFRESH_TIMER);
      TIMELINE_REFRESH_TIMER = null;
    }
    run();
    return;
  }
  if (TIMELINE_REFRESH_TIMER) return;
  TIMELINE_REFRESH_TIMER = setTimeout(() => {
    TIMELINE_REFRESH_TIMER = null;
    run();
  }, Math.max(120, minIntervalMs - elapsed));
}

async function fetchTimeline(jobId) {
  try {
    const r = await fetch('/jobs/' + encodeURIComponent(jobId) + '/timeline');
    if (!r.ok) return;
    const t = await r.json();
    // Only re-render detail panel when data actually changed — prevents flicker from 2s polling.
    // Use stableTimelineKey to ignore growing output file sizes during active renders.
    // render_progress polling already does targeted DOM updates so it's unaffected.
    const tlKey = stableTimelineKey(t);
    LAST_TIMELINE = t;
    if (tlKey !== LAST_TIMELINE_JSON) {
      LAST_TIMELINE_JSON = tlKey;
      renderTimeline(t);
    }
  } catch (e) { console.error(e); }
}

function renderTimeline(t) {
  const root = document.getElementById('detail-panel');
  const allDone = t.stages_total > 0 && t.stages_done === t.stages_total;
  const failed = (t.stages || []).some(s => s.status === 'failed');
  const anyRunning = (t.stages || []).some(s => s.status === 'in_progress');
  const hasPending = (t.stages || []).some(s => s.status !== 'completed');
  const stopped = !!t.stop_requested;
  const renderStage = t.stages.find(s => s.name === 'render');
  // Only show final output when render stage is fully completed — not when
  // video.mp4 happens to exist from a previous run while render is in_progress.
  const videoReady = renderStage &&
    renderStage.status === 'completed' &&
    (renderStage.outputs || []).some(o => o.path === 'video.mp4' && o.exists);
  const channelInitials = (t.channel_id || 'VA').split('-').slice(0, 2).map(s => s[0] || '').join('').toUpperCase();
  const stageStrip = t.stages.map((s, i) => {
    const cls = s.status === 'completed' ? 'completed' : s.status === 'in_progress' ? 'in_progress' : s.status === 'failed' ? 'failed' : '';
    const label = STAGE_LABEL[s.name] || s.name;
    return `<div class="stage-seg ${cls}"><span class="tip">${i + 1}. ${escapeHtml(label)}</span></div>`;
  }).join('');
  const barClass = failed ? 'failed' : allDone ? 'completed' : '';
  root.innerHTML = `
    <div class="summary-card">
      <div class="sum-row">
        <div class="sum-left">
          <div class="sum-id">${escapeHtml(t.job_id)}</div>
          <div class="sum-meta">
            <span class="channel-pill"><span class="avatar">${escapeHtml(channelInitials)}</span>${escapeHtml(t.channel_id || '-')}</span>
            <span>updated ${fmtTime(t.updated_at)}</span>
            <span>created ${fmtTime(t.created_at)}</span>
          </div>
          <div class="sum-actions">
            <button class="action-btn primary" type="button" onclick="resumeJob('${escapeJs(t.job_id)}')" ${(anyRunning || !hasPending) ? 'disabled' : ''}>Resume</button>
            <button class="action-btn warn" type="button" onclick="stopJob('${escapeJs(t.job_id)}')" ${t.stages.some(s => s.status === 'in_progress') ? '' : 'disabled'}>Stop Job</button>
            <button class="action-btn" type="button" onclick="deleteJob('${escapeJs(t.job_id)}')" ${t.stages.some(s => s.status === 'in_progress') ? 'disabled' : ''}>Delete Job</button>
          </div>
        </div>
        <div class="sum-right">
          <div class="sum-pct">${Math.round(t.percent)}<span class="pct-unit">%</span></div>
          <div class="sum-eta">${stopped ? '<b style="color:var(--amber)">Stopped by operator</b>' : failed ? '<b style="color:var(--red)">Failed</b>' : allDone ? '<b>Completed</b>' : anyRunning ? 'Running…' : 'Waiting…'}</div>
        </div>
      </div>
      <div class="sum-bar ${barClass}"><div style="width:${t.percent}%"></div></div>
      <div class="stage-strip" style="grid-template-columns: repeat(${Math.max(1, t.stages.length)}, minmax(16px, 1fr));">${stageStrip}</div>
      <div class="sum-stagerow">
        <span>Current: <b>${escapeHtml(STAGE_LABEL[t.current_stage] || t.current_stage || '-')}</b></span>
        <span>${t.stages_done}/${t.stages_total} stages</span>
      </div>
    </div>
    <div class="detail-tabs">
      <button type="button" class="detail-tab-btn ${ACTIVE_DETAIL_TAB === 'timeline' ? 'active' : ''}" onclick="switchDetailTab('timeline')">Timeline</button>
      <button type="button" class="detail-tab-btn ${ACTIVE_DETAIL_TAB === 'logs' ? 'active' : ''}" onclick="switchDetailTab('logs')">Logs</button>
    </div>
    <div id="detail-tab-timeline" class="detail-tab-content ${ACTIVE_DETAIL_TAB === 'timeline' ? '' : 'hidden'}">
      <div class="section-title"><span>Pipeline stages</span><span>${t.stages_done}/${t.stages_total} done</span></div>
      <div class="timeline" id="timeline"></div>
      ${videoReady ? '<div id="final-mount"></div>' : ''}
      <div class="events ${EVENTS_OPEN ? 'open' : ''}" id="events-panel">
        <div class="events-head" onclick="toggleEvents()">
          <h3>WebSocket events</h3>
          <div style="display:flex;align-items:center;gap:6px;">
            <span class="events-count" id="events-count">live stream</span>
            <button class="action-btn" style="height:22px;padding:0 8px;font-size:10px;" type="button" id="events-pause-btn" onclick="toggleEventsPause(event)">${EVENTS_PAUSED ? 'Resume' : 'Pause'}</button>
            <span class="caret">›</span>
          </div>
        </div>
        <div class="events-body" id="events"></div>
      </div>
    </div>
    <div id="detail-tab-logs" class="detail-tab-content ${ACTIVE_DETAIL_TAB === 'logs' ? '' : 'hidden'}"></div>
  `;
  if (ACTIVE_DETAIL_TAB === 'timeline') {
    const tl = document.getElementById('timeline');
    t.stages.forEach((s, idx) => {
      tl.appendChild(renderStep(t.job_id, s, idx, t.updated_at));
      renderStageExtras(t.job_id, s);  // must be called after appendChild so getElementById finds the element
    });
    if (anyRunning) startStageDurationTicker();
    else stopStageDurationTicker();
    if (videoReady) renderFinal(t);
    renderEventsCache();
    stopLogsPolling();
  } else {
    stopStageDurationTicker();
    renderLogsTab(LAST_LOGS_PAYLOAD);
    if (SELECTED_ID) {
      fetchJobLogs(SELECTED_ID);
      startLogsPolling();
    }
  }
}

function switchDetailTab(tab) {
  if (tab !== 'timeline' && tab !== 'logs') return;
  ACTIVE_DETAIL_TAB = tab;
  updateRailNav();
  // Instant client-side switch so the tab always responds even if
  // timeline fetch is delayed/fails.
  const timelinePane = document.getElementById('detail-tab-timeline');
  const logsPane = document.getElementById('detail-tab-logs');
  if (timelinePane && logsPane) {
    timelinePane.classList.toggle('hidden', tab !== 'timeline');
    logsPane.classList.toggle('hidden', tab !== 'logs');
    document.querySelectorAll('.detail-tab-btn').forEach((btn) => {
      const label = (btn.textContent || '').trim();
      btn.classList.toggle('active', 
        (tab === 'logs' && label === 'Logs') || 
        (tab === 'timeline' && label === 'Timeline')
      );
    });
    if (tab === 'logs') {
      renderLogsTab(LAST_LOGS_PAYLOAD);
      if (SELECTED_ID) {
        fetchJobLogs(SELECTED_ID);
        startLogsPolling();
      }
    } else {
      stopLogsPolling();
    }
    return;
  }
  LAST_TIMELINE_JSON = ''; // fallback: re-render whole detail panel
  if (SELECTED_ID) fetchTimeline(SELECTED_ID);
}

function updateRailNav() {
  const jobs = document.getElementById('rail-jobs');
  const config = document.getElementById('rail-config');
  const logs = document.getElementById('rail-logs');
  if (jobs) jobs.classList.toggle('active', ACTIVE_PAGE === 'jobs' && ACTIVE_DETAIL_TAB !== 'logs');
  if (config) config.classList.toggle('active', ACTIVE_PAGE === 'config');
  if (logs) logs.classList.toggle('active', ACTIVE_PAGE === 'jobs' && ACTIVE_DETAIL_TAB === 'logs');
}

function openLogsTabFromRail() {
  if (ACTIVE_PAGE !== 'jobs') switchPage('jobs');
  if (!SELECTED_ID) {
    showToast('Select a job first');
    return;
  }
  switchDetailTab('logs');
}

function switchPage(page) {
  if (page !== 'jobs' && page !== 'config') return;
  ACTIVE_PAGE = page;
  const jobsPage = document.getElementById('jobs-page');
  const configPage = document.getElementById('config-page');
  if (jobsPage) jobsPage.style.display = (page === 'jobs') ? '' : 'none';
  if (configPage) configPage.classList.toggle('active', page === 'config');
  updateRailNav();
  if (page === 'config') loadEnvConfig();
}

async function fetchJobLogs(jobId) {
  try {
    const r = await fetch('/jobs/' + encodeURIComponent(jobId) + '/logs?tail=200');
    if (!r.ok) return;
    const payload = await r.json();
    LAST_LOGS_PAYLOAD = payload;
    if (ACTIVE_DETAIL_TAB === 'logs' && SELECTED_ID === jobId) renderLogsTab(payload);
  } catch (e) {}
}

function startLogsPolling() {
  if (LOGS_POLL_TIMER || ACTIVE_DETAIL_TAB !== 'logs') return;
  LOGS_POLL_TIMER = setInterval(() => {
    if (SELECTED_ID && ACTIVE_DETAIL_TAB === 'logs') fetchJobLogs(SELECTED_ID);
  }, 2000);
}

function stopLogsPolling() {
  if (LOGS_POLL_TIMER) {
    clearInterval(LOGS_POLL_TIMER);
    LOGS_POLL_TIMER = null;
  }
}

function renderLogsTab(payload) {
  const mount = document.getElementById('detail-tab-logs');
  if (!mount) return;
  if (!payload) {
    mount.innerHTML = '<div class="empty" style="min-height:220px">Chưa có log cho job này.</div>';
    return;
  }
  const rp = payload.render_progress || null;
  const inc = payload.latest_incident || null;
  const events = Array.isArray(payload.events) ? payload.events : [];
  const eventText = events.length
    ? events.map(e => JSON.stringify(e)).join('\\n')
    : '(no events yet)';
  const rpText = rp ? JSON.stringify(rp, null, 2) : '(no render_progress.json yet)';
  const incText = inc ? JSON.stringify(inc, null, 2) : '(no incident found for this job)';
  mount.innerHTML = `
    <div class="logs-head">
      <div class="logs-sub">Realtime logs for <b>${escapeHtml(payload.job_id || '')}</b> · auto refresh 2s</div>
      <button class="copy-btn" data-clip="${escapeHtml(payload.text || '')}" onclick="copyText(this, this.dataset.clip)">copy all logs</button>
    </div>
    <div class="log-card">
      <div class="log-title">Render progress</div>
      <pre class="log-pre">${escapeHtml(rpText)}</pre>
    </div>
    <div class="log-card">
      <div class="log-title">Latest incident</div>
      <pre class="log-pre">${escapeHtml(incText)}</pre>
    </div>
    <div class="log-card">
      <div class="log-title">Event tail (${events.length})</div>
      <pre class="log-pre">${escapeHtml(eventText)}</pre>
    </div>
  `;
}

// Render progress polling state
let RENDER_POLL_TIMER = null;
let STAGE_DUR_TIMER = null;

function parseIsoToMs(iso) {
  if (!iso) return null;
  const ts = Date.parse(iso);
  return Number.isNaN(ts) ? null : ts;
}

function startStageDurationTicker() {
  if (STAGE_DUR_TIMER) return;
  STAGE_DUR_TIMER = setInterval(() => {
    const now = Date.now();
    document.querySelectorAll('.step-dur-live[data-started-at]').forEach((el) => {
      const startedAt = el.getAttribute('data-started-at');
      const startMs = parseIsoToMs(startedAt);
      if (startMs === null) return;
      const elapsed = Math.max(0, Math.floor((now - startMs) / 1000));
      el.textContent = fmtSec(elapsed);
    });
  }, 1000);
}

function stopStageDurationTicker() {
  if (STAGE_DUR_TIMER) {
    clearInterval(STAGE_DUR_TIMER);
    STAGE_DUR_TIMER = null;
  }
}

function startRenderProgressPolling(jobId) {
  if (RENDER_POLL_TIMER) return;
  RENDER_POLL_TIMER = setInterval(async () => {
    try {
      const r = await fetch('/jobs/' + encodeURIComponent(jobId) + '/stages/render/progress');
      if (!r.ok) return;
      const p = await r.json();

      // Build meta string
      const parts = [];
      if (p.frame && p.total_frames) parts.push(p.frame + '/' + p.total_frames + ' frames');
      if (p.fps) parts.push(p.fps.toFixed(1) + ' fps');
      if (p.eta) parts.push('ETA ' + p.eta);
      const metaStr = parts.join(' · ');

      // Save into global cache FIRST — so DOM rebuild in fetchTimeline reads this value
      RENDER_PROGRESS_CACHE = { pct: p.percent || 0, meta: metaStr, eta: p.eta || '' };

      // Then update existing DOM elements (targeted update, no flicker)
      const bar = document.getElementById('render-progress-bar');
      const pct = document.getElementById('render-progress-pct');
      const meta = document.getElementById('render-progress-meta');
      const eta = document.getElementById('render-progress-eta');
      if (bar) bar.style.width = p.percent + '%';
      if (pct) pct.textContent = Math.round(p.percent) + '%';
      if (meta) meta.textContent = metaStr;
      if (eta) eta.textContent = p.eta ? p.eta : 'Calculating...';

      // Stop polling when render completes (step is no longer in_progress)
      const lastTl = LAST_TIMELINE;
      if (lastTl) {
        const rs = (lastTl.stages || []).find(s => s.name === 'render');
        if (rs && rs.status !== 'in_progress') {
          clearInterval(RENDER_POLL_TIMER);
          RENDER_POLL_TIMER = null;
          RENDER_PROGRESS_CACHE = { pct: 0, meta: '', eta: '' };
        }
      }
    } catch(e) {}
  }, 2000);
}

function stopRenderProgressPolling() {
  if (RENDER_POLL_TIMER) { clearInterval(RENDER_POLL_TIMER); RENDER_POLL_TIMER = null; }
}

function renderStep(jobId, s, idx, fallbackStartedAt = null) {
  const el = document.createElement('div');
  el.className = 'step ' + s.status + (OPEN_STAGES.has(s.name) ? ' open' : '');
  const label = STAGE_LABEL[s.name] || s.name;
  const runningStartedAt = s.started_at || fallbackStartedAt || null;
  const runningStartMs = parseIsoToMs(runningStartedAt);
  const runningElapsed = runningStartMs === null ? null : Math.max(0, Math.floor((Date.now() - runningStartMs) / 1000));
  let durHtml = '';
  if (s.actual_seconds !== null && s.actual_seconds !== undefined) {
    durHtml = `<span class="step-dur">${fmtSec(s.actual_seconds)}</span>`;
  } else if (s.status === 'in_progress' && runningStartedAt) {
    durHtml = `<span class="step-dur step-dur-live" data-started-at="${escapeHtml(runningStartedAt)}">${runningElapsed === null ? '' : fmtSec(runningElapsed)}</span>`;
  } else {
    durHtml = '<span class="step-dur"></span>';
  }

  // Render-specific progress bar (shown when in_progress)
  // Read from RENDER_PROGRESS_CACHE — a global that survives DOM rebuilds.
  // The old DOM elements are already gone by the time renderStep() is called,
  // so we CANNOT read them here. The cache is updated by startRenderProgressPolling.
  const _rpc = (s.name === 'render' && s.status === 'in_progress') ? RENDER_PROGRESS_CACHE : null;
  const renderProgressHtml = _rpc ? `
    <div style="margin:10px 0 4px;padding:0 2px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">
        <span style="font-size:12px;color:var(--muted);font-weight:600">Rendering…</span>
        <span id="render-progress-pct" style="font-size:13px;font-weight:700;color:var(--blue)">${Math.round(_rpc.pct)}%</span>
      </div>
      <div style="height:6px;border-radius:4px;background:var(--line-strong);overflow:hidden">
        <div id="render-progress-bar" style="height:100%;width:${_rpc.pct}%;background:var(--blue);border-radius:4px;transition:width 1s linear"></div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;font-size:12px">
        <span style="color:var(--muted);font-weight:500">⏰ Time remaining:</span>
        <span id="render-progress-eta" style="font-weight:700;color:#f5841f">${_rpc.eta ? _rpc.eta : 'Calculating...'}</span>
      </div>
      <div id="render-progress-meta" style="margin-top:5px;font-size:11px;color:var(--muted-2)">${_rpc.meta}</div>
    </div>` : '';

  el.innerHTML = `
    <div class="step-head">
      <span class="step-num">${s.status === 'completed' ? checkIcon() : s.status === 'failed' ? xIcon() : idx + 1}</span>
      <span class="step-label"><span class="name">${escapeHtml(label)}</span><span class="code">${escapeHtml(s.name)}</span></span>
      <span class="step-meta">
        <span class="pill ${s.status}">${statusText(s.status)}</span>${durHtml}<span class="caret">›</span>
      </span>
    </div>
    <div class="step-body">
      ${renderProgressHtml}
      <div class="stage-actions" id="actions-${s.name}"></div>
      <div class="insight-mount" id="insight-${s.name}"></div>
      <div class="io-grid">
        <div class="io-box">
          <div class="io-title">Input</div>
          ${(s.inputs||[]).map(i => renderFile(jobId, i)).join('') || '<div style="color:var(--muted-2);font-size:12px;padding:6px">No input</div>'}
        </div>
        <div class="io-box">
          <div class="io-title">Output</div>
          ${(s.outputs||[]).map(o => renderFile(jobId, o)).join('') || '<div style="color:var(--muted-2);font-size:12px;padding:6px">No output yet</div>'}
        </div>
      </div>
      <div class="preview-mount" id="preview-${s.name}"></div>
    </div>
  `;
  el.querySelector('.step-head').onclick = () => {
    el.classList.toggle('open');
    if (el.classList.contains('open')) OPEN_STAGES.add(s.name);
    else OPEN_STAGES.delete(s.name);
  };
  // Start/stop render progress polling based on stage status
  if (s.name === 'render' && s.status === 'in_progress') {
    startRenderProgressPolling(jobId);
    // Auto-open render step so progress bar is visible
    el.classList.add('open');
    OPEN_STAGES.add('render');
  } else if (s.name === 'render' && s.status !== 'in_progress') {
    stopRenderProgressPolling();
  }
  // NOTE: approval-required stages are NOT auto-expanded.
  // User must click to open them. OPEN_STAGES persists their manual choice.
  return el;
}

function renderFile(jobId, f) {
  const cls = f.exists ? 'file-row' : 'file-row missing';
  const sz = f.exists ? fmtSize(f.size) : 'missing';
  const safePath = escapeHtml(f.path);
  const onclick = f.exists ? `onclick="previewArtifact('${escapeJs(jobId)}','${escapeJs(f.path)}')"` : '';
  return `
    <div class="${cls}" ${onclick}>
      <svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
      <span class="file-name" title="${safePath}">${safePath}</span>
      <span class="file-size">${sz}</span>
    </div>
  `;
}

async function previewArtifact(jobId, path) {
  // Find the step that owns this artifact to mount the preview under.
  let mount = null;
  document.querySelectorAll('.preview-mount').forEach(m => {
    if (m.parentElement.parentElement.innerHTML.includes(path)) mount = m;
  });
  if (!mount) return;
  const url = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=' + encodeURIComponent(path) + '&t=' + Date.now();
  const name = path.split('/').pop();
  if (/\.(png|jpe?g|gif|webp)$/i.test(path)) {
    mount.innerHTML = `<div class="preview"><div class="preview-head"><span><b>${escapeHtml(name)}</b> · ${escapeHtml(path)}</span><button class="preview-close" onclick="this.closest('.preview').remove()">x</button></div><img src="${url}" alt="${escapeHtml(name)}"></div>`;
    return;
  }
  if (/\.mp4$/i.test(path)) {
    mount.innerHTML = `<div class="preview"><div class="preview-head"><span><b>${escapeHtml(name)}</b> · ${escapeHtml(path)}</span><button class="preview-close" onclick="this.closest('.preview').remove()">x</button></div><video src="${url}" controls></video></div>`;
    return;
  }
  try {
    const r = await fetch(url);
    const txt = await r.text();
    let formatted = txt;
    if (/\.json$/i.test(path)) {
      try { formatted = JSON.stringify(JSON.parse(txt), null, 2); } catch (e) {}
    }
    const truncMark = txt.length > 8000 ? ' (truncated)' : '';
    mount.innerHTML = `<div class="preview"><div class="preview-head"><span><b>${escapeHtml(name)}</b> · ${escapeHtml(path)}</span><button class="preview-close" onclick="this.closest('.preview').remove()">x</button></div><pre>${escapeHtml(formatted.slice(0, 8000))}${truncMark}</pre></div>`;
  } catch (e) {
    mount.innerHTML = `<div class="preview"><div class="preview-head"><span>${escapeHtml(path)}</span></div><pre>${escapeHtml(e.message)}</pre></div>`;
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

function escapeJs(s) {
  return String(s).replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'").replace(/\\n/g, '\\\\n').replace(/\\r/g, '');
}

function checkIcon() {
  return '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
}

function xIcon() {
  return '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"></line><line x1="18" y1="6" x2="6" y2="18"></line></svg>';
}

function statusText(status) {
  if (status === 'completed') return 'done';
  if (status === 'in_progress') return 'running';
  if (status === 'failed') return 'failed';
  return 'waiting';
}

function requiredApprovalStages() {
  const t = LAST_TIMELINE || {};
  return Array.isArray(t.required_approvals) ? t.required_approvals : [];
}

function isStageApprovalRequired(stageName) {
  return requiredApprovalStages().includes(stageName);
}

function isStageApproved(stageName) {
  const t = LAST_TIMELINE || {};
  return !!(t.approvals && t.approvals[stageName]);
}

function approvalBlockedBy() {
  const t = LAST_TIMELINE || {};
  return t.approval_blocked_by || null;
}

function stageIndex(stageName) {
  const t = LAST_TIMELINE || {};
  const stages = Array.isArray(t.stages) ? t.stages : [];
  for (let i = 0; i < stages.length; i++) {
    if (stages[i].name === stageName) return i;
  }
  return -1;
}

function isBlockedByApproval(stageName) {
  const blocked = approvalBlockedBy();
  if (!blocked) return false;
  const blockedIdx = stageIndex(blocked);
  const targetIdx = stageIndex(stageName);
  if (blockedIdx < 0 || targetIdx < 0) return false;
  return targetIdx > blockedIdx;
}

function executionStageFor(stageName) {
  const map = {
    script_promote: 'script',
    scenes_promote: 'scenes',
    seo_promote: 'seo',
  };
  return map[stageName] || stageName;
}

async function fetchArtifactJson(jobId, path) {
  const url = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=' + encodeURIComponent(path);
  const r = await fetch(url);
  if (!r.ok) throw new Error('Artifact not found: ' + path);
  return await r.json();
}

async function confirmApproval(jobId, stageName) {
  const r = await fetch(
    '/jobs/' + encodeURIComponent(jobId) + '/approvals/' + encodeURIComponent(stageName) + '/confirm',
    { method: 'POST' },
  );
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.detail || 'Approval failed');
  }
}

async function clearApproval(jobId, stageName) {
  const r = await fetch(
    '/jobs/' + encodeURIComponent(jobId) + '/approvals/' + encodeURIComponent(stageName) + '/clear',
    { method: 'POST' },
  );
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.detail || 'Clear approval failed');
  }
}

async function regenerateStage(jobId, stageName) {
  const r = await fetch(
    '/jobs/' + encodeURIComponent(jobId) + '/stages/' + encodeURIComponent(stageName) + '/regenerate',
    { method: 'POST' },
  );
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.detail || 'Regenerate failed');
  }
}

function scoreColor(n) {
  if (n === null || n === undefined) return '#9ca3af';
  if (n >= 60) return '#16a34a';
  if (n >= 35) return '#d97706';
  return '#dc2626';
}

function renderResearchInsight(data) {
  const scores = Array.isArray(data.scores) ? data.scores : [];
  const verdict = (data.verdict || '').toLowerCase();
  const vbCls = verdict === 'pass' ? 'vb-pass' : verdict === 'skipped' ? 'vb-skip' : 'vb-block';
  const vbIcon = verdict === 'pass' ? '✅' : verdict === 'skipped' ? '⏭' : '❌';
  const vbLabel = verdict === 'pass' ? 'Approved — topic has demand' : verdict === 'skipped' ? 'Skipped (vidIQ unavailable)' : 'Blocked — demand too low';
  const scoreRows = scores.slice(0, 8).map(s => {
    const n = s.score ?? 0;
    const pct = Math.min(100, Math.round((n / 100) * 100));
    const related = Array.isArray(s.related) ? s.related.slice(0, 3).map(r => `<span class="tag-chip">${escapeHtml(r.keyword || '')}</span>`).join('') : '';
    return `
      <div class="score-row">
        <span class="score-kw" title="${escapeHtml(s.keyword || '')}">${escapeHtml(s.keyword || '')}</span>
        <div class="score-bar-outer"><div class="score-bar-inner" style="width:${pct}%;background:${scoreColor(n)}"></div></div>
        <span class="score-num" style="color:${scoreColor(n)}">${n}</span>
        <span class="score-meta">${escapeHtml(s.volume || '')} / ${escapeHtml(s.competition || '')}</span>
      </div>
      ${related ? `<div style="padding-left:148px;margin-bottom:2px">${related}</div>` : ''}
    `;
  }).join('');
  const gateTxt = data.gate ? `Min: ${data.gate.min_score ?? '-'}` : '';
  return `
    <div class="insight">
      <div class="vb ${vbCls}">${vbIcon} ${escapeHtml(vbLabel)}</div>
      <div class="insight-grid">
        <div class="insight-kv"><b>Best keyword score:</b><span style="font-weight:700;color:${scoreColor(data.best_score)}">${data.best_score ?? '-'}/100</span></div>
        <div class="insight-kv"><b>Gate threshold:</b>${escapeHtml(gateTxt || '-')}</div>
      </div>
      ${data.block_reason ? `<div class="gate-note" style="margin-top:8px">⚠️ ${escapeHtml(data.block_reason)}</div>` : ''}
      ${scoreRows ? `<div class="insight-section">Keyword scores</div>${scoreRows}` : ''}
    </div>
  `;
}

function renderScriptInsight(data) {
  const sections = Array.isArray(data.sections) ? data.sections : [];
  const narration = data.narration || '';
  const wordCount = narration.trim() ? narration.trim().split(/\s+/).length : 0;
  const estSec = Math.round(wordCount / 145 * 60);
  const sectionItems = sections.map(s => `
    <div class="section-item">
      <div class="section-title">${escapeHtml(s.title || '')}</div>
      <div class="section-text">${escapeHtml((s.text || '').slice(0, 200))}</div>
    </div>
  `).join('');
  return `
    <div class="insight">
      ${data.hook ? `<div class="insight-section">Hook</div><div class="hook-box">${escapeHtml(data.hook)}</div>` : ''}
      <div class="insight-grid" style="margin-top:10px">
        <div class="insight-kv"><b>Sections:</b>${sections.length}</div>
        <div class="insight-kv"><b>Words:</b>${wordCount} (~${estSec}s)</div>
      </div>
      ${sectionItems ? `<div class="insight-section">Sections</div>${sectionItems}` : ''}
      ${narration ? `<div class="insight-section">Full narration</div><div class="narration-box">${escapeHtml(narration)}</div>` : ''}
      ${data.cta ? `<div class="insight-section">Call to action</div><div class="cta-box">📣 ${escapeHtml(data.cta)}</div>` : ''}
    </div>
  `;
}

function renderScenesInsight(data) {
  const scenes = Array.isArray(data.scenes) ? data.scenes : [];
  const total = data.total_duration_sec;
  const sceneCards = scenes.map(s => `
    <div class="scene-card">
      <div class="scene-card-head">
        <span>${escapeHtml(s.id || '')}</span>
        <span class="scene-dur-badge">⏱ ${s.duration_sec ?? '-'}s</span>
      </div>
      ${s.on_screen_text ? `<div class="scene-text">🖼 ${escapeHtml(s.on_screen_text)}</div>` : ''}
      <div class="scene-narration">${escapeHtml((s.narration || '').slice(0, 220))}</div>
      ${s.visual_prompt ? `<div class="scene-prompt">🎨 ${escapeHtml((s.visual_prompt || '').slice(0, 120))}</div>` : ''}
    </div>
  `).join('');
  return `
    <div class="insight">
      <div class="insight-grid">
        <div class="insight-kv"><b>Total duration:</b><span style="font-weight:700">${total ?? '-'}s</span></div>
        <div class="insight-kv"><b>Scenes:</b>${scenes.length}</div>
      </div>
      ${sceneCards}
    </div>
  `;
}

function renderSeoInsight(data) {
  const tags = Array.isArray(data.tags) ? data.tags : [];
  const variants = Array.isArray(data.title_variants) ? data.title_variants : [];
  const tagChips = tags.map(t => `<span class="tag-chip">#${escapeHtml(t)}</span>`).join('');
  let variantHtml = '';
  if (variants.length) {
    variantHtml = `<div class="insight-section">Title & thumbnail variants (A/B)</div>` +
      variants.map((v, i) => {
        const isWinner = i === 0;
        return `
          <div class="variant-card${isWinner ? ' winner' : ''}">
            ${isWinner ? '<span class="variant-winner-badge">⭐ Winner</span>' : ''}
            <span class="variant-score">${v.score ?? '-'}</span>
            <div class="variant-title">${escapeHtml(v.title || '-')}</div>
            <div class="variant-hook">📌 ${escapeHtml(v.thumbnail_text || '-')}</div>
          </div>
        `;
      }).join('');
  }
  return `
    <div class="insight">
      <div class="hook-box" style="font-size:17px">${escapeHtml(data.title || '-')}</div>
      <div class="insight-grid">
        <div class="insight-kv"><b>Language:</b>${escapeHtml(data.language || '-')}</div>
        <div class="insight-kv"><b>Thumbnail hook:</b>${escapeHtml(data.thumbnail_text || '-')}</div>
      </div>
      ${data.description ? `<div class="insight-section">Description</div><div class="description-box">${escapeHtml(data.description)}</div>` : ''}
      ${tagChips ? `<div class="insight-section">Tags</div><div class="tag-chips">${tagChips}</div>` : ''}
      ${variantHtml}
    </div>
  `;
}

function renderQaInsight(data) {
  const verdict = (data.verdict || '').toUpperCase();
  const isPass = verdict === 'PASS';
  const vbCls = isPass ? 'vb-pass' : 'vb-fail';
  const icon = isPass ? '✅' : '❌';
  const issues = Array.isArray(data.issues) ? data.issues : [];
  const changes = Array.isArray(data.required_changes) ? data.required_changes : [];
  const scores = data.scores && typeof data.scores === 'object' ? data.scores : {};
  const scoreEntries = Object.entries(scores);

  // YouTube Policy block
  const policy = data.youtube_policy && typeof data.youtube_policy === 'object' ? data.youtube_policy : null;
  const policyCompliant = policy ? policy.compliant : null;
  const policyRisk = policy ? (policy.risk_level || 'unknown') : 'unknown';
  const policyViolations = policy && Array.isArray(policy.violations) ? policy.violations : [];
  const policyIcon = policyCompliant === true ? '✅' : policyCompliant === false ? '🚫' : '⚠️';
  const policyColor = policyCompliant === true ? '#16a34a' : policyCompliant === false ? '#dc2626' : '#d97706';
  const policyBg = policyCompliant === true ? '#f0fdf4' : policyCompliant === false ? '#fef2f2' : '#fffbeb';
  const riskBadgeColor = {none:'#16a34a',low:'#ca8a04',medium:'#ea580c',high:'#dc2626'}[policyRisk] || '#6b7280';
  const policyBlock = `
    <div style="border:2px solid ${policyColor};border-radius:8px;background:${policyBg};padding:10px 12px;margin-bottom:10px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:${policyViolations.length ? '8px' : '0'}">
        <span style="font-size:15px">${policyIcon}</span>
        <span style="font-weight:700;color:${policyColor};font-size:13px">YouTube Policy</span>
        <span style="margin-left:auto;font-size:11px;font-weight:700;color:#fff;background:${riskBadgeColor};padding:2px 7px;border-radius:10px">${policyRisk.toUpperCase()} RISK</span>
        ${policyCompliant === true ? '<span style="font-size:12px;color:#16a34a">Compliant ✓</span>' : policyCompliant === false ? '<span style="font-size:12px;color:#dc2626;font-weight:700">NOT COMPLIANT</span>' : ''}
      </div>
      ${policyViolations.length ? `<ul style="margin:0;padding-left:16px;font-size:12px;color:#dc2626">${policyViolations.map(v => `<li>${escapeHtml(v)}</li>`).join('')}</ul>` : ''}
    </div>
  `;

  const scoreBars = scoreEntries.map(([k, v]) => {
    const isPolicy = k === 'youtube_policy';
    const pct = Math.min(100, Math.round((Number(v) / 5) * 100));
    const barColor = isPolicy && Number(v) < 5 ? '#dc2626' : scoreColor(Number(v)*20);
    return `
      <div class="score-row">
        <span class="score-kw" style="${isPolicy ? 'font-weight:700' : ''}">${isPolicy ? '🔒 ' : ''}${escapeHtml(k.replace(/_/g,' '))}</span>
        <div class="score-bar-outer"><div class="score-bar-inner" style="width:${pct}%;background:${barColor}"></div></div>
        <span class="score-num" style="${isPolicy && Number(v) < 5 ? 'color:#dc2626;font-weight:700' : ''}">${v}/5</span>
      </div>
    `;
  }).join('');
  return `
    <div class="insight">
      <div class="vb ${vbCls}">${icon} ${escapeHtml(verdict)}</div>
      ${policyBlock}
      ${scoreBars ? `<div class="insight-section">Scores</div>${scoreBars}` : ''}
      ${issues.length ? `<div class="insight-section">Issues</div><ul class="insight-list">${issues.slice(0, 8).map(i => `<li>${escapeHtml(i)}</li>`).join('')}</ul>` : ''}
      ${changes.length ? `<div class="insight-section">Required changes</div><ul class="insight-list">${changes.slice(0, 6).map(c => `<li>${escapeHtml(c)}</li>`).join('')}</ul>` : ''}
    </div>
  `;
}

function renderSeoVidiqInsight(data) {
  const swaps = Array.isArray(data.swaps) ? data.swaps : [];
  const finalTags = Array.isArray(data.final_tags) ? data.final_tags : [];
  const origTags = Array.isArray(data.original_tags) ? data.original_tags : [];
  const swappedSet = new Set(swaps.map(s => s.replacement));
  const tagChips = finalTags.map(t =>
    `<span class="tag-chip" style="${swappedSet.has(t) ? 'background:#d1fae5;border-color:#6ee7b7;color:#065f46' : ''}">#${escapeHtml(t)}</span>`
  ).join('');
  const swapRows = swaps.map(s => `
    <div class="score-row">
      <span class="score-kw" style="text-decoration:line-through;color:#9ca3af">${escapeHtml(s.original || '')}</span>
      <span style="font-size:13px;color:#6b7280">→</span>
      <span style="font-weight:600;color:#065f46">${escapeHtml(s.replacement || '')}</span>
      <span class="score-meta">was ${s.score ?? '-'}</span>
    </div>
  `).join('');
  return `
    <div class="insight">
      <div class="insight-grid">
        <div class="insight-kv"><b>Tags scored:</b>${origTags.length}</div>
        <div class="insight-kv"><b>Swaps made:</b>${swaps.length}</div>
        <div class="insight-kv"><b>Min score threshold:</b>${data.min_score ?? '-'}</div>
        ${data.vidiq_error ? `<div class="insight-kv"><b>⚠ Error:</b>${escapeHtml(data.vidiq_error.slice(0,80))}</div>` : ''}
      </div>
      ${swapRows ? `<div class="insight-section">Tags swapped</div>${swapRows}` : '<div style="margin-top:8px;font-size:12px;color:#16a34a">✅ All tags above threshold — no swaps needed</div>'}
      ${tagChips ? `<div class="insight-section">Final tags <span style="font-size:11px;color:var(--muted)">(green = replaced)</span></div><div class="tag-chips">${tagChips}</div>` : ''}
    </div>
  `;
}

function toShortText(v) {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') return v.length > 140 ? (v.slice(0, 140) + '…') : v;
  if (Array.isArray(v)) return v.length + ' items';
  if (typeof v === 'object') return Object.keys(v).length + ' fields';
  return String(v);
}

function renderGenericJsonInsight(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return '';
  const rows = Object.entries(data).slice(0, 10).map(([k, v]) => `
    <div class="insight-kv"><b>${escapeHtml(k)}:</b>${escapeHtml(toShortText(v))}</div>
  `).join('');
  if (!rows) return '';
  return `
    <div class="insight">
      <div class="insight-grid">${rows}</div>
    </div>
  `;
}

async function renderStageExtras(jobId, s) {
  const actions = document.getElementById('actions-' + s.name);
  const insight = document.getElementById('insight-' + s.name);
  if (!actions || !insight) return;
  const hasOutput = (path) => Array.isArray(s.outputs) && s.outputs.some(o => o.path === path && o.exists);

  if (isStageApprovalRequired(s.name) && s.status === 'completed') {
    actions.innerHTML = ``;
  }

  try {
    let rendered = false;
    if (s.name === 'idea_research') {
      if (!hasOutput('research.json')) return;
      insight.innerHTML = renderResearchInsight(await fetchArtifactJson(jobId, 'research.json'));
      rendered = true;
    } else if (s.name === 'script_promote') {
      if (!hasOutput('script.json')) return;
      insight.innerHTML = renderScriptInsight(await fetchArtifactJson(jobId, 'script.json'));
      rendered = true;
    } else if (s.name === 'scenes_promote') {
      if (!hasOutput('scenes.json')) return;
      insight.innerHTML = renderScenesInsight(await fetchArtifactJson(jobId, 'scenes.json'));
      rendered = true;
    } else if (s.name === 'seo_promote') {
      if (!hasOutput('seo.json')) return;
      insight.innerHTML = renderSeoInsight(await fetchArtifactJson(jobId, 'seo.json'));
      rendered = true;
    } else if (s.name === 'script_qa') {
      if (!hasOutput('operator/claude/script_qa.json')) return;
      insight.innerHTML = renderQaInsight(await fetchArtifactJson(jobId, 'operator/claude/script_qa.json'));
      rendered = true;
    } else if (s.name === 'scenes_qa') {
      if (!hasOutput('operator/claude/scenes_qa.json')) return;
      insight.innerHTML = renderQaInsight(await fetchArtifactJson(jobId, 'operator/claude/scenes_qa.json'));
      rendered = true;
    } else if (s.name === 'seo_qa') {
      if (!hasOutput('operator/claude/seo_qa.json')) return;
      insight.innerHTML = renderQaInsight(await fetchArtifactJson(jobId, 'operator/claude/seo_qa.json'));
      rendered = true;
    } else if (s.name === 'seo_vidiq') {
      if (!hasOutput('seo_vidiq_report.json')) return;
      insight.innerHTML = renderSeoVidiqInsight(await fetchArtifactJson(jobId, 'seo_vidiq_report.json'));
      rendered = true;
    } else if (s.name === 'thumbnail_image') {
      if (!hasOutput('thumbnail_bg.png') && !hasOutput('thumbnail_1.jpg') && !hasOutput('thumbnail_2.jpg') && !hasOutput('thumbnail_3.jpg') && !hasOutput('thumbnail.jpg')) return;
      if (hasOutput('thumbnail_1.jpg') || hasOutput('thumbnail_2.jpg') || hasOutput('thumbnail_3.jpg') || hasOutput('thumbnail.jpg')) {
        let seo = {};
        try {
          if (hasOutput('seo.json')) seo = await fetchArtifactJson(jobId, 'seo.json');
        } catch(e) {}
        const variants = Array.isArray(seo.title_variants) && seo.title_variants.length
          ? seo.title_variants
          : [{title: seo.title || '', thumbnail_text: seo.thumbnail_text || '', score: null}];
        function thumbUrl(idx) {
          return '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=' + encodeURIComponent('thumbnail_' + (idx+1) + '.jpg') + '&t=' + Date.now();
        }
        function fallbackThumb(idx) {
          return '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=thumbnail.jpg' + '&t=' + Date.now();
        }
        const cards = variants.slice(0,3).map((v, i) => {
          const isWinner = i === 0;
          const score = v.score != null ? ` · ${v.score}pt` : '';
          const badge = isWinner ? `⭐ Best${score}` : `Variant ${i+1}${score}`;
          return `
            <div class="ab-card${isWinner?' ab-winner':''}">
              <div class="ab-badge${isWinner?' winner':''}">${badge}</div>
              <img class="thumb-img" src="${thumbUrl(i)}" alt="thumbnail ${i+1}"
                   onerror="this.src='${fallbackThumb(i)}';this.onerror=null">
              <div class="ab-title">${escapeHtml(v.title || '')}</div>
              ${v.thumbnail_text ? `<div class="ab-hook">${escapeHtml(v.thumbnail_text)}</div>` : ''}
              <button class="copy-btn" data-clip="${escapeHtml(v.title||'')}" onclick="copyText(this,this.dataset.clip)">copy title</button>
            </div>`;
        }).join('');
        insight.innerHTML = `<div class="insight"><div class="insight-section">Generated Thumbnails (ChatGPT Image)</div><div class="ab-grid">${cards}</div></div>`;
        rendered = true;
      } else {
        const bgUrl = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=thumbnail_bg.png';
        insight.innerHTML = `
          <div class="insight">
            <div class="insight-section">Generated thumbnail background</div>
            <img class="thumb-preview" src="${bgUrl}" alt="thumbnail background"
                 onerror="this.style.display='none';this.nextElementSibling.style.display='block'">
            <div style="display:none;color:var(--muted);font-size:12px;padding:8px">Image not ready yet</div>
          </div>`;
        rendered = true;
      }
    } else if (s.name === 'render') {
      if (!hasOutput('video.mp4') && !hasOutput('thumbnail.jpg') && !hasOutput('thumbnail_1.jpg')) return;
      // Show all 3 rendered thumbnails + title variants from seo.json
      let seo = {};
      try {
        if (hasOutput('seo.json')) seo = await fetchArtifactJson(jobId, 'seo.json');
      } catch(e) {}
      const variants = Array.isArray(seo.title_variants) && seo.title_variants.length
        ? seo.title_variants
        : [{title: seo.title || '', thumbnail_text: seo.thumbnail_text || '', score: null}];
      function thumbUrl(idx) {
        return '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=' + encodeURIComponent('thumbnail_' + (idx+1) + '.jpg') + '&t=' + Date.now();
      }
      function fallbackThumb(idx) {
        return '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=thumbnail.jpg' + '&t=' + Date.now();
      }
      const cards = variants.slice(0,3).map((v, i) => {
        const isWinner = i === 0;
        const score = v.score != null ? ` · ${v.score}pt` : '';
        const badge = isWinner ? `⭐ Best${score}` : `Variant ${i+1}${score}`;
        return `
          <div class="ab-card${isWinner?' ab-winner':''}">
            <div class="ab-badge${isWinner?' winner':''}">${badge}</div>
            <img class="thumb-img" src="${thumbUrl(i)}" alt="thumbnail ${i+1}"
                 onerror="this.src='${fallbackThumb(i)}';this.onerror=null">
            <div class="ab-title">${escapeHtml(v.title || '')}</div>
            ${v.thumbnail_text ? `<div class="ab-hook">${escapeHtml(v.thumbnail_text)}</div>` : ''}
            <button class="copy-btn" data-clip="${escapeHtml(v.title||'')}" onclick="copyText(this,this.dataset.clip)">copy title</button>
          </div>`;
      }).join('');
      insight.innerHTML = `<div class="insight"><div class="ab-grid">${cards}</div></div>`;
      rendered = true;
    }
    if (!rendered && Array.isArray(s.outputs)) {
      const jsonOutput = s.outputs.find(o => o.exists && /\.json$/i.test(o.path));
      if (jsonOutput) {
        const generic = renderGenericJsonInsight(await fetchArtifactJson(jobId, jsonOutput.path));
        if (generic) insight.innerHTML = generic;
      }
    }
  } catch (e) {
    // If artifact not ready, skip insight quietly.
  }
}

async function confirmStageAndMaybeContinue(jobId, stageName) {
  try {
    await confirmApproval(jobId, stageName);
    showToast((STAGE_LABEL[stageName] || stageName) + ' confirmed');
    LAST_TIMELINE_JSON = '';
    if (SELECTED_ID) {
      await fetchTimeline(SELECTED_ID);
      const nextStage = (LAST_TIMELINE && LAST_TIMELINE.current_stage) ? LAST_TIMELINE.current_stage : null;
      if (nextStage && nextStage !== stageName) {
        await runStage({stopPropagation: () => {}}, jobId, nextStage);
      }
    }
  } catch (e) {
    showToast(e.message || 'Confirm failed');
  }
}

async function clearStageApprovalOnly(jobId, stageName) {
  try {
    await clearApproval(jobId, stageName);
    showToast('Confirmation cleared');
    LAST_TIMELINE_JSON = '';
    if (SELECTED_ID) fetchTimeline(SELECTED_ID);
  } catch (e) {
    showToast(e.message || 'Clear failed');
  }
}

async function regenerateStageAndRun(jobId, stageName) {
  try {
    await regenerateStage(jobId, stageName);
    showToast((STAGE_LABEL[stageName] || stageName) + ' reset. Running again…');
    await runStage({stopPropagation: () => {}}, jobId, executionStageFor(stageName));
  } catch (e) {
    showToast(e.message || 'Regenerate failed');
  }
}

async function renderFinal(t) {
  const mount = document.getElementById('final-mount');
  if (!mount) return;
  const jobId = t.job_id;
  const videoUrl = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=video.mp4';
  const byName = {};
  for (const st of (t.stages || [])) byName[st.name] = st;
  const stageHas = (stageName, p) => {
    const st = byName[stageName];
    return !!(st && Array.isArray(st.outputs) && st.outputs.some(o => o.path === p && o.exists));
  };
  let seo = {};
  if (stageHas('seo_promote', 'seo.json')) {
    try {
      const sr = await fetch('/jobs/' + encodeURIComponent(jobId) + '/artifact?path=seo.json');
      if (sr.ok) seo = await sr.json();
    } catch (e) {}
  }

  // Fetch all 3 QA files to build YouTube Policy summary
  async function fetchQa(name) {
    if (!stageHas(name, 'operator/claude/' + name + '_qa.json')) return null;
    try {
      const r = await fetch('/jobs/' + encodeURIComponent(jobId) + '/artifact?path=' + encodeURIComponent('operator/claude/' + name + '_qa.json'));
      return r.ok ? await r.json() : null;
    } catch (e) { return null; }
  }
  const [scriptQa, scenesQa, seoQa] = await Promise.all([fetchQa('script'), fetchQa('scenes'), fetchQa('seo')]);
  const qaAll = [{label:'Script', qa: scriptQa}, {label:'Scenes', qa: scenesQa}, {label:'SEO', qa: seoQa}];
  const policyRows = qaAll.map(({label, qa}) => {
    if (!qa) return `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #f3f4f6"><span style="width:60px;font-size:12px;font-weight:600;color:#6b7280">${label}</span><span style="font-size:12px;color:#9ca3af">No QA data</span></div>`;
    const p = qa.youtube_policy || {};
    const compliant = p.compliant;
    const risk = (p.risk_level || 'unknown').toUpperCase();
    const violations = Array.isArray(p.violations) ? p.violations : [];
    const icon = compliant === true ? '✅' : compliant === false ? '🚫' : '⚠️';
    const riskColor = {NONE:'#16a34a',LOW:'#ca8a04',MEDIUM:'#ea580c',HIGH:'#dc2626'}[risk] || '#6b7280';
    const vlText = violations.length ? `<div style="font-size:11px;color:#dc2626;margin-top:3px">${violations.map(v => '• ' + escapeHtml(v)).join('<br>')}</div>` : '';
    return `<div style="padding:6px 0;border-bottom:1px solid #f3f4f6">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="width:60px;font-size:12px;font-weight:700">${label}</span>
        <span style="font-size:14px">${icon}</span>
        <span style="font-size:11px;font-weight:700;color:#fff;background:${riskColor};padding:1px 7px;border-radius:10px">${risk} RISK</span>
        ${compliant === false ? '<span style="font-size:12px;font-weight:700;color:#dc2626">NOT COMPLIANT</span>' : compliant === true ? '<span style="font-size:12px;color:#16a34a">Compliant</span>' : ''}
      </div>
      ${vlText}
    </div>`;
  }).join('');

  const anyPolicyFail = qaAll.some(({qa}) => qa && qa.youtube_policy && qa.youtube_policy.compliant === false);
  const policyBannerBg = anyPolicyFail ? '#fef2f2' : '#f0fdf4';
  const policyBannerBorder = anyPolicyFail ? '#fca5a5' : '#86efac';
  const policyBannerTitle = anyPolicyFail ? '🚫 YouTube Policy — Issues found' : '✅ YouTube Policy — All clear';
  const policyBannerColor = anyPolicyFail ? '#dc2626' : '#16a34a';
  const policyPanel = `
    <div style="border:2px solid ${policyBannerBorder};border-radius:10px;background:${policyBannerBg};padding:12px 14px;margin-bottom:14px">
      <div style="font-weight:700;font-size:13px;color:${policyBannerColor};margin-bottom:8px">${policyBannerTitle}</div>
      ${policyRows}
    </div>
  `;

  const variants = (seo.title_variants && seo.title_variants.length >= 1) ? seo.title_variants : [{title: seo.title || '', thumbnail_text: seo.thumbnail_text || '', score: null}];
  const tags = (seo.tags || []).map(tag => `<span class="tag-chip">${escapeHtml(tag)}</span>`).join('');

  const fallbackThumbUrl = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=thumbnail.jpg' + '&t=' + Date.now();
  function variantCard(v, idx) {
    const thumbPath = 'thumbnail_' + (idx + 1) + '.jpg';
    const thumbUrl = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=' + encodeURIComponent(thumbPath) + '&t=' + Date.now();
    const isWinner = idx === 0;
    const score = (v.score !== undefined && v.score !== null) ? v.score + 'pt' : '';
    const badgeLabel = isWinner
      ? ('⭐ Best' + (score ? ' · ' + score : ''))
      : ('Variant ' + (idx + 1) + (score ? ' · ' + score : ''));
    return `
      <div class="ab-card${isWinner ? ' ab-winner' : ''}">
        <div class="ab-badge${isWinner ? ' winner' : ''}">${badgeLabel}</div>
        <img class="thumb-img" src="${thumbUrl}" alt="thumbnail ${idx + 1}"
             onerror="this.src='${fallbackThumbUrl}';this.onerror=null">
        <div class="ab-title">${escapeHtml(v.title || '')}</div>
        <div class="ab-hook">${escapeHtml(v.thumbnail_text || '')}</div>
        <button class="copy-btn" data-clip="${escapeHtml(v.title || '')}" onclick="copyText(this, this.dataset.clip)">copy title</button>
      </div>`;
  }

  const abSection = `<div class="ab-grid">${variants.slice(0, 3).map((v, i) => variantCard(v, i)).join('')}</div>`;

  mount.innerHTML = `
    <div class="final">
      <div class="final-title"><span>Final output</span><span class="badge">ready</span></div>
      ${policyPanel}
      <video src="${videoUrl}" controls></video>
      ${abSection}
      <div class="final-cols">
        <div>
          <a class="download-link" href="${videoUrl}" download>⬇ Download video.mp4</a>
        </div>
        <div>
          <div class="copy-row">
            <div class="crh"><div class="cr-label">Description <span class="count">${(seo.description || '').length} chars</span></div><button class="copy-btn" onclick="copyText(this, document.getElementById('seo-desc').innerText)">copy</button></div>
            <div class="copy-content scroll" id="seo-desc">${escapeHtml(seo.description || '(missing)')}</div>
          </div>
          <div class="copy-row">
            <div class="crh"><div class="cr-label">Tags <span class="count">${(seo.tags || []).length}</span></div><button class="copy-btn" data-clip="${escapeHtml((seo.tags || []).join(', '))}" onclick="copyText(this, this.dataset.clip)">copy</button></div>
            <div class="tag-chips">${tags || '<span class="tag-chip">(missing)</span>'}</div>
          </div>
          <div class="copy-row">
            <div class="copy-content">Language: ${escapeHtml(seo.language || '?')} · AI disclosure: ${seo.ai_disclosure ? 'yes' : 'no'}</div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function copyText(btn, text) {
  navigator.clipboard.writeText(text || '').then(() => {
    const orig = btn.textContent;
    btn.textContent = 'copied!';
    btn.classList.add('copied');
    showToast('Copied to clipboard');
    setTimeout(() => btn.textContent = orig, 1200);
    setTimeout(() => btn.classList.remove('copied'), 1200);
  });
}

function reopenWs(jobId) {
  if (WS_RETRY_TIMER) {
    clearTimeout(WS_RETRY_TIMER);
    WS_RETRY_TIMER = null;
  }
  if (WS) { try { WS.close(); } catch (e) {} WS = null; }
  if (!jobId) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  WS = new WebSocket(`${proto}//${location.host}/jobs/${jobId}/events`);
  const dot = document.getElementById('ws-dot');
  const label = document.getElementById('ws-label');
  WS.onopen = () => {
    WS_RETRY_MS = 1000;
    dot.className = 'ws-dot live';
    label.textContent = 'live';
  };
  WS.onclose = () => {
    dot.className = 'ws-dot off';
    label.textContent = 'disconnected';
    scheduleWsReconnect(jobId);
  };
  WS.onerror = () => { dot.className = 'ws-dot off'; label.textContent = 'error'; };
  WS.onmessage = (msg) => {
    try {
      const ev = JSON.parse(msg.data);
      appendEvent(ev);
      scheduleTimelineRefresh(jobId, 1200);
    } catch (e) {}
  };
}

function scheduleWsReconnect(jobId) {
  if (!jobId || SELECTED_ID !== jobId) return;
  if (WS_RETRY_TIMER) return;
  const wait = WS_RETRY_MS;
  WS_RETRY_TIMER = setTimeout(() => {
    WS_RETRY_TIMER = null;
    if (SELECTED_ID === jobId) reopenWs(jobId);
  }, wait);
  WS_RETRY_MS = Math.min(15000, Math.round(WS_RETRY_MS * 1.7));
}

function startTimelinePolling() {
  if (TIMELINE_POLL_TIMER) return;
  TIMELINE_POLL_TIMER = setInterval(() => {
    if (SELECTED_ID) fetchTimeline(SELECTED_ID);
  }, 2000);
}

function toggleEvents() {
  EVENTS_OPEN = !EVENTS_OPEN;
  const panel = document.getElementById('events-panel');
  if (panel) panel.classList.toggle('open', EVENTS_OPEN);
}

function toggleEventsPause(evt) {
  if (evt) evt.stopPropagation();
  EVENTS_PAUSED = !EVENTS_PAUSED;
  const btn = document.getElementById('events-pause-btn');
  if (btn) btn.textContent = EVENTS_PAUSED ? 'Resume' : 'Pause';
}

function renderEventsCache() {
  const root = document.getElementById('events');
  if (!root) return;
  root.innerHTML = '';
  for (const row of EVENT_ROWS_CACHE) {
    const div = document.createElement('div');
    div.className = 'ev-row';
    const ts = (row.ts || '').slice(11, 19);
    const stage = row.stage || '';
    div.innerHTML = `<span class="ts">${escapeHtml(ts)}</span><span class="ev-kind ${escapeHtml(row.event || '')}">${escapeHtml(row.event || '')}</span><span class="ev-stage">${escapeHtml(STAGE_LABEL[stage] || stage)}</span>`;
    root.appendChild(div);
  }
  const count = document.getElementById('events-count');
  if (count) count.textContent = root.children.length + ' events';
  root.scrollTop = root.scrollHeight;
}

function appendEvent(ev) {
  if (EVENTS_PAUSED) return;
  EVENT_ROWS_CACHE.push({
    ts: ev.ts || '',
    event: ev.event || '',
    stage: (ev.data && ev.data.stage) ? ev.data.stage : '',
  });
  if (EVENT_ROWS_CACHE.length > EVENT_ROWS_LIMIT) {
    EVENT_ROWS_CACHE = EVENT_ROWS_CACHE.slice(EVENT_ROWS_CACHE.length - EVENT_ROWS_LIMIT);
  }
  const root = document.getElementById('events');
  if (!root) return;
  const div = document.createElement('div');
  div.className = 'ev-row';
  const ts = (ev.ts || '').slice(11, 19);
  const stage = (ev.data && ev.data.stage) ? ev.data.stage : '';
  div.innerHTML = `<span class="ts">${escapeHtml(ts)}</span><span class="ev-kind ${escapeHtml(ev.event)}">${escapeHtml(ev.event)}</span><span class="ev-stage">${escapeHtml(STAGE_LABEL[stage] || stage)}</span>`;
  root.appendChild(div);
  const count = document.getElementById('events-count');
  if (count) count.textContent = root.children.length + ' events';
  root.scrollTop = root.scrollHeight;
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => t.classList.remove('show'), 1600);
}

async function loadEnvConfig() {
  const meta = document.getElementById('env-meta');
  const editor = document.getElementById('env-editor');
  if (!meta || !editor) return;
  meta.textContent = 'Loading...';
  try {
    const r = await fetch('/config/env');
    const d = await r.json();
    if (!r.ok) {
      meta.textContent = 'Failed to load .env';
      return;
    }
    editor.value = d.content || '';
    meta.textContent = `Path: ${d.path} | .env: ${d.exists ? 'exists' : 'missing'} | .env.example: ${d.example_exists ? 'found' : 'missing'}`;
  } catch (e) {
    meta.textContent = 'Failed to load .env';
  }
}

async function saveEnvConfig() {
  const editor = document.getElementById('env-editor');
  if (!editor) return;
  try {
    const r = await fetch('/config/env', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: editor.value || ''}),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showToast('Save failed: ' + (d.detail || r.status));
      return;
    }
    showToast('Saved .env');
    loadEnvConfig();
  } catch (e) {
    showToast('Save failed');
  }
}

async function bootstrapEnvFromExample() {
  try {
    const r = await fetch('/config/env/bootstrap', { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      showToast('Init failed: ' + (d.detail || r.status));
      return;
    }
    if (d.created) showToast('Created .env from .env.example');
    else showToast(d.reason || '.env already exists');
    loadEnvConfig();
  } catch (e) {
    showToast('Init failed');
  }
}

async function runStage(evt, jobId, stageName) {
  evt.stopPropagation(); // don't toggle the step accordion
  if (isBlockedByApproval(stageName)) {
    const blocked = approvalBlockedBy();
    const blockedLabel = STAGE_LABEL[blocked] || blocked || 'required stage';
    showToast('Please confirm ' + blockedLabel + ' before running next stages.');
    return;
  }
  const executeStage = executionStageFor(stageName);
  const btn = document.getElementById('run-btn-' + stageName);
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Running…'; }
  try {
    const autoStages = new Set([
      'idea_research',
      'script',
      'script_qa',
      'scenes',
      'scenes_qa',
      'seo',
      'seo_qa',
      'thumbnail_image',
    ]);
    const suffix = autoStages.has(executeStage) ? '/auto' : '/run';
    const r = await fetch(
      '/jobs/' + encodeURIComponent(jobId) + '/stages/' + encodeURIComponent(executeStage) + suffix,
      { method: 'POST' }
    );
    const d = await r.json();
    if (!r.ok) {
      showToast('Error: ' + (d.detail || r.status));
      if (btn) { btn.disabled = false; btn.textContent = '▶ Run'; }
    } else {
      showToast(stageName + ' completed');
      LAST_TIMELINE_JSON = ''; // force re-render to reflect new state
      if (SELECTED_ID) fetchTimeline(SELECTED_ID);
    }
  } catch (e) {
    showToast('Network error');
    if (btn) { btn.disabled = false; btn.textContent = '▶ Run'; }
  }
}

document.getElementById('refresh-btn').onclick = fetchJobs;
fetchJobs();
setInterval(fetchJobs, 4000);
startTimelinePolling();

// ---- New Job Modal ----
let _NJ_SAVED_IDEAS = [];   // [{idea, path}]
let _NJ_SAVED_LOADED = '';  // last channel loaded

function _njGenId() {
  return 'job-' + Math.floor(Date.now() / 1000);
}

function openNewJobModal() {
  document.getElementById('nj-job-id').value = _njGenId();
  document.getElementById('new-job-overlay').classList.remove('hidden');
  document.getElementById('nj-error').style.display = 'none';
  // sync channel options from idea-channel select if available
  const ideaCh = document.getElementById('idea-channel');
  const njCh = document.getElementById('nj-channel');
  if (ideaCh && njCh) njCh.innerHTML = ideaCh.innerHTML;
  loadModalIdeas();
}

function closeNewJobModal() {
  document.getElementById('new-job-overlay').classList.add('hidden');
}

function njSourceChange() {
  const src = document.querySelector('input[name="nj-src"]:checked').value;
  document.getElementById('nj-saved-wrap').classList.toggle('hidden', src !== 'saved');
  document.getElementById('nj-paste-wrap').classList.toggle('hidden', src !== 'paste');
}

async function loadModalIdeas() {
  const ch = document.getElementById('nj-channel').value;
  const sel = document.getElementById('nj-idea-select');
  if (_NJ_SAVED_LOADED === ch && _NJ_SAVED_IDEAS.length > 0) return; // cached
  sel.innerHTML = '<option value="">Loading…</option>';
  try {
    const r = await fetch('/channels/' + encodeURIComponent(ch) + '/ideas');
    const d = await r.json();
    _NJ_SAVED_IDEAS = (d.ideas || []).map((idea, i) => ({idea, path: (d.paths || [])[i] || ''}));
    _NJ_SAVED_LOADED = ch;
    if (_NJ_SAVED_IDEAS.length === 0) {
      sel.innerHTML = '<option value="">No saved ideas — use Paste JSON or Idea Generator</option>';
    } else {
      sel.innerHTML = _NJ_SAVED_IDEAS.map((item, i) =>
        `<option value="${i}">${escapeHtml(item.idea.title_seed || item.idea.topic || 'Idea ' + (i+1))}</option>`
      ).join('');
    }
  } catch (e) {
    sel.innerHTML = '<option value="">Failed to load</option>';
  }
}

async function submitNewJob() {
  const errEl = document.getElementById('nj-error');
  errEl.style.display = 'none';
  const jobId = document.getElementById('nj-job-id').value.trim() || _njGenId();
  const channelId = document.getElementById('nj-channel').value;
  const src = document.querySelector('input[name="nj-src"]:checked').value;
  const btn = document.getElementById('nj-submit');

  let idea = null;
  let savedPath = '';

  if (src === 'saved') {
    const idx = parseInt(document.getElementById('nj-idea-select').value, 10);
    if (isNaN(idx) || !_NJ_SAVED_IDEAS[idx]) {
      errEl.textContent = 'Select a saved idea or switch source.';
      errEl.style.display = '';
      return;
    }
    idea = _NJ_SAVED_IDEAS[idx].idea;
    savedPath = _NJ_SAVED_IDEAS[idx].path;
  } else if (src === 'paste') {
    const raw = document.getElementById('nj-idea-json').value.trim();
    if (!raw) { errEl.textContent = 'Paste idea JSON.'; errEl.style.display = ''; return; }
    try { idea = JSON.parse(raw); } catch (e) { errEl.textContent = 'Invalid JSON: ' + e.message; errEl.style.display = ''; return; }
  }

  btn.disabled = true;
  btn.textContent = 'Creating…';

  try {
    // 1. Create job
    const r1 = await fetch('/jobs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({job_id: jobId, channel_id: channelId, idea_path: savedPath || ''}),
    });
    const d1 = await r1.json();
    if (!r1.ok) {
      errEl.textContent = d1.detail || ('Create failed: ' + r1.status);
      errEl.style.display = '';
      return;
    }

    // 2. Upload idea if we have one
    if (idea) {
      const r2 = await fetch('/jobs/' + encodeURIComponent(jobId) + '/idea', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(idea),
      });
      if (!r2.ok) {
        errEl.textContent = 'Job created but idea upload failed.';
        errEl.style.display = '';
      }
    }

    closeNewJobModal();
    showToast('Job created — pipeline starting…');
    await fetchJobs();
    SELECTED_ID = jobId;
    LAST_TIMELINE_JSON = '';
    await fetchTimeline(jobId);

    // Auto-run full pipeline (fire-and-forget — UI polls for progress)
    fetch('/jobs/' + encodeURIComponent(jobId) + '/run-all?enforce_approvals=false', { method: 'POST' })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          const msg = (d && (d.error || d.detail?.error || d.detail)) || ('HTTP ' + r.status);
          throw new Error(String(msg));
        }
        return d;
      })
      .then(() => {
        LAST_TIMELINE_JSON = '';
        if (SELECTED_ID === jobId) fetchTimeline(jobId);
      })
      .catch(e => showToast('Pipeline error: ' + e.message));
  } catch (e) {
    errEl.textContent = 'Error: ' + e.message;
    errEl.style.display = '';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Create Job';
  }
}
// ---- end New Job Modal ----

// ---- Idea Generator ----
let IDEAS_PANEL_OPEN = false;

async function loadChannels() {
  try {
    const r = await fetch('/channels');
    const d = await r.json();
    const opts = (d.channels || []).map(c =>
      `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`
    ).join('') || '<option value="">no channels</option>';
    document.getElementById('idea-channel').innerHTML = opts;
    document.getElementById('nj-channel').innerHTML = opts;
  } catch (e) {}
}
loadChannels();

function toggleIdeasPanel() {
  IDEAS_PANEL_OPEN = !IDEAS_PANEL_OPEN;
  document.getElementById('ideas-body').style.display = IDEAS_PANEL_OPEN ? '' : 'none';
  document.getElementById('ideas-caret').textContent = IDEAS_PANEL_OPEN ? '‹ collapse' : '› expand';
  if (IDEAS_PANEL_OPEN && document.getElementById('ideas-grid').children.length === 0) {
    loadSavedIdeas();
  }
}

async function loadSavedIdeas() {
  const channelId = document.getElementById('idea-channel').value;
  const status = document.getElementById('ideas-status');
  status.textContent = 'Loading saved ideas…';
  try {
    const r = await fetch('/channels/' + encodeURIComponent(channelId) + '/ideas');
    const d = await r.json();
    if (!d.ideas || d.ideas.length === 0) {
      document.getElementById('ideas-grid').innerHTML = '<div class="ideas-empty">No saved ideas for this channel yet. Click ✨ Generate to create some.</div>';
      status.textContent = '';
      return;
    }
    renderIdeaCards(d.ideas, d.paths);
    status.textContent = d.ideas.length + ' saved idea' + (d.ideas.length !== 1 ? 's' : '') + ' (most recent first)';
  } catch (e) {
    status.textContent = 'Failed to load saved ideas.';
  }
}

let _CURRENT_IDEAS = [];
let _CURRENT_PATHS = [];

async function generateAndScore() {
  const channelId = document.getElementById('idea-channel').value;
  const count = parseInt(document.getElementById('idea-count').value, 10) || 5;
  const seedRaw = document.getElementById('seed-topics').value.trim();
  const seedTopics = seedRaw ? seedRaw.split('\\n').map(s => s.trim()).filter(Boolean) : [];
  const btn = document.getElementById('gen-btn');
  const status = document.getElementById('ideas-status');

  btn.disabled = true;
  document.getElementById('ideas-grid').innerHTML = '';

  // Single request: backend does vidIQ discovery → ChatGPT expansion internally
  btn.innerHTML = '<span class="spinner-inline"></span>Scanning vidIQ…';
  status.textContent = 'Step 1/2 — scoring keywords on vidIQ to find high-opportunity topics…';

  let ideas = [], savedPaths = [];
  try {
    const r = await fetch('/channels/' + encodeURIComponent(channelId) + '/ideas/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({count, seed_topics: seedTopics}),
    });

    // While waiting, update label to hint that ChatGPT phase started
    // (we can't stream, so just show a generic second step message after short delay)
    const labelTimer = setTimeout(() => {
      if (btn.disabled) {
        btn.innerHTML = '<span class="spinner-inline"></span>Generating ideas…';
        status.textContent = 'Step 2/2 — ChatGPT generating ideas from top keywords…';
      }
    }, 30000);

    const d = await r.json();
    clearTimeout(labelTimer);

    if (!r.ok) {
      status.textContent = '❌ Failed: ' + (d.detail?.error || d.detail || r.status);
      return;
    }
    ideas = d.ideas || [];
    savedPaths = (d.saved || []).map(p => 'ideas/' + p.split('ideas/').pop());

    // Ideas already have vidiq_score/vidiq_volume/vidiq_competition from backend
    renderIdeaCards(ideas, savedPaths);

    const topKws = d.top_keywords || [];
    const best = topKws.length ? ' (best: ' + (topKws[0].score ?? '?') + '/100)' : '';
    const srcLabel = {'trend': '📈 từ Google Trends', 'fallback': '📂 từ channel niche', 'user': '✏️ từ seeds bạn nhập'}[d.seed_source] || '';
    status.textContent = '✅ ' + ideas.length + ' ideas — ' + srcLabel + best + '.';
  } catch (e) {
    status.textContent = '❌ Error: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.innerHTML = '✨ Generate + Score';
  }
}

async function syncChannelVideos() {
  const channelId = document.getElementById('idea-channel').value;
  const btn = document.getElementById('sync-btn');
  const status = document.getElementById('ideas-status');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-inline"></span>Syncing…';
  try {
    const r = await fetch('/channels/' + encodeURIComponent(channelId) + '/sync-videos');
    const d = await r.json();
    if (!r.ok) { status.textContent = '❌ Sync failed: ' + (d.detail || r.status); return; }
    status.textContent = '✅ Synced ' + d.count + ' published videos from YouTube.';
  } catch (e) {
    status.textContent = '❌ Sync error: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.innerHTML = '🔄 Sync channel';
  }
}

function renderIdeaCards(ideas, paths) {
  _CURRENT_IDEAS = ideas;
  _CURRENT_PATHS = paths;
  const grid = document.getElementById('ideas-grid');
  grid.innerHTML = '';
  ideas.forEach((idea, i) => {
    const el = document.createElement('div');
    el.innerHTML = renderIdeaCard(idea, paths[i] || '');
    el.firstElementChild.id = 'idea-card-' + i;
    grid.appendChild(el.firstElementChild);
  });
}

function _scoreClass(n) {
  if (n == null) return 'none';
  if (n >= 50) return 'high';
  if (n >= 25) return 'mid';
  return 'low';
}

function _scoreColor(n) {
  if (n == null) return '#9ca3af';
  if (n >= 50) return '#10b981';
  if (n >= 25) return '#f59e0b';
  return '#ef4444';
}

async function scoreOneIdea(btn, idea) {
  const card = btn.closest('.idea-card');
  const mount = card ? card.querySelector('.idea-score-mount') : null;
  if (!mount) return;
  const channelId = document.getElementById('idea-channel').value;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-inline"></span>';
  try {
    const r = await fetch('/channels/' + encodeURIComponent(channelId) + '/ideas/score', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ideas: [idea]}),
    });
    const d = await r.json();
    const res = (d.results || [])[0];
    mount.innerHTML = res ? renderIdeaScoreBlock(res) : '<div style="font-size:11px;color:var(--muted)">No data</div>';
  } catch (e) {
    mount.innerHTML = '<div style="font-size:11px;color:var(--red)">' + escapeHtml(e.message) + '</div>';
  } finally {
    btn.disabled = false;
    btn.innerHTML = '📊';
  }
}

function renderIdeaScoreBlock(res) {
  if (!res) return '';
  const n = res.best_score;
  const cls = _scoreClass(n);
  const color = _scoreColor(n);
  const pct = n != null ? Math.min(n, 100) : 0;
  const comp = res.competition ? `<span class="idea-comp-badge">⚔️ ${escapeHtml(res.competition)}</span>` : '';
  const related = (res.related || []).slice(0, 6).map(r => `<span style="background:#f3f4f6;border-radius:4px;padding:1px 7px;font-size:11px">${escapeHtml(r)}</span>`).join(' ');
  const err = res.error ? `<div style="font-size:11px;color:var(--muted)">vidIQ unavailable</div>` : '';
  return `
    <div class="idea-score-row">
      <span class="idea-score-badge ${cls}">${n != null ? n + '/100' : 'N/A'}</span>
      <div class="idea-score-bar"><div class="idea-score-bar-fill" style="width:${pct}%;background:${color}"></div></div>
      ${comp}
    </div>
    ${related ? `<div class="idea-related">Related: ${related}</div>` : ''}
    ${err}
  `;
}

function renderIdeaCard(idea, savedPath) {
  const dur = idea.target_duration_sec ? Math.round(idea.target_duration_sec / 60) + ' min' : '';
  const points = (idea.key_points || []).slice(0, 4).map(p => `<li>${escapeHtml(p)}</li>`).join('');
  const safeIdea = escapeHtml(JSON.stringify(idea));
  const safePath = escapeHtml(savedPath);

  // Inline score block from pre-computed vidiq_score (no extra fetch needed)
  const score = idea.vidiq_score ?? null;
  const vol = idea.vidiq_volume || '';
  const comp = idea.vidiq_competition || '';
  const kw = idea.target_keyword || '';
  let scoreHtml = '';
  if (score !== null || kw) {
    const cls = _scoreClass(score);
    const color = _scoreColor(score);
    const pct = score != null ? Math.min(100, score) : 0;
    scoreHtml = `
      <div class="idea-score-row">
        ${score !== null ? `<span class="idea-score-badge ${cls}">${score}/100</span>` : ''}
        ${score !== null ? `<div class="idea-score-bar"><div class="idea-score-bar-fill" style="width:${pct}%;background:${color}"></div></div>` : ''}
        ${comp ? `<span class="idea-comp-badge">${escapeHtml(comp)}</span>` : ''}
        ${vol ? `<span class="idea-comp-badge" style="background:#eff6ff;color:#1d4ed8">${escapeHtml(vol)}</span>` : ''}
      </div>
      ${kw ? `<div class="idea-related" style="margin-top:4px">🔑 <b>${escapeHtml(kw)}</b></div>` : ''}`;
  }

  const dupHtml = idea.is_duplicate
    ? `<div class="idea-duplicate-badge">⚠️ Trùng nội dung: "${escapeHtml((idea.duplicate_of || '').slice(0, 60))}"</div>`
    : '';

  return `
    <div class="idea-card${idea.is_duplicate ? ' opacity-60' : ''}">
      <div class="idea-card-title">${escapeHtml(idea.title_seed || idea.topic || '-')}</div>
      ${dupHtml}
      ${scoreHtml}
      <div class="idea-card-angle">${escapeHtml(idea.angle || '')}</div>
      <div class="idea-card-meta">
        ${dur ? `<span class="idea-card-dur">⏱ ${dur}</span>` : ''}
        <span class="idea-card-dur">${escapeHtml(idea.topic || '')}</span>
      </div>
      ${points ? `<ul class="idea-card-points">${points}</ul>` : ''}
      <div class="idea-card-actions">
        <button class="action-btn primary"
          onclick='createJobFromIdea(${safeIdea}, "${safePath}")'>+ Create Job</button>
      </div>
    </div>`;
}

async function createJobFromIdea(idea, savedPath) {
  const channelId = document.getElementById('idea-channel').value;
  // Generate job ID: slug from title_seed + timestamp
  const slug = (idea.title_seed || idea.topic || 'idea')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 30);
  const jobId = slug + '-' + Math.floor(Date.now() / 1000);

  try {
    // 1. Create job
    const r1 = await fetch('/jobs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({job_id: jobId, channel_id: channelId, idea_path: savedPath}),
    });
    if (!r1.ok) {
      const d = await r1.json();
      showToast('Create job failed: ' + (d.detail || r1.status));
      return;
    }

    // 2. Write idea.json into the job dir
    const r2 = await fetch('/jobs/' + encodeURIComponent(jobId) + '/idea', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(idea),
    });
    if (!r2.ok) {
      showToast('Idea upload failed');
      return;
    }

    showToast('Job created — pipeline starting…');
    await fetchJobs();
    // Select the new job
    SELECTED_ID = jobId;
    LAST_TIMELINE_JSON = '';
    await fetchTimeline(jobId);

    // Auto-run full pipeline (fire-and-forget — UI polls for progress)
    fetch('/jobs/' + encodeURIComponent(jobId) + '/run-all?enforce_approvals=false', { method: 'POST' })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          const msg = (d && (d.error || d.detail?.error || d.detail)) || ('HTTP ' + r.status);
          throw new Error(String(msg));
        }
        return d;
      })
      .then(() => {
        LAST_TIMELINE_JSON = '';
        if (SELECTED_ID === jobId) fetchTimeline(jobId);
      })
      .catch(e => showToast('Pipeline error: ' + e.message));
  } catch (e) {
    showToast('Error: ' + e.message);
  }
}
// ---- end Idea Generator ----
</script>
</body>
</html>
"""


@app.post("/jobs", status_code=201)
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


@app.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return load_job(job_dir).to_dict()


@app.delete("/jobs/{job_id}", status_code=204)
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


@app.post("/jobs/{job_id}/stop")
def stop_job(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    """Request graceful stop for the current /run-all execution."""
    job_dir = _safe_job_dir(jobs_root, job_id)
    job_file = job_dir / "job.json"
    if not job_file.exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    stop_request_path(job_dir).write_text("1\n", encoding="utf-8")
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


@app.post("/jobs/{job_id}/advance")
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


@app.get("/jobs/{job_id}/events")
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


@app.post("/jobs/{job_id}/idea", status_code=201)
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
    idea_path.write_text(
        json.dumps(idea, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"job_id": job_id, "idea_path": str(idea_path)}


@app.post("/jobs/{job_id}/stages/script/run")
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


@app.post("/jobs/{job_id}/stages/script/promote")
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


@app.post("/jobs/{job_id}/stages/scenes/run")
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


@app.post("/jobs/{job_id}/stages/scenes/promote")
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


@app.post("/jobs/{job_id}/stages/seo/run")
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


@app.post("/jobs/{job_id}/stages/seo/promote")
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


@app.post("/jobs/{job_id}/stages/whisper_timestamps/run")
def post_run_whisper_timestamps(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_whisper_timestamps_stage(job_dir)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/render/run")
def post_run_render(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_render_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.get("/jobs/{job_id}/stages/render/progress")
def get_render_progress(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    """Return current Remotion render progress. Polling-friendly — always 200."""
    job_dir = _safe_job_dir(jobs_root, job_id)
    progress_path = job_dir / "render_progress.json"
    if not progress_path.exists():
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
    try:
        import json as _json
        return _json.loads(progress_path.read_text(encoding="utf-8"))
    except Exception:
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}


@app.post("/jobs/{job_id}/stages/review/run")
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


@app.post("/jobs/{job_id}/stages/persona_eval/run")
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


@app.get("/jobs/{job_id}/approvals")
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


@app.post("/jobs/{job_id}/approvals/{stage_name}/confirm")
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


@app.post("/jobs/{job_id}/approvals/{stage_name}/clear")
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


@app.post("/jobs/{job_id}/stages/idea_research/auto")
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
            client.run_vidiq_scores,
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    # Fresh research run invalidates prior manual confirmation.
    set_approval(job_dir, "idea_research", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/seo_vidiq/auto")
async def post_auto_seo_vidiq(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_seo_vidiq_stage(
            job_dir,
            channel_path,
            client.run_vidiq_scores,
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/{stage_name}/regenerate")
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


class ScoreIdeasRequest(BaseModel):
    ideas: list[dict]


@app.get("/channels")
def list_channels() -> dict:
    """List available channel configs."""
    configs_dir = repo_root() / "configs"
    channels = []
    if configs_dir.exists():
        for p in sorted(configs_dir.iterdir()):
            if p.is_dir() and (p / "channel.yaml").exists():
                channels.append(p.name)
    return {"channels": channels}


@app.get("/channels/{channel_id}/ideas")
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


@app.post("/channels/{channel_id}/ideas/score")
async def post_score_ideas(
    channel_id: str,
    req: ScoreIdeasRequest,
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    """Score a list of ideas via vidIQ in a single browser session.

    Returns per-idea: keywords scored, best_score, competition, related keywords.
    Scoring is best-effort — if vidIQ is unavailable all scores are null.
    """
    _safe_channel_id(channel_id)
    # Collect all keywords from all ideas in one flat list, track ranges
    all_keywords: list[str] = []
    ranges: list[tuple[int, int]] = []
    for idea in req.ideas:
        kws = _idea_keywords(idea)
        ranges.append((len(all_keywords), len(all_keywords) + len(kws)))
        all_keywords.extend(kws)

    all_scores: list[dict] = []
    score_error: str | None = None
    if all_keywords:
        try:
            all_scores = await client.run_vidiq_scores(all_keywords)
        except Exception as exc:
            score_error = str(exc)
            all_scores = [{}] * len(all_keywords)

    results = []
    for i, idea in enumerate(req.ideas):
        start, end = ranges[i]
        idea_scores = all_scores[start:end]
        valid = [s for s in idea_scores if isinstance(s.get("score"), (int, float))]
        best = int(max(s["score"] for s in valid)) if valid else None
        # collect related keywords from all keyword results for this idea
        related: list[str] = []
        for s in idea_scores:
            raw_rel = s.get("related", []) or []
            for r in raw_rel:
                # vidIQ returns related as strings or {keyword, score} dicts
                if isinstance(r, str):
                    related.append(r)
                elif isinstance(r, dict):
                    kw = r.get("keyword") or r.get("term") or ""
                    if kw:
                        related.append(str(kw))
        # deduplicate while preserving order
        seen_set: set[str] = set()
        related_unique = [r for r in related if r not in seen_set and not seen_set.add(r)]  # type: ignore[func-returns-value]
        # pick best competition label
        competition = None
        for s in valid:
            comp = (s.get("competition") or "").strip()
            if comp:
                competition = comp
                break
        results.append({
            "topic": idea.get("topic", ""),
            "title_seed": idea.get("title_seed", ""),
            "keywords": all_keywords[start:end],
            "scores": idea_scores,
            "best_score": best,
            "competition": competition,
            "related": related_unique[:10],
            "error": score_error if score_error else None,
        })

    return {"results": results, "error": score_error}


@app.get("/channels/{channel_id}/sync-videos")
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


@app.post("/channels/{channel_id}/ideas/generate", status_code=201)
async def post_generate_ideas(
    channel_id: str,
    req: GenerateIdeasRequest,
    inputs_root: Path = Depends(get_inputs_root),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    """Discover high-opportunity keywords via vidIQ, then generate ideas via ChatGPT.

    Flow:
      1. Score seed_topics on vidIQ + expand to related keywords.
      2. Pick top-N keywords by score (high volume / low competition).
      3. ChatGPT fleshes out one idea per top keyword.

    Returns ideas with pre-computed keyword scores so the UI shows scores immediately.
    """
    _safe_channel_id(channel_id)
    channel_path = repo_root() / "configs" / channel_id / "channel.yaml"
    if not channel_path.exists():
        raise HTTPException(status_code=404, detail=f"No config for channel: {channel_id}")

    if not 1 <= req.count <= 50:
        raise HTTPException(status_code=422, detail="count must be between 1 and 50")

    try:
        ideas, top_keywords, seed_source = await generate_ideas(
            channel_path=channel_path,
            chatgpt_fn=lambda msgs: client.run_session("chatgpt", msgs),
            vidiq_fn=getattr(client, "run_vidiq_scores", None),
            seed_topics=req.seed_topics,
            count=req.count,
            with_metadata=True,
        )
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Attach keyword score to each idea by matching target_keyword field
    # Load cached published videos for duplicate detection
    from video_agent.utils.json_io import read_yaml as _read_yaml
    published = load_published_videos(_read_yaml(channel_path), repo_root() / "configs")

    kw_score_map = {kw["keyword"].lower(): kw for kw in top_keywords}
    ideas_with_scores = []
    for idea in ideas:
        enriched = dict(idea)
        target_kw = idea.get("target_keyword", "")
        kw_data = kw_score_map.get(target_kw.lower(), {})
        if kw_data:
            enriched["vidiq_score"] = kw_data.get("score")
            enriched["vidiq_volume"] = kw_data.get("volume", "")
            enriched["vidiq_competition"] = kw_data.get("competition", "")
        # Duplicate check against published channel videos
        dup = find_duplicate(enriched, published)
        enriched["is_duplicate"] = dup is not None
        enriched["duplicate_of"] = dup
        ideas_with_scores.append(enriched)

    paths = save_ideas(ideas_with_scores, channel_id=channel_id, out_dir=inputs_root)
    rel_paths = [str(p.relative_to(inputs_root)) for p in paths]

    return {
        "channel_id": channel_id,
        "count": len(ideas_with_scores),
        "ideas": ideas_with_scores,
        "top_keywords": top_keywords,
        "seed_source": seed_source,
        "published_videos_checked": len(published),
        "saved": rel_paths,
    }


@app.post("/jobs/{job_id}/stages/script/auto")
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


@app.post("/jobs/{job_id}/stages/scenes/auto")
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


@app.post("/jobs/{job_id}/stages/seo/auto")
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


@app.post("/jobs/{job_id}/stages/script_qa/auto")
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
            _one_shot_with_briefing(client, "claude", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/scenes_qa/auto")
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
            _one_shot_with_briefing(client, "claude", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/seo_qa/auto")
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
            _one_shot_with_briefing(client, "claude", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/thumbnail_image/auto")
async def post_auto_thumbnail_image(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
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


@app.post("/jobs/{job_id}/scenes/{scene_id}/generate_asset")
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


@app.post("/jobs/{job_id}/run-all")
async def post_run_all(
    job_id: str,
    enforce_approvals: bool = True,
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

    from video_agent.orchestrator.queue import JobQueue
    db_path = jobs_root / "queue.db"
    queue = JobQueue(db_path)
    queue.enqueue(job_id, enforce_approvals)

    state = load_job(job_dir)
    return {
        "job_id": job_id,
        "status": "enqueued",
        "state": state.to_dict(),
    }


class RunBatchRequest(BaseModel):
    job_ids: list[str]
    enforce_approvals: bool = False


@app.post("/run-batch")
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


@app.websocket("/jobs/{job_id}/events")
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
