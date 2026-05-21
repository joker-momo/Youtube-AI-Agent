from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from video_agent.contracts import EVENT_LOG, repo_root
from video_agent.orchestrator import (
    JobAlreadyExistsError,
    JobNotFoundError,
    advance,
    create_job,
    load_job,
)
from video_agent.orchestrator.idea_generator import generate_ideas, save_ideas
from video_agent.orchestrator.browser_client import (
    BrowserClient,
    BrowserClientError,
    LoginRequiredFromWorker,
)
from video_agent.orchestrator.orchestrator import StageError
from video_agent.orchestrator.stages import (
    IDEA_FILE,
    StageInputMissingError,
    auto_assets_chatgpt_stage,
    auto_idea_research_stage,
    auto_qa_with_rework,
    auto_seo_vidiq_stage,
    auto_scenes_qa_stage,
    auto_scenes_stage,
    auto_script_qa_stage,
    auto_script_stage,
    auto_seo_qa_stage,
    auto_seo_stage,
    generate_scene_asset,
    promote_qa_stage,
    promote_scenes_stage,
    promote_seo_stage,
    promote_script_stage,
    run_render_stage,
    run_review_stage,
    run_scenes_stage,
    run_seo_stage,
    run_script_stage,
    run_whisper_timestamps_stage,
)

app = FastAPI(title="video-agent-web", version="0.1.0")


class CreateJobRequest(BaseModel):
    job_id: str
    channel_id: str
    idea_path: str


class RawScriptRequest(BaseModel):
    raw_response: str


def get_jobs_root() -> Path:
    return Path(os.environ.get("JOBS_DIR", "/app/jobs"))


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "app"}


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
            stages = []
            for raw_stage in payload.get("stages", []):
                stage = dict(raw_stage)
                stage["status"] = _effective_stage_status(stage, current_stage)
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

# Per-stage input + output file plumbing for the dashboard. Each stage
# entry lists the artifact relative paths (input + output) the operator
# wants to see, so the UI can show "what went into" and "what came out
# of" each step without per-stage hardcoding in JS.
_STAGE_ARTIFACTS = {
    "script": {
        "input": ["idea.json"],
        "output": ["operator/chatgpt/script_prompt.md"],
    },
    "script_promote": {
        "input": ["operator/chatgpt/script.raw.txt"],
        "output": ["script.json"],
    },
    "script_qa": {
        "input": ["script.json"],
        "output": ["operator/gemini/script_qa.json"],
    },
    "scenes": {
        "input": ["script.json"],
        "output": ["operator/chatgpt/scenes_prompt.md"],
    },
    "scenes_promote": {
        "input": ["operator/chatgpt/scenes.raw.txt"],
        "output": ["scenes.json"],
    },
    "scenes_qa": {
        "input": ["scenes.json"],
        "output": ["operator/gemini/scenes_qa.json"],
    },
    "seo": {
        "input": ["scenes.json"],
        "output": ["operator/chatgpt/seo_prompt.md"],
    },
    "seo_promote": {
        "input": ["operator/chatgpt/seo.raw.txt"],
        "output": ["seo.json"],
    },
    "seo_qa": {
        "input": ["seo.json"],
        "output": ["operator/gemini/seo_qa.json"],
    },
    "whisper_timestamps": {
        "input": ["assets/narration.wav"],
        "output": ["whisper_timestamps.json"],
    },
    "render": {
        "input": ["script.json", "scenes.json", "seo.json"],
        "output": [
            "render_props.json",
            "video.mp4",
            "thumbnail.jpg",
            "visual_review.json",
            "report.md",
        ],
    },
    "review": {
        "input": ["video.mp4"],
        "output": ["operator_review.html"],
    },
}

# Empirical seconds per stage when long-form 20-30 min config is in
# play. Used for an ETA when the stage hasn't run yet. Render scales
# with the target_duration_sec of the idea so we compute it from
# scenes.json or idea.json instead of a constant.
_STAGE_ETA_SECONDS = {
    "script": 60,
    "script_promote": 60,
    "script_qa": 90,
    "scenes": 90,
    "scenes_promote": 60,
    "scenes_qa": 120,
    "seo": 60,
    "seo_promote": 30,
    "seo_qa": 90,
    "whisper_timestamps": 30,
    "render": 600,     # overridden when target_duration_sec known
    "review": 5,
}


