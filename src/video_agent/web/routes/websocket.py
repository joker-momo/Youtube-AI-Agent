"""WebSocket event-stream route.

Extracted from ``_legacy.py``.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from video_agent.contracts import EVENT_LOG

from video_agent.web.routes._common import (
    _safe_job_dir,
    get_jobs_root,
)

router = APIRouter()

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
