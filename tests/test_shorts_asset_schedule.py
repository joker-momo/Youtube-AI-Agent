"""Compiled-schedule + manifest-adapter tests (spec v3.2.3 §15/§16/§16.1/§17, §41.2)."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from video_agent.shorts import asset_schedule as sched
from video_agent.shorts.asset_schedule import (
    adapt_assets_manifest,
    build_scene_frame_timeline,
    compile_asset_schedule,
    select_continuous_asset_for_span,
    validate_compiled_asset_schedule,
)

FPS = 30


def _scene(sid: str, dur: float, *, graphic: bool = False, importance: str = "normal") -> dict[str, Any]:
    sc: dict[str, Any] = {"id": sid, "duration_sec": dur, "visual_importance": importance}
    sc["layout"] = "graphic_definition" if graphic else "short_tip"
    if graphic:
        sc["visual_type"] = "graphic"
    return sc


def _native(sid: str, *, dur_sec: float = 11.4, score: float = 82.0, match: str = "strong_match") -> dict[str, Any]:
    return {
        "scene_id": sid,
        "public_ref": f"jobs/job-1__short-04/assets/{sid}.mp4",
        "local_path": None,
        "provider": "pexels_video",
        "asset_id": f"pexels-{sid}",
        "render_media_kind": "video",
        "source_media_kind": sched.NATIVE_VIDEO,
        "source_duration_sec": dur_sec,
        "selection_score": score,
        "asset_match_status": match,
        "semantic_rejected": False,
        "exists": True,
        "crop_plan": {"mode": "cover", "anchor": "center-right", "scale": 1.08, "target": "walking"},
    }


def _image_backed(sid: str) -> dict[str, Any]:
    return {
        "scene_id": sid,
        "public_ref": f"jobs/job-1__short-04/assets/{sid}.mp4",
        "local_path": None,
        "provider": "ai_generated",
        "asset_id": f"ai-{sid}",
        "render_media_kind": "video",
        "source_media_kind": sched.IMAGE_BACKED_VIDEO,
        "source_duration_sec": None,
        "selection_score": None,
        "asset_match_status": "ai_generated",
        "semantic_rejected": False,
        "exists": True,
        "crop_plan": None,
    }


def _continuous_span(scene_ids: list[str]) -> dict[str, Any]:
    return {"spans": [{"id": "vs01", "scene_ids": scene_ids, "planned_mode": "continuous_clip"}]}


def _legacy_spans(scene_ids: list[str]) -> dict[str, Any]:
    return {"spans": [{"id": f"vs{i+1:02d}", "scene_ids": [s], "planned_mode": "continuous_clip"} for i, s in enumerate(scene_ids)]}


# --------------------------------------------------------------------------- #
# frame timeline (cases 1-3)
# --------------------------------------------------------------------------- #
def test_cumulative_boundaries_and_total() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.5), _scene("s03", 1.5)]
    tl = build_scene_frame_timeline(scenes, FPS)
    assert [(b["from_frame"], b["end_frame_exclusive"]) for b in tl] == [(0, 60), (60, 135), (135, 180)]
    assert tl[-1]["end_frame_exclusive"] == 60 + 75 + 45


def test_deterministic_output() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.5)]
    a = build_scene_frame_timeline(scenes, FPS)
    b = build_scene_frame_timeline(copy.deepcopy(scenes), FPS)
    assert a == b


# --------------------------------------------------------------------------- #
# continuous_clip compile (cases 4, 16)
# --------------------------------------------------------------------------- #
def test_continuous_clip_one_track_spans_three_scenes() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.5), _scene("s03", 1.5)]
    doc = {"scenes": scenes}
    resolved = {"scenes": {"s01": _native("s01"), "s02": _native("s02"), "s03": _native("s03")}}
    schedule = compile_asset_schedule(
        short_id="short-04", scene_doc=doc, visual_spans=_continuous_span(["s01", "s02", "s03"]),
        resolved_visuals=resolved, fps=FPS, timing_source="tts_final", scene_version=7,
    )
    assert schedule["qa"]["verdict"] == "PASS", schedule["qa"]["errors"]
    assert len(schedule["tracks"]) == 1
    tr = schedule["tracks"][0]
    assert tr["scene_ids"] == ["s01", "s02", "s03"]
    assert tr["from_frame"] == 0 and tr["end_frame_exclusive"] == 180  # exact span union
    assert tr["source_media_kind"] == sched.NATIVE_VIDEO
    assert tr["motion_plan"] == {"name": "none", "apply_to_native_video": False}
    assert tr["selection_debug"]["mode"] == "continuous_clip"


def test_continuous_compile_is_deterministic() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.5)]
    doc = {"scenes": scenes}
    resolved = {"scenes": {"s01": _native("s01"), "s02": _native("s02", score=90.0)}}
    kw = dict(short_id="short-04", visual_spans=_continuous_span(["s01", "s02"]),
              resolved_visuals=resolved, fps=FPS, timing_source="tts_final", scene_version=1)
    a = compile_asset_schedule(scene_doc=doc, **kw)
    b = compile_asset_schedule(scene_doc=copy.deepcopy(doc), **kw)
    assert a["tracks"] == b["tracks"]


# --------------------------------------------------------------------------- #
# fallback + media-kind gating (cases 13, 5)
# --------------------------------------------------------------------------- #
def test_image_backed_not_selected_as_native_continuous() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.5)]
    doc = {"scenes": scenes}
    resolved = {"scenes": {"s01": _image_backed("s01"), "s02": _image_backed("s02")}}
    schedule = compile_asset_schedule(
        short_id="x", scene_doc=doc, visual_spans=_continuous_span(["s01", "s02"]),
        resolved_visuals=resolved, fps=FPS, timing_source="tts_final", scene_version=1,
    )
    # falls back to one legacy track per scene
    assert len(schedule["tracks"]) == 2
    assert all(t["selection_debug"]["mode"] == "legacy_scene_assets" for t in schedule["tracks"])
    assert schedule["qa"]["verdict"] == "PASS"


def test_unconverted_graphic_scene_is_rejected() -> None:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.0, graphic=True), _scene("s03", 2.0)]
    doc = {"scenes": scenes}
    spans = {"spans": [
        {"id": "vs01", "scene_ids": ["s01"], "planned_mode": "continuous_clip"},
        {"id": "vs02", "scene_ids": ["s02"], "planned_mode": "graphic_led"},
        {"id": "vs03", "scene_ids": ["s03"], "planned_mode": "continuous_clip"},
    ]}
    resolved = {"scenes": {"s01": _image_backed("s01"), "s03": _image_backed("s03")}}
    with pytest.raises(RuntimeError, match=r"s02.*graphic_definition.*ChatGPT"):
        compile_asset_schedule(
            short_id="x", scene_doc=doc, visual_spans=spans, resolved_visuals=resolved,
            fps=FPS, timing_source="tts_final", scene_version=1,
        )


# --------------------------------------------------------------------------- #
# selector gates (cases 8, 18, 19, 21, 22)
# --------------------------------------------------------------------------- #
def test_short_source_rejected() -> None:
    resolved = {"s01": _native("s01", dur_sec=3.0)}  # 3s < 6s span
    assert select_continuous_asset_for_span(["s01"], resolved, span_seconds=6.0) is None


def test_semantic_rejection_excludes() -> None:
    cand = _native("s01")
    cand["semantic_rejected"] = True
    assert select_continuous_asset_for_span(["s01"], {"s01": cand}, span_seconds=4.0) is None


def test_unknown_match_status_not_strong() -> None:
    cand = _native("s01", match="unknown")
    assert select_continuous_asset_for_span(["s01"], {"s01": cand}, span_seconds=4.0) is None
    weak = _native("s02", match="weak_match")
    assert select_continuous_asset_for_span(["s02"], {"s02": weak}, span_seconds=4.0) is None


def test_missing_duration_triggers_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "s01.mp4"
    f.write_bytes(b"x")
    cand = _native("s01")
    cand["source_duration_sec"] = None
    cand["local_path"] = str(f)
    monkeypatch.setattr("video_agent.stages.render.probe_video_duration_sec", lambda p: 12.0)
    chosen = select_continuous_asset_for_span(["s01"], {"s01": cand}, span_seconds=5.0)
    assert chosen is not None
    assert cand["source_duration_sec"] == 12.0  # cached after probe


def test_failed_probe_excludes_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "s01.mp4"
    f.write_bytes(b"x")
    cand = _native("s01")
    cand["source_duration_sec"] = None
    cand["local_path"] = str(f)
    monkeypatch.setattr("video_agent.stages.render.probe_video_duration_sec", lambda p: None)
    assert select_continuous_asset_for_span(["s01"], {"s01": cand}, span_seconds=5.0) is None


# --------------------------------------------------------------------------- #
# validation rejections (cases 6, 7, 10, 11, 12, 14, 15)
# --------------------------------------------------------------------------- #
def _valid_schedule() -> tuple[dict[str, Any], dict[str, Any]]:
    scenes = [_scene("s01", 2.0), _scene("s02", 2.5)]
    doc = {"scenes": scenes}
    resolved = {"scenes": {"s01": _native("s01"), "s02": _native("s02")}}
    schedule = compile_asset_schedule(
        short_id="x", scene_doc=doc, visual_spans=_continuous_span(["s01", "s02"]),
        resolved_visuals=resolved, fps=FPS, timing_source="tts_final", scene_version=3,
    )
    return schedule, doc


def test_any_background_gap_rejected() -> None:
    schedule, doc = _valid_schedule()
    schedule["tracks"] = []  # drop coverage
    qa = validate_compiled_asset_schedule(schedule, doc)
    assert qa["verdict"] == "FAIL"
    assert any("uncovered_frame" in e for e in qa["errors"])


def test_overlap_rejected() -> None:
    schedule, doc = _valid_schedule()
    dup = copy.deepcopy(schedule["tracks"][0])
    dup["track_id"] = "vtDUP"
    schedule["tracks"].append(dup)
    qa = validate_compiled_asset_schedule(schedule, doc)
    assert qa["verdict"] == "FAIL"
    assert any("overlapping_track_frame" in e for e in qa["errors"])


def test_playback_rate_not_1_rejected() -> None:
    schedule, doc = _valid_schedule()
    schedule["tracks"][0]["playback_rate"] = 1.5
    qa = validate_compiled_asset_schedule(schedule, doc)
    assert any("playback_rate_not_1" in e for e in qa["errors"])


def test_loop_rejected() -> None:
    schedule, doc = _valid_schedule()
    schedule["tracks"][0]["loop_policy"] = "allow_if_safe"
    qa = validate_compiled_asset_schedule(schedule, doc)
    assert any("loop_policy_not_forbid" in e for e in qa["errors"])


def test_render_media_kind_container_mismatch() -> None:
    schedule, doc = _valid_schedule()
    schedule["tracks"][0]["asset_ref"] = "jobs/x/assets/s01.jpg"  # image ext but render video
    qa = validate_compiled_asset_schedule(schedule, doc)
    assert any("render_media_kind_container_mismatch" in e for e in qa["errors"])


def test_scene_version_mismatch_rejected() -> None:
    schedule, doc = _valid_schedule()
    qa = validate_compiled_asset_schedule(schedule, doc, expected_scene_version=999)
    assert any("scene_version_mismatch" in e for e in qa["errors"])


def test_fps_mismatch_rejected() -> None:
    schedule, doc = _valid_schedule()
    qa = validate_compiled_asset_schedule(schedule, doc, render_fps=24)
    assert any("fps_mismatch" in e for e in qa["errors"])


def test_native_video_synthetic_drift_rejected() -> None:
    schedule, doc = _valid_schedule()
    schedule["tracks"][0]["motion_plan"] = {"name": "push_in", "apply_to_native_video": False}
    qa = validate_compiled_asset_schedule(schedule, doc)
    assert any("native_video_synthetic_drift" in e for e in qa["errors"])


# --------------------------------------------------------------------------- #
# manifest adapter (cases 17, 20)
# --------------------------------------------------------------------------- #
def test_adapter_derives_image_backed_mp4(tmp_path: Path) -> None:
    manifest = {"scenes": [
        {"scene_id": "s01", "public_background": "jobs/x/assets/s01.mp4", "media_kind": "image",
         "asset_tier": "ai_image", "provider": "ai_generated",
         "asset_selection": {"asset_match_status": "ai_generated"}},
        {"scene_id": "s02", "public_background": "jobs/x/assets/s02.mp4", "asset_tier": "pexels_video",
         "provider": "pexels_video", "asset_selection": {"score": 80, "asset_match_status": "strong_match"}},
    ]}
    out = adapt_assets_manifest(manifest, short_dir=tmp_path)
    assert out["scenes"]["s01"]["source_media_kind"] == sched.IMAGE_BACKED_VIDEO
    assert out["scenes"]["s01"]["render_media_kind"] == "video"
    assert out["scenes"]["s01"]["eligible_for_multi_scene_continuity"] is False
    assert out["scenes"]["s02"]["source_media_kind"] == sched.NATIVE_VIDEO
    assert out["scenes"]["s02"]["eligible_for_multi_scene_continuity"] is True


def test_adapter_missing_score_warns_preserves_ranking(tmp_path: Path) -> None:
    manifest = {"scenes": [
        {"scene_id": "s01", "public_background": "jobs/x/assets/s01.mp4", "asset_tier": "pexels_video",
         "provider": "pexels_video", "asset_selection": {"asset_match_status": "strong_match"}},  # no score
    ]}
    out = adapt_assets_manifest(manifest, short_dir=tmp_path)
    assert out["scenes"]["s01"]["selection_score"] is None
    assert any("missing_selection_score" in w for w in out["warnings"])


def test_adapter_semantic_rejection_from_background_report(tmp_path: Path) -> None:
    manifest = {"scenes": [
        {"scene_id": "s01", "public_background": "jobs/x/assets/s01.mp4", "asset_tier": "pexels_video",
         "provider": "pexels_video", "asset_selection": {"asset_match_status": "strong_match", "score": 70}},
    ]}
    bg = {"scenes": [{"scene_id": "s01", "rejection_reasons": ["forbidden_evidence"]}]}
    out = adapt_assets_manifest(manifest, short_dir=tmp_path, background_report=bg)
    assert out["scenes"]["s01"]["semantic_rejected"] is True
    assert out["scenes"]["s01"]["semantic_rejection_source"] == "background_report.scene"
    # rejected native video must not be continuity-eligible
    assert out["scenes"]["s01"]["eligible_for_multi_scene_continuity"] is False


def test_adapter_unknown_source_excluded(tmp_path: Path) -> None:
    # no media_kind, no recognizable tier/provider, .mp4 container → unknown, not native.
    manifest = {"scenes": [
        {"scene_id": "s01", "public_background": "jobs/x/assets/s01.mp4", "provider": "mystery"},
    ]}
    out = adapt_assets_manifest(manifest, short_dir=tmp_path)
    assert out["scenes"]["s01"]["source_media_kind"] is None
    assert out["scenes"]["s01"]["eligible_for_multi_scene_continuity"] is False
