"""Summarize Shorts state for the jobs list / Shorts tab."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_agent.shorts import paths


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def summarize_shorts(long_job_dir: Path) -> dict[str, Any]:
    """Return {state, counts, label, shorts} for UI consumption."""
    manifest = _read(paths.manifest_path(long_job_dir))
    running = paths.autopilot_lock_path(long_job_dir).exists()
    shorts = manifest.get("shorts") or []

    rendered = sum(1 for s in shorts if s.get("status") == "rendered")
    ready_for_render = sum(1 for s in shorts if s.get("status") == "ready_for_render")
    needs_review = sum(1 for s in shorts if s.get("status") == "needs_review")
    failed = sum(1 for s in shorts if s.get("status") == "failed")
    total = len(shorts)

    if running:
        state, label = "running", "running"
    elif not paths.manifest_path(long_job_dir).exists():
        state, label = "none", "none"
    else:
        run = _read(paths.autopilot_run_path(long_job_dir))
        state = run.get("status") or manifest.get("status") or "completed"
        if total == 0:
            label = "failed" if state == "failed" else "none"
        elif ready_for_render and not rendered and not needs_review and not failed:
            label = f"{ready_for_render} ready for render"
        elif ready_for_render and (needs_review or failed):
            label = f"{ready_for_render} ready · {needs_review + failed} warnings"
        elif needs_review and rendered:
            label = f"{rendered} rendered · {needs_review} needs review"
        elif needs_review:
            label = f"{needs_review} needs review"
        else:
            label = f"{rendered} rendered"

    return {
        "state": state,
        "counts": {
            "ready_for_render": ready_for_render,
            "rendered": rendered,
            "needs_review": needs_review,
            "failed": failed,
            "total": total,
        },
        "label": label,
        "running": running,
        "shorts": shorts,
        "manifest": manifest,
    }
