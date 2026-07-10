from __future__ import annotations


def test_audio_visual_delta_uses_tail_constants():
    from video_agent.shorts import validate_scenes

    s = validate_scenes.audio_sync_summary(render_duration_sec=28.6, narration_audio_sec=27.9)
    # thresholds are derived from the real tail-margin/buffer constants, not hardcoded.
    assert s["tail_margin_sec"] == validate_scenes.AUDIO_TAIL_MARGIN_SEC
    assert s["tail_buffer_sec"] == validate_scenes.AUDIO_TAIL_REPAIR_BUFFER_SEC
    expected_pass = round(
        validate_scenes.AUDIO_TAIL_MARGIN_SEC
        + validate_scenes.AUDIO_TAIL_REPAIR_BUFFER_SEC
        + validate_scenes.AUDIO_SYNC_EPSILON_SEC,
        3,
    )
    assert s["pass_delta_sec"] == expected_pass
    assert s["warn_delta_sec"] == round(expected_pass + 0.5, 3)


def test_audio_visual_delta_pass_threshold():
    from video_agent.shorts import validate_scenes

    s = validate_scenes.audio_sync_summary(render_duration_sec=28.6, narration_audio_sec=27.9)
    assert s["audio_visual_delta_sec"] == 0.7
    assert s["verdict"] == "PASS"


def test_audio_visual_delta_warn_threshold():
    from video_agent.shorts import validate_scenes

    # delta just above PASS (0.95) but below WARN cap (1.45)
    s = validate_scenes.audio_sync_summary(render_duration_sec=29.1, narration_audio_sec=28.0)
    assert s["audio_visual_delta_sec"] == 1.1
    assert s["verdict"] == "WARN"


def test_audio_visual_delta_fail_threshold():
    from video_agent.shorts import validate_scenes

    # original desync bug: ~11s gap must FAIL
    s = validate_scenes.audio_sync_summary(render_duration_sec=30.2, narration_audio_sec=19.4)
    assert s["verdict"] == "FAIL"


def _scenes(*specs):
    return {"scenes": [{"id": sid, "layout": lay, "duration_sec": d} for sid, lay, d in specs]}


def test_tail_repair_prefers_final_scene():
    from video_agent.shorts import validate_scenes
    doc = _scenes(("s06", "short_tip", 3.0), ("s07", "short_tip", 3.0), ("s08", "short_tip", 3.0))
    out = validate_scenes.extend_scene_durations_for_audio_tail(doc, 8.55)
    assert out["changed"] is True
    dist = out["tail_repair_distribution"]
    assert [d["scene_id"] for d in dist] == ["s08"], dist
    assert doc["scenes"][0]["duration_sec"] == 3.0  # s06 untouched
    assert doc["scenes"][1]["duration_sec"] == 3.0  # s07 untouched
    assert doc["scenes"][2]["duration_sec"] > 3.0   # s08 extended


def test_tail_repair_distributes_backward_when_needed():
    from video_agent.shorts import validate_scenes
    # s08 (CTA) already at its hard max (5.5s since bug-505) → tail must go to
    # the prior scene.
    doc = _scenes(("s06", "short_tip", 3.0), ("s07", "short_tip", 3.0), ("s08", "short_cta", 5.5))
    out = validate_scenes.extend_scene_durations_for_audio_tail(doc, 11.1)
    assert out["changed"] is True
    ids = [d["scene_id"] for d in out["tail_repair_distribution"]]
    assert "s07" in ids and "s08" not in ids, ids


def test_audio_sync_summary_includes_distribution():
    from video_agent.shorts import validate_scenes
    s = validate_scenes.audio_sync_summary(
        render_duration_sec=31.2,
        narration_audio_sec=30.5,
        tail_added_sec=0.7,
        tail_repair_distribution=[{"scene_id": "s08", "added_sec": 0.7}],
    )
    assert s["verdict"] == "PASS"
    assert s["tail_repair_distribution"] == [{"scene_id": "s08", "added_sec": 0.7}]


def test_audio_sync_threshold_still_passes_sample():
    from video_agent.shorts import validate_scenes
    s = validate_scenes.audio_sync_summary(render_duration_sec=31.2, narration_audio_sec=30.5)
    assert s["audio_visual_delta_sec"] == 0.7
    assert s["verdict"] == "PASS"
