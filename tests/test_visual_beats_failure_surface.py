"""Regression: a visual_beats enforced FAIL must surface failure_stage +
failure_reason on short_status (and refresh qa_decision_summary), so the UI
shows WHY a short failed instead of a silent 'failed' with no reason."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.builder.context import BuildContext
from video_agent.shorts.builder.stages.visual_beats import _stage_visual_beats
from video_agent.shorts.manifest import read_short_status
from video_agent.utils.json_io import read_json


def _noop(*args: Any, **kwargs: Any) -> Any:
    return None


def _make_ctx(tmp_path: Path) -> BuildContext:
    short_id = "short-01_test"
    long_job_dir = tmp_path / "job"
    short_dir = long_job_dir / "shorts" / short_id
    json_dir = short_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    status: dict[str, Any] = {
        "short_id": short_id,
        "status": "in_progress",
        "stages": [
            {"name": "visual_beats", "status": "in_progress"},
            {"name": "render", "status": "pending"},
        ],
    }

    def _update_stage(name: str, st: str, **kw: Any) -> None:
        for stage in status["stages"]:
            if stage.get("name") == name:
                stage["status"] = st
                stage.update(kw)
                return

    # A single NON-graphic scene span with no resolved asset and no trim yields
    # zero beat candidates -> missing_selected_plan -> visual_sequence_qa FAIL.
    extras: dict[str, Any] = {
        "short_scenes": {"scenes": [{"id": "s01", "duration_sec": 2.0, "layout": "short_tip"}]},
        "visual_spans": {"spans": [{"id": "vs01", "scene_ids": ["s01"]}]},
        "trim_window_plan": {"spans": []},
        "visual_span_asset_qa": None,
        "assets_manifest": {},
    }

    return BuildContext(
        long_job_dir=long_job_dir,
        short_dir=short_dir,
        json_dir=json_dir,
        outputs_dir=short_dir / "outputs",
        short_plan={"short_id": short_id},
        plan_for_prompt={},
        channel_config={
            "shorts": {
                "visual_quality_flow": {
                    "enabled": True,
                    "mode": "enforced",
                    "beat_planner": {"enabled": True},
                }
            }
        },
        llm_fn=_noop,
        gemini_fn=_noop,
        background_fn=_noop,
        tts_fn=_noop,
        mix_fn=_noop,
        render_fn=_noop,
        cover_fn=_noop,
        status=status,
        recorder=None,  # unused on the failure path
        update_stage=_update_stage,
        check_stop=_noop,
        max_regen=0,
        max_structural_attempts=0,
        max_product_attempts=0,
        max_chatgpt_provider_retries=0,
        music_track=None,
        cover_words=0,
        long_video_url="",
        require_render_confirmation=False,
        source_artifacts=None,
        extras=extras,
    )


def test_visual_beats_enforced_fail_surfaces_failure_stage_and_reason(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    result = _stage_visual_beats(ctx)

    # Stage signals a hard failure to the orchestrator.
    assert result.returns is not None
    assert result.returns["status"] == "failed"
    assert result.returns["failure_stage"] == "visual_beats"
    assert result.returns["failure_reason"]

    # In-memory status carries the reason (no silent None).
    assert ctx.status["status"] == "failed"
    assert ctx.status["failure_stage"] == "visual_beats"
    assert ctx.status["failure_reason"]
    assert ctx.status["failure_reason"] != "None"
    # Pending stages are marked skipped, not left dangling as 'pending'.
    render_stage = next(s for s in ctx.status["stages"] if s["name"] == "render")
    assert render_stage["status"] == "skipped"

    # Persisted short_status.json reflects the same surfaced reason.
    persisted = read_short_status(ctx.long_job_dir, "short-01_test")
    assert persisted["failure_stage"] == "visual_beats"
    assert persisted["failure_reason"]

    # qa_decision_summary.json now points at the failing stage (not a stale pass).
    summary = read_json(ctx.json_dir / paths.SHORT_QA_DECISION_SUMMARY_FILE)
    assert summary["stage"] == "visual_beats"
    assert summary["decision"] == "failed_hard_blocker"
    assert summary["renderable"] is False
