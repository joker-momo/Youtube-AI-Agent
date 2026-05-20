from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from video_agent.contracts import EVENT_LOG
from video_agent.orchestrator import (
    JobAlreadyExistsError,
    JobNotFoundError,
    advance,
    create_job,
    load_job,
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
    auto_qa_with_rework,
    auto_scenes_qa_stage,
    auto_scenes_stage,
    auto_script_qa_stage,
    auto_script_stage,
    auto_seo_qa_stage,
    auto_seo_stage,
    promote_qa_stage,
    promote_scenes_stage,
    promote_seo_stage,
    promote_script_stage,
    run_render_stage,
    run_review_stage,
    run_scenes_stage,
    run_seo_stage,
    run_script_stage,
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
            stages = payload.get("stages", [])
            done = sum(1 for s in stages if s.get("status") == "completed")
            total = len(stages)
            in_progress = [
                s["name"] for s in stages if s.get("status") == "in_progress"
            ]
            items.append(
                {
                    "job_id": payload.get("job_id"),
                    "channel_id": payload.get("channel_id"),
                    "current_stage": payload.get("current_stage"),
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
    for stage in state.get("stages", []):
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
  * { box-sizing: border-box; }
  body { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #0c0c0e; color: #e6e6e6; }
  header { padding: 12px 20px; background: #14141a; border-bottom: 1px solid #23232a; display: flex; justify-content: space-between; align-items: center; }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; }
  header .meta { font-size: 12px; color: #888; }
  main { display: grid; grid-template-columns: 320px 1fr; height: calc(100vh - 49px); }
  #jobs-panel { border-right: 1px solid #23232a; overflow-y: auto; padding: 10px; }
  #detail-panel { padding: 16px 22px; overflow-y: auto; }
  .job-card { padding: 10px 12px; margin-bottom: 8px; background: #15151b; border: 1px solid #23232a; border-radius: 6px; cursor: pointer; }
  .job-card:hover { background: #1c1c24; }
  .job-card.active { border-color: #4a78d6; background: #1a2030; }
  .job-id { font-size: 12px; font-weight: 600; word-break: break-all; }
  .job-meta { font-size: 11px; color: #777; margin-top: 4px; display: flex; justify-content: space-between; }
  .progress-bar { height: 4px; background: #23232a; margin-top: 6px; border-radius: 2px; overflow: hidden; }
  .progress-fill { height: 100%; background: #4a78d6; transition: width 200ms; }
  .progress-fill.completed { background: #4ad67a; }
  .progress-fill.failed { background: #d65d4a; }
  h2 { font-size: 14px; color: #aaa; margin: 18px 0 10px; }
  h2:first-child { margin-top: 0; }
  .summary { background: #15151b; padding: 14px 16px; border-radius: 6px; margin-bottom: 16px; }
  .summary .top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .summary .pct { font-size: 26px; font-weight: 600; color: #4a78d6; }
  .summary .eta { font-size: 12px; color: #888; }
  .big-bar { height: 8px; background: #23232a; border-radius: 4px; overflow: hidden; }
  .big-fill { height: 100%; background: linear-gradient(90deg, #4a78d6, #4ad67a); transition: width 300ms; }
  .timeline { display: flex; flex-direction: column; gap: 6px; }
  .step { background: #15151b; border: 1px solid #23232a; border-radius: 6px; overflow: hidden; }
  .step .head { padding: 10px 14px; display: flex; align-items: center; cursor: pointer; gap: 12px; }
  .step .head:hover { background: #1a1a22; }
  .step .num { width: 22px; height: 22px; border-radius: 50%; background: #23232a; color: #aaa; display: inline-flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; }
  .step.completed .num { background: #4ad67a; color: #0c0c0e; }
  .step.in_progress .num { background: #4a78d6; color: #0c0c0e; animation: pulse 1.4s infinite; }
  .step.failed .num { background: #d65d4a; color: #fff; }
  @keyframes pulse { 0%,100% { transform: scale(1); opacity:1; } 50% { transform: scale(1.15); opacity:0.7;} }
  .step .name { flex: 1; font-size: 13px; font-weight: 500; }
  .step .badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #23232a; color: #aaa; }
  .step.completed .badge { background: #1f3522; color: #4ad67a; }
  .step.in_progress .badge { background: #1a2030; color: #4a78d6; }
  .step.failed .badge { background: #36211e; color: #d65d4a; }
  .step .dur { font-size: 11px; color: #555; min-width: 50px; text-align: right; }
  .step .body { display: none; padding: 0 14px 14px; border-top: 1px solid #23232a; }
  .step.open .body { display: block; }
  .artgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }
  .artbox { background: #0a0a0d; border: 1px solid #23232a; border-radius: 4px; padding: 8px 10px; min-height: 60px; }
  .artbox .lbl { font-size: 10px; color: #555; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
  .artbox .file { font-size: 11px; color: #4a78d6; cursor: pointer; padding: 2px 0; }
  .artbox .file:hover { text-decoration: underline; }
  .artbox .file.missing { color: #555; cursor: default; }
  .artbox .file.missing:hover { text-decoration: none; }
  pre.dump { background: #0a0a0d; padding: 10px; border-radius: 4px; max-height: 300px; overflow: auto; font-size: 11px; color: #c9c9c9; white-space: pre-wrap; word-break: break-word; margin-top: 6px; }
  .events { background: #0a0a0d; padding: 10px; border-radius: 4px; max-height: 240px; overflow-y: auto; font-size: 11px; }
  .events .ev { padding: 3px 0; border-bottom: 1px dotted #1a1a22; }
  .ev-ts { color: #555; margin-right: 8px; }
  .ev-kind { color: #4a78d6; margin-right: 6px; font-weight: 600; }
  .ev-stage { color: #4ad67a; }
  .ev-kind.JOB_COMPLETED { color: #4ad67a; }
  .ev-kind.STAGE_FAILED, .ev-kind.STAGE_NEEDS_REWORK { color: #d65d4a; }
  .empty { color: #555; padding: 30px; text-align: center; }
  .ws-indicator { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; vertical-align: middle; }
  .ws-indicator.live { background: #4ad67a; box-shadow: 0 0 4px #4ad67a; }
  .ws-indicator.off { background: #555; }
  .final-block { background: linear-gradient(180deg, #14141a, #15151b); border: 1px solid #2a4a2e; border-radius: 8px; padding: 18px; margin-top: 16px; }
  .final-block video { width: 100%; max-height: 480px; background: #000; border-radius: 4px; }
  .final-block .thumb { display: inline-block; vertical-align: top; margin-right: 14px; }
  .final-block .thumb img { width: 240px; height: auto; border-radius: 4px; }
  .final-block .meta-line { font-size: 12px; color: #aaa; margin-bottom: 6px; }
  .final-block .copybox { background: #0a0a0d; padding: 10px; border-radius: 4px; font-size: 12px; margin: 6px 0; position: relative; }
  .final-block .copybtn { position: absolute; top: 6px; right: 6px; font-size: 10px; padding: 2px 8px; background: #23232a; color: #aaa; border: none; border-radius: 3px; cursor: pointer; }
  .final-block .copybtn:hover { background: #2c2c34; }
  .final-block .tag { display: inline-block; background: #1a2030; color: #4a78d6; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin: 2px 4px 2px 0; }
</style>
</head>
<body>
<header>
  <h1>Video Agent Dashboard</h1>
  <div class="meta"><span class="ws-indicator off" id="ws-dot"></span><span id="ws-label">disconnected</span> · <span id="job-count">0</span> jobs</div>
</header>
<main>
  <div id="jobs-panel">
    <div id="jobs-list"></div>
  </div>
  <div id="detail-panel">
    <div class="empty">Chọn 1 job bên trái để xem chi tiết từng bước.</div>
  </div>
</main>
<script>
let SELECTED_ID = null;
let WS = null;
let OPEN_STAGES = new Set();

const STAGE_LABEL = {
  script: '1. Script — sinh prompt cho ChatGPT',
  script_promote: '2. Script promote — nhận + validate JSON',
  script_qa: '3. Script QA — Gemini review',
  scenes: '4. Scenes — sinh prompt scenes',
  scenes_promote: '5. Scenes promote — nhận + validate JSON',
  scenes_qa: '6. Scenes QA — Gemini review',
  seo: '7. SEO — sinh prompt SEO',
  seo_promote: '8. SEO promote — nhận + validate JSON',
  seo_qa: '9. SEO QA — Gemini review',
  render: '10. Render — assets + TTS + Remotion → video.mp4',
  review: '11. Review — operator_review.html',
};

function fmtSec(s) {
  if (s === null || s === undefined) return '';
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + 's';
  return Math.floor(s/60) + 'm ' + (s%60) + 's';
}

async function fetchJobs() {
  try {
    const r = await fetch('/jobs');
    const d = await r.json();
    document.getElementById('job-count').textContent = d.count;
    renderJobsList(d.jobs);
    if (SELECTED_ID) fetchTimeline(SELECTED_ID);
  } catch (e) { console.error(e); }
}

function renderJobsList(jobs) {
  const root = document.getElementById('jobs-list');
  root.innerHTML = '';
  for (const j of jobs) {
    const pct = j.stages_total ? (100 * j.stages_done / j.stages_total) : 0;
    const failed = (j.stages || []).some(s => s.status === 'failed');
    const allDone = j.stages_total > 0 && j.stages_done === j.stages_total;
    const fillCls = allDone ? 'completed' : (failed ? 'failed' : '');
    const card = document.createElement('div');
    card.className = 'job-card' + (SELECTED_ID === j.job_id ? ' active' : '');
    card.innerHTML = `
      <div class="job-id">${j.job_id}</div>
      <div class="job-meta">
        <span>${j.current_stage}</span>
        <span>${j.stages_done}/${j.stages_total}</span>
      </div>
      <div class="progress-bar"><div class="progress-fill ${fillCls}" style="width:${pct}%"></div></div>
    `;
    card.onclick = () => selectJob(j.job_id);
    root.appendChild(card);
  }
}

function selectJob(jobId) {
  SELECTED_ID = jobId;
  OPEN_STAGES = new Set();
  document.querySelectorAll('.job-card').forEach(c => c.classList.remove('active'));
  fetchTimeline(jobId);
  reopenWs(jobId);
}

async function fetchTimeline(jobId) {
  try {
    const r = await fetch('/jobs/' + encodeURIComponent(jobId) + '/timeline');
    if (!r.ok) return;
    const t = await r.json();
    renderTimeline(t);
  } catch (e) { console.error(e); }
}

function renderTimeline(t) {
  const root = document.getElementById('detail-panel');
  const allDone = t.stages_total > 0 && t.stages_done === t.stages_total;
  const renderStage = t.stages.find(s => s.name === 'render');
  const videoReady = renderStage && (renderStage.outputs || []).some(o => o.path === 'video.mp4' && o.exists);
  root.innerHTML = `
    <div class="summary">
      <div class="top">
        <div>
          <div style="font-size:14px;font-weight:600;color:#fff">${t.job_id}</div>
          <div style="font-size:11px;color:#666">${t.channel_id} · cập nhật ${(t.updated_at||'').slice(11,19)}</div>
        </div>
        <div style="text-align:right">
          <div class="pct">${t.percent}%</div>
          <div class="eta">${allDone ? 'Hoàn thành' : 'ETA còn ~' + fmtSec(t.remaining_eta_seconds)}</div>
        </div>
      </div>
      <div class="big-bar"><div class="big-fill" style="width:${t.percent}%"></div></div>
    </div>
    <h2>Các bước</h2>
    <div class="timeline" id="timeline"></div>
    ${videoReady ? '<div id="final-mount"></div>' : ''}
    <h2>Events</h2>
    <div class="events" id="events"></div>
  `;
  const tl = document.getElementById('timeline');
  t.stages.forEach((s, idx) => tl.appendChild(renderStep(t.job_id, s, idx)));
  if (videoReady) renderFinal(t);
}

function renderStep(jobId, s, idx) {
  const el = document.createElement('div');
  el.className = 'step ' + s.status + (OPEN_STAGES.has(s.name) ? ' open' : '');
  const label = STAGE_LABEL[s.name] || s.name;
  const durTxt = s.actual_seconds !== null && s.actual_seconds !== undefined
    ? fmtSec(s.actual_seconds)
    : (s.status === 'completed' ? '' : '~' + fmtSec(s.eta_seconds));
  el.innerHTML = `
    <div class="head">
      <span class="num">${idx+1}</span>
      <span class="name">${label}</span>
      <span class="badge">${s.status}</span>
      <span class="dur">${durTxt}</span>
    </div>
    <div class="body">
      <div class="artgrid">
        <div class="artbox">
          <div class="lbl">INPUT</div>
          ${(s.inputs||[]).map(i => renderFile(jobId, i)).join('') || '<div style="color:#555;font-size:11px">(không có)</div>'}
        </div>
        <div class="artbox">
          <div class="lbl">OUTPUT</div>
          ${(s.outputs||[]).map(o => renderFile(jobId, o)).join('') || '<div style="color:#555;font-size:11px">(chưa có)</div>'}
        </div>
      </div>
      <div class="preview-mount" id="preview-${s.name}"></div>
    </div>
  `;
  el.querySelector('.head').onclick = () => {
    el.classList.toggle('open');
    if (el.classList.contains('open')) OPEN_STAGES.add(s.name);
    else OPEN_STAGES.delete(s.name);
  };
  return el;
}

function renderFile(jobId, f) {
  const cls = f.exists ? 'file' : 'file missing';
  const sz = f.exists ? ' (' + (f.size < 1024 ? f.size + 'B' : Math.round(f.size/1024) + 'KB') + ')' : ' (chưa có)';
  const onclick = f.exists ? `onclick="previewArtifact('${jobId}','${f.path.replace(/'/g,"\'")}')"` : '';
  return `<div class="${cls}" ${onclick}>📄 ${f.path}${sz}</div>`;
}

async function previewArtifact(jobId, path) {
  // Find the step that owns this artifact to mount the preview under.
  let mount = null;
  document.querySelectorAll('.preview-mount').forEach(m => {
    if (m.parentElement.parentElement.innerHTML.includes(path)) mount = m;
  });
  if (!mount) return;
  const url = '/jobs/' + encodeURIComponent(jobId) + '/artifact?path=' + encodeURIComponent(path);
  if (/\.(png|jpe?g|gif|webp)$/i.test(path)) {
    mount.innerHTML = `<img src="${url}" style="max-width:100%;border-radius:4px;margin-top:8px">`;
    return;
  }
  if (/\.mp4$/i.test(path)) {
    mount.innerHTML = `<video src="${url}" controls style="width:100%;margin-top:8px;border-radius:4px"></video>`;
    return;
  }
  try {
    const r = await fetch(url);
    const txt = await r.text();
    let formatted = txt;
    if (/\.json$/i.test(path)) {
      try { formatted = JSON.stringify(JSON.parse(txt), null, 2); } catch (e) {}
    }
    mount.innerHTML = `<pre class="dump">${escapeHtml(formatted.slice(0, 8000))}${txt.length > 8000 ? '\n…(truncated)' : ''}</pre>`;
  } catch (e) {
    mount.innerHTML = `<div style="color:#d65d4a;font-size:11px;margin-top:6px">${e.message}</div>`;
  }
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
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
  const tags = (seo.tags || []).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('');
  mount.innerHTML = `
    <div class="final-block">
      <h2 style="margin-top:0;color:#4ad67a">🎬 Video sẵn sàng đăng YouTube</h2>
      <video src="${videoUrl}" controls></video>
      <div style="margin-top:14px">
        <div class="thumb">
          <div class="meta-line">Thumbnail:</div>
          <img src="${thumbUrl}" alt="thumbnail">
        </div>
        <div style="display:inline-block;width:calc(100% - 270px);vertical-align:top">
          <div class="meta-line">Title (${(seo.title||'').length} chars):</div>
          <div class="copybox">
            <button class="copybtn" onclick="copyText(this,'${(seo.title||'').replace(/'/g,"\\'").replace(/\n/g,' ')}')">copy</button>
            ${escapeHtml(seo.title || '(missing)')}
          </div>
          <div class="meta-line">Description (${(seo.description||'').length} chars):</div>
          <div class="copybox" style="max-height:200px;overflow:auto">
            <button class="copybtn" onclick="copyText(this, document.getElementById('seo-desc').innerText)">copy</button>
            <span id="seo-desc">${escapeHtml(seo.description || '(missing)')}</span>
          </div>
          <div class="meta-line">Tags (${(seo.tags||[]).length}):</div>
          <div class="copybox">
            <button class="copybtn" onclick="copyText(this, '${(seo.tags||[]).join(', ').replace(/'/g,"\\'")}')">copy</button>
            ${tags || '(missing)'}
          </div>
          <div class="meta-line">Language: ${seo.language || '?'} · AI disclosure: ${seo.ai_disclosure ? 'yes' : 'no'}</div>
          <a href="${videoUrl}" download style="display:inline-block;margin-top:8px;color:#4a78d6;font-size:12px">⬇️ tải video.mp4</a>
        </div>
      </div>
    </div>
  `;
}

function copyText(btn, text) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'copied!';
    setTimeout(() => btn.textContent = orig, 1200);
  });
}

function reopenWs(jobId) {
  if (WS) { try { WS.close(); } catch (e) {} WS = null; }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  WS = new WebSocket(`${proto}//${location.host}/jobs/${jobId}/events`);
  const dot = document.getElementById('ws-dot');
  const label = document.getElementById('ws-label');
  WS.onopen = () => { dot.className = 'ws-indicator live'; label.textContent = 'live'; };
  WS.onclose = () => { dot.className = 'ws-indicator off'; label.textContent = 'disconnected'; };
  WS.onerror = () => { dot.className = 'ws-indicator off'; label.textContent = 'error'; };
  WS.onmessage = (msg) => {
    try {
      const ev = JSON.parse(msg.data);
      appendEvent(ev);
      setTimeout(() => fetchTimeline(jobId), 250);
    } catch (e) {}
  };
}

function appendEvent(ev) {
  const root = document.getElementById('events');
  if (!root) return;
  const div = document.createElement('div');
  div.className = 'ev';
  const ts = (ev.ts || '').slice(11, 19);
  const stage = (ev.data && ev.data.stage) ? ev.data.stage : '';
  div.innerHTML = `<span class="ev-ts">${ts}</span><span class="ev-kind ${ev.event}">${ev.event}</span><span class="ev-stage">${stage}</span>`;
  root.appendChild(div);
  root.scrollTop = root.scrollHeight;
}

fetchJobs();
setInterval(fetchJobs, 4000);
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
            _one_shot_with_briefing(client, "gemini", "qa", channel_path, job_dir),
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
            _one_shot_with_briefing(client, "gemini", "qa", channel_path, job_dir),
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
            _one_shot_with_briefing(client, "gemini", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


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

    # Open ONE ChatGPT temp chat for the whole writing pipeline
    # (script_promote, scenes_promote, seo_promote) and ONE Gemini
    # temp chat for the whole QA pipeline (script_qa, scenes_qa,
    # seo_qa). The tabs stay open across stages so the model carries
    # context, and we only close them when /run-all finishes.
    # Send the role+context+constraints briefing ONCE per tab; each
    # stage then only sends its short task message.
    from video_agent.orchestrator.briefing import build_initial_briefing
    from video_agent.utils.json_io import read_yaml as _read_yaml

    channel_config = _read_yaml(channel_path)
    state = load_job(job_dir)

    async def _noop_close() -> None:
        return None

    chatgpt_sender = None
    gemini_sender = None
    chatgpt_close = _noop_close
    gemini_close = _noop_close
    try:
        chatgpt_sender, chatgpt_close = await client.open_persistent_session("chatgpt")
        gemini_sender, gemini_close = await client.open_persistent_session("gemini")

        async def chatgpt_fn(msgs):
            return await chatgpt_sender(list(msgs))

        async def gemini_fn(msgs):
            return await gemini_sender(list(msgs))

        # Brief each tab once before any task message.
        await chatgpt_sender(
            [
                build_initial_briefing(
                    channel_config,
                    kind="writing",
                    job_id=state.job_id,
                    channel_id=state.channel_id,
                )
            ]
        )
        await gemini_sender(
            [
                build_initial_briefing(
                    channel_config,
                    kind="qa",
                    job_id=state.job_id,
                    channel_id=state.channel_id,
                )
            ]
        )
        await _record(
            "script_promote",
            await auto_script_stage(job_dir, channel_path, chatgpt_fn),
        )
        await _record(
            "script_qa",
            await auto_qa_with_rework(
                "script", job_dir, channel_path, chatgpt_fn, gemini_fn
            ),
        )
        await _record(
            "scenes_promote",
            await auto_scenes_stage(job_dir, channel_path, chatgpt_fn),
        )
        await _record(
            "scenes_qa",
            await auto_qa_with_rework(
                "scenes", job_dir, channel_path, chatgpt_fn, gemini_fn
            ),
        )
        await _record(
            "seo_promote",
            await auto_seo_stage(job_dir, channel_path, chatgpt_fn),
        )
        await _record(
            "seo_qa",
            await auto_qa_with_rework(
                "seo", job_dir, channel_path, chatgpt_fn, gemini_fn
            ),
        )
        await _record("render", run_render_stage(job_dir, channel_path))
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
            await gemini_close()
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
