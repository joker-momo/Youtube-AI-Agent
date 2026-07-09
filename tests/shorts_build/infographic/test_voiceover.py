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
