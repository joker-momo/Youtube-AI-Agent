import json as _json
import re

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
    # The CROSS/CHECK semantics stay; their hues now come from the negative /
    # positive style-DNA roles instead of a hardcoded red/green (bug-541).
    from video_agent.shorts.infographic.poster_prompt import build_effective_palette

    roles = build_effective_palette({
        "poster_format": "myth_vs_truth", "title": "Mitos del café",
        "items": [{"label": "el café deshidrata", "note": "hidrata casi igual"}],
    })
    assert "CROSS" in body and "CHECK" in body
    assert f"negative/warning color ({roles['negative']})" in body
    assert f"positive color ({roles['positive']})" in body
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
    from video_agent.shorts.infographic.poster_prompt import _BASE, build_poster_body
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


def test_poster_base_enforces_consistent_photo_icon_style():
    """Operator reference (2026-07-11): viral infographic Shorts get their polish
    from EVERY item photo/icon sharing one rendering style (same lighting, scale,
    isolated per cell). The base prompt must demand that consistency explicitly,
    for every format."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body(_plan("category_grid", [{"label": "Chía"}] * 5))
    lowered = body.lower()
    assert "consistent" in lowered
    assert "lighting" in lowered
    assert "scale" in lowered


def test_numbered_tips_numbers_sit_in_solid_circular_badges():
    """Operator reference (2026-07-11): sample posters render list numbers inside
    solid colored circular badges (all badges the same accent color), not as bare
    digits — that badge repetition is what makes the grid look designed."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body(_plan("numbered_tips", [{"label": f"tip{i}"} for i in range(5)]))
    lowered = body.lower()
    assert "circular badge" in lowered
    assert "same" in lowered and "color" in lowered


def test_warning_list_numbers_sit_in_solid_circular_badges():
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body(_plan("warning_list", [{"label": "Pan", "note": "no"}] * 5))
    assert "circular badge" in body.lower()


