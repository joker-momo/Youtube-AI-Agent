"""Shorts Autopilot v5 — Phase 1 foundation tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def test_paths_layout(tmp_path: Path):
    from video_agent.shorts import paths
    job = tmp_path / "long-job"
    assert paths.shorts_dir(job) == job / "shorts"
    assert paths.short_dir(job, "short-01") == job / "shorts" / "short-01"
    assert paths.manifest_path(job) == job / "shorts" / "shorts_manifest.json"
    assert paths.autopilot_run_path(job) == job / "shorts" / "autopilot_run.json"
    assert paths.plan_path(job) == job / "shorts" / "shorts_plan.json"
    assert paths.autopilot_lock_path(job) == job / "shorts" / ".autopilot.lock"
    assert paths.short_status_path(job, "short-01") == job / "shorts" / "short-01" / "short_status.json"


# --------------------------------------------------------------------------
# legacy cleanup
# --------------------------------------------------------------------------

def test_detect_legacy_when_shorts_dir_has_files_but_no_manifest(tmp_path: Path):
    from video_agent.shorts import legacy_cleanup
    job = tmp_path / "job"
    legacy = job / "shorts" / "1"
    legacy.mkdir(parents=True)
    (legacy / "script.json").write_text("{}", encoding="utf-8")
    assert legacy_cleanup.detect_legacy_shorts(job) is True


def test_detect_legacy_false_when_new_manifest_present(tmp_path: Path):
    from video_agent.shorts import legacy_cleanup
    job = tmp_path / "job"
    (job / "shorts").mkdir(parents=True)
    (job / "shorts" / "shorts_manifest.json").write_text("{}", encoding="utf-8")
    assert legacy_cleanup.detect_legacy_shorts(job) is False


def test_detect_legacy_false_when_no_shorts_dir(tmp_path: Path):
    from video_agent.shorts import legacy_cleanup
    assert legacy_cleanup.detect_legacy_shorts(tmp_path / "job") is False


def test_archive_legacy_moves_old_artifacts_under_archive(tmp_path: Path):
    from video_agent.shorts import legacy_cleanup
    job = tmp_path / "job"
    legacy = job / "shorts" / "1"
    legacy.mkdir(parents=True)
    (legacy / "script.json").write_text('{"a":1}', encoding="utf-8")
    archive_dir = legacy_cleanup.archive_legacy_shorts(job)
    assert archive_dir.exists()
    assert "archive" in archive_dir.parts
    assert archive_dir.name.startswith("legacy-")
    # old content moved, shorts/ no longer has the stray "1" dir
    assert not (job / "shorts" / "1").exists()
    assert (archive_dir / "1" / "script.json").exists()


# --------------------------------------------------------------------------
# manifest / status writers (atomic)
# --------------------------------------------------------------------------

def test_write_and_read_manifest_roundtrip(tmp_path: Path):
    from video_agent.shorts import manifest
    job = tmp_path / "job"
    data = {"source_long_job_id": "j1", "status": "completed", "shorts": []}
    manifest.write_manifest(job, data)
    got = manifest.read_manifest(job)
    assert got["source_long_job_id"] == "j1"
    assert got["status"] == "completed"


def test_write_short_status_roundtrip(tmp_path: Path):
    from video_agent.shorts import manifest
    job = tmp_path / "job"
    manifest.write_short_status(job, "short-01", {"short_id": "short-01", "status": "rendered"})
    got = manifest.read_short_status(job, "short-01")
    assert got["status"] == "rendered"


def test_write_autopilot_run_roundtrip(tmp_path: Path):
    from video_agent.shorts import manifest
    job = tmp_path / "job"
    manifest.write_autopilot_run(job, {"status": "completed", "rendered_count": 3})
    got = manifest.read_autopilot_run(job)
    assert got["rendered_count"] == 3


# --------------------------------------------------------------------------
# long_review_passed + review.json verdict
# --------------------------------------------------------------------------

def test_long_review_passed_true_when_verdict_pass(tmp_path: Path):
    from video_agent.shorts.review_verdict import long_review_passed
    job = tmp_path / "job"
    job.mkdir()
    (job / "review.json").write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    assert long_review_passed(job) is True


def test_long_review_passed_false_when_verdict_fail(tmp_path: Path):
    from video_agent.shorts.review_verdict import long_review_passed
    job = tmp_path / "job"
    job.mkdir()
    (job / "review.json").write_text(json.dumps({"verdict": "FAIL"}), encoding="utf-8")
    assert long_review_passed(job) is False


def test_long_review_passed_false_when_no_review_artifact(tmp_path: Path):
    from video_agent.shorts.review_verdict import long_review_passed
    job = tmp_path / "job"
    job.mkdir()
    assert long_review_passed(job) is False


# --------------------------------------------------------------------------
# autopilot sequential skeleton
# --------------------------------------------------------------------------

def _make_long_job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    (job / "script.json").write_text(json.dumps({"sections": []}), encoding="utf-8")
    (job / "scenes.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    (job / "seo.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
    (job / "video.mp4").write_bytes(b"x")
    return job


def _make_long_job_subdir_layout(tmp_path: Path) -> Path:
    """Real job layout: JSON artifacts under ``json/`` and video under ``outputs/``."""
    job = tmp_path / "long-job-subdir"
    (job / "json").mkdir(parents=True)
    (job / "outputs").mkdir(parents=True)
    (job / "json" / "script.json").write_text(json.dumps({"sections": []}), encoding="utf-8")
    (job / "json" / "scenes.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    (job / "json" / "seo.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
    (job / "outputs" / "video.mp4").write_bytes(b"x")
    return job


def test_autopilot_source_validation_accepts_subdir_layout(tmp_path: Path):
    """Regression: source artifacts live under json/ + outputs/, not job root.

    Previously the validation checked ``long_job_dir / name`` (flat) and reported
    "Missing source artifact: script.json/..." which silently failed every
    regenerate/resume for new-layout jobs.
    """
    from video_agent.shorts import autopilot
    job = _make_long_job_subdir_layout(tmp_path)

    def fake_plan(long_job_dir, channel_config, requested_count=None):
        return {"selected_shorts": [{"short_id": "short-01"}], "warnings": []}

    def fake_build(long_job_dir, short_plan, channel_config):
        return {"short_id": short_plan["short_id"], "status": "rendered", "qa_verdict": "PASS"}

    cfg = {"shorts": {"autopilot": {}}}
    result = autopilot.run_shorts_autopilot(job, cfg, plan_fn=fake_plan, build_short_fn=fake_build)
    assert result["status"] != "failed", result
    assert not any("Missing source artifact" in e for e in result.get("errors", []))


def test_autopilot_skips_when_manifest_exists(tmp_path: Path):
    from video_agent.shorts import autopilot, manifest
    job = _make_long_job(tmp_path)
    manifest.write_manifest(job, {"shorts": [], "status": "completed"})
    cfg = {"shorts": {"autopilot": {"skip_if_shorts_already_exist": True}}}
    result = autopilot.run_shorts_autopilot(job, cfg, build_short_fn=lambda *a, **k: {"status": "rendered"})
    assert result["status"] == "skipped"


def test_autopilot_sequential_runs_each_short_in_order(tmp_path: Path):
    from video_agent.shorts import autopilot
    job = _make_long_job(tmp_path)
    order: list[str] = []

    def fake_build(long_job_dir, short_plan, channel_config):
        order.append(short_plan["short_id"])
        return {"short_id": short_plan["short_id"], "status": "rendered", "qa_verdict": "PASS"}

    def fake_plan(long_job_dir, channel_config, requested_count=None):
        return {
            "selected_shorts": [
                {"short_id": "short-01", "format": "pain_to_tip"},
                {"short_id": "short-02", "format": "mistake_to_avoid"},
                {"short_id": "short-03", "format": "mini_checklist"},
            ],
            "warnings": [],
        }

    cfg = {"shorts": {"autopilot": {"execution_mode": "sequential", "continue_if_one_short_fails": True}}}
    result = autopilot.run_shorts_autopilot(job, cfg, plan_fn=fake_plan, build_short_fn=fake_build)
    assert order == ["short-01", "short-02", "short-03"]
    assert result["status"] in ("completed", "completed_with_warnings")
    assert result["rendered_count"] == 3


def test_autopilot_continues_when_one_short_needs_review(tmp_path: Path):
    from video_agent.shorts import autopilot
    job = _make_long_job(tmp_path)
    seen: list[str] = []

    def fake_build(long_job_dir, short_plan, channel_config):
        seen.append(short_plan["short_id"])
        if short_plan["short_id"] == "short-02":
            return {"short_id": "short-02", "status": "needs_review", "qa_verdict": "FAIL"}
        return {"short_id": short_plan["short_id"], "status": "rendered", "qa_verdict": "PASS"}

    def fake_plan(long_job_dir, channel_config, requested_count=None):
        return {"selected_shorts": [{"short_id": f"short-0{i}"} for i in (1, 2, 3)], "warnings": []}

    cfg = {"shorts": {"autopilot": {"continue_if_one_short_fails": True}}}
    result = autopilot.run_shorts_autopilot(job, cfg, plan_fn=fake_plan, build_short_fn=fake_build)
    assert seen == ["short-01", "short-02", "short-03"]  # did not stop at 02
    assert result["status"] == "completed_with_warnings"
    assert result["rendered_count"] == 2
    assert result["failed_count"] == 1


def test_autopilot_prepare_mode_writes_drafts_ready_run_and_manifest(tmp_path: Path):
    from video_agent.shorts import autopilot, manifest, paths

    job = _make_long_job(tmp_path)

    def fake_build(long_job_dir, short_plan, channel_config, **kwargs):
        assert kwargs["require_render_confirmation"] is True
        return {
            "short_id": short_plan["short_id"],
            "status": "ready_for_render",
            "rendered": False,
            "qa_verdict": "PASS",
            "requires_render_confirmation": True,
            "source_scene_ids": ["scene-09"],
        }

    def fake_plan(long_job_dir, channel_config, requested_count=None):
        return {"selected_shorts": [{"short_id": "short-01", "format": "pain_to_tip"}], "warnings": []}

    cfg = {"shorts": {"autopilot": {"continue_if_one_short_fails": True}}}
    result = autopilot.run_shorts_autopilot(
        job,
        cfg,
        plan_fn=fake_plan,
        build_short_fn=fake_build,
        require_render_confirmation=True,
    )

    assert result["status"] == "drafts_ready"
    run = manifest.read_autopilot_run(job)
    assert run["mode"] == "prepare_drafts"
    assert run["status"] == "drafts_ready"
    assert run["ready_for_render_count"] == 1
    saved_manifest = manifest.read_manifest(job)
    assert saved_manifest["status"] == "drafts_ready"
    assert saved_manifest["shorts"][0]["status"] == "ready_for_render"
    assert saved_manifest["shorts"][0]["requires_render_confirmation"] is True
    assert not (paths.short_dir(job, "short-01") / paths.SHORT_VIDEO_FILE).exists()


def test_autopilot_force_archives_existing_then_runs(tmp_path: Path):
    from video_agent.shorts import autopilot, manifest
    job = _make_long_job(tmp_path)
    # existing legacy stray artifact
    (job / "shorts" / "1").mkdir(parents=True)
    (job / "shorts" / "1" / "script.json").write_text("{}", encoding="utf-8")

    def fake_plan(long_job_dir, channel_config, requested_count=None):
        return {"selected_shorts": [{"short_id": "short-01"}], "warnings": []}

    def fake_build(long_job_dir, short_plan, channel_config):
        return {"short_id": "short-01", "status": "rendered", "qa_verdict": "PASS"}

    cfg = {"shorts": {"autopilot": {"archive_on_force_regenerate": True}}}
    result = autopilot.run_shorts_autopilot(
        job, cfg, plan_fn=fake_plan, build_short_fn=fake_build, force=True
    )
    assert result["status"] in ("completed", "completed_with_warnings")
    assert (job / "shorts" / "archive").exists()
    assert not (job / "shorts" / "1").exists()


def test_autopilot_force_false_refuses_on_legacy(tmp_path: Path):
    from video_agent.shorts import autopilot
    job = _make_long_job(tmp_path)
    (job / "shorts" / "1").mkdir(parents=True)
    (job / "shorts" / "1" / "script.json").write_text("{}", encoding="utf-8")
    cfg = {"shorts": {"autopilot": {}}}
    result = autopilot.run_shorts_autopilot(
        job, cfg, plan_fn=lambda *a, **k: {"selected_shorts": []}, build_short_fn=lambda *a, **k: {},
        force=False,
    )
    assert result["status"] == "legacy_detected"


# --------------------------------------------------------------------------
# legacy deprecation + long-form stages unchanged
# --------------------------------------------------------------------------

def test_legacy_shorts_workflow_is_deprecated_or_disabled():
    import asyncio
    from pathlib import Path as _P
    from video_agent.orchestrator import shorts_stages
    with pytest.raises(Exception) as exc:
        asyncio.run(shorts_stages.auto_shorts_script_stage(_P("/tmp/x"), _P("/tmp/c.yaml"), None))
    assert "deprecat" in str(exc.value).lower() or "autopilot" in str(exc.value).lower()


def test_long_form_default_stages_unchanged():
    from video_agent.orchestrator import DEFAULT_STAGES
    assert DEFAULT_STAGES == (
        "idea_research",
        "script",
        "script_promote",
        "script_qa",
        "scenes",
        "scenes_promote",
        "scenes_qa",
        "visual_spans",
        "seo",
        "seo_promote",
        "seo_qa",
        "graphic_images",
        "thumbnail_image",
        "whisper_timestamps",
        "visual_schedule",
        "render",
        "review",
    )
    for s in DEFAULT_STAGES:
        assert not s.startswith("shorts_")


def test_run_all_pipeline_does_not_import_legacy_shorts_stage_calls():
    import inspect
    from video_agent.web import run_all_pipeline
    src = inspect.getsource(run_all_pipeline)
    assert "auto_shorts_script_stage(" not in src
    assert "auto_shorts_render_stage(" not in src


def test_legacy_shorts_prompt_is_removed():
    """Spec §2.1: _chatgpt_shorts_script_prompt must be removed (not just
    deprecated) so no caller can use the old prompt path."""
    import video_agent.operator as op
    assert not hasattr(op, "_chatgpt_shorts_script_prompt")


# ============================================================================
# Spec compliance v5: §8.3 resume, §18.4 single-short regen, §3.4 exclusive lock
# ============================================================================

# --- §8.3 resume behavior ---------------------------------------------------

def test_autopilot_resumes_skips_already_rendered_shorts(tmp_path: Path):
    """When short-01 is already rendered (per short_status.json), autopilot
    must skip its build and only run remaining shorts."""
    from video_agent.shorts import autopilot, manifest
    job = _make_long_job(tmp_path)
    # Mark short-01 as already rendered in its short_status.json
    manifest.write_short_status(job, "short-01", {
        "short_id": "short-01", "status": "rendered", "rendered": True, "qa_verdict": "PASS",
    })

    built: list[str] = []

    def fake_plan(long_job_dir, channel_config, requested_count=None):
        return {"selected_shorts": [{"short_id": f"short-0{i}"} for i in (1, 2, 3)], "warnings": []}

    def fake_build(long_job_dir, short_plan, cfg):
        built.append(short_plan["short_id"])
        return {"short_id": short_plan["short_id"], "status": "rendered", "qa_verdict": "PASS"}

    cfg = {"shorts": {"autopilot": {}}}
    result = autopilot.run_shorts_autopilot(job, cfg, plan_fn=fake_plan, build_short_fn=fake_build)
    # short-01 must be skipped (already rendered), only 02 + 03 built
    assert built == ["short-02", "short-03"]
    assert result["rendered_count"] == 3  # 1 skipped (already rendered) + 2 freshly built


def test_autopilot_force_rebuilds_already_rendered_shorts(tmp_path: Path):
    """force=true must rebuild every short even if short_status says rendered."""
    from video_agent.shorts import autopilot, manifest
    job = _make_long_job(tmp_path)
    manifest.write_short_status(job, "short-01", {"short_id": "short-01", "status": "rendered", "rendered": True})

    built: list[str] = []

    def fake_plan(long_job_dir, channel_config, requested_count=None):
        return {"selected_shorts": [{"short_id": "short-01"}], "warnings": []}

    def fake_build(long_job_dir, short_plan, cfg):
        built.append(short_plan["short_id"])
        return {"short_id": short_plan["short_id"], "status": "rendered", "qa_verdict": "PASS"}

    cfg = {"shorts": {"autopilot": {"archive_on_force_regenerate": True}}}
    autopilot.run_shorts_autopilot(job, cfg, plan_fn=fake_plan, build_short_fn=fake_build, force=True)
    assert built == ["short-01"]


# --- §3.4 exclusive lock ---------------------------------------------------

def test_autopilot_lock_blocks_concurrent_runs(tmp_path: Path):
    """A second autopilot run while a first is holding the lock must refuse
    cleanly instead of silently racing."""
    from video_agent.shorts import autopilot, paths
    job = _make_long_job(tmp_path)

    # Pre-acquire the lock as if another process held it (fcntl exclusive).
    import fcntl
    paths.shorts_dir(job).mkdir(parents=True, exist_ok=True)
    held = open(paths.autopilot_lock_path(job), "w")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        cfg = {"shorts": {"autopilot": {}}}
        result = autopilot.run_shorts_autopilot(
            job, cfg,
            plan_fn=lambda *a, **k: {"selected_shorts": [{"short_id": "short-01"}], "warnings": []},
            build_short_fn=lambda *a, **k: {"short_id": "short-01", "status": "rendered", "qa_verdict": "PASS"},
        )
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()
    assert result["status"] == "locked"
