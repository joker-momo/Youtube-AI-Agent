"""Artifact serving routes.

Extracted from ``_legacy.py``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from video_agent.web.timeline_helpers import resolve_inside

from video_agent.web.routes._common import (
    _safe_job_dir,
    get_jobs_root,
)

router = APIRouter()


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
        # Backward/forward-compat fallback: data files (.json/.jsonl) live under a
        # ``json/`` subdir and deliverables under ``outputs/``, but older jobs (and
        # older shorts) stored them at the parent root. Try inserting the expected
        # subdir before the filename, and the reverse (stripping it). Works for both
        # top-level paths and nested ones like ``shorts/<sid>/short_script.json``
        # (bug-260 — the shorts stage timeline pointed json links at the short root).
        rel = path.replace("\\", "/")
        name = rel.rsplit("/", 1)[-1]
        parent = rel[: len(rel) - len(name)].rstrip("/")
        sub = "json" if (name.endswith(".json") or name.endswith(".jsonl")) else "outputs"
        segs = parent.split("/") if parent else []
        candidates = []
        if not segs or segs[-1] != sub:
            candidates.append("/".join([*segs, sub, name]))
        if segs and segs[-1] in ("json", "outputs"):
            candidates.append("/".join([*segs[:-1], name]))
        for cand in candidates:
            fallback = resolve_inside(job_dir, cand)
            if fallback and fallback.exists() and fallback.is_file():
                target = fallback
                break

    if target is None or not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")
    # FastAPI guesses media type from the path; this is enough for our
    # mix of .json / .md / .txt / .mp4 / .jpg.
    return FileResponse(target)


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
