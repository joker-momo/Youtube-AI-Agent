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


def test_myth_vs_truth_layout_renders_two_column_numbered_cards():
    """Operator reference (2026-07-10): a sample myth/truth poster used TWO
    side-by-side columns (Mito cards left / Verdad cards right, numbered,
    color-tinted), not a single stacked column. Redesigned to match, minus
    the mascot/speech-bubble the operator explicitly excluded."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body({
        "poster_format": "myth_vs_truth", "title": "Mitos del café",
        "items": [{"label": "el café deshidrata", "note": "hidrata casi igual"}],
    })
    assert "el café deshidrata" in body
    assert "hidrata casi igual" in body
    assert "two-column" in body.lower() or "two column" in body.lower()
    assert "MITO 1" in body and "VERDAD 1" in body
    assert "red CROSS" in body and "green CHECK" in body
    # No mascot/speech-bubble treatment (operator explicitly scoped it out).
    assert "mascot" not in body.lower()


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


def test_poster_body_carries_channel_brand_identity_without_watermark_ban():
    """Operator iterated: corner tag (too faint) -> bottom banner + separate trust
    badge (redundant, two brand marks saying the same thing) -> FINAL: a single
    small badge near the bottom showing the full channel name, no bottom banner.
    Must not contradict the base prompt's "no watermark" rule."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body, _BASE
    body = build_poster_body(
        {"poster_format": "numbered_tips", "title": "Foods For Eyes",
         "items": [{"label": f"i{n}"} for n in range(5)]},
        channel_config={"channel": {"name": "Vida Plena 45+: Salud y Bienestar"}},
    )
    assert "Vida Plena 45+: Salud y Bienestar" in body
    assert "add a mascot" not in body.lower()
    # The exception must be carved out of the base "no watermark" rule, not just
    # bolted on afterward (bug: the base rule silently won, AI skipped the tag).
    assert "except the channel brand" in _BASE.lower()


def test_poster_header_uses_two_tone_title_and_decorative_accents():
    """Operator reference (2026-07-10): visual polish pass on the header — a
    two-line, two-color bold title, a rounded pill badge for the subtitle, small
    decorative topic icons, and a dotted separator under the header. No mascot
    (explicitly excluded by the operator)."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body({
        "poster_format": "numbered_tips", "title": "Foods For Eyes",
        "subtitle": "Cuida tu vista",
        "items": [{"label": f"i{n}"} for n in range(5)],
    })
    lowered = body.lower()
    assert "two lines" in lowered or "two bold lines" in lowered
    assert "pill" in lowered or "badge" in lowered
    assert "dotted" in lowered
    assert "mascot" not in lowered


def test_poster_body_adds_single_brand_badge_no_duplicate_banner():
    """Operator follow-up: the badge originally read "Fuente oficial: {short_name}"
    while a SEPARATE bottom banner also showed the channel name — two marks saying
    the same thing. Final ask: change the badge text to the full channel name and
    drop the redundant banner entirely."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body(
        {"poster_format": "numbered_tips", "title": "Foods For Eyes",
         "items": [{"label": f"i{n}"} for n in range(5)]},
        channel_config={"channel": {"name": "Vida Plena 45+: Salud y Bienestar"}},
    )
    lowered = body.lower()
    assert "vida plena 45+: salud y bienestar" in lowered
    assert "fuente oficial" not in lowered  # dropped prefix, use the full name instead
    # Only ONE brand mark instruction (the badge) -- no separate bottom banner/bar.
    assert "add a bottom banner" not in lowered


def test_brand_badge_has_no_hardcoded_leaf_motif():
    """Bug found via operator question ("sao poster banh mi lai gen ra co hinh la"):
    a fixed "green leaf icon accents" instruction was baked into EVERY poster's
    brand-identity line regardless of topic (coffee, bread, sleep...) -- unlike the
    header's topic-adaptive decorative icons. Operator chose to drop the fixed
    motif entirely and let decorative accents near the badge follow the topic,
    same as the header icons."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body(
        {"poster_format": "numbered_tips", "title": "Foods For Eyes",
         "items": [{"label": f"i{n}"} for n in range(5)]},
        channel_config={"channel": {"name": "Vida Plena 45+: Salud y Bienestar"}},
    )
    lowered = body.lower()
    assert "leaf" not in lowered
    # Still asks for SOME small decorative accent near the badge, just topic-driven.
    assert "thematically" in lowered or "topic" in lowered
    assert "only on-screen brand mark" in lowered
    # A "do NOT add a mascot" instruction is fine (reinforces the exclusion); an
    # instruction to actually ADD one would not be.
    assert "add a mascot" not in lowered and "add a speech bubble" not in lowered


def test_poster_body_omits_brand_line_when_no_channel_name_configured():
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body(
        {"poster_format": "numbered_tips", "title": "Foods For Eyes",
         "items": [{"label": f"i{n}"} for n in range(5)]},
    )
    # No crash, no dangling "Brand identity:" line with an empty name.
    assert "Brand identity" not in body
