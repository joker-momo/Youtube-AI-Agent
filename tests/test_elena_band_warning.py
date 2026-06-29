"""Elena simple per-scene rule (no frequency/cadence/band).

Three categories, decided purely by layout (+ annotation overrides):
  - HIDDEN (no cue): {checklist, quote, cta} (uncoverable), elena.mode="hidden",
    wordy ``warning`` (unless annotation forces it).
  - LARGE (large/talk-emphasis): ``hook`` + eligible ``warning`` (short label).
  - CIRCLE (circle/talk-neutral): ``subtitle`` (and any other non-hidden layout).

Every eligible scene gets exactly ONE cue spanning the scene (capped to the clip);
hidden scenes get none. No spacing, no visibility band.
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


# --------------------------------------------------------------------------- #
# HIDDEN
# --------------------------------------------------------------------------- #
def test_hard_hidden_set_no_cue():
    for layout in ("checklist", "quote", "cta"):
        res = build_elena_cues(_doc(_scene("scene-01", layout, on_screen_text="corto")), {}, _FPS)
        assert res["cues"] == [], layout


def test_warning_wordy_is_hidden():
    wordy = "Consulta siempre con tu medico antes de cambiar cualquier parte de tu dieta diaria"
    res = build_elena_cues(_doc(_scene("scene-01", "warning", on_screen_text=wordy)), {}, _FPS)
    assert res["cues"] == []


def test_mode_hidden_annotation_hides():
    res = build_elena_cues(_doc(_scene("scene-01", "subtitle", elena={"mode": "hidden"})), {}, _FPS)
    assert res["cues"] == []


def test_annotation_cannot_override_hard_hidden():
    for layout in ("checklist", "quote", "cta"):
        res = build_elena_cues(
            _doc(_scene("scene-01", layout, elena={"treatment": "large", "mode": "talking"})), {}, _FPS
        )
        assert res["cues"] == [], layout


# --------------------------------------------------------------------------- #
# LARGE
# --------------------------------------------------------------------------- #
def test_hook_is_large_emphasis():
    c = build_elena_cues(_doc(_scene("scene-01", "hook")), {}, _FPS)["cues"][0]
    assert c["treatment"] == "large" and c["variant"] == "talk-emphasis"
    assert c["position"] == "bottom-right"


def test_warning_short_is_large():
    c = build_elena_cues(
        _doc(_scene("scene-01", "warning", on_screen_text="No te saltes el desayuno")), {}, _FPS
    )["cues"][0]
    assert c["treatment"] == "large"


def test_annotation_large_shows_wordy_warning():
    wordy = "Consulta siempre con tu medico antes de cambiar tu dieta o tomar suplementos"
    res = build_elena_cues(
        _doc(_scene("scene-01", "warning", on_screen_text=wordy, elena={"treatment": "large"})), {}, _FPS
    )
    assert len(res["cues"]) == 1 and res["cues"][0]["treatment"] == "large"


# --------------------------------------------------------------------------- #
# CIRCLE
# --------------------------------------------------------------------------- #
def test_subtitle_is_circle_neutral():
    c = build_elena_cues(_doc(_scene("scene-01", "subtitle")), {}, _FPS)["cues"][0]
    assert c["treatment"] == "circle" and c["variant"] == "talk-neutral"


# --------------------------------------------------------------------------- #
# NO FREQUENCY — one cue per eligible scene, every eligible scene shows
# --------------------------------------------------------------------------- #
def test_one_cue_per_eligible_scene():
    scenes = [
        _scene("scene-01", "hook"),
        _scene("scene-02", "subtitle"),
        _scene("scene-03", "checklist"),  # hidden
        _scene("scene-04", "subtitle"),
        _scene("scene-05", "quote"),  # hidden
        _scene("scene-06", "warning", on_screen_text="Cuidado con la sal"),  # large
        _scene("scene-07", "cta"),  # hidden
    ]
    res = build_elena_cues(_doc(*scenes), {}, _FPS)
    # 4 eligible (hook, 2 subtitle, short warning) -> 4 cues; 3 hidden -> none.
    assert res["metrics"]["appearance_count"] == 4
    treatments = [c["treatment"] for c in res["cues"]]
    assert treatments == ["large", "circle", "circle", "large"]


def test_every_subtitle_shows_no_skipping():
    scenes = [_scene(f"scene-{i:02d}", "subtitle", 12.0) for i in range(1, 9)]
    res = build_elena_cues(_doc(*scenes), {}, _FPS)
    assert res["metrics"]["appearance_count"] == 8  # all 8, none skipped for frequency
    assert all(c["treatment"] == "circle" for c in res["cues"])


def test_short_scene_still_shows():
    # No minimum-frequency skip: even a 3s subtitle gets a cue.
    res = build_elena_cues(_doc(_scene("scene-01", "subtitle", 3.0)), {}, _FPS)
    assert len(res["cues"]) == 1
    assert res["cues"][0]["duration_frames"] == 3 * _FPS


# --------------------------------------------------------------------------- #
# cue mechanics
# --------------------------------------------------------------------------- #
def test_cue_starts_at_scene_start():
    scenes = [_scene("scene-01", "subtitle", 10.0), _scene("scene-02", "subtitle", 12.0)]
    res = build_elena_cues(_doc(*scenes), {}, _FPS)
    assert res["cues"][0]["start_frame"] == 0
    assert res["cues"][1]["start_frame"] == 10 * _FPS  # scene-02 boundary


def test_cue_trims_first_second():
    c = build_elena_cues(_doc(_scene("scene-01", "hook", 10.0)), {}, _FPS)["cues"][0]
    assert c["source_trim_frames"] == _FPS  # 1.0s


def test_duration_spans_full_scene():
    # Elena spans the FULL scene (renderer loops the ~10s clip) — never capped/cut
    # mid-sentence. A 30s scene → a 30s cue.
    c = build_elena_cues(_doc(_scene("scene-01", "subtitle", 30.0)), {}, _FPS)["cues"][0]
    assert c["duration_frames"] == 30 * _FPS


def test_deterministic():
    a = build_elena_cues(_doc(_scene("s1", "hook"), _scene("s2", "subtitle")), {}, _FPS, job_id="j")
    b = build_elena_cues(_doc(_scene("s1", "hook"), _scene("s2", "subtitle")), {}, _FPS, job_id="j")
    assert a["cues"] == b["cues"]


def test_qa_verdict_pass_always():
    res = build_elena_cues(_doc(_scene("scene-01", "subtitle")), {}, _FPS)
    assert res["qa"]["verdict"] == "PASS"
    assert res["qa"]["errors"] == []
