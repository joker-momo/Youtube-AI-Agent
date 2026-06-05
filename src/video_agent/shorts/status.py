"""Summarize Shorts state for the jobs list / Shorts tab.

Also owns *orphaned-Short recovery*: a synchronous browser-ChatGPT build
(``short_builder.build_short``) that dies mid-stage during an app/worker restart
leaves ``short_status.json`` stuck at an active status (e.g. ``generating``)
forever. Nothing writes a terminal status because ``check_stop`` only raises an
in-process ``HTTPException``. The helpers here detect such orphans (active status
+ stale heartbeat + no live owning process) and transition them to a terminal
state so the UI releases the Short and the Stop button works.
"""
from __future__ import annotations

import datetime
import errno
import fcntl
import json
from pathlib import Path
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.manifest import write_short_status

# Statuses that mean "a build is (or claims to be) in flight". A short stuck in
# one of these with no live owner is an orphan candidate.
ACTIVE_SHORT_STATUSES = frozenset(
    {"generating", "in_progress", "rendering", "queued", "pending"}
)

# A short_status.json whose heartbeat is older than this (and has no live owner)
# is considered stale/orphaned. Render of a long short can exceed this, which is
# exactly why the ``owner_alive`` guard exists — the queue heartbeat keeps a live
# render's owner "alive" even while no per-stage heartbeat is written.
DEFAULT_STALE_SECONDS = 300


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_short_status_or_empty(long_job_dir: Path, short_id: str) -> dict[str, Any]:
    """Best-effort read of a short_status.json; ``{}`` if missing/unreadable."""
    return _read(paths.short_status_path(long_job_dir, short_id))


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _is_locked(path: Path) -> bool:
    """True if a *live* process currently holds an exclusive fcntl lock on path.

    fcntl/flock locks are released by the kernel when the owning process dies, so
    this is a reliable "is the owner still alive" probe for lock-based builds
    (the autopilot / prepare-drafts path holds ``.autopilot.lock``).
    """
    if not path.exists():
        return False
    try:
        fd = path.open("a+")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return True
            raise
        else:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            return False
    finally:
        fd.close()


def short_owner_is_alive(long_job_dir: Path, *, queue: Any = None) -> bool:
    """Best-effort: is a live process currently building shorts for this job?

    Two ownership signals, matching the two synchronous build paths:

    * ``.autopilot.lock`` held (fcntl) — the autopilot / prepare-drafts path.
    * a queue row ``running`` with a fresh heartbeat — the render-selected /
      confirm-render path runs under the queue worker, which calls
      ``touch_running`` on an interval. A dead worker stops refreshing it, so
      ``is_running_stale`` flips true.

    Pass ``queue`` (a ``JobQueue``) to enable the second check; without it only
    the lock is consulted.
    """
    if _is_locked(paths.autopilot_lock_path(long_job_dir)):
        return True
    if queue is not None:
        try:
            if not queue.is_running_stale(long_job_dir.name):
                # is_running_stale is True for non-running/absent rows too, so a
                # False here means a running row with a fresh heartbeat.
                for row in queue.active_jobs():
                    if row.get("job_id") == long_job_dir.name and row.get("status") == "running":
                        return True
        except Exception:
            pass
    return False


def short_heartbeat_age_seconds(
    long_job_dir: Path,
    short_id: str,
    status_doc: dict,
    *,
    now: datetime.datetime | None = None,
) -> float | None:
    """Seconds since the short last showed progress.

    Prefers explicit ``heartbeat_at`` / ``updated_at`` fields; falls back to the
    status file's mtime. Returns ``None`` if no timestamp can be derived.
    """
    now = now or _now_utc()
    for key in ("heartbeat_at", "updated_at"):
        dt = _parse_iso(status_doc.get(key))
        if dt is not None:
            return (now - dt).total_seconds()
    try:
        mtime = paths.short_status_path(long_job_dir, short_id).stat().st_mtime
    except OSError:
        return None
    return now.timestamp() - mtime


def recover_stale_short(
    long_job_dir: Path,
    short_id: str,
    status_doc: dict,
    *,
    owner_alive: bool,
    now: datetime.datetime | None = None,
    threshold: float = DEFAULT_STALE_SECONDS,
    require_stale: bool = True,
    terminal_status: str = "failed",
    reason: str | None = None,
) -> dict[str, Any] | None:
    """Transition an orphaned active short to a terminal status, persisting it.

    Returns the recovered status doc (already written to disk) when a transition
    happened, else ``None``. A short is recovered only when ALL hold:

    * its status is in :data:`ACTIVE_SHORT_STATUSES`;
    * no live process owns the build (``owner_alive`` is False);
    * (when ``require_stale``) its heartbeat is older than ``threshold``.

    ``require_stale=False`` is used by the explicit Stop path, which terminates
    an ownerless build immediately rather than waiting out the stale window.
    """
    status = status_doc.get("status")
    if status not in ACTIVE_SHORT_STATUSES:
        return None
    if owner_alive:
        return None
    now = now or _now_utc()
    if require_stale:
        age = short_heartbeat_age_seconds(long_job_dir, short_id, status_doc, now=now)
        if age is None or age < threshold:
            return None
        default_reason = f"orphaned_{status}_no_owner_stale_{int(age)}s"
    else:
        default_reason = f"stopped_orphaned_{status}_no_owner"

    recovered = dict(status_doc)
    now_iso = now.isoformat()
    recovered["status"] = terminal_status
    recovered["stop_requested"] = True
    recovered["recovered_from_stale"] = True
    recovered["recovery_reason"] = reason or default_reason
    recovered["recovered_at"] = now_iso
    recovered["updated_at"] = now_iso
    recovered["heartbeat_at"] = now_iso
    write_short_status(long_job_dir, short_id, recovered)
    return recovered


