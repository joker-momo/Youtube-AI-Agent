"""Unit tests for the long-form Elena cue planner (``video_agent.visual.elena``).

Locks the simple per-scene rule: talking+hidden only, ONE cue per eligible scene
(no frequency/spacing/band), graphic scenes hidden, hook/short-warning -> large
emphasis, subtitle -> small circle, deterministic.
"""

from __future__ import annotations

import math

from video_agent.visual.elena import ELENA_ASSETS, build_elena_cues


def _scene(sid, layout="subtitle", dur=12.0, **extra):
    s = {"id": sid, "layout": layout, "duration_sec": dur}
    s.update(extra)
    return s


def _doc(*scenes):
    return {"job_id": "job-x", "scenes": list(scenes)}


def _f(dur, fps=30):
    return math.floor(dur * fps + 0.5)


def test_hook_gets_large_emphasis_cue():
    res = build_elena_cues(_doc(_scene("scene-01", "hook", 10.0)), {}, 30, job_id="job-x")
    assert len(res["cues"]) == 1
    cue = res["cues"][0]
    assert cue["mode"] == "talking"
    assert cue["treatment"] == "large"
    assert cue["variant"] == "talk-emphasis"
    assert cue["asset_ref"] == ELENA_ASSETS["talk-emphasis"]
    assert cue["position"] == "bottom-right"


def test_graphic_scene_is_hidden_no_cue():
    res = build_elena_cues(_doc(_scene("scene-01", "checklist", 12.0)), {}, 30, job_id="job-x")
    assert res["cues"] == []


def test_annotation_mode_hidden_forces_hidden():
    res = build_elena_cues(
        _doc(_scene("scene-01", "hook", 10.0, elena={"mode": "hidden"})), {}, 30, job_id="job-x"
    )
    assert res["cues"] == []


def test_only_talking_and_hidden_modes_no_idle():
    scenes = [_scene(f"scene-{i:02d}", "hook" if i == 1 else "subtitle", 12.0) for i in range(1, 10)]
    res = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-x")
    assert all(c["mode"] == "talking" for c in res["cues"])


def test_every_eligible_scene_gets_one_cue():
    # No frequency/spacing: every eligible scene gets exactly one cue.
    scenes = [_scene(f"scene-{i:02d}", "subtitle", 6.0) for i in range(1, 13)]
    res = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-x")
    assert len(res["cues"]) == 12


def test_consecutive_hooks_all_large_emphasis():
    # No alternation any more: same-type scenes use the same asset.
    scenes = [_scene(f"scene-{i:02d}", "hook", 9.0) for i in range(1, 6)]
    res = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-x")
    assert res["cues"] and all(
        c["treatment"] == "large" and c["variant"] == "talk-emphasis" for c in res["cues"]
    )


def test_cue_duration_spans_full_scene():
    # Elena spans the FULL scene (renderer loops the clip) — no cap, no mid-sentence cut.
    res = build_elena_cues(
        _doc(_scene("s1", "subtitle", 20.0), _scene("s2", "subtitle", 4.0)), {}, 30, job_id="job-x"
    )
    assert res["cues"][0]["duration_frames"] == 20 * 30
    assert res["cues"][1]["duration_frames"] == 4 * 30


def test_short_scene_still_gets_a_cue():
    res = build_elena_cues(_doc(_scene("scene-01", "subtitle", 3.0)), {}, 30, job_id="job-x")
    assert len(res["cues"]) == 1 and res["cues"][0]["duration_frames"] == 3 * 30


def test_deterministic_by_job_id():
    scenes = [_scene(f"scene-{i:02d}", "subtitle", 12.0) for i in range(1, 20)]
    a = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-A")
    b = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-A")
    assert a["cues"] == b["cues"]  # reproducible


def test_metrics_and_qa_present():
    scenes = [_scene("scene-01", "hook", 10.0)] + [_scene(f"scene-{i:02d}", "subtitle", 12.0) for i in range(2, 12)]
    res = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-x")
    m = res["metrics"]
    assert "appearance_count" in m and "talking_pct" in m and "visible_pct" in m
    assert res["qa"]["verdict"] == "PASS"
    assert res["schema_version"] == 1
    assert res["fps"] == 30
