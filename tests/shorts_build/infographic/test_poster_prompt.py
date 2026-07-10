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


def test_poster_prompt_enforces_label_vs_note_type_hierarchy():
    """Operator audit (82/100): item sub-headings must be much larger/bolder
    than the small explanation text — a phone viewer should get the message
    from the sub-headings alone."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body({
        "poster_format": "warning_list", "title": "Errores con café",
        "items": [{"label": "Mismo efecto", "note": "no todos los cafés sientan igual"}],
    })
    lowered = body.lower()
    assert "typographic hierarchy" in lowered
    assert "label" in lowered and "bold" in lowered


def test_myth_vs_truth_layout_renders_mito_verdad_pairs():
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body({
        "poster_format": "myth_vs_truth", "title": "Mitos del café",
        "items": [{"label": "el café deshidrata", "note": "hidrata casi igual"}],
    })
    assert "Mito: el café deshidrata" in body
    assert "Verdad: hidrata casi igual" in body
    assert "red CROSS" in body and "green CHECK" in body


def test_timeline_routine_layout_renders_times_in_order():
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body({
        "poster_format": "timeline_routine", "title": "Tu día sin insomnio",
        "items": [{"label": "café suave", "time": "7:00"},
                  {"label": "sin pantallas", "time": "22:00"}],
    })
    assert "TIMELINE" in body.upper()
    assert "7:00 — café suave" in body
    assert "22:00 — sin pantallas" in body


def test_checklist_score_layout_renders_checkboxes_and_score_line():
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body({
        "poster_format": "checklist_score", "title": "¿Cuántos cumples?",
        "items": [{"label": "duermo siete horas"}],
        "score_line": "4+ = vas bien",
    })
    assert "checkbox" in body.lower()
    assert "duermo siete horas" in body
    assert "4+ = vas bien" in body