def _resolve_inside(job_dir: Path, rel: str) -> Path | None:
    """Return ``job_dir / rel`` if it stays inside ``job_dir``, else None.

    Defends the artifact endpoint against ``..`` path traversal.
    """
    try:
        candidate = (job_dir / rel).resolve()
        if str(candidate).startswith(str(job_dir.resolve())):
            return candidate
    except Exception:
        pass
    return None


def _isoformat_to_epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _stage_duration_seconds(stage: dict) -> float | None:
    start = _isoformat_to_epoch(stage.get("started_at"))
    end = _isoformat_to_epoch(stage.get("completed_at"))
    if start is None or end is None:
        return None
    return max(0.0, end - start)


def _effective_stage_status(stage: dict, current_stage: str | None) -> str:
    status = str(stage.get("status") or "pending")
    if status == "pending" and stage.get("name") == current_stage:
        return "in_progress"
    return status


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
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))

    # Render ETA scales with target_duration_sec when known.
    render_eta = _STAGE_ETA_SECONDS["render"]
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
        stage["status"] = _effective_stage_status(stage, current_stage)
        name = stage.get("name")
        cfg = _STAGE_ARTIFACTS.get(name, {})
        inputs = []
        for rel in cfg.get("input", []):
            p = _resolve_inside(job_dir, rel)
            inputs.append(
                {"path": rel, "exists": bool(p and p.exists()), "size": (p.stat().st_size if p and p.exists() else 0)}
            )
        outputs = []
        for rel in cfg.get("output", []):
            p = _resolve_inside(job_dir, rel)
            outputs.append(
                {"path": rel, "exists": bool(p and p.exists()), "size": (p.stat().st_size if p and p.exists() else 0)}
            )
        actual = _stage_duration_seconds(stage)
        eta = render_eta if name == "render" else _STAGE_ETA_SECONDS.get(name, 30)
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
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    target = _resolve_inside(job_dir, path)
    if target is None or not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")
    # FastAPI guesses media type from the path; this is enough for our
    # mix of .json / .md / .txt / .mp4 / .jpg.
    return FileResponse(target)


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
  }
  .rail-pill.active { color: #fff; background: #242424; border-color: #343434; }
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
  .stage-strip { margin-top: 16px; display: grid; grid-template-columns: repeat(11, minmax(16px, 1fr)); gap: 6px; }
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
  .caret { color: var(--muted-2); font-size: 20px; line-height: 1; transition: transform 150ms; }
  .step.open .caret { transform: rotate(90deg); }
  .step-body { display: none; padding: 0 14px 14px 55px; }
  .step.open .step-body { display: block; }
  .io-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .io-box { border: 1px solid var(--line); border-radius: 8px; background: #fafafa; padding: 10px; min-height: 68px; }
  .io-title { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .05em; font-weight: 750; margin-bottom: 7px; }
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
      <div class="rail-pill active">Jobs</div>
      <div class="rail-pill">AI</div>
      <div class="rail-pill">Files</div>
      <div class="rail-pill">Logs</div>
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
        <button class="primary-action" type="button" onclick="fetchJobs()">Refresh jobs</button>
      </div>
      <section class="kpis" id="kpis"></section>
      <section class="content-grid">
        <div class="panel jobs-panel">
          <div class="panel-head">
            <div class="panel-title">Job queue</div>
            <div class="panel-count" id="panel-count">0 jobs</div>
          </div>
          <div id="jobs-list"></div>
        </div>
        <div class="panel detail-panel" id="detail-panel">
          <div class="empty">Chọn một job bên trái để xem timeline, artifact và output.</div>
        </div>
      </section>
    </main>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
let SELECTED_ID = null;
let WS = null;
let OPEN_STAGES = new Set();
let EVENTS_OPEN = false;
let LAST_TIMELINE = null;
let LAST_TIMELINE_JSON = '';
let LAST_JOBS_JSON = '';
let TIMELINE_POLL_TIMER = null;
let WS_RETRY_TIMER = null;
let WS_RETRY_MS = 1000;

const STAGE_LABEL = {
  script: 'Script prompt',
  script_promote: 'Script JSON',
  script_qa: 'Script QA',
  scenes: 'Scenes prompt',
  scenes_promote: 'Scenes JSON',
  scenes_qa: 'Scenes QA',
  seo: 'SEO prompt',
  seo_promote: 'SEO JSON',
  seo_qa: 'SEO QA',
  render: 'Render video',
  review: 'Review page',
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
  return ts ? ts.slice(11, 19) : '-';
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
        selectJob(jobs[0].job_id);
        return;
      }
      if (WS) { try { WS.close(); } catch (e) {} WS = null; }
      const dot = document.getElementById('ws-dot');
      const label = document.getElementById('ws-label');
      dot.className = 'ws-dot off';
      label.textContent = 'disconnected';
      return;
    }
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
    const card = document.createElement('div');
    card.className = 'job-card' + (SELECTED_ID === j.job_id ? ' active' : '');
    card.innerHTML = `
      <div class="job-row">
        <div class="job-id">${escapeHtml(j.job_id)}</div>
        <div class="job-count"><b>${j.stages_done}</b>/${j.stages_total}</div>
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
    card.onclick = () => selectJob(j.job_id);
    root.appendChild(card);
  }
  if (!jobs.length) root.innerHTML = '<div class="empty" style="min-height:320px">Chưa có job nào.</div>';
}

function selectJob(jobId) {
  if (!jobId) return;
  SELECTED_ID = jobId;
  OPEN_STAGES = new Set();
  LAST_TIMELINE_JSON = ''; // force re-render on job switch
  document.querySelectorAll('.job-card').forEach(c => c.classList.remove('active'));
  fetchTimeline(jobId);
  reopenWs(jobId);
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
  return JSON.stringify(copy);
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
  const renderStage = t.stages.find(s => s.name === 'render');
  const videoReady = renderStage && (renderStage.outputs || []).some(o => o.path === 'video.mp4' && o.exists);
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
        </div>
        <div class="sum-right">
          <div class="sum-pct">${Math.round(t.percent)}<span class="pct-unit">%</span></div>
          <div class="sum-eta">${failed ? '<b style="color:var(--red)">Failed</b>' : allDone ? '<b>Completed</b>' : 'ETA ~' + fmtSec(t.remaining_eta_seconds)}</div>
        </div>
      </div>
      <div class="sum-bar ${barClass}"><div style="width:${t.percent}%"></div></div>
      <div class="stage-strip">${stageStrip}</div>
      <div class="sum-stagerow">
        <span>Current: <b>${escapeHtml(STAGE_LABEL[t.current_stage] || t.current_stage || '-')}</b></span>
        <span>${t.stages_done}/${t.stages_total} stages</span>
      </div>
    </div>
    <div class="section-title"><span>Pipeline stages</span><span>${t.stages_done}/${t.stages_total} done</span></div>
    <div class="timeline" id="timeline"></div>
    ${videoReady ? '<div id="final-mount"></div>' : ''}
    <div class="events ${EVENTS_OPEN ? 'open' : ''}" id="events-panel">
      <div class="events-head" onclick="toggleEvents()">
        <h3>WebSocket events</h3>
        <div><span class="events-count" id="events-count">live stream</span><span class="caret">›</span></div>
      </div>
      <div class="events-body" id="events"></div>
    </div>
  `;
  const tl = document.getElementById('timeline');
  t.stages.forEach((s, idx) => tl.appendChild(renderStep(t.job_id, s, idx)));
  if (videoReady) renderFinal(t);
}

