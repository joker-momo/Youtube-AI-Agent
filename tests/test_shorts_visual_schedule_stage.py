"""Stage tests for `_stage_visual_schedule` (spec v3.2.3 §21, §22, §41.3)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.builder.stages.visual_schedule import _stage_visual_schedule
from video_agent.shorts.builder.types import StageSignal


def _state(version: int = 3, tail_ok: bool = True, tail_version: int | None = 3) -> SimpleNamespace:
    return SimpleNamespace(
        current_scenes_version=version,
        latest_audio_tail_ok=tail_ok,
        latest_audio_tail_version=tail_version,
    )


def _ctx(
    tmp_path: Path,
    *,
    short_scenes: dict[str, Any],
    visual_spans: dict[str, Any],
    manifest: dict[str, Any],
    channel_config: dict[str, Any] | None = None,
    state: SimpleNamespace | None = None,
    audio: bool = True,
) -> SimpleNamespace:
    json_dir = tmp_path / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[str, str]] = []
    ctx = SimpleNamespace(
        short_plan={"short_id": "short-04"},
        short_dir=tmp_path,
        json_dir=json_dir,
        long_job_dir=tmp_path.parent,
        channel_config=channel_config or {"render": {"fps": 30}},
        status={"status": "generating"},
        tts_fn=(lambda *a, **k: None) if audio else None,
        extras={
            "short_scenes": short_scenes,
            "visual_spans": visual_spans,
            "assets_manifest": manifest,
            "scene_pipeline_state": state or _state(),
            "narration_wav": (tmp_path / "n.wav") if audio else None,
        },
        update_stage=lambda name, status, **kw: calls.append((name, status)),
        check_stop=lambda: None,
    )
    ctx.calls = calls  # type: ignore[attr-defined]
    return ctx


def _scenes() -> dict[str, Any]:
    return {"scenes": [
        {"id": "s01", "layout": "short_tip", "duration_sec": 2.0},
        {"id": "s02", "layout": "short_tip", "duration_sec": 2.5},
    ]}


def _spans(scene_ids: list[str]) -> dict[str, Any]:
    return {"spans": [{"id": "vs01", "scene_ids": scene_ids, "planned_mode": "continuous_clip"}]}


def _native_manifest() -> dict[str, Any]:
    return {"scenes": [
        {"scene_id": "s01", "public_background": "jobs/job-1__short-04/assets/s01.mp4",
         "asset_tier": "pexels_video", "provider": "pexels_video", "source_duration_sec": 11.4,
         "asset_selection": {"score": 82, "asset_match_status": "strong_match"}},
        {"scene_id": "s02", "public_background": "jobs/job-1__short-04/assets/s02.mp4",
         "asset_tier": "pexels_video", "provider": "pexels_video", "source_duration_sec": 11.4,
         "asset_selection": {"score": 70, "asset_match_status": "strong_match"}},
    ]}


def test_compiles_continuous_clip_and_persists_artifacts(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, short_scenes=_scenes(), visual_spans=_spans(["s01", "s02"]), manifest=_native_manifest())
    result = _stage_visual_schedule(ctx)
    assert result.signal is StageSignal.PROCEED

    sched = json.loads((tmp_path / "json" / paths.SHORT_COMPILED_ASSET_SCHEDULE_FILE).read_text())
    qa = json.loads((tmp_path / "json" / paths.SHORT_COMPILED_ASSET_SCHEDULE_QA_FILE).read_text())
    assert sched["qa"]["verdict"] == "PASS"
    assert sched["timing_source"] == "tts_final"
    assert len(sched["tracks"]) == 1
    assert sched["tracks"][0]["scene_ids"] == ["s01", "s02"]
    assert qa["continuous_clip_count"] == 1
    assert qa["native_continuous_track_count"] == 1
    assert qa["image_backed_track_count"] == 0
    assert qa["graphic_fallback_track_count"] == 0
    assert len(qa["schedule_hash"]) == 64
    assert ctx.extras["visual_schedule_hash"] == qa["schedule_hash"]
    assert ("visual_schedule", "completed") in ctx.calls


def test_stale_audio_tail_blocks_tts_final(tmp_path: Path) -> None:
    # tail repaired for an older version → not tts_final.
    stale = _state(version=4, tail_ok=True, tail_version=3)
    ctx = _ctx(tmp_path, short_scenes=_scenes(), visual_spans=_spans(["s01", "s02"]),
               manifest=_native_manifest(), state=stale)
    _stage_visual_schedule(ctx)
    sched = json.loads((tmp_path / "json" / paths.SHORT_COMPILED_ASSET_SCHEDULE_FILE).read_text())
    assert sched["timing_source"] == "scene_plan"


def test_audio_disabled_is_scene_plan(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, short_scenes=_scenes(), visual_spans=_spans(["s01", "s02"]),
               manifest=_native_manifest(), audio=False)
    _stage_visual_schedule(ctx)
    sched = json.loads((tmp_path / "json" / paths.SHORT_COMPILED_ASSET_SCHEDULE_FILE).read_text())
    assert sched["timing_source"] == "scene_plan"


def test_image_backed_falls_back_to_legacy_tracks(tmp_path: Path) -> None:
    manifest = {"scenes": [
        {"scene_id": "s01", "public_background": "jobs/x/assets/s01.mp4", "asset_tier": "ai_image",
         "provider": "ai_generated", "asset_selection": {"asset_match_status": "ai_generated"}},
        {"scene_id": "s02", "public_background": "jobs/x/assets/s02.mp4", "asset_tier": "pexels_photo",
         "provider": "pexels", "asset_selection": {"asset_match_status": "weak_match"}},
    ]}
    ctx = _ctx(tmp_path, short_scenes=_scenes(), visual_spans=_spans(["s01", "s02"]), manifest=manifest)
    _stage_visual_schedule(ctx)
    sched = json.loads((tmp_path / "json" / paths.SHORT_COMPILED_ASSET_SCHEDULE_FILE).read_text())
    assert len(sched["tracks"]) == 2
    assert all(t["selection_debug"]["mode"] == "legacy_scene_assets" for t in sched["tracks"])
    assert sched["qa"]["verdict"] == "PASS"


def test_disabled_mode_skips_compile(tmp_path: Path) -> None:
    cfg = {"render": {"fps": 30}, "shorts": {"visual_timeline": {"mode": "disabled"}}}
    ctx = _ctx(tmp_path, short_scenes=_scenes(), visual_spans=_spans(["s01", "s02"]),
               manifest=_native_manifest(), channel_config=cfg)
    result = _stage_visual_schedule(ctx)
    assert result.signal is StageSignal.PROCEED
    assert not (tmp_path / "json" / paths.SHORT_COMPILED_ASSET_SCHEDULE_FILE).exists()


def test_enforced_invalid_schedule_fails_closed(tmp_path: Path, monkeypatch: Any) -> None:
    # Force the validator to FAIL; enforced mode must fail closed before render.
    monkeypatch.setattr(
        "video_agent.shorts.asset_schedule.validate_compiled_asset_schedule",
        lambda *a, **k: {"verdict": "FAIL", "errors": ["forced_invalid"], "warnings": []},
    )
    cfg = {"render": {"fps": 30}, "shorts": {"visual_timeline": {"mode": "enforced"}}}
    ctx = _ctx(tmp_path, short_scenes=_scenes(), visual_spans=_spans(["s01", "s02"]),
               manifest=_native_manifest(), channel_config=cfg)
    result = _stage_visual_schedule(ctx)
    assert result.returns is not None
    assert result.returns["failure_stage"] == "visual_schedule"
    assert ctx.status["status"] == "failed"
    assert ("visual_schedule", "failed") in ctx.calls


def test_report_only_invalid_schedule_does_not_fail_build(tmp_path: Path, monkeypatch: Any) -> None:
    # Same forced FAIL, but report_only must keep building (legacy render).
    monkeypatch.setattr(
        "video_agent.shorts.asset_schedule.validate_compiled_asset_schedule",
        lambda *a, **k: {"verdict": "FAIL", "errors": ["forced_invalid"], "warnings": []},
    )
    ctx = _ctx(tmp_path, short_scenes=_scenes(), visual_spans=_spans(["s01", "s02"]), manifest=_native_manifest())
    result = _stage_visual_schedule(ctx)
    assert result.signal is StageSignal.PROCEED
    assert result.returns is None
    assert ctx.status["status"] == "generating"
