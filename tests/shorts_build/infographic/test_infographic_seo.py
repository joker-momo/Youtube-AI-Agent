import json

from video_agent.shorts.infographic.seo import build_infographic_seo

PLAN = {
    "title": "Vista",
    "hook_line": "Si tienes más de 60: cuida tu vista",
    "items": [{"label": "Chía"}, {"label": "Salmón"}, {"label": "Huevos"}],
    "cta": "Sigue",
}


def test_build_infographic_seo_writes_title_aligned_with_hook(tmp_path):
    # Return a valid scroll-stopper formula title that shares a word with the hook
    # (a non-formula title would be rejected/replaced by the shipped title validator).
    def llm_fn(prompt):
        return json.dumps({
            "title": "Si tienes más de 60: cuida vista",
            "description": "Alimentos para la vista.",
            "hashtags": ["#vista", "#shorts"],
            "pinned_comment": "¿Cuidas tu vista?",
        })

    seo = build_infographic_seo(
        tmp_path / "job-1", "short-01", PLAN, {"audience": {"age_range": [45, 75]}}, llm_fn,
    )

    assert len(seo["title"]) <= 40
    assert "vista" in seo["title"].lower()
    seo_file = tmp_path / "job-1" / "shorts" / "short-01" / "json" / "short_seo.json"
    assert seo_file.exists()
    assert json.loads(seo_file.read_text())["title"] == seo["title"]