// Render progress polling state
let RENDER_POLL_TIMER = null;

function startRenderProgressPolling(jobId) {
  if (RENDER_POLL_TIMER) return;
  RENDER_POLL_TIMER = setInterval(async () => {
    try {
      const r = await fetch('/jobs/' + encodeURIComponent(jobId) + '/stages/render/progress');
      if (!r.ok) return;
      const p = await r.json();
      const bar = document.getElementById('render-progress-bar');
      const pct = document.getElementById('render-progress-pct');
      const meta = document.getElementById('render-progress-meta');
      if (bar) bar.style.width = p.percent + '%';
      if (pct) pct.textContent = Math.round(p.percent) + '%';
      if (meta) {
        const parts = [];
        if (p.frame && p.total_frames) parts.push(p.frame + '/' + p.total_frames + ' frames');
        if (p.fps) parts.push(p.fps.toFixed(1) + ' fps');
        if (p.eta) parts.push('ETA ' + p.eta);
        meta.textContent = parts.join(' · ');
      }
      // Stop polling when render completes (step is no longer in_progress)
      const lastTl = LAST_TIMELINE;
      if (lastTl) {
        const rs = (lastTl.stages || []).find(s => s.name === 'render');
        if (rs && rs.status !== 'in_progress') {
          clearInterval(RENDER_POLL_TIMER);
          RENDER_POLL_TIMER = null;
        }
      }
    } catch(e) {}
  }, 2000);
}

