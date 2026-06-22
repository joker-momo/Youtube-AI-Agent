from __future__ import annotations

from typing import Any

from video_agent.shorts.visual_beat_planner import (
    _evidence_score,
    build_visual_beat_plan,
    resolve_visual_beat_planner_config,
)


def _scene(sid: str, dur: float, *, graphic: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"id": sid, "duration_sec": dur, "layout": "short_tip"}
    if graphic:
        out["layout"] = "graphic_definition"
        out["visual_type"] = "graphic"
        out["graphic_intent"] = "MOVEMENT -> RECOVERY"
    return out


def _resolved(scene_id: str, *, source_kind: str = "native_video") -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "asset_id": f"asset-{scene_id}",
        "provider": "pexels_video",
        "provider_asset_id": f"provider-{scene_id}",
        "public_ref": f"jobs/short-04/assets/{scene_id}.mp4",
        "render_media_kind": "video",
        "source_media_kind": source_kind,
        "source_duration_sec": 12.0,
        "selection_score": 80.0,
        "asset_match_status": "strong_match",
        "exists": True,
    }


def _trim() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "spans": [
            {
                "visual_span_id": "vs01",
                "scene_ids": ["s01", "s02"],
                "final_candidate_id": "final-01",
                "provider": "pexels_video",
                "provider_asset_id": "provider-final",
                "asset_ref": "jobs/short-04/assets/final.mp4",
                "selected_window_start_in_frames": 12,
                "selected_window_end_in_frames": 132,
                "trim_timebase_fps": 30,
                "source_duration_sec": 12.0,
                "window_score": 92.0,
                "motion_band": "normal_motion",
                "crop_stability_score": 0.9,
            }
        ],
    }


def _asset_qa(verdict: str = "PASS") -> dict[str, Any]:
    return {
        "spans": [
            {
                "visual_span_id": "vs01",
                "final_candidate_id": "final-01",
                "render_eligible": verdict != "FAIL",
                "qa": {"verdict": verdict, "errors": [], "warnings": []},
            }
        ]
    }