def test_item_notes_get_tiny_mini_icons():
    """Operator reference (2026-07-11): sample posters prefix each small benefit
    note with a tiny matching mini-icon (heart, bolt...) — the base prompt should
    ask for that so notes read as designed rows, not plain text."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    body = build_poster_body(_plan("numbered_tips", [{"label": "Pasas", "note": "mejoran la digestión"}] * 5))
    lowered = body.lower()
    assert "mini-icon" in lowered or "mini icon" in lowered


# --- message match (2026-07-12, mirrors long-form bug-532) -------------------


def _salt_plan() -> dict:
    return {
        "poster_format": "numbered_tips",
        "title": "Auditoría fácil de la sal",
        "subtitle": "Compara etiquetas sin complicarte",
        "hook_line": "Sal: compárala en 5 pasos",
        "items": [
            {"label": "Lee la sal", "note": "Compara gramos por 100 g"},
            {"label": "Mira ingredientes", "note": "Anota los añadidos"},
            {"label": "Comprueba el yodo", "note": "Revisa si es yodada"},
            {"label": "Valora el formato", "note": "Fina, gruesa o escamas"},
            {"label": "Calcula el coste", "note": "Precio por kilo"},
        ],
        "cta": "Revisa antes de elegir",
    }


def test_poster_prompt_message_match_names_topic_objects():
    from video_agent.shorts.infographic.poster_prompt import build_poster_body

    body = build_poster_body(_salt_plan())
    assert "MESSAGE MATCH" in body
    assert "salt" in body.lower()  # derive_topic_props: sal -> salt objects


def test_poster_prompt_message_match_survives_unknown_topic():
    from video_agent.shorts.infographic.poster_prompt import build_poster_body

    plan = _salt_plan()
    plan.update(title="Rutina que cambia tu semana", subtitle="", hook_line="Cambia tu semana")
    plan["items"] = [
        {"label": "Ordena tu semana", "note": ""},
        {"label": "Marca un hueco", "note": ""},
        {"label": "Apunta el logro", "note": ""},
        {"label": "Repite el ciclo", "note": ""},
        {"label": "Celebra el avance", "note": ""},
    ]
    body = build_poster_body(plan)
    assert "MESSAGE MATCH" in body  # generic message-match rule still present


# ── bug-541: style-DNA-driven, deterministic, contrast-aware palette ────────


PALETTE_A = {"background": "#101010", "primary": "#AA1111", "secondary": "#22BB22", "accent": "#3333CC", "text": "#F0F0F0"}
PALETTE_B = {"background": "#FFFFFF", "primary": "#EE7700", "secondary": "#00AACC", "accent": "#9900AA", "text": "#111111"}

_FORBIDDEN_COLOR_PHRASES = (
    "dark navy", "red or orange", "color (green)", "bold white digit",
    "red CROSS", "solid red", "light red/pink", "light green card",
    "red circular badge", "red ribbon", "green circular badge",
    "green ribbon", "green CHECK", "a green check", "a red cross",
    "bold white text",
)


def _cfg_with_palette(tmp_path, palette: dict) -> dict:
    p = tmp_path / "style-dna.json"
    p.write_text(_json.dumps({"version": "t", "palette": palette}), encoding="utf-8")
    return {"channel": {"name": "Vida Plena 45+"}, "style_dna": {"path": str(p)}}


def _all_format_plans():
    return [
        _plan("category_grid", [{"label": f"i{n}"} for n in range(5)]),
        _plan("numbered_tips", [{"label": f"tip{n}"} for n in range(5)], title="Cinco pasos"),
        _plan("warning_list", [{"label": f"err{n}", "note": "cuidado"} for n in range(5)], title="Errores con la sal"),
        _plan("myth_vs_truth", [{"label": f"mito{n}", "note": f"verdad{n}"} for n in range(3)], title="Mitos del pan"),
        _plan("timeline_routine", [{"time": "7:00", "label": "desayuno"}, {"time": "14:00", "label": "comida"}, {"time": "21:00", "label": "cena"}], title="Rutina diaria"),
        _plan("checklist_score", [{"label": f"c{n}"} for n in range(5)], score_line="4+ = vas bien", title="Autochequeo"),
        _plan("comparison", [{"label": "bueno1", "group": "bien"}, {"label": "malo1", "group": "mal"}], title="Bien vs mal"),
        _plan("unknown_format_xyz", [{"label": "x"}], title="Formato raro"),
    ]


def test_palette_from_style_dna_drives_the_prompt(tmp_path):
    """R2: the configured style-DNA palette must reach the prompt; two different
    palettes must produce different bodies carrying their own hexes."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body

    plan = _plan("numbered_tips", [{"label": f"t{n}"} for n in range(5)])
    a = build_poster_body(plan, _cfg_with_palette(tmp_path / "a", PALETTE_A) if (tmp_path / "a").mkdir() or True else {})
    b = build_poster_body(plan, _cfg_with_palette(tmp_path / "b", PALETTE_B) if (tmp_path / "b").mkdir() or True else {})

    assert a != b, "changing the style-DNA palette must change the prompt body"
    assert PALETTE_A["primary"] in a and PALETTE_A["primary"] not in b
    assert PALETTE_B["primary"] in b and PALETTE_B["primary"] not in a


def test_missing_or_malformed_style_dna_uses_neutral_fallback_without_crashing(tmp_path):
    """R2: missing path / missing file / malformed JSON / no config must not crash."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body
    from video_agent.style_dna import DEFAULT_STYLE

    plan = _plan("numbered_tips", [{"label": "t"}])
    bad = tmp_path / "broken.json"
    bad.write_text("not json", encoding="utf-8")
    for cfg in (
        None,
        {},
        {"channel": {"name": "X"}},
        {"style_dna": {"path": str(tmp_path / "missing.json")}},
        {"style_dna": {"path": str(bad)}},
        {"style_dna": {}},
    ):
        body = build_poster_body(plan, cfg)
        assert DEFAULT_STYLE["palette"]["primary"] in body, cfg


def test_role_mapping_is_deterministic_across_repeats(tmp_path):
    """R3/R8: same plan+palette => byte-identical body every time (no hash()/rand)."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body

    cfg = _cfg_with_palette(tmp_path, PALETTE_A)
    plan = _plan("warning_list", [{"label": f"e{n}", "note": "n"} for n in range(5)])
    bodies = {build_poster_body(plan, cfg) for _ in range(10)}
    assert len(bodies) == 1


