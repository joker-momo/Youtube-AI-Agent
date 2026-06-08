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
