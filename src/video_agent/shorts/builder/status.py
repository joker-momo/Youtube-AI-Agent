"""Stage status helpers extracted from short_builder."""
from __future__ import annotations

import datetime
from typing import Any


def _update_short_stage(status: dict[str, Any], stage_name: str, new_status: str, *, now_str: str | None = None, **kwargs) -> None:
    now_str = now_str or datetime.datetime.now(datetime.timezone.utc).isoformat()
    for s in status["stages"]:
        if s["name"] != stage_name:
            continue

        previous_status = str(s.get("status") or "pending")
        s["status"] = new_status

        if new_status == "pending":
            s["started_at"] = None
            s["completed_at"] = None
            s["actual_seconds"] = None
            s.pop("error", None)
            s.pop("qa_verdict", None)
        elif new_status == "in_progress":
            if previous_status != "in_progress" or not s.get("started_at"):
                s["started_at"] = now_str
            s["completed_at"] = None
            s["actual_seconds"] = None
            s.pop("error", None)
            s.pop("qa_verdict", None)
        elif new_status in ("completed", "failed", "skipped"):
            if not s.get("started_at"):
                s["started_at"] = now_str
            s["completed_at"] = now_str
            try:
                from datetime import datetime as dt
                t_start = dt.fromisoformat(str(s["started_at"]).replace("Z", "+00:00"))
                t_end = dt.fromisoformat(now_str.replace("Z", "+00:00"))
                s["actual_seconds"] = max(0, int((t_end - t_start).total_seconds()))
            except Exception:
                s["actual_seconds"] = 1

        for k, v in kwargs.items():
            s[k] = v
        break

    status["updated_at"] = now_str
    status["heartbeat_at"] = now_str