def test_dict_key_order_does_not_change_the_mapping(tmp_path):
    """R3 scenario 4: reordering keys without semantic change keeps the mapping."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body

    cfg = _cfg_with_palette(tmp_path, PALETTE_A)
    a = _plan("numbered_tips", [{"label": "uno"}, {"label": "dos"}])
    b = {k: a[k] for k in reversed(list(a.keys()))}
    assert build_poster_body(a, cfg) == build_poster_body(b, cfg)


def test_unrelated_runtime_metadata_does_not_change_the_mapping(tmp_path):
    """R8: retry counters / runtime metadata must not influence palette roles."""
    from video_agent.shorts.infographic.poster_prompt import build_effective_palette

    cfg = _cfg_with_palette(tmp_path, PALETTE_A)
    plan = _plan("numbered_tips", [{"label": "uno"}])
    base = build_effective_palette(plan, cfg)
    noisy = build_effective_palette({**plan, "qa_attempt": 3, "generated_at": "2026-07-16T00:00:00Z"}, cfg)
    assert base == noisy


def test_different_content_diversifies_role_assignment(tmp_path):
    """R3 scenario 3: representative ideas must exercise >=3 distinct mappings."""
    from video_agent.shorts.infographic.poster_prompt import build_effective_palette

    cfg = _cfg_with_palette(tmp_path, PALETTE_A)
    plans = [
        _plan("numbered_tips", [{"label": "sal"}], title="Auditoría de la sal"),
        _plan("warning_list", [{"label": "pan"}], title="Errores con el pan"),
        _plan("myth_vs_truth", [{"label": "cafe", "note": "v"}], title="Mitos del café"),
        _plan("category_grid", [{"label": "avena"}], title="Alimentos con avena"),
        _plan("timeline_routine", [{"time": "7:00", "label": "agua"}], title="Rutina de hidratación"),
        _plan("comparison", [{"label": "aceite", "group": "bien"}], title="Aceite bueno o malo"),
    ]
    signatures = {
        tuple(sorted((k, v) for k, v in build_effective_palette(p, cfg).items()))
        for p in plans
    }
    assert len(signatures) >= 3, f"only {len(signatures)} distinct mappings"


def test_every_role_value_comes_from_the_loaded_palette(tmp_path):
    """R4: no role may silently fall back to a model-chosen color."""
    from video_agent.shorts.infographic.poster_prompt import build_effective_palette

    cfg = _cfg_with_palette(tmp_path, PALETTE_A)
    allowed = set(PALETTE_A.values())
    for plan in _all_format_plans():
        roles = build_effective_palette(plan, cfg)
        required = {
            "canvas", "body_text", "headline_1", "headline_2", "badge_fill",
            "badge_text", "positive", "negative", "divider_accent",
        }
        assert required <= set(roles), roles
        for role, value in roles.items():
            assert value in allowed, f"{role}={value} not in style-DNA palette"


def test_badge_text_is_the_highest_contrast_palette_candidate(tmp_path):
    """R5/KTD3: foreground over a fill is chosen by deterministic contrast."""
    from video_agent.shorts.infographic.poster_prompt import (
        _contrast_ratio,
        build_effective_palette,
    )

    cfg = _cfg_with_palette(tmp_path, PALETTE_A)
    for plan in _all_format_plans():
        roles = build_effective_palette(plan, cfg)
        chosen = roles["badge_text"]
        fill = roles["badge_fill"]
        best = max(
            (PALETTE_A["background"], PALETTE_A["text"]),
            key=lambda c: _contrast_ratio(c, fill),
        )
        assert chosen == best


def test_prompt_carries_the_palette_contract_for_every_format(tmp_path):
    """R4/R6/U3-5: every format renders the same explicit role->hex contract."""
    from video_agent.shorts.infographic.poster_prompt import (
        build_effective_palette,
        build_poster_body,
    )

    cfg = _cfg_with_palette(tmp_path, PALETTE_A)
    for plan in _all_format_plans():
        body = build_poster_body(plan, cfg)
        roles = build_effective_palette(plan, cfg)
        assert "PALETTE" in body.upper()
        for role, value in roles.items():
            assert value in body, f"{plan['poster_format']}: {role} hex missing"
        # The model must be told not to substitute its habitual scheme.
        low = body.lower()
        assert "do not" in low and ("substitute" in low or "replace" in low)


def test_no_forbidden_fixed_color_phrase_in_any_format_prompt(tmp_path):
    """R1 + U3-6: lexical regression — the legacy fixed recipe must not return."""
    from video_agent.shorts.infographic.poster_prompt import build_poster_body

    cfg = _cfg_with_palette(tmp_path, PALETTE_A)
    for plan in _all_format_plans():
        body = build_poster_body(plan, cfg)
        low = body.lower()
        for phrase in _FORBIDDEN_COLOR_PHRASES:
            # Word-boundary match: "numbered circular badge" must not read as a
            # forbidden "red circular badge".
            assert not re.search(rf"\b{re.escape(phrase.lower())}", low), (
                f"{plan['poster_format']}: '{phrase}'"
            )
