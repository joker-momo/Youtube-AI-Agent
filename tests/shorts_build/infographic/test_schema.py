from video_agent.shorts.infographic.schema import (
    POSTER_FORMATS, validate_poster_plan,
)

def _plan(**over):
    base = {
        "short_type": "infographic",
        "poster_format": "category_grid",
        "title": "Alimentos para la vista",
        "subtitle": "",
        "hook_line": "Si tienes más de 60: cuida tu vista",
        "items": [{"label": f"item{i}", "note": "", "group": ""} for i in range(6)],
        "cta": "Sigue para más",
        "audience_min_age": 60,
    }
    base.update(over)
    return base

def test_valid_category_grid_plan_has_no_issues():
    assert validate_poster_plan(_plan()) == []

def test_unknown_format_is_flagged():
    issues = validate_poster_plan(_plan(poster_format="map_grid"))
    assert any("poster_format" in i for i in issues)

def test_category_grid_item_count_bounds():
    assert any("items" in i for i in validate_poster_plan(_plan(items=[{"label": "x"}])))
    big = [{"label": f"i{n}"} for n in range(9)]
    assert any("items" in i for i in validate_poster_plan(_plan(items=big)))

def test_title_word_cap_and_item_word_cap():
    assert any("title" in i for i in validate_poster_plan(_plan(title="una uno dos tres cuatro cinco seis")))
    long_item = {"label": "una etiqueta demasiado larga aquí"}
    assert any("item" in i.lower() for i in validate_poster_plan(_plan(items=[long_item] * 6)))

def test_known_formats_registered():
    assert set(POSTER_FORMATS) == {"category_grid", "numbered_tips", "warning_list", "comparison"}
