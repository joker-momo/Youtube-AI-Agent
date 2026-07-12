from video_agent.shorts.infographic.voiceover import build_narration_text

PLAN = {
    "hook_line": "Si tienes más de 60: cuida tu vista",
    "title": "Alimentos para la vista",
    "items": [{"label": "Chía"}, {"label": "Salmón"}, {"label": "Huevos"}],
    "cta": "Sigue para más",
}


def test_narration_starts_with_hook_line():
    assert build_narration_text(PLAN).startswith("Si tienes más de 60: cuida tu vista")


def test_narration_reads_every_item_and_ends_with_cta():
    text = build_narration_text(PLAN).lower()
    for label in ("chía", "salmón", "huevos"):
        assert label in text
    assert text.strip().endswith("sigue para más")


def test_voiceover_seeds_scene_duration_sec(monkeypatch):
    # Regression: shorts TTS forces dynamic_sync=False and reads scene["duration_sec"];
    # omitting it produced a 44-byte silent wav. The scene must carry a seed duration.
    from pathlib import Path

    import video_agent.shorts.audio as audio_mod
    captured = {}

    def fake_synth(short_dir, scene_doc, cfg):
        captured["doc"] = scene_doc
        return Path(short_dir) / "audio" / "short_narration.wav"

    monkeypatch.setattr(audio_mod, "synthesize_short_narration", fake_synth)
    from video_agent.shorts.infographic.voiceover import synthesize_infographic_voiceover
    synthesize_infographic_voiceover("/tmp/ig-x", {"hook_line": "Hola", "items": [{"label": "Agua"}], "cta": "Sigue"}, {})
    scene = captured["doc"]["scenes"][0]
    assert scene.get("duration_sec", 0) >= 1
    assert scene["narration"].strip()
