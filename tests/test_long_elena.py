"""Unit tests for the long-form Elena cue planner (``video_agent.visual.elena``).

Locks the §6 ruleset: talking+hidden only (no idle), 5-10s appearances spaced
>=15s, no adjacent same asset, graphic/evidence scenes hidden, hook -> large
emphasis, deterministic by job_id.
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


def test_appearances_spaced_at_least_15s_apart():
    # Force every scene to want talking via annotation; spacing must still hold.
    scenes = [_scene(f"scene-{i:02d}", "subtitle", 6.0, elena={"mode": "talking"}) for i in range(1, 13)]
    res = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-x")
    cues = res["cues"]
    for i in range(1, len(cues)):
        gap = cues[i]["start_frame"] - (cues[i - 1]["start_frame"] + cues[i - 1]["duration_frames"])
        assert gap >= 15 * 30, f"gap {gap} < 15s at cue {i}"


def test_no_two_consecutive_cues_share_asset():
    scenes = [_scene(f"scene-{i:02d}", "hook", 9.0, elena={"mode": "talking"}) for i in range(1, 13)]
    res = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-x")
    cues = res["cues"]
    for i in range(1, len(cues)):
        assert cues[i]["variant"] != cues[i - 1]["variant"]


def test_appearance_duration_per_treatment():
    # circle (subtitle/neutral) 6-10s; large (hook/emphasis) 5-8s.
    scenes = [_scene(f"scene-{i:02d}", "subtitle", 20.0, elena={"mode": "talking"}) for i in range(1, 8)]
    res = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-x")
    for c in res["cues"]:
        if c["treatment"] == "circle":
            assert 6 * 30 <= c["duration_frames"] <= 10 * 30
        else:
            assert 5 * 30 <= c["duration_frames"] <= 8 * 30

    hooks = [_scene(f"h{i}", "hook", 20.0, elena={"mode": "talking"}) for i in range(1, 6)]
    rh = build_elena_cues(_doc(*hooks), {}, 30, job_id="job-x")
    larges = [c for c in rh["cues"] if c["treatment"] == "large"]
    assert larges and all(5 * 30 <= c["duration_frames"] <= 8 * 30 for c in larges)


def test_short_scene_below_5s_gets_no_cue():
    res = build_elena_cues(
        _doc(_scene("scene-01", "subtitle", 3.0, elena={"mode": "talking"})), {}, 30, job_id="job-x"
    )
    assert res["cues"] == []


def test_deterministic_by_job_id():
    scenes = [_scene(f"scene-{i:02d}", "subtitle", 12.0) for i in range(1, 20)]
    a = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-A")
    b = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-A")
    assert a["cues"] == b["cues"]  # reproducible


def test_metrics_and_qa_present():
    scenes = [_scene("scene-01", "hook", 10.0)] + [_scene(f"scene-{i:02d}", "subtitle", 12.0) for i in range(2, 12)]
    res = build_elena_cues(_doc(*scenes), {}, 30, job_id="job-x")
    m = res["metrics"]
    assert "appearance_count" in m and "talking_pct" in m and "min_gap_sec" in m
    assert res["qa"]["verdict"] == "PASS"
    assert res["schema_version"] == 1
    assert res["fps"] == 30
