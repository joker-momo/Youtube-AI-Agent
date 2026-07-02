"""Sequential Shorts Autopilot runner.

Owns ``jobs/<long_job_id>/shorts/`` end to end: legacy detection, idempotency,
source validation/snapshot, planning, and the sequential per-short pipeline
(generate → QA → audio → mix → render). One failed Short never blocks the next.

The heavy per-short pipeline and planner are injected (``plan_fn`` /
``build_short_fn``) so the orchestration is unit-testable without a browser,
Kokoro, or Remotion. The real implementations are wired in as defaults.
"""
from __future__ import annotations

import errno
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import legacy_cleanup, manifest, paths
from video_agent.shorts.idea_generator import resolve_long_job_artifact

REQUIRED_SOURCE_FILES = ("script.json", "scenes.json", "seo.json", "video.mp4")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ap_cfg(channel_config: dict) -> dict:
    return ((channel_config.get("shorts") or {}).get("autopilot")) or {}


def _default_plan_fn(long_job_dir: Path, channel_config: dict, requested_count: int | None = None) -> dict:
    from video_agent.shorts.planner import plan_shorts_from_long_video

    return plan_shorts_from_long_video(long_job_dir, channel_config, requested_count)


def _default_build_short_fn(
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    *,
    require_render_confirmation: bool = False,
) -> dict:
    from video_agent.shorts.short_builder import build_short

    return build_short(
        long_job_dir,
        short_plan,
        channel_config,
        require_render_confirmation=require_render_confirmation,
    )


def aggregate_prepare_status(short_statuses: list[dict]) -> str:
    if not short_statuses:
        return "failed"
    ready = [s for s in short_statuses if s.get("status") == "ready_for_render"]
    review = [s for s in short_statuses if s.get("status") == "needs_review"]
    failed = [s for s in short_statuses if s.get("status") == "failed"]
    if ready and not review and not failed:
        return "drafts_ready"
    if ready and (review or failed):
        return "completed_with_warnings"
    if review and not ready:
        return "needs_review"
    return "failed"


def _call_build_short_fn(
    build_short_fn: Callable[..., dict],
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    *,
    require_render_confirmation: bool,
) -> dict:
    try:
        return build_short_fn(
            long_job_dir,
            short_plan,
            channel_config,
            require_render_confirmation=require_render_confirmation,
        ) or {}
    except TypeError:
        if require_render_confirmation:
            raise
        return build_short_fn(long_job_dir, short_plan, channel_config) or {}


def _write_source_snapshot(long_job_dir: Path) -> None:
    from video_agent.storage.atomic import atomic_write_json

    snap: dict[str, Any] = {"captured_at": _now(), "files": {}}
    for name in REQUIRED_SOURCE_FILES:
        p = resolve_long_job_artifact(long_job_dir, name)
        exists = p is not None
        snap["files"][name] = {"exists": exists, "size": p.stat().st_size if exists else 0}
    atomic_write_json(paths.source_snapshot_path(long_job_dir), snap)


