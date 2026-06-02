from __future__ import annotations

import json
import os
import platform
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from video_agent.contracts import repo_root
from video_agent.storage.atomic import atomic_write_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _tail_lines(path: Path, limit: int = 20) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    return lines[-limit:]


def _tail_jsonl(path: Path, limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in _tail_lines(path, limit=limit):
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def recent_job_snapshots(jobs_root: Path, limit: int = 3) -> list[dict[str, Any]]:
    if not jobs_root.exists():
        return []
    dirs = [p for p in jobs_root.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    snapshots: list[dict[str, Any]] = []

    file_mappings = {
        "script.json": ["json/script.json", "script.json"],
        "scenes.json": ["json/scenes.json", "scenes.json"],
        "seo.json": ["json/seo.json", "seo.json"],
        "render_props.json": ["json/render_props.json", "render_props.json"],
        "video.mp4": ["outputs/video.mp4", "video.mp4"],
        "thumbnail.jpg": ["outputs/thumbnail.jpg", "thumbnail.jpg"],
    }

    for job_dir in dirs[:limit]:
        files = {}
        for name, candidates in file_mappings.items():
            for c in candidates:
                p = job_dir / c
                if p.exists():
                    files[name] = {"size": p.stat().st_size, "mtime": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}
                    break
        
        progress_path = job_dir / "json" / "render_progress.json"
        if not progress_path.exists():
            progress_path = job_dir / "render_progress.json"

        snapshots.append(
            {
                "job_dir": str(job_dir),
                "event_log_tail": _tail_jsonl(job_dir / "events.jsonl", limit=15),
                "render_progress": _read_json(progress_path),
                "files": files,
            }
        )
    return snapshots


@dataclass
class RunIncidentMonitor:
    command: str
    argv: list[str]
    jobs_root: Path
    interval_sec: float = 5.0

    def __post_init__(self) -> None:
        root = repo_root()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{stamp}-{os.getpid()}"
        self.run_dir = root / "logs" / "runs"
        self.incident_dir = root / "logs" / "incidents"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.incident_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path = self.run_dir / f"{self.run_id}.heartbeat.json"
        self.summary_path = self.run_dir / f"{self.run_id}.summary.json"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at = _now_iso()
        self._start_monotonic = time.monotonic()

    def start(self) -> None:
        self._write_heartbeat(status="running")
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"incident-monitor-{self.run_id}")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_sec):
            self._write_heartbeat(status="running")

    def _write_heartbeat(self, status: str) -> None:
        payload = {
            "run_id": self.run_id,
            "status": status,
            "started_at": self.started_at,
            "updated_at": _now_iso(),
            "elapsed_sec": round(time.monotonic() - self._start_monotonic, 2),
            "command": self.command,
            "argv": self.argv,
            "cwd": str(Path.cwd()),
            "jobs_root": str(self.jobs_root),
            "recent_jobs": recent_job_snapshots(self.jobs_root),
        }
        atomic_write_json(self.heartbeat_path, payload)

    def finish(self, ok: bool, error: BaseException | None = None) -> Path | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

        status = "ok" if ok else "error"
        self._write_heartbeat(status=status)
        summary = {
            "run_id": self.run_id,
            "status": status,
            "started_at": self.started_at,
            "ended_at": _now_iso(),
            "elapsed_sec": round(time.monotonic() - self._start_monotonic, 2),
            "command": self.command,
            "argv": self.argv,
            "cwd": str(Path.cwd()),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "recent_jobs": recent_job_snapshots(self.jobs_root),
        }
        if error is not None:
            summary["error"] = {
                "type": error.__class__.__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            }
        atomic_write_json(self.summary_path, summary)
        if ok:
            return None
        incident_path = self.incident_dir / f"{self.run_id}.incident.json"
        atomic_write_json(incident_path, summary)
        return incident_path
