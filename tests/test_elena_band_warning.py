"""Elena planner v3 — content-aware warning, annotation-large override, band QA.

Locks the three Part-1 behaviours on top of the §6 ruleset:

A. ``warning`` is content-aware: a warning scene with a SHORT on-screen label
   (<=8 words) may host a LARGE/emphasis Elena; a wordy warning stays hidden.
   The Elena hard-hidden set is exactly ``{checklist, quote, cta}`` — warning is
   NOT in it.
B. Annotation can force LARGE: ``scene.elena.treatment="large"`` (or
   ``mode="talking"``) wins over the warning forced-hidden default — but
   ``checklist``/``quote``/``cta`` stay hidden no matter what the annotation says.
C. ``build_elena_cues`` enforces the visibility band: verdict FAILs when
   ``visible_pct`` leaves [20, 30] or any gap exceeds 30s, while spacing >=15s,
   the no-adjacent-asset rule and determinism still hold.
"""

from __future__ import annotations

from video_agent.visual.elena import build_elena_cues

_FPS = 30


def _scene(sid, layout="subtitle", dur=12.0, **extra):
    s = {"id": sid, "layout": layout, "duration_sec": dur}
    s.update(extra)
    return s


def _doc(*scenes, job_id="job-x"):
    return {"job_id": job_id, "scenes": list(scenes)}


def _gaps_sec(cues, fps=_FPS):
    return [
        (cues[i]["start_frame"] - (cues[i - 1]["start_frame"] + cues[i - 1]["duration_frames"])) / fps
        for i in range(1, len(cues))
    ]


# --------------------------------------------------------------------------- #
# A. content-aware warning + hard-hidden set
# --------------------------------------------------------------------------- #
def test_warning_short_label_gets_large_cue():
    res = build_elena_cues(
        _doc(_scene("scene-01", "warning", 10.0, on_screen_text="No te saltes el desayuno")),
        {},
        _FPS,
        job_id="job-x",
    )
    assert len(res["cues"]) == 1
    cue = res["cues"][0]
    assert cue["mode"] == "talking"
    assert cue["treatment"] == "large"
    assert cue["variant"] == "talk-emphasis"
    assert cue["position"] == "bottom-right"


def test_warning_wordy_label_is_hidden():
    wordy = "Consulta siempre con tu medico antes de cambiar cualquier parte de tu dieta diaria"
    res = build_elena_cues(
        _doc(_scene("scene-01", "warning", 10.0, on_screen_text=wordy)),
        {},
        _FPS,
        job_id="job-x",
    )
    assert res["cues"] == []


def test_hard_hidden_set_is_checklist_quote_cta_only():
    for layout in ("checklist", "quote", "cta"):
        res = build_elena_cues(
            _doc(_scene("scene-01", layout, 12.0, on_screen_text="corto")),
            {},
            _FPS,
            job_id="job-x",
        )
        assert res["cues"] == [], f"{layout} must hide Elena"


# --------------------------------------------------------------------------- #
# B. annotation-large override
# --------------------------------------------------------------------------- #
def test_annotation_large_wins_over_wordy_warning():
    wordy = "Consulta siempre con tu medico antes de cambiar cualquier parte de tu dieta"
    res = build_elena_cues(
        _doc(_scene("scene-01", "warning", 10.0, on_screen_text=wordy, elena={"treatment": "large"})),
        {},
        _FPS,
        job_id="job-x",
    )
    assert len(res["cues"]) == 1
    assert res["cues"][0]["treatment"] == "large"


def test_annotation_talking_shows_wordy_warning():
    wordy = "Consulta siempre con tu medico antes de cambiar cualquier parte de tu dieta"
    res = build_elena_cues(
        _doc(_scene("scene-01", "warning", 10.0, on_screen_text=wordy, elena={"mode": "talking"})),
        {},
        _FPS,
        job_id="job-x",
    )
    assert len(res["cues"]) == 1


def test_annotation_cannot_override_hard_hidden():
    for layout in ("checklist", "quote", "cta"):
        res = build_elena_cues(
            _doc(_scene("scene-01", layout, 12.0, elena={"treatment": "large", "mode": "talking"})),
            {},
            _FPS,
            job_id="job-x",
        )
        assert res["cues"] == [], f"annotation must not unhide {layout}"


# --------------------------------------------------------------------------- #
# C. band QA — visible_pct in [20,30], every gap in [15,30]s, verdict PASS
# --------------------------------------------------------------------------- #
def _long_doc():
    # hook + 19 subtitle scenes ~12s each -> ~240s, room for a 25% cadence.
    scenes = [_scene("scene-01", "hook", 12.0)]
    scenes += [_scene(f"scene-{i:02d}", "subtitle", 12.0) for i in range(2, 21)]
    return _doc(*scenes)


def test_realistic_plan_lands_in_band_and_passes():
    res = build_elena_cues(_long_doc(), {}, _FPS, job_id="job-x")
    vis = res["metrics"]["visible_pct"]
    assert 20.0 <= vis <= 30.0, f"visible_pct {vis} out of band"
    for g in _gaps_sec(res["cues"]):
        assert 15.0 <= g <= 30.0, f"gap {g}s out of [15,30]"
    assert res["qa"]["verdict"] == "PASS"


