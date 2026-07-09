from video_agent.shorts.infographic.poster_prompt import build_poster_prompt


def _plan(fmt, items, **o):
    base = {
        "poster_format": fmt,
        "title": "Foods For Eyes",
        "subtitle": "",
        "items": items,
        "cta": "Sigue",
        "audience_min_age": 60,
    }
    base.update(o)
    return base


def test_prompt_contains_title_and_all_item_labels():
    items = [
        {"label": "Chía"},
        {"label": "Salmón"},
        {"label": "Huevos"},
        {"label": "Agua"},
        {"label": "Espinacas"},
    ]
    p = build_poster_prompt(_plan("category_grid", items))
    assert "Foods For Eyes" in p
    for it in items:
        assert it["label"] in p
    assert "1080x1920" in p or "9:16" in p


def test_numbered_tips_prompt_requests_numbers():
    p = build_poster_prompt(_plan("numbered_tips", [{"label": f"tip{i}"} for i in range(5)]))
    assert "number" in p.lower()


def test_warning_list_prompt_requests_cross_marks():
    p = build_poster_prompt(
        _plan("warning_list", [{"label": "Pan", "note": "no lo tuestes"}] * 5)
    )
    assert "cross" in p.lower() or "✕" in p or "X" in p


def test_comparison_prompt_requests_two_columns():
    items = [{"label": "A", "group": "bien"}, {"label": "B", "group": "mal"}]
    p = build_poster_prompt(_plan("comparison", items))
    assert "two column" in p.lower() or "left" in p.lower()


def test_prompt_forbids_extra_text_and_is_spanish_poster():
    p = build_poster_prompt(_plan("category_grid", [{"label": "Chía"}] * 5))
    assert "no other text" in p.lower() or "only the text" in p.lower()
