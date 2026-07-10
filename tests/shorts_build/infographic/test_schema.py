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
    assert set(POSTER_FORMATS) == {
        "category_grid", "numbered_tips", "warning_list", "comparison",
        "myth_vs_truth", "timeline_routine", "checklist_score",
    }


def test_myth_vs_truth_requires_truth_note_and_allows_longer_labels():
    items = [{"label": "el café deshidrata siempre", "note": "hidrata casi igual"} for _ in range(4)]
    assert validate_poster_plan(_plan(poster_format="myth_vs_truth", items=items)) == []
    # A myth without its truth is invalid.
    missing = [{"label": "el café deshidrata", "note": ""} for _ in range(4)]
    issues = validate_poster_plan(_plan(poster_format="myth_vs_truth", items=missing))
    assert any("note" in i.lower() or "verdad" in i.lower() for i in issues)


def test_timeline_routine_requires_time_per_item():
    items = [{"label": "café suave", "time": "7:00"},
             {"label": "paseo corto", "time": "14h30"},
             {"label": "sin pantallas", "time": "22:00"}]
    assert validate_poster_plan(_plan(poster_format="timeline_routine", items=items)) == []
    no_time = [{"label": "café suave"}, {"label": "paseo"}, {"label": "cena ligera"}]
    issues = validate_poster_plan(_plan(poster_format="timeline_routine", items=no_time))
    assert any("time" in i.lower() for i in issues)


def test_checklist_score_allows_four_word_labels():
    items = [{"label": "duermo siete horas seguidas"} for _ in range(5)]
    plan = _plan(poster_format="checklist_score", items=items, score_line="4+ = vas bien")
    assert validate_poster_plan(plan) == []


def test_legacy_idea_format_aliases_map_to_poster_formats():
    from video_agent.shorts.infographic.schema import resolve_poster_format
    assert resolve_poster_format("mistake_list") == "warning_list"
    assert resolve_poster_format("myth_truth") == "myth_vs_truth"
    assert resolve_poster_format("checklist") == "numbered_tips"
    assert resolve_poster_format("top_tips") == "numbered_tips"
    assert resolve_poster_format("problem_solution") == "comparison"
    assert resolve_poster_format("recap") == "category_grid"
    assert resolve_poster_format("timeline_routine") == "timeline_routine"  # identity
    assert resolve_poster_format("unknown_junk") == ""  # unmapped -> let the LLM pick