def _continuous_score(*, motion_band: str, crop: float, verdict: str) -> float:
    trim = _trim()
    trim["spans"][0]["motion_band"] = motion_band
    trim["spans"][0]["crop_stability_score"] = crop
    plan = build_visual_beat_plan(
        short_id="short-04",
        scene_doc={"scenes": [_scene("s01", 2.0), _scene("s02", 2.0)]},
        visual_spans={"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]},
        resolved_visuals={"scenes": {"s01": _resolved("s01"), "s02": _resolved("s02")}},
        trim_window_plan=trim,
        visual_span_asset_qa=_asset_qa(verdict),
        channel_config={
            "shorts": {"visual_quality_flow": {"beat_planner": {"enabled": True}}}
        },
        fps=30,
    )
    cands = {c["mode"]: c for c in plan["spans"][0]["candidate_plans"]}
    return float(cands["continuous_clip"]["score"])


def test_beat_score_is_evidence_derived_not_constant() -> None:
    # The planner must be a quality optimizer: identical mode, different evidence,
    # different score (review P1: scores were hardcoded 84/86/88/72).
    strong = _continuous_score(motion_band="normal_motion", crop=0.9, verdict="PASS")
    weak_motion = _continuous_score(motion_band="unstable", crop=0.9, verdict="PASS")
    weak_crop = _continuous_score(motion_band="normal_motion", crop=0.1, verdict="PASS")
    weak_semantic = _continuous_score(motion_band="normal_motion", crop=0.9, verdict="CAPABILITY_REDUCED")
    assert strong > weak_motion
    assert strong > weak_crop
    assert strong > weak_semantic
    # breakdown is recorded for traceability
    # (sanity: a strong clip out-scores a degraded one by a meaningful margin)
    assert strong - weak_motion >= 3.0


def test_missing_span_qa_is_not_scored_as_confident_pass() -> None:
    # Unverified native (no span_qa) must NOT get the confident-PASS bonus, else
    # never-validated footage outranks a fallback (review #5).
    trim = {"motion_band": "normal_motion", "crop_stability_score": 0.9}
    beats = [{"type": "native_video"}]
    verified, _ = _evidence_score("continuous_clip", trim=trim, span_qa={"qa": {"verdict": "PASS"}}, beats=beats)
    unverified, bd = _evidence_score("continuous_clip", trim=trim, span_qa=None, beats=beats)
    assert unverified < verified
    assert bd["semantic_verdict"] != "PASS"


def test_resolve_config_bounds_non_legacy_plans_to_three() -> None:
    cfg = resolve_visual_beat_planner_config(
        {
            "shorts": {
                "visual_quality_flow": {
                    "enabled": True,
                    "beat_planner": {"enabled": True, "max_non_legacy_plans": 9},
                }
            }
        }
    )

    assert cfg["enabled"] is True
    assert cfg["max_non_legacy_plans"] == 3
    assert cfg["simplicity_margin_points"] == 3.0


def test_continuous_plan_selected_when_pr_d_trim_is_valid_and_candidate_count_is_bounded() -> None:
    plan = build_visual_beat_plan(
        short_id="short-04",
        scene_doc={"scenes": [_scene("s01", 2.0), _scene("s02", 2.0)]},
        visual_spans={"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]},
        resolved_visuals={"scenes": {"s01": _resolved("s01"), "s02": _resolved("s02")}},
        trim_window_plan=_trim(),
        visual_span_asset_qa=_asset_qa("CAPABILITY_REDUCED"),
        channel_config={
            "shorts": {
                "visual_quality_flow": {
                    "beat_planner": {"enabled": True, "max_non_legacy_plans": 3}
                }
            }
        },
        fps=30,
    )

    span = plan["spans"][0]
    assert len(span["candidate_plans"]) <= 3
    assert span["selected_plan"]["mode"] == "continuous_clip"
    assert span["selected_plan"]["beats"][0]["trim_window_ref"] == "vs01-trim"
    assert span["selected_plan"]["beats"][0]["source"] == "pr_d_trim_window"
    assert span["qa"]["verdict"] == "CAPABILITY_REDUCED"
    assert "visual_performance_features" not in plan


def test_simplicity_margin_prefers_continuous_over_small_two_clip_gain() -> None:
    plan = build_visual_beat_plan(
        short_id="short-04",
        scene_doc={"scenes": [_scene("s01", 2.0), _scene("s02", 2.0)]},
        visual_spans={"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]},
        resolved_visuals={"scenes": {"s01": _resolved("s01"), "s02": _resolved("s02")}},
        trim_window_plan=_trim(),
        visual_span_asset_qa=_asset_qa("PASS"),
        channel_config={
            "shorts": {
                "visual_quality_flow": {
                    "beat_planner": {"enabled": True, "simplicity_margin_points": 3}
                }
            }
        },
        fps=30,
    )

    span = plan["spans"][0]
    scores = {candidate["mode"]: candidate["score"] for candidate in span["candidate_plans"]}
    # Evidence-derived scoring: with identical span evidence, continuous (no cut)
    # scores at least as high as two_clip (which pays a complexity penalty for the
    # extra cut). Adding a cut must never raise the score.
    assert scores["continuous_clip"] >= scores["two_clip"]
    assert scores["continuous_clip"] - scores["two_clip"] <= 3
    assert span["selected_plan"]["mode"] == "continuous_clip"


def test_two_clip_plan_has_editorial_boundaries_not_punctuation() -> None:
    plan = build_visual_beat_plan(
        short_id="short-04",
        scene_doc={"scenes": [_scene("s01", 2.0), _scene("s02", 2.0)]},
        visual_spans={"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]},
        resolved_visuals={"scenes": {"s01": _resolved("s01"), "s02": _resolved("s02")}},
        trim_window_plan={"spans": []},
        visual_span_asset_qa=_asset_qa("FAIL"),
        channel_config={"shorts": {"visual_quality_flow": {"beat_planner": {"enabled": True}}}},
        fps=30,
    )

    selected = plan["spans"][0]["selected_plan"]
    assert selected["mode"] == "two_clip"
    assert [beat["scene_ids"] for beat in selected["beats"]] == [["s01"], ["s02"]]
    assert all(beat["boundary_reason"] != "sentence_punctuation" for beat in selected["beats"])


def test_clip_plus_graphic_uses_generated_image_instead_of_graphic_renderer() -> None:
    plan = build_visual_beat_plan(
        short_id="short-04",
        scene_doc={"scenes": [_scene("s01", 2.0), _scene("s02", 2.0, graphic=True)]},
        visual_spans={"spans": [{"id": "vs01", "scene_ids": ["s01", "s02"]}]},
        resolved_visuals={"scenes": {"s01": _resolved("s01"), "s02": _resolved("s02", source_kind="image_backed_video")}},
        trim_window_plan={"spans": []},
        visual_span_asset_qa=_asset_qa("FAIL"),
        channel_config={"shorts": {"visual_quality_flow": {"beat_planner": {"enabled": True}}}},
        fps=30,
    )

    selected = plan["spans"][0]["selected_plan"]
    assert selected["mode"] == "clip_plus_graphic"
    graphic = selected["beats"][1]
    assert graphic["type"] == "generated_image"
    assert graphic["asset_ref"] == "jobs/short-04/assets/s02.mp4"
    assert graphic["source_media_kind"] == "image_backed_video"


def test_no_valid_pexels_span_uses_generated_image_fallback_plan() -> None:
    plan = build_visual_beat_plan(
        short_id="short-04",
        scene_doc={"scenes": [_scene("s01", 2.0)]},
        visual_spans={"spans": [{"id": "vs01", "scene_ids": ["s01"]}]},
        resolved_visuals={"scenes": {"s01": _resolved("s01", source_kind="image_backed_video")}},
        trim_window_plan={"spans": []},
        visual_span_asset_qa=_asset_qa("FAIL"),
        channel_config={"shorts": {"visual_quality_flow": {"beat_planner": {"enabled": True}}}},
        fps=30,
    )

    span = plan["spans"][0]
    selected = span["selected_plan"]
    assert selected["mode"] == "generated_image_fallback"
    assert selected["beats"][0]["type"] == "generated_image"
    assert span["qa"]["verdict"] == "PASS"
