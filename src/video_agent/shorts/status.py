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
    needs_review = sum(1 for s in shorts if s.get("status") == "needs_review")
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
        elif needs_review and rendered:
            label = f"{rendered} rendered · {needs_review} needs review"
        elif needs_review:
            label = f"{needs_review} needs review"
        else:
            label = f"{rendered} rendered"

    return {
        "state": state,
        "counts": {"rendered": rendered, "needs_review": needs_review, "total": total},
        "label": label,
        "running": running,
        "shorts": shorts,
        "manifest": manifest,
    }