def test_band_qa_fails_when_visible_pct_out_of_band():
    # A single short hook -> one cue spanning most of the comp -> visible_pct >> 30.
    res = build_elena_cues(_doc(_scene("scene-01", "hook", 8.0)), {}, _FPS, job_id="job-x")
    assert res["metrics"]["visible_pct"] > 30.0
    assert res["qa"]["verdict"] == "FAIL"
    assert any("visible_pct" in e for e in res["qa"]["errors"])


def test_band_qa_fails_on_gap_above_30s():
    # Two eligible hooks separated by a long hard-hidden run -> gap > 30s.
    scenes = [
        _scene("scene-01", "hook", 8.0),
        _scene("scene-02", "checklist", 20.0),
        _scene("scene-03", "checklist", 20.0),
        _scene("scene-04", "hook", 8.0),
    ]
    res = build_elena_cues(_doc(*scenes), {}, _FPS, job_id="job-x")
    gaps = _gaps_sec(res["cues"])
    assert gaps and max(gaps) > 30.0
    assert res["qa"]["verdict"] == "FAIL"
    assert any("gap_above_max" in e for e in res["qa"]["errors"])


def test_spacing_at_least_15s_and_no_adjacent_asset():
    res = build_elena_cues(_long_doc(), {}, _FPS, job_id="job-x")
    cues = res["cues"]
    assert len(cues) >= 3
    for g in _gaps_sec(cues):
        assert g >= 15.0
    for i in range(1, len(cues)):
        assert cues[i]["variant"] != cues[i - 1]["variant"]


def test_band_plan_is_deterministic():
    a = build_elena_cues(_long_doc(), {}, _FPS, job_id="job-determinism")
    b = build_elena_cues(_long_doc(), {}, _FPS, job_id="job-determinism")
    assert a["cues"] == b["cues"]


def test_metrics_expose_max_gap():
    res = build_elena_cues(_long_doc(), {}, _FPS, job_id="job-x")
    m = res["metrics"]
    assert "max_gap_sec" in m and "min_gap_sec" in m
    assert m["max_gap_sec"] is not None


# --------------------------------------------------------------------------- #
# Subtitle screens use the SMALL (circle) overlay; assets still alternate.
# (The running word-highlight caption band reaches bottom-right, so a LARGE
# overlay would collide — size is decoupled from the alternating asset.)
# --------------------------------------------------------------------------- #
def test_subtitle_layout_always_uses_circle():
    scenes = [_scene(f"scene-{i:02d}", "subtitle", 14.0, elena={"mode": "talking"}) for i in range(1, 13)]
    res = build_elena_cues(_doc(*scenes), {}, _FPS, job_id="job-x")
    cues = res["cues"]
    assert len(cues) >= 3
    assert all(c["treatment"] == "circle" for c in cues), [c["treatment"] for c in cues]
    for i in range(1, len(cues)):  # assets still never repeat back-to-back
        assert cues[i]["variant"] != cues[i - 1]["variant"]


def test_hook_and_warning_stay_large():
    # Memory-beat layouts keep the LARGE overlay (their overlay is not the band).
    hook = build_elena_cues(_doc(_scene("s1", "hook", 10.0)), {}, _FPS, job_id="j")
    assert hook["cues"][0]["treatment"] == "large"
    warn = build_elena_cues(
        _doc(_scene("s1", "warning", 10.0, on_screen_text="No te saltes el desayuno")), {}, _FPS, job_id="j"
    )
    assert warn["cues"][0]["treatment"] == "large"


def test_cue_trims_first_second_of_clip():
    res = build_elena_cues(_doc(_scene("scene-01", "hook", 10.0)), {}, _FPS, job_id="job-x")
    assert res["cues"][0]["source_trim_frames"] == _FPS  # skip the clip's first 1.0s


def test_long_scene_hosts_multiple_cues_no_oversized_gap():
    # A single long eligible scene must host SEVERAL cues (cadence fill), so it
    # never leaves a gap above the 30s max — a one-cue-per-scene planner would
    # leave the whole scene as one >30s gap.
    res = build_elena_cues(_doc(_scene("s1", "subtitle", 80.0, elena={"mode": "talking"})), {}, _FPS, job_id="j")
    cues = res["cues"]
    assert len(cues) >= 2, [c["start_frame"] for c in cues]
    for g in _gaps_sec(cues):
        assert 15.0 <= g <= 30.0, f"gap {g}s out of [15,30]"
    for i in range(1, len(cues)):  # asset alternation still holds within one scene
        assert cues[i]["variant"] != cues[i - 1]["variant"]


def test_cue_duration_never_exceeds_clip_after_trim():
    # trim (1s) + duration must stay within the ~10s clip (no tail freeze), even
    # for a circle cue (bounds up to 10s) on a very long scene.
    res = build_elena_cues(_doc(_scene("s1", "subtitle", 120.0, elena={"mode": "talking"})), {}, _FPS, job_id="j")
    for c in res["cues"]:
        assert c["source_trim_frames"] + c["duration_frames"] <= 10 * _FPS