def run_shorts_autopilot(
    long_job_dir: Path,
    channel_config: dict,
    *,
    force: bool = False,
    trigger: str = "after_review_passed",
    target_short_id: str | None = None,
    plan_fn: Callable[..., dict] | None = None,
    build_short_fn: Callable[..., dict] | None = None,
    require_render_confirmation: bool = False,
) -> dict:
    plan_fn = plan_fn or _default_plan_fn
    build_short_fn = build_short_fn or _default_build_short_fn
    ap = _ap_cfg(channel_config)
    started_at = _now()

    shorts_root = paths.shorts_dir(long_job_dir)
    manifest_exists = paths.manifest_path(long_job_dir).exists()

    # 1. Acquire exclusive lock FIRST (fcntl LOCK_EX | LOCK_NB) so a second
    #    concurrent autopilot run refuses cleanly instead of racing the
    #    idempotency/legacy checks.
    shorts_root.mkdir(parents=True, exist_ok=True)
    lock = paths.autopilot_lock_path(long_job_dir)
    lock_fd = lock.open("w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_fd.close()
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return {
                "status": "locked",
                "message": f"Another Shorts autopilot run already holds {lock.name}.",
            }
        raise
    lock_fd.write(json.dumps({"pid_started_at": started_at, "trigger": trigger}))
    lock_fd.flush()

    try:
        # 2. Idempotency — skip when finished shorts already exist.
        if (
            not force
            and not target_short_id
            and manifest_exists
            and ap.get("skip_if_shorts_already_exist", True)
        ):
            return {"status": "skipped", "reason": "shorts_already_exist"}

        # 3. Legacy detection.
        if legacy_cleanup.detect_legacy_shorts(long_job_dir):
            if force and ap.get("archive_on_force_regenerate", True):
                legacy_cleanup.archive_legacy_shorts(long_job_dir)
            else:
                return {
                    "status": "legacy_detected",
                    "message": "Legacy Shorts artifacts detected. Run force regenerate to archive and create new Shorts.",
                }
        elif (
            force
            and manifest_exists
            and not target_short_id
            and ap.get("archive_on_force_regenerate", True)
        ):
            # Force-regenerate of an existing new-structure run: archive first.
            legacy_cleanup.archive_legacy_shorts(long_job_dir)
        # 4. Validate source artifacts.
        missing = [f for f in REQUIRED_SOURCE_FILES if resolve_long_job_artifact(long_job_dir, f) is None]
        if missing:
            run = {
                "source_long_job_id": long_job_dir.name,
                "status": "failed",
                "trigger": trigger,
                "execution_mode": "sequential",
                "started_at": started_at,
                "completed_at": _now(),
                "errors": [f"Missing source artifact: {m}" for m in missing],
                "warnings": [],
            }
            manifest.write_autopilot_run(long_job_dir, run)
            return {"status": "failed", "errors": run["errors"]}

        # 5. Snapshot source.
        _write_source_snapshot(long_job_dir)

        # 6. Plan.
        plan = plan_fn(long_job_dir, channel_config) or {}
        manifest.write_plan(long_job_dir, plan)
        selected = plan.get("selected_shorts") or []
        if target_short_id:
            selected = [sp for sp in selected if sp.get("short_id") == target_short_id]
            if not selected:
                run = {
                    "source_long_job_id": long_job_dir.name,
                    "status": "failed",
                    "trigger": trigger,
                    "execution_mode": "sequential",
                    "started_at": started_at,
                    "completed_at": _now(),
                    "errors": [f"Unknown target Short: {target_short_id}"],
                    "warnings": [],
                }
                manifest.write_autopilot_run(long_job_dir, run)
                return {"status": "failed", "errors": run["errors"]}
        warnings: list[str] = list(plan.get("warnings") or [])

        # 7. Sequential per-short pipeline.
        # §8.3 Resume: when a short_status.json exists with status="rendered",
        # treat it as already done and skip its build (unless force=true). This
        # lets the autopilot resume after a crash without re-rendering finished
        # shorts.
        results: list[dict] = []
        rendered = 0
        ready_for_render = 0
        failed = 0
        needs_review = 0
        generated = 0
        continue_on_fail = ap.get("continue_if_one_short_fails", True)
        for sp in selected:
            short_id = sp.get("short_id")
            if (long_job_dir / ".stop_requested").exists() or (paths.short_dir(long_job_dir, short_id) / ".stop_requested").exists():
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "Stop requested by operator.",
                        "stop_requested": True,
                    }
                )
            res: dict[str, Any]
            prior = _prior_status(long_job_dir, short_id)
            if not force and prior and prior.get("status") == "rendered":
                res = prior
                rendered += 1
            else:
                res = _call_build_short_fn(
                    build_short_fn,
                    long_job_dir,
                    sp,
                    channel_config,
                    require_render_confirmation=require_render_confirmation,
                )
                generated += 1
                status = res.get("status")
                if status == "rendered":
                    rendered += 1
                elif status == "ready_for_render":
                    ready_for_render += 1
                elif status == "needs_review":
                    needs_review += 1
                    failed += 1
                    warnings.append(f"{short_id}: {status or 'unknown'}")
                else:
                    failed += 1
                    warnings.append(f"{short_id}: {status or 'unknown'}")
                manifest.write_short_status(long_job_dir, short_id, res)
            results.append(_manifest_entry(sp, res))
            if res.get("status") not in ("rendered", "ready_for_render") and not continue_on_fail:
                break

        overall = (
            aggregate_prepare_status(results)
            if require_render_confirmation
            else ("completed" if failed == 0 else "completed_with_warnings")
        )

        # 8. Manifest + autopilot run.
        source_title = _source_title(long_job_dir)
        manifest.write_manifest(
            long_job_dir,
            {
                "source_long_job_id": long_job_dir.name,
                "source_title": source_title,
                "source_video_url": "",
                "created_at": started_at,
                "updated_at": _now(),
                "status": overall,
                "shorts": results,
            },
        )
        run = {
            "source_long_job_id": long_job_dir.name,
            "status": overall,
            "mode": "prepare_drafts" if require_render_confirmation else "autopilot",
            "trigger": trigger,
            "execution_mode": "sequential",
            "started_at": started_at,
            "completed_at": _now(),
            "attempted_count": len(selected),
            "generated_count": generated,
            "qa_passed_count": sum(1 for r in results if r.get("qa_verdict") == "PASS"),
            "ready_for_render_count": ready_for_render,
            "rendered_count": rendered,
            "needs_review_count": needs_review,
            "failed_count": failed,
            "warnings": warnings,
            "errors": [],
        }
        manifest.write_autopilot_run(long_job_dir, run)

        return {
            "status": overall,
            "ready_for_render_count": ready_for_render,
            "rendered_count": rendered,
            "failed_count": failed,
            "generated_count": generated,
            "warnings": warnings,
            "shorts": results,
        }
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            lock_fd.close()
            lock.unlink(missing_ok=True)


def _manifest_entry(short_plan: dict, res: dict) -> dict:
    return {
        "short_id": res.get("short_id") or short_plan.get("short_id"),
        "format": res.get("format") or short_plan.get("format"),
        "hook": res.get("hook", ""),
        "cover_text": res.get("cover_text", ""),
        "duration_sec": res.get("duration_sec"),
        "score": res.get("score"),
        "status": res.get("status"),
        "qa_verdict": res.get("qa_verdict"),
        "qa_scenes_attempts": res.get("qa_scenes_attempts"),
        "qa_scenes_structural_attempts": res.get("qa_scenes_structural_attempts"),
        "qa_scenes_product_attempts": res.get("qa_scenes_product_attempts"),
        "rendered": bool(res.get("rendered")),
        "requires_render_confirmation": bool(res.get("requires_render_confirmation")),
        "requires_user_review": bool(res.get("requires_user_review")),
        "source_scene_ids": res.get("source_scene_ids") or short_plan.get("source_scene_ids") or [],
        "music_track": res.get("music_track"),
        "video_path": res.get("video_path"),
    }


def _prior_status(long_job_dir: Path, short_id: str) -> dict | None:
    p = paths.short_status_path(long_job_dir, short_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _source_title(long_job_dir: Path) -> str:
    seo = resolve_long_job_artifact(long_job_dir, "seo.json")
    if seo is not None:
        try:
            return str(json.loads(seo.read_text(encoding="utf-8")).get("title", ""))
        except Exception:
            return ""
    return ""
