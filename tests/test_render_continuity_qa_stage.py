"""Stage-level tests for `_stage_render_continuity_qa`: artifact write + gating."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from video_agent.shorts import paths
from video_agent.shorts.builder.stages import media


def _ctx(tmp_path: Path, channel_config: dict, video_exists: bool = True) -> SimpleNamespace:
    json_dir = tmp_path / "json"
    json_dir.mkdir()
    video_path = tmp_path / "short.mp4"
    if video_exists:
        video_path.write_bytes(b"fake")
    (json_dir / "render_props.json").write_text(json.dumps({"render": {"resolution": "1080x1920", "fps": 30}}))
    stages = [{"name": "render_continuity_qa", "status": "pending"}]
    return SimpleNamespace(
        short_plan={"short_id": "s1"},
        channel_config=channel_config,
        json_dir=json_dir,
        long_job_dir=tmp_path,
        extras={"video_path": str(video_path), "visual_schedule": {"fps": 30, "total_duration_in_frames": 180}},
        status={"stages": stages},
        update_stage=lambda *a, **k: None,
    )


def _enforced_cfg(on: bool) -> dict:
    return {"shorts": {"visual_timeline": {"qa": {"production_boundary_check_enabled": on}}}}


def test_pass_writes_artifact_and_proceeds(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "video_agent.shorts.render_continuity_qa.build_render_continuity_qa",
        lambda **kw: {"verdict": "PASS", "errors": []},
    )
    ctx = _ctx(tmp_path, _enforced_cfg(True))
    result = media._stage_render_continuity_qa(ctx)
    assert result.returns is None  # proceed
    artifact = ctx.json_dir / paths.SHORT_RENDER_CONTINUITY_QA_FILE
    assert artifact.exists()
    assert json.loads(artifact.read_text())["verdict"] == "PASS"


def test_enforced_fail_blocks_rendered_mark(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "video_agent.shorts.render_continuity_qa.build_render_continuity_qa",
        lambda **kw: {"verdict": "FAIL", "errors": ["frame_count:150!=schedule:180"]},
    )
    ctx = _ctx(tmp_path, _enforced_cfg(True))
    result = media._stage_render_continuity_qa(ctx)
    assert result.returns is not None  # blocked
    assert ctx.status["rendered"] is False
    assert ctx.status["status"] == "needs_review"
    assert ctx.status["failure_stage"] == "render_continuity_qa"


def test_report_only_fail_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "video_agent.shorts.render_continuity_qa.build_render_continuity_qa",
        lambda **kw: {"verdict": "FAIL", "errors": ["whatever"]},
    )
    ctx = _ctx(tmp_path, _enforced_cfg(False))  # flag off
    result = media._stage_render_continuity_qa(ctx)
    assert result.returns is None  # report-only never blocks
    assert (ctx.json_dir / paths.SHORT_RENDER_CONTINUITY_QA_FILE).exists()


def test_no_video_skips(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, _enforced_cfg(True), video_exists=False)
    result = media._stage_render_continuity_qa(ctx)
    assert result.returns is None


def test_qa_crash_is_caught_and_gated(tmp_path, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("ffprobe blew up")

    monkeypatch.setattr(
        "video_agent.shorts.render_continuity_qa.build_render_continuity_qa", _boom
    )
    ctx = _ctx(tmp_path, _enforced_cfg(True))
    result = media._stage_render_continuity_qa(ctx)
    assert result.returns is not None  # crash -> FAIL -> blocked when enforced
    qa = json.loads((ctx.json_dir / paths.SHORT_RENDER_CONTINUITY_QA_FILE).read_text())
    assert qa["verdict"] == "FAIL"
    assert any("qa_crashed" in e for e in qa["errors"])
