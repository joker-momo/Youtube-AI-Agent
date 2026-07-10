from video_agent.shorts.infographic.render_props import build_infographic_render_props


def test_props_reference_poster_and_audio_and_duration():
    props = build_infographic_render_props(
        poster_ref="jobs/short-01/assets/poster.png",
        audio_ref="jobs/short-01/audio/short_narration.wav",
        duration_sec=28.0,
        music_track="shorts_sleep_stress",
        channel_name="Vida Plena 45+",
    )
    assert props["poster"] == "jobs/short-01/assets/poster.png"
    assert props["audio"] == "jobs/short-01/audio/short_narration.wav"
    assert props["durationInFrames"] == 28 * 30
    assert props["music"] == "shorts_sleep_stress"
    assert props["width"] == 1080 and props["height"] == 1920
    # A music-only infographic holds one poster completely still for readability.
    assert props["kenBurns"] is False
    # Any explicit Ken Burns motion remains capped so baked-in poster text is never cropped.
    assert props["kenBurnsScaleMax"] <= 1.02
    # Overlays OFF by default (safe area — poster carries all text).
    assert props["showSubscribeCue"] is False
    # Renders the InfographicShort composition; concurrency stays "auto" (HARD RULE).
    assert props["render"]["composition"] == "InfographicShort"
    assert props["render"]["concurrency"] == "auto"
