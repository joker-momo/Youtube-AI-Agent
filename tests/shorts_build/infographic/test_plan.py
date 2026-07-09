import json

from video_agent.shorts.infographic.plan import build_poster_plan

CFG = {"audience": {"age_range": [45, 75]}}

def test_build_poster_plan_parses_validates_and_sets_age():
    good = {
        "poster_format": "category_grid",
        "title": "Alimentos para la vista",
        "hook_line": "Si tienes más de 60: cuida tu vista",
        "items": [{"label": f"i{n}"} for n in range(6)],
        "cta": "Sigue",
    }
    def llm_fn(prompt):
        return json.dumps(good)
    plan = build_poster_plan(CFG, {"topic": "vista después de los 60"}, llm_fn)
    assert plan["poster_format"] == "category_grid"
    assert plan["short_type"] == "infographic"
    assert plan["audience_min_age"] == 60

def test_invalid_then_valid_retries():
    calls = {"n": 0}
    def llm_fn(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"poster_format": "map_grid", "title": "x", "items": []})
        return json.dumps({"poster_format": "numbered_tips", "title": "Tips",
                           "hook_line": "Si tienes más de 45: haz esto",
                           "items": [{"label": f"t{n}"} for n in range(5)], "cta": "Sigue"})
    plan = build_poster_plan(CFG, {"topic": "habitos"}, llm_fn)
    assert plan["poster_format"] == "numbered_tips"
    assert calls["n"] == 2
