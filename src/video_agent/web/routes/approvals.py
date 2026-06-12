"""Approval-gate routes.

Extracted from ``_legacy.py``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from video_agent.orchestrator import load_job
from video_agent.orchestrator.stages import StageInputMissingError
from video_agent.web.approval_flow import (
    APPROVAL_REQUIRED_STAGES,
    approval_block_for_current_stage,
    load_approvals,
    reset_stage_for_regen,
    set_approval,
)

from video_agent.web.routes._common import (
    _safe_job_dir,
    get_jobs_root,
)

router = APIRouter()


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
