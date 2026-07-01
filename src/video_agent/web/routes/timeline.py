"""Timeline and logs routes.

Extracted from ``_legacy.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from video_agent.contracts import EVENT_LOG, repo_root
from video_agent.orchestrator.stages import IDEA_FILE
from video_agent.orchestrator.stages.graphic_images import _wants_graphic
from video_agent.web.approval_flow import (
    APPROVAL_REQUIRED_STAGES,
    approval_block_for_current_stage,
    load_approvals,
)
from video_agent.web.routes._common import (
    _queue_status,
    _safe_job_dir,
    get_jobs_root,
)
from video_agent.web.run_all_pipeline import stop_request_path
from video_agent.web.timeline_helpers import (
    STAGE_ARTIFACTS,
    STAGE_ETA_SECONDS,
    effective_stage_status,
    job_has_in_progress_stage,
    resolve_inside,
    stage_duration_seconds,
)

router = APIRouter()


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
    shorts = list(summary.get("shorts") or [])
    # short_owner_is_alive's queue-row-running signal only proves the PARENT
    # run_all job is active SOMEWHERE — true for the whole idea_research..review
    # run, long before shorts autopilot (which fires only after "review"
    # completes) could plausibly have started. Trust "running" only once there
    # is actual evidence shorts work has begun: a manifest, enrolled shorts, or
    # the review stage already completed. Otherwise the dashboard shows
    # "Shorts Autopilot: in_progress" from minute 1 of every long-form run.
    review_completed = False
    try:
        job_state = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        review_completed = any(
            s.get("name") == "review" and s.get("status") == "completed"
            for s in job_state.get("stages", [])
        )
    except Exception:
        pass
    running = bool(summary.get("running")) and (
        manifest_exists or bool(shorts) or review_completed
    )
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

        # Live background-acquisition state from the short's status.json (merged
        # into the manifest entry by summarize_shorts). Lets the card stream
        # "scene X/N · <source>" while backgrounds are being resolved.
        stage_list = entry.get("stages") or []
        bg_stage = next((s for s in stage_list if s.get("name") == "background"), {})
        bg_status = bg_stage.get("status")
        background_live = {
            "status": bg_status,
            "current_scene": bg_stage.get("current_scene"),
            "total_scenes": bg_stage.get("total_scenes"),
            "last_scene_id": bg_stage.get("last_scene_id"),
            "last_source": bg_stage.get("last_source"),
            "per_scene": bg_stage.get("per_scene") or [],
        }
        bg_step_status = bg_status or ("completed" if rendered else ("skipped" if needs_review else "pending"))

        steps = [
            {"name": "idea", "status": "completed"},
            {"name": "script", "status": "completed"},
            {"name": "scenes", "status": "completed"},
            {"name": "source_map", "status": "completed"},
            {"name": "seo", "status": "completed"},
            {"name": "qa", "status": "completed" if qa == "PASS" or needs_review else "pending", "label": f"QA {qa}"},
            {"name": "background", "status": bg_step_status, "label": "Background"},
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
                "background_live": background_live,
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
    # state["current_stage"] is a single linear pointer that only advances inside
    # _apply_stage_completion, which is SKIPPED while the run executes under the
    # parallel DAG scheduler (post-scenes stages run as concurrent resource lanes,
    # see run_all_pipeline.py set_dag_mode). During a DAG run the pointer freezes
    # at whatever stage was current when DAG mode kicked in (e.g. "visual_spans")
    # and NEVER moves again — not while render/graphic_images/etc. are actually
    # in_progress, and not even once the job fully completes (an in_progress-only
    # fallback still returns the frozen pointer once nothing is in_progress
    # anymore). Derive current_stage from the stage list itself instead: the
    # LAST stage in pipeline order that isn't still "pending" is, by
    # definition, the furthest the run has actually reached — in_progress,
    # completed, or failed — which is correct for a live DAG run, a finished
    # job, and a linear (non-DAG) run alike.
    non_pending_stages = [
        s.get("name")
        for s in state.get("stages", [])
        if str(s.get("status") or "pending") != "pending"
    ]
    current_stage = (non_pending_stages[-1] if non_pending_stages else None) or state.get(
        "current_stage"
    )
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
        elif name == "graphic_images":
            # Per-image progress: count the graphic PNGs already on disk vs the
            # number of graphic-layout scenes that need one. PNG count is robust
            # across a resume (no double-count from re-emitted events).
            assets_dir = job_dir / "assets"
            done = (
                len(list(assets_dir.glob("graphic-*.png"))) if assets_dir.is_dir() else 0
            )
            total = 0
            for cand in (job_dir / "json" / "scenes.json", job_dir / "scenes.json"):
                try:
                    if cand.exists():
                        scenes_doc = json.loads(cand.read_text(encoding="utf-8"))
                        # _wants_graphic is the SAME predicate the graphic_images stage
                        # itself uses to decide which scenes get a card — reusing it
                        # keeps this count in sync as new layout types are added
                        # (previously hardcoded to only checklist/warning/quote/cta,
                        # which undercounted once stat/steps/myth/plate_map/etc. shipped
                        # and made "done" exceed "total", e.g. "Image 20/4").
                        total = sum(
                            1
                            for sc in (scenes_doc.get("scenes") or [])
                            if _wants_graphic(sc)
                        )
                        break
                except Exception:
                    total = 0
            if done or total:
                sub_progress = {
                    "kind": "graphic_images",
                    "done": done,
                    "total": total,
                    "label": f"Image {done}/{total or '?'} generated",
                }
        # NOTE: do NOT add a fast-changing (per-frame) render sub_progress here —
        # the dashboard's stableTimelineKey serializes the timeline to gate full
        # detail-panel re-renders, so a frame-by-frame label changes the key every
        # poll → full root.innerHTML rebuild every tick → visible UI flicker. Live
        # render % is already shown via the dashboard's dedicated render_progress
        # poller (targeted in-place DOM updates), so the render row stays live
        # without churning the whole panel.
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
        "current_stage": current_stage,
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
        # approval_blocked_by means "the job is paused, waiting on you" — that
        # can't be true while it is actively running, already finished, or
        # failed. approval_block_for_current_stage only compares stage ORDER,
        # so an approval-gated stage the job already advanced past (e.g. a run
        # started with enforce_approvals=False, which never flips the approval
        # flag to True but proceeds anyway) still reads as unapproved and gets
        # reported as blocking a job that plainly isn't blocked — both while
        # running AND after it has fully completed (bug:
        # "approval_blocked_by=idea_research" on a running AND on a completed job).
        "approval_blocked_by": (
            None
            if queue_status in ("failed", "completed") or current_stage_active
            else approval_block_for_current_stage(current_stage, approvals)
        ),
        "stop_requested": stop_requested,
    }


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