function stopRenderProgressPolling() {
  if (RENDER_POLL_TIMER) { clearInterval(RENDER_POLL_TIMER); RENDER_POLL_TIMER = null; }
}

function renderStep(jobId, s, idx) {
  const el = document.createElement('div');
  el.className = 'step ' + s.status + (OPEN_STAGES.has(s.name) ? ' open' : '');
  const label = STAGE_LABEL[s.name] || s.name;
  const durTxt = s.actual_seconds !== null && s.actual_seconds !== undefined
    ? fmtSec(s.actual_seconds)
    : (s.status === 'completed' ? '' : '~' + fmtSec(s.eta_seconds));

  // Render-specific progress bar (shown when in_progress)
  const renderProgressHtml = (s.name === 'render' && s.status === 'in_progress') ? `
    <div style="margin:10px 0 4px;padding:0 2px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">
        <span style="font-size:12px;color:var(--muted);font-weight:600">Rendering…</span>
        <span id="render-progress-pct" style="font-size:13px;font-weight:700;color:var(--blue)">0%</span>
      </div>
      <div style="height:6px;border-radius:4px;background:var(--line-strong);overflow:hidden">
        <div id="render-progress-bar" style="height:100%;width:0%;background:var(--blue);border-radius:4px;transition:width 1s linear"></div>
      </div>
      <div id="render-progress-meta" style="margin-top:4px;font-size:11px;color:var(--muted-2)"></div>
    </div>` : '';

  el.innerHTML = `
    <div class="step-head">
      <span class="step-num">${s.status === 'completed' ? checkIcon() : s.status === 'failed' ? xIcon() : idx + 1}</span>
      <span class="step-label"><span class="name">${escapeHtml(label)}</span><span class="code">${escapeHtml(s.name)}</span></span>
      <span class="step-meta"><span class="pill ${s.status}">${statusText(s.status)}</span><span class="step-dur">${durTxt}</span><span class="caret">›</span></span>
    </div>
    <div class="step-body">
      ${renderProgressHtml}
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
  const url = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=' + encodeURIComponent(path);
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

async function renderFinal(t) {
  const mount = document.getElementById('final-mount');
  if (!mount) return;
  const jobId = t.job_id;
  const videoUrl = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=video.mp4';
  const thumbUrl = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=thumbnail.jpg';
  let seo = {};
  try {
    const sr = await fetch('/jobs/' + encodeURIComponent(jobId) + '/artifact?path=seo.json');
    if (sr.ok) seo = await sr.json();
  } catch (e) {}
  const tags = (seo.tags || []).map(t => `<span class="tag-chip">${escapeHtml(t)}</span>`).join('');
  mount.innerHTML = `
    <div class="final">
      <div class="final-title"><span>Final output</span><span class="badge">ready</span></div>
      <video src="${videoUrl}" controls></video>
      <div class="final-cols">
        <div>
          <img class="thumb-img" src="${thumbUrl}" alt="thumbnail">
          <a class="download-link" href="${videoUrl}" download>Download video.mp4</a>
        </div>
        <div>
          <div class="copy-row">
            <div class="crh"><div class="cr-label">Title <span class="count">${(seo.title || '').length}/100</span></div><button class="copy-btn" data-clip="${escapeHtml((seo.title||'').replace(/\\n/g,' '))}" onclick="copyText(this, this.dataset.clip)">copy</button></div>
            <div class="copy-content">${escapeHtml(seo.title || '(missing)')}</div>
          </div>
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
      setTimeout(() => fetchTimeline(jobId), 250);
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

function appendEvent(ev) {
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

document.getElementById('refresh-btn').onclick = fetchJobs;
fetchJobs();
setInterval(fetchJobs, 4000);
startTimelinePolling();
</script>
</body>
</html>
"""


@app.post("/jobs", status_code=201)
def post_job(
    payload: CreateJobRequest,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = jobs_root / payload.job_id
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
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return load_job(job_dir).to_dict()


@app.post("/jobs/{job_id}/advance")
def post_advance(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
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
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = promote_script_stage(job_dir, channel_path, payload.raw_response)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/scenes/run")
def post_run_scenes(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = promote_scenes_stage(job_dir, channel_path, payload.raw_response)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/seo/run")
def post_run_seo(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = promote_seo_stage(job_dir, channel_path, payload.raw_response)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/whisper_timestamps/run")
def post_run_whisper_timestamps(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
    progress_path = job_dir / "render_progress.json"
    if not progress_path.exists():
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
    try:
        import json as _json
        return _json.loads(progress_path.read_text())
    except Exception:
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}


@app.post("/jobs/{job_id}/stages/review/run")
def post_run_review(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_review_stage(job_dir)
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


class GenerateIdeasRequest(BaseModel):
    seed_topics: list[str] = []
    count: int = 10


@app.post("/channels/{channel_id}/ideas/generate", status_code=201)
async def post_generate_ideas(
    channel_id: str,
    req: GenerateIdeasRequest,
    inputs_root: Path = Depends(get_inputs_root),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    """Generate N idea JSON files via ChatGPT and save to inputs/ideas/<channel_id>/.

    Returns the list of ideas and their saved file paths relative to inputs_root.
    """
    channel_path = repo_root() / "configs" / channel_id / "channel.yaml"
    if not channel_path.exists():
        raise HTTPException(status_code=404, detail=f"No config for channel: {channel_id}")

    if not 1 <= req.count <= 50:
        raise HTTPException(status_code=422, detail="count must be between 1 and 50")

    try:
        ideas = await generate_ideas(
            channel_path=channel_path,
            chatgpt_fn=lambda msgs: client.run_session("chatgpt", msgs),
            seed_topics=req.seed_topics,
            count=req.count,
        )
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    paths = save_ideas(ideas, channel_id=channel_id, out_dir=inputs_root)
    rel_paths = [str(p.relative_to(inputs_root)) for p in paths]

    return {
        "channel_id": channel_id,
        "count": len(ideas),
        "ideas": ideas,
        "saved": rel_paths,
    }


@app.post("/jobs/{job_id}/stages/script/auto")
async def post_auto_script(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_script_stage(job_dir, channel_path, _one_shot_with_briefing(client, "chatgpt", "writing", channel_path, job_dir))
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/scenes/auto")
async def post_auto_scenes(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_scenes_stage(job_dir, channel_path, _one_shot_with_briefing(client, "chatgpt", "writing", channel_path, job_dir))
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/seo/auto")
async def post_auto_seo(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_seo_stage(job_dir, channel_path, _one_shot_with_briefing(client, "chatgpt", "writing", channel_path, job_dir))
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@app.post("/jobs/{job_id}/stages/script_qa/auto")
async def post_auto_script_qa(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
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
    job_dir = jobs_root / job_id
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
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    """End-to-end pipeline: script -> scenes -> seo -> render -> review.

    Runs the three auto stages (which hit browser-worker) followed by
    the render and review stages (pure local). Returns the list of
    completed stages on success. On partial failure returns HTTP 502
    (browser worker) or 409 (stage misuse) with the completed-so-far
    list in ``detail`` so the caller can resume from the same job.
    """
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    completed: list[dict] = []

    async def _record(stage_label: str, output_path: Path) -> None:
        completed.append(
            {"stage": stage_label, "output": str(output_path.relative_to(job_dir))}
        )

    # Resume from the current stage instead of always restarting from
    # script/script_promote. This lets callers continue a partially
    # completed pipeline by re-hitting /run-all.
    state = load_job(job_dir)
    stage_order = [s.name for s in state.stages]
    pending_stage = next((s.name for s in state.stages if s.status != "completed"), None)
    if pending_stage is None:
        return {"completed": completed, "state": state.to_dict()}
    if pending_stage not in stage_order:
        raise HTTPException(
            status_code=409,
            detail={"error": f"Unknown pending stage: {pending_stage}"},
        )

    # idea_research uses run_vidiq_scores (not the session tab API) so
    # run it BEFORE opening persistent tabs. This way a browser-worker
    # error on the ChatGPT/Claude briefing doesn't hide a gate block.
    start_idx = stage_order.index(pending_stage)
    remaining = set(stage_order[start_idx:])

    if "idea_research" in remaining:
        try:
            await _record(
                "idea_research",
                await auto_idea_research_stage(
                    job_dir, channel_path, client.run_vidiq_scores
                ),
            )
        except StageInputMissingError as exc:
            state = load_job(job_dir)
            raise HTTPException(
                status_code=409,
                detail={
                    "error": str(exc),
                    "completed": completed,
                    "stopped_at": state.current_stage,
                    "state": state.to_dict(),
                },
            ) from exc
        # Reload remaining from updated state.
        state = load_job(job_dir)
        new_pending = next((s.name for s in state.stages if s.status != "completed"), None)
        if new_pending is None:
            return {"completed": completed, "state": state.to_dict()}
        remaining = set(stage_order[stage_order.index(new_pending):])

    # Open ONE ChatGPT temp chat for the whole writing pipeline
    # (script_promote, scenes_promote, seo_promote) and ONE Claude
    # temp chat for the whole QA pipeline (script_qa, scenes_qa,
    # seo_qa). The tabs stay open across stages so the model carries
    # context, and we only close them when /run-all finishes.
    # Send the role+context+constraints briefing ONCE per tab; each
    # stage then only sends its short task message.
    from video_agent.orchestrator.briefing import build_initial_briefing
    from video_agent.utils.json_io import read_yaml as _read_yaml

    channel_config = _read_yaml(channel_path)
    async def _noop_close() -> None:
        return None

    chatgpt_sender = None
    qa_sender = None
    chatgpt_close = _noop_close
    qa_close = _noop_close

    async def _open_with_retry(site: str, attempts: int = 3):
        last_exc: BrowserClientError | None = None
        for idx in range(attempts):
            try:
                return await client.open_persistent_session(site)
            except BrowserClientError as exc:
                last_exc = exc
                # Retry only for transient 5xx worker failures.
                if exc.status_code < 500 or idx == attempts - 1:
                    raise
                await asyncio.sleep(1.0 + idx * 0.5)
        assert last_exc is not None
        raise last_exc

    async def _send_with_retry(sender, messages, attempts: int = 3) -> str:
        last_exc: BrowserClientError | None = None
        for idx in range(attempts):
            try:
                return await sender(list(messages))
            except BrowserClientError as exc:
                last_exc = exc
                if exc.status_code < 500 or idx == attempts - 1:
                    raise
                await asyncio.sleep(1.0 + idx * 0.5)
        assert last_exc is not None
        raise last_exc

    need_writing_tab = any(
        s in remaining for s in ("script", "script_promote", "scenes", "scenes_promote", "seo", "seo_promote")
    )
    need_qa_tab = any(s in remaining for s in ("script_qa", "scenes_qa", "seo_qa"))

    try:
        if need_writing_tab:
            chatgpt_sender, chatgpt_close = await _open_with_retry("chatgpt")
        if need_qa_tab:
            qa_sender, qa_close = await _open_with_retry("claude")

        async def chatgpt_fn(msgs):
            if chatgpt_sender is None:
                raise StageInputMissingError(
                    "ChatGPT session not available for writing stage."
                )
            return await _send_with_retry(chatgpt_sender, msgs)

        async def qa_fn(msgs):
            if qa_sender is None:
                raise StageInputMissingError(
                    "Claude session not available for QA stage."
                )
            return await _send_with_retry(qa_sender, msgs)

        # Brief each tab once before any task message.
        if need_writing_tab:
            await _send_with_retry(
                chatgpt_sender,
                [
                    build_initial_briefing(
                        channel_config,
                        kind="writing",
                        job_id=state.job_id,
                        channel_id=state.channel_id,
                    )
                ],
            )
        if need_qa_tab:
            await _send_with_retry(
                qa_sender,
                [
                    build_initial_briefing(
                        channel_config,
                        kind="qa",
                        job_id=state.job_id,
                        channel_id=state.channel_id,
                    )
                ],
            )

        if "script" in remaining or "script_promote" in remaining:
            await _record(
                "script_promote",
                await auto_script_stage(job_dir, channel_path, chatgpt_fn),
            )
        if "script_qa" in remaining:
            await _record(
                "script_qa",
                await auto_qa_with_rework(
                    "script", job_dir, channel_path, chatgpt_fn, qa_fn
                ),
            )
        if "scenes" in remaining or "scenes_promote" in remaining:
            await _record(
                "scenes_promote",
                await auto_scenes_stage(job_dir, channel_path, chatgpt_fn),
            )
        if "scenes_qa" in remaining:
            await _record(
                "scenes_qa",
                await auto_qa_with_rework(
                    "scenes", job_dir, channel_path, chatgpt_fn, qa_fn
                ),
            )
        if "seo" in remaining or "seo_promote" in remaining:
            await _record(
                "seo_promote",
                await auto_seo_stage(job_dir, channel_path, chatgpt_fn),
            )
        if "seo_qa" in remaining:
            await _record(
                "seo_qa",
                await auto_qa_with_rework(
                    "seo", job_dir, channel_path, chatgpt_fn, qa_fn
                ),
            )
        if "seo_vidiq" in remaining:
            await _record(
                "seo_vidiq",
                await auto_seo_vidiq_stage(
                    job_dir, channel_path, client.run_vidiq_scores
                ),
            )
        if "assets_chatgpt" in remaining:
            await _record(
                "assets_chatgpt",
                await auto_assets_chatgpt_stage(
                    job_dir, channel_path, client.generate_image
                ),
            )
        if "whisper_timestamps" in remaining:
            await _record("whisper_timestamps", run_whisper_timestamps_stage(job_dir))
        if "render" in remaining:
            await _record("render", run_render_stage(job_dir, channel_path))
        if "review" in remaining:
            await _record("review", run_review_stage(job_dir))
    except StageInputMissingError as exc:
        state = load_job(job_dir)
        raise HTTPException(
            status_code=409,
            detail={
                "error": str(exc),
                "completed": completed,
                "stopped_at": state.current_stage,
                "state": state.to_dict(),
            },
        ) from exc
    except BrowserClientError as exc:
        state = load_job(job_dir)
        http_exc = _handle_browser_client_error(exc)
        # Re-pack the detail to include progress.
        detail = (
            http_exc.detail if isinstance(http_exc.detail, dict) else {"error": http_exc.detail}
        )
        detail["completed"] = completed
        detail["stopped_at"] = state.current_stage
        detail["state"] = state.to_dict()
        raise HTTPException(status_code=http_exc.status_code, detail=detail) from exc
    finally:
        # Always close the persistent tabs so a failure never leaks
        # browser-runtime pages.
        try:
            await chatgpt_close()
        except Exception:
            pass
        try:
            await qa_close()
        except Exception:
            pass

    state = load_job(job_dir)
    return {"completed": completed, "state": state.to_dict()}


EVENTS_POLL_SECONDS = float(os.environ.get("EVENTS_POLL_SECONDS", "0.2"))


@app.websocket("/jobs/{job_id}/events")
async def ws_events(
    websocket: WebSocket,
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> None:
    job_dir = jobs_root / job_id
    if not (job_dir / "job.json").exists():
        await websocket.close(code=4404)
        return

    await websocket.accept()
    events_path = job_dir / EVENT_LOG
    offset = 0
    try:
        while True:
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