def _iter_short_ids(long_job_dir: Path) -> list[str]:
    import re

    shorts_root = paths.shorts_dir(long_job_dir)
    if not shorts_root.exists():
        return []
    short_dir_re = re.compile(r"^short-\d+(?:_.*)?$")
    ids: list[str] = []
    for child in sorted(shorts_root.iterdir()):
        if child.is_dir() and short_dir_re.match(child.name):
            ids.append(child.name)
    return ids


def force_terminate_orphaned_shorts(
    long_job_dir: Path,
    *,
    owner_alive: bool,
    now: datetime.datetime | None = None,
) -> list[str]:
    """Stop path: terminate every ownerless active short under this job.

    Used by ``POST /jobs/{id}/stop`` when no worker owns the task — writes a
    terminal status (``failed``, ``stop_requested=True``) so the UI releases
    shorts that no living process will ever finish. Returns the list of short
    ids that were transitioned. A no-op when ``owner_alive`` (the running build
    will observe ``.stop_requested`` via ``check_stop`` and fail itself).
    """
    if owner_alive:
        return []
    recovered: list[str] = []
    for short_id in _iter_short_ids(long_job_dir):
        doc = read_short_status_or_empty(long_job_dir, short_id)
        if not doc:
            continue
        result = recover_stale_short(
            long_job_dir,
            short_id,
            doc,
            owner_alive=False,
            now=now,
            require_stale=False,
            reason="stopped_by_operator_no_owner",
        )
        if result is not None:
            recovered.append(short_id)
    return recovered


def summarize_shorts(
    long_job_dir: Path,
    *,
    owner_alive: bool | None = None,
) -> dict[str, Any]:
    """Return {state, counts, label, shorts} for UI consumption.

    Reading the summary also opportunistically recovers orphaned shorts: any
    short stuck in an active status with no live owner and a stale heartbeat is
    transitioned to ``failed`` before counting, so the UI never reports a dead
    build as "running". Pass ``owner_alive`` to override the liveness probe
    (tests inject it; callers may pass a precomputed value).
    """
    manifest = _read(paths.manifest_path(long_job_dir))
    shorts = manifest.get("shorts") or []

    if owner_alive is None:
        try:
            from video_agent.orchestrator.queue import JobQueue

            queue = JobQueue(long_job_dir.parent / "queue.db")
        except Exception:
            queue = None
        owner_alive = short_owner_is_alive(long_job_dir, queue=queue)
    running = bool(owner_alive)

    # Dynamically enrich shorts in manifest summary with their title/hook
    enriched_shorts = []
    for s in shorts:
        s_copy = dict(s)
        short_id = s.get("short_id")
        if short_id:
            short_dir = paths.short_dir(long_job_dir, short_id)
            status_doc = read_short_status_or_empty(long_job_dir, short_id)
            # Recover an orphaned/stale "generating" short before we count it.
            recovered = recover_stale_short(
                long_job_dir, short_id, status_doc, owner_alive=bool(owner_alive)
            )
            if recovered is not None:
                status_doc = recovered
            seo_doc = _read(paths.resolve_short_json(short_dir, paths.SHORT_SEO_FILE))

            # Merge status doc fields if not present in manifest entry. Status
            # doc wins for the lifecycle "status" field so a recovered terminal
            # state overrides the stale manifest entry.
            for k, v in status_doc.items():
                if k not in s_copy or k == "status":
                    s_copy[k] = v

            # Populate title
            s_copy["title"] = seo_doc.get("title") or s.get("hook") or status_doc.get("hook") or ""
        enriched_shorts.append(s_copy)

    rendered = sum(1 for s in enriched_shorts if s.get("status") == "rendered")
    ready_for_render = sum(1 for s in enriched_shorts if s.get("status") == "ready_for_render")
    needs_review = sum(1 for s in enriched_shorts if s.get("status") == "needs_review")
    failed = sum(1 for s in enriched_shorts if s.get("status") == "failed")
    total = len(enriched_shorts)

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
        "shorts": enriched_shorts,
        "manifest": manifest,
    }
