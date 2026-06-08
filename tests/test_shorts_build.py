"""Shorts Autopilot v5 — Phase 3/4: prompts, source map, seo, QA, build_short."""
from __future__ import annotations

import json
from pathlib import Path


def _long_job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    scenes = [
        {"id": "scene-09", "duration_sec": 16.0, "narration": "Empieza por marcar una hora de cierre.",
         "visual_prompt": "woman at night, vertical", "layout": "short_tip", "audio_offset_sec": 183.0},
    ]
    (job / "scenes.json").write_text(json.dumps({"scenes": scenes, "total_duration_sec": 600}), encoding="utf-8")
    (job / "script.json").write_text(json.dumps({"hook": "h", "sections": [], "narration": "n", "cta": "c"}), encoding="utf-8")
    (job / "seo.json").write_text(json.dumps({"title": "Dormir mejor después de los 45"}), encoding="utf-8")
    (job / "whisper_timestamps.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    (job / "video.mp4").write_bytes(b"x")
    return job


def _cfg():
    return {
        "channel": {"id": "vida-plena-45"},
        "shorts": {
            "autopilot": {"max_regeneration_attempts": 2},
            "duration": {"min_sec": 20, "target_max_sec": 60},
            "tts": {"provider": "kokoro", "voice_id": "ef_dora", "speed": 1.07},
            "funnel": {"default_cta_without_url": "Vídeo completo en el canal.", "cta_max_words": 8},
        },
    }


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------

def test_short_script_prompt_has_retention_and_language_rules():
    from video_agent.shorts import prompts
    p = prompts.short_script_prompt(_cfg(), {"short_id": "short-01", "format": "pain_to_tip", "narration_seed": "x"}, {})
    low = p.lower()
    assert "json" in low
    assert "no greeting" in low or "no greetings" in low or "saludo" in low
    assert "ancianos" in low  # forbidden term explicitly listed
    assert "pending_shorts_qa" in low


def test_v13_script_prompt_uses_calibrated_word_budget_without_old_rule():
    from video_agent.shorts import prompts

    p = prompts.short_script_prompt(
        _cfg(),
        {
            "short_id": "short-01",
            "format": "checklist",
            "narration_seed": "Revisa cinco cosas antes de comprar pan.",
        },
        {},
    ).lower()

    assert "80–105" not in p
    assert "80-105" not in p
    assert "60–70 spoken spanish words" in p or "60-70 spoken spanish words" in p
    assert "2.25" in p
    assert "checklist point count policy" in p
    assert "if there is no locked count" in p
    assert "3 spoken checklist points is ideal" in p
    assert "4 is a normal upper target" in p
    assert "4 spoken checklist points = maximum" not in p
    assert "5 spoken checklist points = too dense" not in p


def test_short_scene_prompt_requires_vertical_and_layouts():
    from video_agent.shorts import prompts
    p = prompts.short_scene_prompt(_cfg(), {"short_id": "short-01"}, {"narration": "x"})
    low = p.lower()
    assert "vertical" in low
    assert "short_hook" in low


def test_phase15_graphic_layouts_are_in_scene_and_qa_prompts():
    from video_agent.shorts import prompts

    scene_prompt = prompts.short_scene_prompt_v6(
        _cfg(),
        {"short_id": "short-01"},
        {
            "narration": (
                "Mira la etiqueta del pan: fibra, azúcares y sal por 100 g. "
                "Compara mejor y cuidado. Divide la rutina en 10 min + 10 min."
            )
        },
        feedback="",
    ).lower()
    qa_prompt = prompts.gemini_scenes_qa_prompt(
        _cfg(),
        {"narration": "Mira la etiqueta y compara opciones."},
        {"scenes": []},
    ).lower()

    for layout in ("graphic_label_callout", "graphic_comparison", "graphic_routine_split"):
        assert layout in scene_prompt
        assert layout in qa_prompt

    assert "productlabel" in scene_prompt
    assert "callouts" in scene_prompt
    assert "six allowed graphic" in qa_prompt


def test_graphic_prompt_tuning_rules_are_in_scene_and_qa_prompts():
    from video_agent.shorts import prompts

    scene_prompt = prompts.short_scene_prompt_v6(
        _cfg(),
        {"short_id": "short-01"},
        {"narration": "El pan marrón no basta. Mira la etiqueta antes de comprar."},
        feedback="",
    ).lower()
    qa_prompt = prompts.gemini_scenes_qa_prompt(
        _cfg(),
        {"narration": "El pan marrón no basta. Mira la etiqueta antes de comprar."},
        {"scenes": []},
    ).lower()

    assert "explanatory bursts" in scene_prompt
    assert "graphic_checklist: target 3.0" in scene_prompt
    assert "graphic_label_callout: target 3.5" in scene_prompt
    assert "0.5 sec" in scene_prompt
    assert "bread package" in scene_prompt
    assert "guárdalo para la compra" in scene_prompt
    assert "use visual variants deliberately" in scene_prompt
    assert "mito rápido" in scene_prompt

    assert "longer than 5.0 sec" in qa_prompt
    assert "graphic_checklist or graphic_step_list" in qa_prompt
    assert "12–15%" in qa_prompt
    assert "generic hook visual" in qa_prompt
    assert "checklist guardada" in qa_prompt


def test_v13_scene_and_qa_prompts_supersede_old_scene_count_and_numeric_authority():
    from video_agent.shorts import prompts

    scene_prompt = prompts.short_scene_prompt_v6(
        _cfg(),
        {"short_id": "short-01", "format": "checklist"},
        {"target_duration_sec": 35, "narration": "Mira la etiqueta del pan antes de comprar."},
        feedback="",
    ).lower()
    qa_prompt = prompts.gemini_scenes_qa_prompt(
        _cfg(),
        {"target_duration_sec": 35, "narration": "Mira la etiqueta del pan."},
        {"scenes": []},
    ).lower()

    assert "create 4–7 scenes" not in scene_prompt
    assert "create 4-7 scenes" not in scene_prompt
    assert "5–12 scenes" in scene_prompt or "5-12 scenes" in scene_prompt
    assert "6–12 scenes" in scene_prompt or "6-12 scenes" in scene_prompt
    assert "soft planning target" in scene_prompt
    assert "never create a 7–12 sec scene" in scene_prompt or "never create a 7-12 sec scene" in scene_prompt
    assert "deterministic validator is authoritative" in qa_prompt
    assert "do not fail solely for a numeric threshold" in qa_prompt
    assert "inside the accepted numeric ranges" in qa_prompt
    assert "s02–s06 are 3.6s" in qa_prompt
    assert "s08 is 2.6s" in qa_prompt
    assert "acceptable value that merely needs visual/render verification" in qa_prompt
    assert "audience_fit" in qa_prompt
    assert "audio_fit_risk" in qa_prompt


def test_short_seo_prompt_prefers_broad_nutrition_tags_over_nutricion45():
    from video_agent.shorts import prompts

    p = prompts.short_seo_prompt(
        _cfg(),
        {"short_id": "short-01", "format": "pain_to_tip"},
        {"hook": "¿El pan engorda?", "narration": "Usa la regla del plato saludable."},
    )
    low = p.lower()

    assert "nutricion45" not in low
    assert "nutrición45" not in low
    assert "#alimentacionsaludable" in low
    assert "#platosaludable" in low


def test_short_seo_prompt_uses_high_volume_keywords_with_spain_45_intent():
    from video_agent.shorts import prompts

    p = prompts.short_seo_prompt(
        _cfg(),
        {"short_id": "short-01", "format": "pain_to_tip", "pillar": "nutrition"},
        {
            "hook": "El error no es comer pan. Es no darle sitio.",
            "narration": "Usa la regla del plato: medio verduras, un cuarto proteína y un cuarto pan o hidrato.",
        },
    )
    low = p.lower()

    assert "high-volume" in low
    assert "alimentación saludable" in low
    assert "plato saludable" in low
    assert "el pan engorda" in low
    assert "combine one broad search keyword" in low
    assert "spain" in low
    assert "45+" in low
    assert "description must reuse" in low


def test_short_seo_normalizes_concatenated_hashtags_and_removes_nutricion45():
    from video_agent.shorts.short_seo_builder import _normalize_hashtags

    assert _normalize_hashtags(["#nutricion45#pan", "#Plato Saludable", "#shorts"]) == [
        "#nutricion",
        "#pan",
        "#platosaludable",
        "#shorts",
    ]


def test_build_short_seo_rewrites_description_with_spaced_normalized_hashtags(tmp_path: Path):
    from video_agent.shorts import short_seo_builder

    job = _long_job(tmp_path)

    def llm_fn(kind, prompt):
        return json.dumps({
            "title": "¿El pan engorda?",
            "description": "Dale sitio al pan.#nutricion45#pan#platosaludable",
            "hashtags": ["#nutricion45#pan", "#platosaludable"],
            "pinned_comment": "¿Cómo lo haces tú?",
        })

    seo = short_seo_builder.build_short_seo(
        job,
        "short-01",
        {"short_id": "short-01"},
        {"hook": "¿El pan engorda?", "narration": "Dale sitio al pan."},
        _cfg(),
        llm_fn,
    )

    assert seo["hashtags"] == ["#nutricion", "#pan", "#platosaludable"]
    assert seo["description"].endswith("#nutricion #pan #platosaludable")
    assert "#nutricion45#pan" not in seo["description"]


def test_phase15_graphic_layouts_preserved_by_scene_normalizer():
    from video_agent.shorts.short_scene_builder import normalize_short_scenes

    doc = {
        "scenes": [
            {"scene_id": "s1", "layout": "graphic_label_callout", "narration": "Mira la etiqueta."},
            {"scene_id": "s2", "layout": "graphic_comparison", "narration": "Compara dos opciones."},
            {"scene_id": "s3", "layout": "graphic_routine_split", "narration": "Divide la rutina."},
        ]
    }

    out = normalize_short_scenes(doc, {"narration": "Mira. Compara. Divide."})

    assert [s["layout"] for s in out["scenes"]] == [
        "graphic_label_callout",
        "graphic_comparison",
        "graphic_routine_split",
    ]
    assert all(s["visual_type"] == "graphic" for s in out["scenes"])


def test_phase15_graphic_validator_accepts_new_layouts_and_stubs_blank_fields():
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scenes = [
        {
            "id": "s1",
            "layout": "graphic_label_callout",
            "duration_sec": 4,
            "on_screen_text": "",
            "layout_payload": {
                "title": "MIRA PRIMERO",
                "productLabel": "Pan integral",
                "callouts": [
                    {"label": "Fibra", "value": "6 g", "note": "mejor saciedad"},
                    {"label": "Azúcares", "value": "3 g", "note": "por 100 g"},
                ],
            },
        },
        {
            "id": "s2",
            "layout": "graphic_comparison",
            "duration_sec": 4,
            "layout_payload": {
                "title": "EN EL SÚPER",
                "left": {"heading": "MEJOR", "text": "Integral con buena fibra"},
                "right": {"heading": "CUIDADO", "text": "Oscuro, pero sin grano integral"},
            },
        },
        {
            "id": "s3",
            "layout": "graphic_routine_split",
            "duration_sec": 4,
            "layout_payload": {
                "title": "RUTINA 30 MINUTOS",
                "totalLabel": "30 min",
                "blocks": [
                    {"time": "10 min", "text": "Cerrar el día"},
                    {"time": "10 min", "text": "Preparar dormitorio"},
                    {"time": "10 min", "text": "Respirar"},
                ],
            },
        },
    ]

    warnings = validate_short_graphic_scenes(scenes)

    assert warnings  # three graphics is allowed for preview but warned for normal Shorts.
    assert scenes[0]["on_screen_text"] == "MIRA PRIMERO"
    assert scenes[0]["asset_refs"]["background"] == ""


def test_phase15_graphic_validator_rejects_forbidden_comparison_words():
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scene = {
        "id": "s1",
        "layout": "graphic_comparison",
        "duration_sec": 4,
        "layout_payload": {
            "title": "EN EL SÚPER",
            "left": {"heading": "MEJOR", "text": "Integral con fibra"},
            "right": {"heading": "CUIDADO", "text": "Esto es veneno"},
        },
    }

    try:
        validate_short_graphic_scenes([scene])
    except ValueError as exc:
        assert "veneno" in str(exc).lower()
    else:
        raise AssertionError("expected forbidden comparison language to fail validation")


def test_graphic_validator_rejects_slow_graphic_bursts():
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scene = {
        "id": "slow",
        "layout": "graphic_label_callout",
        "duration_sec": 5.2,
        "layout_payload": {
            "title": "MIRA ETIQUETA",
            "productLabel": "Pan integral",
            "callouts": [
                {"label": "Fibra", "value": "6 g"},
                {"label": "Azúcar", "value": "3 g"},
            ],
        },
    }

    try:
        validate_short_graphic_scenes([scene])
    except ValueError as exc:
        assert "exceeds hard max 5.0s" in str(exc)
        assert "explanatory bursts" in str(exc)
    else:
        raise AssertionError("expected >5s graphic to fail validation")


def test_graphic_validator_rejects_checklist_over_layout_cap():
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scene = {
        "id": "slow-checklist",
        "layout": "graphic_checklist",
        "duration_sec": 5.2,
        "layout_payload": {
            "title": "BUSCA INTEGRAL",
            "items": ["Harina integral", "Centeno integral"],
        },
    }

    try:
        validate_short_graphic_scenes([scene])
    except ValueError as exc:
        assert "hard max 5.0s" in str(exc)
    else:
        raise AssertionError("expected checklist >5.0s to fail validation")


def test_graphic_validator_warns_passive_cta_and_generic_bread_hook():
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scenes = [
        {
            "id": "hook",
            "layout": "short_hook",
            "duration_sec": 2.4,
            "on_screen_text": "MARRÓN NO BASTA",
            "narration": "El pan marrón no basta.",
            "visual_prompt": "abstract close-up of food texture",
            "layout_payload": {},
        },
        {
            "id": "graphic",
            "layout": "graphic_label_callout",
            "duration_sec": 4.0,
            "layout_payload": {
                "title": "MIRA ETIQUETA",
                "productLabel": "Pan integral",
                "callouts": [
                    {"label": "Fibra", "value": "6 g"},
                    {"label": "Azúcar", "value": "3 g"},
                ],
            },
        },
        {
            "id": "cta",
            "layout": "short_cta",
            "duration_sec": 2.2,
            "on_screen_text": "CHECKLIST GUARDADA",
            "visual_prompt": "shopping basket with bread",
            "layout_payload": {},
        },
    ]

    warnings = validate_short_graphic_scenes(scenes)

    assert any("bread/label hook visual is too generic" in w for w in warnings)
    assert any("passive/status-like" in w for w in warnings)


def test_bread_label_prompt_tuning_sample_validates_cleanly():
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    sample_path = Path(__file__).parent / "fixtures" / "bread_label_prompt_tuning_sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))

    for s in sample["scenes"]:
        if s.get("layout") == "graphic_checklist":
            s["duration_sec"] = 4.5

    warnings = validate_short_graphic_scenes(sample["scenes"])

    assert warnings == []
    assert sample["scenes"][0]["duration_sec"] <= 2.8
    assert "bread" in sample["scenes"][0]["visual_prompt"].lower()
    assert sample["scenes"][-1]["on_screen_text"] == "GUÁRDALO PARA LA COMPRA"


def test_scene_structure_validator_flags_long_scene_with_repair_plan():
    from video_agent.shorts.validate_scenes import (
        build_scene_repair_plan,
        validate_scene_structure,
    )

    scenes_doc = {
        "total_duration_sec": 35.0,
        "scenes": [
            {
                "id": "s01",
                "layout": "short_hook",
                "duration_sec": 2.5,
                "on_screen_text": "MARRÓN NO BASTA",
                "visual_prompt": "vertical supermarket bread shelf with ingredient label",
                "narration": "El pan marrón no basta.",
            },
            {
                "id": "s06",
                "layout": "short_checklist",
                "duration_sec": 11.3,
                "on_screen_text": "COMPÁRALO CON OTRO",
                "visual_prompt": "vertical shot of hands comparing two bread packages",
                "narration": "Si la lista es larguísima, compara con otro pan antes de comprar.",
            },
            {
                "id": "s07",
                "layout": "short_cta",
                "duration_sec": 2.4,
                "on_screen_text": "GUARDA ESTA LISTA",
                "visual_prompt": "vertical warm supermarket shopping basket",
                "narration": "Guarda esta lista para la compra.",
            },
        ],
    }

    issues = validate_scene_structure(scenes_doc["scenes"], scenes_doc=scenes_doc)

    assert any(
        issue.type == "duration_cap"
        and issue.scene_id == "s06"
        and issue.severity == "repairable_error"
        for issue in issues
    )
    repair = build_scene_repair_plan(scenes_doc["scenes"], issues)
    repair_text = "\n".join(repair["instructions"])
    assert "No scene may exceed 5.0 sec" in repair_text
    assert "s06" in repair_text
    assert "split" in repair_text.lower() or "regenerate" in repair_text.lower()


def test_scene_structure_accepts_soft_target_when_audio_estimate_fits():
    from video_agent.shorts.validate_scenes import validate_scene_structure

    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.5, "on_screen_text": "MARRÓN NO BASTA", "visual_prompt": "vertical bread package label", "narration": "El pan marrón no basta."},
        {"id": "s02", "layout": "short_myth", "duration_sec": 3.0, "on_screen_text": "REVISA ANTES", "visual_prompt": "vertical supermarket shelf", "narration": "Antes de comprar, mira la etiqueta."},
        {"id": "s03", "layout": "graphic_checklist", "duration_sec": 4.2, "on_screen_text": "BUSCA INTEGRAL", "visual_prompt": "vertical label graphic", "narration": "Busca harina integral como primer ingrediente.", "layout_payload": {"title": "BUSCA INTEGRAL", "items": ["Harina integral", "Buena fibra"]}},
        {"id": "s04", "layout": "graphic_label_callout", "duration_sec": 5.0, "on_screen_text": "MIRA ETIQUETA", "visual_prompt": "vertical nutrition label close-up", "narration": "Compara fibra y azúcares por cien gramos.", "layout_payload": {"title": "MIRA ETIQUETA", "productLabel": "Pan integral", "callouts": [{"label": "Fibra", "value": "6 g"}, {"label": "Azúcar", "value": "3 g"}]}},
        {"id": "s05", "layout": "short_tip", "duration_sec": 4.5, "on_screen_text": "COMPARA FIBRA", "visual_prompt": "vertical hands comparing bread labels", "narration": "Si dudas, compara dos panes y elige el más claro."},
        {"id": "s06", "layout": "short_tip", "duration_sec": 4.3, "on_screen_text": "COMPARA CON OTRO", "visual_prompt": "vertical supermarket basket with bread", "narration": "No hace falta que sea perfecto, solo mejor elegido."},
        {"id": "s07", "layout": "short_cta", "duration_sec": 2.4, "on_screen_text": "GUARDA ESTA LISTA", "visual_prompt": "vertical calm person shopping", "narration": "Guárdalo para la compra."},
    ]

    issues = validate_scene_structure(
        scenes,
        scenes_doc={"total_duration_sec": 25.9, "scenes": scenes},
        script={"target_duration_sec": 35, "narration": " ".join(s["narration"] for s in scenes)},
    )

    assert not [issue for issue in issues if issue.severity != "warning"], issues
    assert not any("target" in issue.type for issue in issues)


def test_missing_graphic_is_warning_only_when_two_graphics_exist():
    from video_agent.shorts.validate_scenes import validate_scene_structure

    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.5, "on_screen_text": "MARRÓN NO BASTA", "visual_prompt": "vertical bread package label", "narration": "El pan marrón no basta."},
        {"id": "s02", "layout": "graphic_checklist", "duration_sec": 4.0, "on_screen_text": "BUSCA INTEGRAL", "visual_prompt": "vertical label graphic", "narration": "Busca harina integral.", "layout_payload": {"title": "BUSCA INTEGRAL", "items": ["Harina integral", "Buena fibra"]}},
        {"id": "s03", "layout": "graphic_label_callout", "duration_sec": 4.5, "on_screen_text": "MIRA ETIQUETA", "visual_prompt": "vertical nutrition label close-up", "narration": "Mira la etiqueta.", "layout_payload": {"title": "MIRA ETIQUETA", "productLabel": "Pan", "callouts": [{"label": "Fibra", "value": "6 g"}, {"label": "Azúcar", "value": "3 g"}]}},
        {"id": "s04", "layout": "short_tip", "duration_sec": 4.0, "on_screen_text": "POR 100 G", "visual_prompt": "vertical hands comparing labels", "narration": "Compara fibra y azúcares por 100 g antes de elegir."},
        {"id": "s05", "layout": "short_cta", "duration_sec": 2.4, "on_screen_text": "GUARDA ESTA LISTA", "visual_prompt": "vertical shopping basket", "narration": "Guarda esta lista."},
    ]

    issues = validate_scene_structure(scenes, scenes_doc={"total_duration_sec": 17.4, "scenes": scenes})

    assert any(issue.type == "missing_graphic_warning" and issue.severity == "warning" for issue in issues)
    assert not any(issue.type == "missing_graphic_warning" and issue.severity != "warning" for issue in issues)


def test_audio_fit_blocks_when_narration_audio_exceeds_video_duration():
    from video_agent.shorts.validate_scenes import validate_audio_fit

    issue = validate_audio_fit(render_duration_sec=32.0, narration_audio_sec=41.5)

    assert issue is not None
    assert issue.type == "audio_fit"
    assert issue.severity == "blocking_error"
    assert "Condense narration" in (issue.repair_hint or "")


def test_rendered_short_audio_fit_regression_blocks_35s_video_with_40s_audio():
    from video_agent.shorts.validate_scenes import validate_audio_fit

    issue = validate_audio_fit(render_duration_sec=35.0, narration_audio_sec=40.3)

    assert issue is not None
    assert issue.severity == "blocking_error"
    assert "40.3s" in issue.detail
    assert "35.0s" in issue.detail


def test_audio_fit_allows_tiny_tail_margin_rounding_shortage():
    from video_agent.shorts.validate_scenes import validate_audio_fit

    assert validate_audio_fit(render_duration_sec=23.6, narration_audio_sec=23.005, margin_sec=0.6) is None

    issue = validate_audio_fit(render_duration_sec=23.4, narration_audio_sec=23.005, margin_sec=0.6)
    assert issue is not None
    assert issue.type == "audio_fit"


def test_v13_spanish_narration_estimate_uses_calibrated_wps():
    from video_agent.shorts.validate_scenes import (
        DEFAULT_SPANISH_WPS,
        estimate_spanish_narration_sec,
    )

    text = " ".join(["palabra"] * 90)

    assert DEFAULT_SPANISH_WPS == 2.25
    assert 39.5 <= estimate_spanish_narration_sec(text) <= 41.0


# --------------------------------------------------------------------------
# source map
# --------------------------------------------------------------------------

def test_build_source_map_records_used_scenes_with_timestamps(tmp_path: Path):
    from video_agent.shorts import source_map
    job = _long_job(tmp_path)
    sm = source_map.build_source_map(
        job,
        short_plan={"short_id": "short-01", "scene_ids": ["scene-09"], "source_start_sec": 183.0, "source_end_sec": 199.0},
        short_script={"narration": "Marca una hora de cierre.", "cta": "Vídeo completo en el canal."},
        channel_config=_cfg(),
    )
    assert sm["short_id"] == "short-01"
    used = sm["used_source_scenes"]
    assert used[0]["scene_id"] == "scene-09"
    assert used[0]["source_start_sec"] == 183.0
    assert "original_narration" in used[0]
    assert sm["funnel"]["cta"]


def test_build_source_map_includes_synthesis_idea_metadata(tmp_path: Path):
    from video_agent.shorts import source_map

    job = _long_job(tmp_path)
    sm = source_map.build_source_map(
        job,
        short_plan={
            "short_id": "short-01",
            "idea_id": "idea-01",
            "idea_type": "synthesis",
            "scene_ids": ["scene-09"],
            "source_scene_ids": ["scene-09"],
            "key_points": [{"point": "Marca una hora", "source_scene_ids": ["scene-09"]}],
        },
        short_script={"narration": "Marca una hora de cierre.", "cta": "Vídeo completo en el canal."},
        channel_config=_cfg(),
    )

    assert sm["idea_id"] == "idea-01"
    assert sm["idea_type"] == "synthesis"
    assert sm["key_points"][0]["source_scene_ids"] == ["scene-09"]


# --------------------------------------------------------------------------
# QA (rule-based)
# --------------------------------------------------------------------------

def _good_short_dir(tmp_path: Path) -> Path:
    from video_agent.shorts import paths
    job = _long_job(tmp_path)
    sd = paths.short_dir(job, "short-01")
    sd.mkdir(parents=True)
    (sd / "short_script.json").write_text(json.dumps({
        "short_id": "short-01", "hook": "¿Duermes pero te levantas cansado?",
        "narration": "¿Duermes pero te levantas cansado? Marca una hora de cierre y apaga la pantalla.\nNotarás la diferencia.",
        "cta": "Vídeo completo en el canal.", "target_duration_sec": 32,
    }), encoding="utf-8")
    (sd / "short_scenes.json").write_text(json.dumps({
        "short_id": "short-01", "total_duration_sec": 21.0,
        "scenes": [
            {"id": "s1", "duration_sec": 2.5, "on_screen_text": "Mente encendida", "caption": "c", "layout": "short_hook", "visual_prompt": "v vertical"},
            {"id": "s2", "duration_sec": 4.2, "on_screen_text": "Hora de cierre", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical"},
            {"id": "s3", "duration_sec": 4.2, "on_screen_text": "Apaga pantalla", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical"},
            {"id": "s4", "duration_sec": 4.2, "on_screen_text": "Respira despacio", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical"},
            {"id": "s5", "duration_sec": 3.5, "on_screen_text": "Baja el ritmo", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical"},
            {"id": "s6", "duration_sec": 2.4, "on_screen_text": "Guarda esta idea", "caption": "c", "layout": "short_cta", "visual_prompt": "v vertical"},
        ],
    }), encoding="utf-8")
    (sd / "short_source_map.json").write_text(json.dumps({"used_source_scenes": [{"scene_id": "scene-09"}]}), encoding="utf-8")
    return job


def test_qa_passes_clean_short(tmp_path: Path):
    from video_agent.shorts import qa
    job = _good_short_dir(tmp_path)
    out = qa.run_short_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")
    assert out["verdict"] == "PASS", out


def test_qa_rejects_greeting(tmp_path: Path):
    from video_agent.shorts import qa, paths
    job = _good_short_dir(tmp_path)
    sp = paths.short_dir(job, "short-01") / "short_script.json"
    d = json.loads(sp.read_text())
    d["narration"] = "Hola, bienvenidos al canal. Hoy vamos a hablar de dormir."
    d["hook"] = "Hola a todos"
    sp.write_text(json.dumps(d), encoding="utf-8")
    out = qa.run_short_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")
    assert out["verdict"] == "FAIL"
    assert any("greeting" in i or "saludo" in i for i in out["issues"])


def test_qa_rejects_long_disclaimer(tmp_path: Path):
    from video_agent.shorts import qa, paths
    job = _good_short_dir(tmp_path)
    sp = paths.short_dir(job, "short-01") / "short_script.json"
    d = json.loads(sp.read_text())
    d["narration"] = ("Marca una hora de cierre. Este contenido es informativo y no sustituye la opinión "
                      "de un profesional de salud; consulta siempre a tu médico antes de cualquier cambio en tu rutina.")
    sp.write_text(json.dumps(d), encoding="utf-8")
    out = qa.run_short_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")
    assert out["verdict"] == "FAIL"
    assert any("disclaimer" in i for i in out["issues"])


def test_qa_rejects_medical_overclaim(tmp_path: Path):
    from video_agent.shorts import qa, paths
    job = _good_short_dir(tmp_path)
    sp = paths.short_dir(job, "short-01") / "short_script.json"
    d = json.loads(sp.read_text())
    d["narration"] = "Esta rutina cura el insomnio para siempre, garantizado."
    sp.write_text(json.dumps(d), encoding="utf-8")
    out = qa.run_short_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")
    assert out["verdict"] == "FAIL"
    assert any("overclaim" in i or "medical" in i for i in out["issues"])


def test_script_rule_qa_rejects_over_word_budget_before_scenes(tmp_path: Path):
    from video_agent.shorts import qa, paths

    job = _good_short_dir(tmp_path)
    sd = paths.short_dir(job, "short-01")
    long_narration = " ".join(["palabra"] * 150)
    (sd / "short_script.json").write_text(json.dumps({
        "short_id": "short-01",
        "hook": "Mira esto antes de comprar pan",
        "narration": long_narration,
        "cta": "Guarda esta lista",
        "target_duration_sec": 35,
    }), encoding="utf-8")

    out = qa.run_short_script_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")

    assert out["verdict"] == "FAIL"
    assert any("word_budget" in str(issue) or "spoken_duration" in str(issue) for issue in out["issues"])


def test_script_rule_qa_rejects_too_many_spoken_checklist_points(tmp_path: Path):
    from video_agent.shorts import qa, paths

    job = _good_short_dir(tmp_path)
    sd = paths.short_dir(job, "short-01")
    (sd / "short_script.json").write_text(json.dumps({
        "short_id": "short-01",
        "short_format": "checklist",
        "hook": "Mira esto antes de comprar pan",
        "narration": (
            "Revisa cinco cosas. Uno: mira el ingrediente. Dos: compara la fibra. "
            "Tres: revisa el azúcar. Cuatro: mira la sal. Cinco: compara con otro pan."
        ),
        "cta": "Guarda esta lista",
        "target_duration_sec": 35,
    }), encoding="utf-8")

    out = qa.run_short_script_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")

    assert out["verdict"] == "FAIL"
    assert "script_checklist_point_cap" in out["issues"]


# --------------------------------------------------------------------------
# build_short orchestration (injected LLM/tts/mix/render)
# --------------------------------------------------------------------------

_GOOD_SCRIPT = {
    "short_id": "short-01", "source_long_job_id": "long-job", "short_format": "pain_to_tip",
    "target_duration_sec": 32, "hook": "¿Duermes pero te levantas cansado?",
    "narration": "¿Duermes pero te levantas cansado? Marca una hora de cierre y apaga la pantalla.\nNotarás la diferencia.",
    "beats": ["pain", "tip"], "cta": "Vídeo completo en el canal.", "qa": {"verdict": "PENDING_SHORTS_QA"},
}
_GOOD_SCENES = {
    "channel_id": "vida-plena-45", "short_id": "short-01", "total_duration_sec": 21.0,
    "scenes": [
        {"id": "s1", "duration_sec": 2.5, "on_screen_text": "MENTE ENCENDIDA", "caption": "c", "layout": "short_hook", "visual_prompt": "v vertical", "narration": "¿Duermes pero te levantas cansado?"},
        {"id": "s2", "duration_sec": 4.2, "on_screen_text": "HORA DE CIERRE", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical", "narration": "Marca una hora de cierre."},
        {"id": "s3", "duration_sec": 4.2, "on_screen_text": "APAGA PANTALLA", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical", "narration": "Apaga la pantalla."},
        {"id": "s4", "duration_sec": 4.2, "on_screen_text": "RESPIRA DESPACIO", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical", "narration": "Respira despacio."},
        {"id": "s5", "duration_sec": 3.5, "on_screen_text": "BAJA EL RITMO", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical", "narration": "Baja el ritmo antes de dormir."},
        {"id": "s6", "duration_sec": 2.4, "on_screen_text": "GUARDA ESTA IDEA", "caption": "c", "layout": "short_cta", "visual_prompt": "v vertical", "narration": "Guarda esta idea."},
    ],
    "qa": {"verdict": "PENDING_SHORTS_QA"},
}


def _llm_fn_factory(script=_GOOD_SCRIPT, scenes=_GOOD_SCENES):
    def fn(kind, prompt):
        if kind == "script":
            return json.dumps(script)
        if kind == "scenes":
            return json.dumps(scenes)
        if kind == "seo":
            return json.dumps({"title": "Dormir mejor 45+", "description": "d", "hashtags": ["#shorts"],
                               "pinned_comment": "Mira el vídeo largo"})
        return "{}"
    return fn


def _stub_io(calls):
    def tts_fn(short_dir, short_scenes, channel_config):
        calls.append("tts"); (short_dir / "audio").mkdir(parents=True, exist_ok=True)
        (short_dir / "audio" / "short_narration.wav").write_bytes(b"w"); return short_dir / "audio" / "short_narration.wav"
    def mix_fn(short_dir, narration_wav, music_track, channel_config, duration_sec):
        calls.append("mix"); (short_dir / "audio" / "short_mix.m4a").write_bytes(b"m"); return short_dir / "audio" / "short_mix.m4a"
    def render_fn(short_dir, channel_config):
        calls.append("render"); (short_dir / "outputs").mkdir(parents=True, exist_ok=True)
        out = short_dir / "outputs" / "short.mp4"
        out.write_bytes(b"v"); return out
    def cover_fn(short_dir, channel_config):
        calls.append("cover"); (short_dir / "outputs").mkdir(parents=True, exist_ok=True)
        out = short_dir / "outputs" / "short_cover.jpg"
        out.write_bytes(b"j"); return out
    return dict(tts_fn=tts_fn, mix_fn=mix_fn, render_fn=render_fn, cover_fn=cover_fn)


def test_short_stage_retry_clears_stale_completion_and_error():
    from video_agent.shorts.short_builder import _update_short_stage

    status = {
        "stages": [{
            "name": "audio",
            "label": "Audio TTS & Mix",
            "status": "failed",
            "started_at": "2026-06-06T20:53:33+00:00",
            "completed_at": "2026-06-06T20:54:19+00:00",
            "actual_seconds": 45,
            "error": "Narration audio exceeds video duration.",
            "qa_verdict": "FAIL",
        }]
    }

    _update_short_stage(status, "audio", "in_progress", now_str="2026-06-06T20:58:27+00:00")

    stage = status["stages"][0]
    assert stage["status"] == "in_progress"
    assert stage["completed_at"] is None
    assert stage["actual_seconds"] is None
    assert "error" not in stage
    assert "qa_verdict" not in stage


def test_build_short_pass_renders_and_writes_artifacts(tmp_path: Path):
    from video_agent.shorts import short_builder, paths
    job = _long_job(tmp_path)
    calls: list[str] = []
    plan = {"short_id": "short-01", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), **_stub_io(calls))
    assert res["status"] == "rendered"
    assert res["qa_verdict"] == "PASS"
    sd = paths.short_dir(job, "short-01")
    for f in ("short_script.json", "short_scenes.json", "short_source_map.json", "short_seo.json", "short_script_qa.json", "short_scenes_qa.json"):
        assert (sd / "json" / f).exists(), f
    for f in ("short.mp4", "short_cover.jpg"):
        assert (sd / "outputs" / f).exists(), f
    assert calls == ["tts", "mix", "render", "cover"]
    assert res["music_track"] == "shorts_sleep_stress"


def test_build_short_records_stage_pass_fail_status_in_prompt_history(tmp_path: Path):
    from video_agent.shorts import llm_history, paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    plan = {"short_id": "short-history-stages", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), **_stub_io(calls))

    hist_path = paths.short_dir(job, "short-history-stages") / "json" / paths.SHORT_LLM_HISTORY_FILE
    stage_events = [
        h for h in llm_history.read_history(hist_path)
        if h.get("provider") == "deterministic" and h.get("kind") == "stage_status"
    ]
    assert any(e["payload"]["stage"] == "script" and e["payload"]["status"] == "completed" for e in stage_events)
    assert any(e["payload"]["stage"] == "qa_script" and e["payload"]["verdict"] == "PASS" for e in stage_events)
    assert any(e["payload"]["stage"] == "audio" and e["payload"]["status"] == "completed" for e in stage_events)


def test_short_render_props_use_scene_sum_when_total_duration_is_stale(tmp_path: Path):
    from video_agent.shorts import paths, short_builder

    short_dir = tmp_path / "short"
    scenes = [
        {"id": "s01", "duration_sec": 2.5},
        {"id": "s02", "duration_sec": 3.5},
        {"id": "s03", "duration_sec": 3.5},
        {"id": "s04", "duration_sec": 3.5},
        {"id": "s05", "duration_sec": 3.5},
        {"id": "s06", "duration_sec": 3.5},
        {"id": "s07", "duration_sec": 5.0},
        {"id": "s08", "duration_sec": 2.5},
    ]
    assert round(sum(float(scene["duration_sec"]) for scene in scenes), 1) == 27.5

    short_builder._write_render_props(
        short_dir,
        {"total_duration_sec": 21.7, "scenes": scenes},
        _cfg(),
        "shorts_sleep_stress",
    )

    props = json.loads((short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE).read_text(encoding="utf-8"))
    assert props["total_duration_sec"] == 27.5
    assert props["render"]["duration_sec"] == 27.5


def test_build_short_keeps_audio_and_video_in_sync_at_planned_durations(tmp_path: Path):
    # Shorts TTS runs with dynamic_sync=False (see shorts.audio): each scene's
    # audio is padded to its planned duration_sec, so the single Remotion
    # narration track stays aligned with the per-scene visual sequences. The
    # builder must therefore KEEP the planned scene durations and drive
    # render/mix at the planned total — never shrink visuals to raw speech,
    # which previously desynced audio from video.
    import wave

    from video_agent.shorts import llm_history, paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    mix_durations: list[float] = []
    plan = {"short_id": "short-preserve-duration", "format": "mistake_list", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Cinco errores con el pan."}
    scenes_doc = {
        "channel_id": "vida-plena-45",
        "short_id": "short-preserve-duration",
        "total_duration_sec": 27.6,
        "scenes": [
            {"id": "s01", "duration_sec": 2.2, "on_screen_text": "NO ES EL PAN", "caption": "SON 5 HÁBITOS", "layout": "short_hook", "visual_prompt": "Realistic bread on Spanish kitchen table", "narration": "No es el pan."},
            {"id": "s02", "duration_sec": 3.6, "on_screen_text": "DE PIE", "caption": "Sin plato", "layout": "short_pain", "visual_prompt": "Realistic person eating bread standing in kitchen", "narration": "Uno: comerlo de pie."},
            {"id": "s03", "duration_sec": 3.6, "on_screen_text": "SUMAR SIN DECIDIR", "caption": "Con arroz o pasta", "layout": "short_pain", "visual_prompt": "Realistic bread next to rice on Spanish table", "narration": "Dos: sumarlo sin decidir."},
            {"id": "s04", "duration_sec": 3.6, "on_screen_text": "BARRA A LA VISTA", "caption": "Demasiado a mano", "layout": "short_pain", "visual_prompt": "Realistic bread bar left on dining table", "narration": "Tres: dejar la barra a la vista."},
            {"id": "s05", "duration_sec": 3.6, "on_screen_text": "CANSANCIO", "caption": "Otro trozo", "layout": "short_pain", "visual_prompt": "Realistic tired adult cutting another bread slice", "narration": "Cuatro: cortar por cansancio."},
            {"id": "s06", "duration_sec": 3.6, "on_screen_text": "CENA IMPROVISADA", "caption": "A bocados", "layout": "short_pain", "visual_prompt": "Realistic bread and cheese dinner bites on plate", "narration": "Cinco: cenar improvisando."},
            {"id": "s07", "duration_sec": 4.8, "on_screen_text": "MEJOR ASÍ", "caption": "Porción visible", "layout": "graphic_checklist", "visual_prompt": "Realistic small plate with bread portion", "narration": "Mejor: porción visible, plato pequeño, comida completa.", "layout_payload": {"title": "MEJOR ASÍ", "items": ["Porción visible", "Plato pequeño", "Comida completa"]}},
            {"id": "s08", "duration_sec": 2.6, "on_screen_text": "GUÁRDALO", "caption": "PARA TU PRÓXIMA CENA", "layout": "short_cta", "visual_prompt": "Realistic warm kitchen close-up", "narration": "Guárdalo."},
        ],
        "qa": {"verdict": "PENDING_SCENES_QA"},
    }

    def tts_fn(short_dir, short_scenes, channel_config):
        # Faithful dynamic_sync=False stand-in: pad each scene's audio to its
        # planned duration, leave scene durations untouched, and emit a
        # narration track whose length equals the planned total (27.6s).
        calls.append("tts")
        planned_total = round(
            sum(float(s["duration_sec"]) for s in short_scenes["scenes"]), 2
        )
        audio_dir = short_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        wav_path = audio_dir / "short_narration.wav"
        with wave.open(str(wav_path), "w") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\0\0" * int(planned_total * 8000))
        return wav_path

    io = _stub_io(calls)
    io["tts_fn"] = tts_fn

    def mix_fn(short_dir, narration_wav, music_track, channel_config, duration_sec):
        calls.append("mix")
        mix_durations.append(duration_sec)
        (short_dir / "audio").mkdir(parents=True, exist_ok=True)
        out = short_dir / "audio" / "short_mix.m4a"
        out.write_bytes(b"m")
        return out

    io["mix_fn"] = mix_fn

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(scenes=scenes_doc),
        gemini_fn=lambda p: json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "warnings": [],
            "product_scores": {
                "audience_fit_45_plus": 10,
                "hook_strength": 10,
                "visual_specificity": 10,
                "clarity": 10,
                "retention_pacing": 9,
                "natural_spanish": 10,
                "saveability": 10,
            },
        }),
        **io,
    )

    short_dir = paths.short_dir(job, "short-preserve-duration")
    saved_scenes = json.loads((short_dir / "json" / paths.SHORT_SCENES_FILE).read_text(encoding="utf-8"))
    render_props = json.loads((short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE).read_text(encoding="utf-8"))
    hist = llm_history.read_history(short_dir / "json" / paths.SHORT_LLM_HISTORY_FILE)
    audio_tail_events = [h for h in hist if h.get("kind") == "audio_tail_repair"]

    assert res["status"] == "rendered"
    # Sync invariant: the visual timeline, the render duration, the mix duration
    # and the narration audio all agree. This is what keeps audio aligned with
    # video; the exact number depends on the planned scene durations.
    visual_total = round(sum(float(scene["duration_sec"]) for scene in saved_scenes["scenes"]), 1)
    assert saved_scenes["total_duration_sec"] == visual_total
    assert render_props["render"]["duration_sec"] == visual_total
    assert mix_durations == [visual_total]
    # Narration audio fills essentially the whole visual timeline. Before the
    # fix, audio (19.4s) ran ~11s short of the 30.2s visuals — gross desync.
    # Now the gap is only the intentional end-of-video tail hold (<= ~1s).
    with wave.open(str(short_dir / "audio" / "short_narration.wav")) as w:
        narration_sec = round(w.getnframes() / w.getframerate(), 1)
    assert narration_sec <= visual_total
    assert visual_total - narration_sec <= 1.0

    # Fix E: a measurable audio_sync_summary is written and passes.
    sync = json.loads((short_dir / "json" / paths.SHORT_AUDIO_SYNC_SUMMARY_FILE).read_text(encoding="utf-8"))
    assert sync["verdict"] == "PASS"
    assert sync["pass_delta_sec"] > 0

    # Fix C: a reason-aware call_budget_summary is written on success.
    budget = json.loads((short_dir / "json" / paths.SHORT_CALL_BUDGET_SUMMARY_FILE).read_text(encoding="utf-8"))
    assert budget["stage"] == "call_budget_summary"
    assert "by_reason" in budget and "provider_error" in budget["by_reason"]
    assert "by_provider" in budget and "retry_counts" in budget
    assert budget["verdict"] in ("PASS", "WARN")


def test_build_short_soft_scene_validation_warning_proceeds_to_gemini_qa(tmp_path: Path, monkeypatch):
    from video_agent.shorts import short_builder, validate_scenes

    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_calls = {"n": 0}

    def soft_scene_validation(*args, **kwargs):
        return [
            validate_scenes.SceneValidationIssue(
                type="slideshow_risk",
                scene_id=None,
                severity="warning",
                detail="Footage-led candidate has mild list density.",
            )
        ]

    def gemini_fn(prompt):
        if "Scenes QA reviewer" in prompt:
            qa_calls["n"] += 1
        return json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "warnings": [],
            "product_scores": {
                "hook_strength": 8,
                "retention_pacing": 8,
                "visual_scene_fit": 8,
                "mobile_readability": 8,
                "layout_variety": 8,
                "source_fidelity": 8,
                "overall_product_quality": 8,
            },
        })

    monkeypatch.setattr(validate_scenes, "validate_scene_structure", soft_scene_validation)

    plan = {"short_id": "short-soft-scene-warning", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(scenes={**_GOOD_SCENES, "short_id": "short-soft-scene-warning"}),
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    assert qa_calls["n"] >= 1
    assert res["status"] in {"rendered", "needs_review"}


def test_build_short_records_render_exception_in_status(tmp_path: Path):
    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []

    def render_fn(short_dir, channel_config):
        calls.append("render")
        raise RuntimeError("render schema validation failed")

    io = _stub_io(calls)
    io["render_fn"] = render_fn
    plan = {"short_id": "short-render-error", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    try:
        short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), **io)
    except RuntimeError:
        pass

    status_doc = json.loads((job / "shorts" / "short-render-error" / "short_status.json").read_text())
    render_stage = next(s for s in status_doc["stages"] if s["name"] == "render")
    assert status_doc["status"] == "failed"
    assert "render schema validation failed" in render_stage["error"]
    assert "render schema validation failed" in status_doc["error"]


def test_build_short_persists_auto_extended_scene_durations_before_gemini_qa(tmp_path: Path):
    from video_agent.shorts import paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    captured: dict[str, str] = {}
    scenes = {
        **_GOOD_SCENES,
        "short_id": "short-auto-duration",
        "total_duration_sec": 20.3,
        "scenes": [
            *_GOOD_SCENES["scenes"][:1],
            {
                **_GOOD_SCENES["scenes"][1],
                "duration_sec": 2.0,
                "narration": "Marca una hora de cierre.",
            },
            *_GOOD_SCENES["scenes"][2:4],
            {**_GOOD_SCENES["scenes"][4], "duration_sec": 5.0},
            *_GOOD_SCENES["scenes"][5:],
        ],
    }

    def gemini_fn(prompt):
        captured["prompt"] = prompt
        return json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "product_scores": {
                "audience_fit_45_plus": 9,
                "hook_strength": 9,
                "visual_specificity": 9,
                "clarity": 9,
                "retention_pacing": 9,
                "natural_spanish": 9,
                "saveability": 8.5,
            },
        })

    plan = {"short_id": "short-auto-duration", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(scenes=scenes),
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    sd = paths.short_dir(job, "short-auto-duration")
    saved = json.loads((sd / "json" / paths.SHORT_SCENES_FILE).read_text(encoding="utf-8"))

    assert res["status"] == "rendered"
    assert saved["scenes"][1]["duration_sec"] == 2.7
    assert saved["total_duration_sec"] == 21.2
    assert '"duration_sec": 2.7' in captured["prompt"]
    assert '"total_duration_sec": 21.2' in captured["prompt"]


def test_build_short_regenerates_then_needs_review_after_limit(tmp_path: Path):
    from video_agent.shorts import short_builder, paths
    job = _long_job(tmp_path)
    calls: list[str] = []
    attempts = {"n": 0}
    bad_script = {**_GOOD_SCRIPT, "hook": "Hola a todos", "narration": "Hola, bienvenidos. Hoy vamos a hablar."}

    def llm_fn(kind, prompt):
        if kind == "script":
            attempts["n"] += 1
            return json.dumps(bad_script)  # always greeting → always FAIL
        if kind == "scenes":
            return json.dumps(_GOOD_SCENES)
        return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})

    plan = {"short_id": "short-02", "format": "mistake_to_avoid", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress", "narration_seed": "x"}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=llm_fn, **_stub_io(calls))
    assert res["status"] == "needs_review"
    assert res["requires_user_review"] is True
    # initial + 2 regenerations = 3 generations
    assert attempts["n"] == 3
    assert "render" not in calls  # never rendered a failing short
    assert not (paths.short_dir(job, "short-02") / "short.mp4").exists()


def test_build_short_exposes_qa_scenes_attempt_count(tmp_path: Path):
    from video_agent.shorts import short_builder, paths

    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_attempts = {"n": 0}

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_attempts["n"] += 1
            if qa_attempts["n"] == 1:
                return json.dumps({
                    "verdict": "FAIL",
                    "issues": [{"type": "retention_pacing", "severity": "repairable_error", "detail": "Merge repeated final scenes."}],
                    "required_changes": ["Merge repeated final scenes."],
                    "warnings": [],
                    "product_scores": {
                        "hook_strength": 8,
                        "retention_pacing": 6,
                        "visual_scene_fit": 8,
                        "mobile_readability": 8,
                        "layout_variety": 8,
                        "source_fidelity": 8,
                        "overall_product_quality": 8,
                    },
                })
            return json.dumps({
                "verdict": "PASS",
                "issues": [],
                "required_changes": [],
                "warnings": [],
                "product_scores": {
                    "hook_strength": 8,
                    "retention_pacing": 8,
                    "visual_scene_fit": 8,
                    "mobile_readability": 8,
                    "layout_variety": 8,
                    "source_fidelity": 8,
                    "overall_product_quality": 8,
                },
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-qa-scenes-count", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(),
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    status_doc = json.loads(
        (paths.short_dir(job, "short-qa-scenes-count") / paths.SHORT_STATUS_FILE).read_text(encoding="utf-8")
    )
    assert res["qa_scenes_attempts"] == qa_attempts["n"]
    assert status_doc["qa_scenes_attempts"] == qa_attempts["n"]


def test_gemini_scene_qa_fail_blocks_audio_seo_and_render(tmp_path: Path):
    from video_agent.shorts import paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []

    def llm_fn(kind: str, prompt: str):
        calls.append(kind)
        if kind == "script":
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            return json.dumps(_GOOD_SCENES)
        if kind == "seo":
            return json.dumps({"title": "Should not run", "description": "d", "hashtags": ["#shorts"]})
        return "{}"

    def gemini_fn(prompt: str):
        if "Scenes QA reviewer" not in prompt:
            return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})
        return json.dumps({
            "verdict": "FAIL",
            "issues": [
                {
                    "type": "visual",
                    "scene_id": "s2",
                    "severity": "major",
                    "detail": "Acceptable duration, but verify the visual is warm enough.",
                }
            ],
            "required_changes": ["Verify the visual is warm enough."],
            "warnings": [],
            "product_scores": {
                "audience_fit_45_plus": 9,
                "hook_strength": 9,
                "visual_specificity": 9,
                "clarity": 9,
                "retention_pacing": 9,
                "natural_spanish": 9,
                "saveability": 9,
            },
        })

    cfg = _cfg()
    cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 0
    plan = {
        "short_id": "short-qa-fail-gate",
        "format": "pain_to_tip",
        "scene_ids": ["scene-09"],
        "source_start_sec": 183.0,
        "source_end_sec": 199.0,
        "music_track": "shorts_sleep_stress",
        "narration_seed": "Marca una hora de cierre.",
    }

    res = short_builder.build_short(
        job,
        plan,
        cfg,
        llm_fn=llm_fn,
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    short_dir = paths.short_dir(job, "short-qa-fail-gate")
    qa_doc = json.loads(paths.resolve_short_json(short_dir, paths.SHORT_SCENES_QA_FILE).read_text(encoding="utf-8"))
    failure_doc = json.loads(paths.resolve_short_json(short_dir, paths.SHORT_FAILURE_REPORT_FILE).read_text(encoding="utf-8"))
    retry_memory = json.loads((short_dir / "json" / "scene_retry_memory.json").read_text(encoding="utf-8"))

    assert qa_doc["verdict"] == "FAIL"
    assert qa_doc["provider_call_ok"] is True
    assert qa_doc["qa_pass"] is False
    assert failure_doc["latest_scene_qa_ok"] is False
    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"
    assert "tts" not in calls
    assert "seo" not in calls
    assert "render" not in calls
    assert not (short_dir / paths.SHORT_SEO_FILE).exists()
    assert not (short_dir / "outputs" / "short.mp4").exists()
    active_details = [
        str(issue.get("detail") or "")
        for issue in retry_memory["active_issues"].values()
    ]
    assert any("visual is warm enough" in detail for detail in active_details)
    assert any("Verify the visual is warm enough" in detail for detail in active_details)


def test_build_short_prepare_mode_stops_before_render(tmp_path: Path):
    from video_agent.shorts import short_builder, paths

    job = _long_job(tmp_path)
    calls: list[str] = []
    plan = {"short_id": "short-03", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(),
        require_render_confirmation=True,
        **_stub_io(calls),
    )

    assert res["status"] == "ready_for_render"
    assert res["rendered"] is False
    assert res["requires_render_confirmation"] is True
    assert calls == ["tts"]


def test_audio_fit_guard_runs_after_tts_before_mix_and_render(tmp_path: Path):
    import wave

    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    plan = {"short_id": "short-audio-fit", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    valid_scenes = {
        "channel_id": "vida-plena-45",
        "short_id": "short-audio-fit",
        "total_duration_sec": 21.0,
        "scenes": [
            {"id": "s1", "duration_sec": 2.5, "on_screen_text": "MENTE ENCENDIDA", "caption": "c", "layout": "short_hook", "visual_prompt": "vertical bedroom", "narration": "Abre fuerte."},
            {"id": "s2", "duration_sec": 4.2, "on_screen_text": "HORA DE CIERRE", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical clock", "narration": "Marca una hora de cierre."},
            {"id": "s3", "duration_sec": 4.2, "on_screen_text": "APAGA PANTALLA", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical phone", "narration": "Apaga la pantalla."},
            {"id": "s4", "duration_sec": 4.2, "on_screen_text": "RESPIRA DESPACIO", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical calm person", "narration": "Respira despacio."},
            {"id": "s5", "duration_sec": 3.5, "on_screen_text": "BAJA EL RITMO", "caption": "c", "layout": "short_tip", "visual_prompt": "vertical calm room", "narration": "Baja el ritmo."},
            {"id": "s6", "duration_sec": 2.4, "on_screen_text": "GUARDA ESTA IDEA", "caption": "c", "layout": "short_cta", "visual_prompt": "vertical calm person", "narration": "Guarda esta idea."},
        ],
        "qa": {"verdict": "PENDING_SCENES_QA"},
    }

    def tts_fn(short_dir, short_scenes, channel_config):
        calls.append("tts")
        audio_dir = short_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        wav_path = audio_dir / "short_narration.wav"
        with wave.open(str(wav_path), "w") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\0\0" * int(30.0 * 8000))
        return wav_path

    io = _stub_io(calls)
    io["tts_fn"] = tts_fn

    cfg = _cfg()
    cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 0

    res = short_builder.build_short(
        job,
        plan,
        cfg,
        llm_fn=_llm_fn_factory(scenes=valid_scenes),
        **io,
    )

    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"
    assert calls == ["tts"]
    assert "audio_fit" in json.dumps(res).lower()


def test_audio_fit_small_tail_shortage_extends_scene_durations():
    from video_agent.shorts import validate_scenes

    scenes_doc = {
        "total_duration_sec": 22.4,
        "scenes": [
            {"id": "s1", "layout": "short_hook", "duration_sec": 2.5},
            {"id": "s2", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s3", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s4", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s5", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s6", "layout": "short_cta", "duration_sec": 2.4},
        ],
    }

    result = validate_scenes.extend_scene_durations_for_audio_tail(
        scenes_doc,
        narration_audio_sec=22.42,
    )

    assert result["changed"] is True
    assert scenes_doc["total_duration_sec"] >= 23.0
    assert validate_scenes.validate_audio_fit(scenes_doc["total_duration_sec"], 22.42) is None
    assert not validate_scenes.has_blocking_or_repairable(
        validate_scenes.validate_scene_structure(scenes_doc["scenes"], scenes_doc=scenes_doc)
    )


def test_audio_tail_repair_does_not_compress_scene_sum_to_audio_duration():
    from video_agent.shorts import validate_scenes

    scenes_doc = {
        "total_duration_sec": 21.7,
        "scenes": [
            {"id": "s01", "layout": "short_hook", "duration_sec": 2.5},
            {"id": "s02", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s03", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s04", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s05", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s06", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s07", "layout": "short_checklist", "duration_sec": 5.0},
            {"id": "s08", "layout": "short_cta", "duration_sec": 2.5},
        ],
    }

    result = validate_scenes.extend_scene_durations_for_audio_tail(
        scenes_doc,
        narration_audio_sec=20.92,
    )

    assert result["changed"] is False
    assert result["reason"] == "already_fits"
    assert scenes_doc["total_duration_sec"] == 27.5
    assert validate_scenes.validate_audio_fit(scenes_doc["total_duration_sec"], 20.92) is None


def test_audio_fit_large_shortage_is_not_stretched_past_pacing():
    from video_agent.shorts import validate_scenes

    scenes_doc = {
        "total_duration_sec": 21.0,
        "scenes": [
            {"id": "s1", "layout": "short_hook", "duration_sec": 2.5},
            {"id": "s2", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s3", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s4", "layout": "short_tip", "duration_sec": 4.2},
            {"id": "s5", "layout": "short_tip", "duration_sec": 3.5},
            {"id": "s6", "layout": "short_cta", "duration_sec": 2.4},
        ],
    }

    result = validate_scenes.extend_scene_durations_for_audio_tail(
        scenes_doc,
        narration_audio_sec=30.0,
    )

    assert result["changed"] is False
    assert scenes_doc["total_duration_sec"] == 21.0


def test_build_short_passes_source_artifacts_to_script_builder(tmp_path: Path, monkeypatch):
    from video_agent.shorts import paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    captured: dict[str, object] = {}
    plan = {
        "short_id": "short-04",
        "format": "pain_to_tip",
        "scene_ids": ["scene-09"],
        "source_scene_ids": ["scene-09"],
        "idea_id": "idea-01",
        "narration_seed": "Marca una hora de cierre.",
        "music_track": "shorts_sleep_stress",
    }

    def fake_build_short_script(long_job_dir, short_plan, channel_config, llm_fn, **kwargs):
        captured["source_artifacts"] = kwargs.get("source_artifacts")
        return _GOOD_SCRIPT

    monkeypatch.setattr(short_builder.short_script_builder, "build_short_script", fake_build_short_script)
    monkeypatch.setattr(
        short_builder.qa,
        "run_short_script_qa",
        lambda *args, **kwargs: {"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []},
    )
    monkeypatch.setattr(
        short_builder.qa,
        "run_short_scenes_qa",
        lambda *args, **kwargs: {"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []},
    )

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(),
        source_artifacts={"idea": {"idea_id": "idea-01"}, "source_scenes": [{"scene_id": "scene-09"}]},
        **_stub_io(calls),
    )

    assert res["status"] == "rendered"
    assert captured["source_artifacts"]["idea"]["idea_id"] == "idea-01"
    sd = paths.short_dir(job, "short-03")
    assert not (sd / "short.mp4").exists()
    assert not (sd / "short_cover.jpg").exists()


# --- scene normalization (render/TTS compatibility) ------------------------

def test_normalize_short_scenes_renames_scene_id_and_injects_narration():
    from video_agent.shorts import short_scene_builder
    scenes_doc = {
        "scenes": [
            {"scene_id": "s1", "duration_sec": 2.5, "on_screen_text": "Hook", "layout": "short_hook", "visual_prompt": "v"},
            {"scene_id": "s2", "duration_sec": 4.0, "on_screen_text": "Tip", "layout": "short_tip", "visual_prompt": "v"},
        ]
    }
    script = {"narration": "Primera idea clave. Segunda idea practica.", "hook": "Hook"}
    out = short_scene_builder.normalize_short_scenes(scenes_doc, script)
    scenes = out["scenes"]
    # scene_id → id for render/TTS
    assert all("id" in s for s in scenes)
    assert scenes[0]["id"] == "s1"
    # every scene has non-empty narration (TTS needs it)
    assert all(str(s.get("narration", "")).strip() for s in scenes)
    # narration distributed (not identical empty)
    assert scenes[0]["narration"] != scenes[1]["narration"]


def test_normalize_short_scenes_keeps_existing_narration():
    from video_agent.shorts import short_scene_builder
    scenes_doc = {"scenes": [{"scene_id": "s1", "narration": "Ya tengo voz.", "duration_sec": 3.0}]}
    out = short_scene_builder.normalize_short_scenes(scenes_doc, {"narration": "otra"})
    assert out["scenes"][0]["narration"] == "Ya tengo voz."
    assert out["scenes"][0]["id"] == "s1"


def test_normalize_short_scenes_does_not_turn_empty_llm_output_into_cta_only():
    from video_agent.shorts import short_scene_builder

    out = short_scene_builder.normalize_short_scenes({}, {"cta": "Guárdalo para tu próxima compra."})

    assert out["scenes"] == []
    assert out["total_duration_sec"] == 0


def test_normalize_short_scenes_seeds_full_render_contract():
    from video_agent.shorts import short_scene_builder
    out = short_scene_builder.normalize_short_scenes(
        {"scenes": [{"scene_id": "s1", "on_screen_text": "Hook", "duration_sec": 2.5}]},
        {"narration": "Una idea."},
    )
    sc = out["scenes"][0]
    for key in ("id", "narration", "on_screen_text", "caption", "visual_prompt", "layout",
                "layout_payload", "layout_reason", "motion", "asset_refs", "word_segments",
                "planner_warnings", "audio_offset_sec", "duration_sec"):
        assert key in sc, key
    assert isinstance(sc["asset_refs"], dict)
    assert out["total_duration_sec"] > 0


def test_normalize_short_scenes_maps_layout_to_render_enum():
    from video_agent.shorts import short_scene_builder
    out = short_scene_builder.normalize_short_scenes(
        {"scenes": [
            {"scene_id": "s1", "layout": "short_hook", "duration_sec": 2.0},
            {"scene_id": "s2", "layout": "short_cta", "duration_sec": 3.0},
            {"scene_id": "s3", "layout": "short_tip", "duration_sec": 3.0},
        ]},
        {"narration": "Uno. Dos. Tres."},
    )
    # Spec v6 §10: short_* is the first-class layout family. Long-form names
    # are only accepted via the legacy adapter (separate test).
    valid = {"short_hook", "short_pain", "short_tip", "short_checklist",
             "short_myth", "short_quote", "short_cta"}
    assert all(s["layout"] in valid for s in out["scenes"])
    assert out["scenes"][0]["layout"] == "short_hook"
    assert out["scenes"][1]["layout"] == "short_cta"


def test_normalize_short_scenes_maps_legacy_long_form_layouts():
    """Legacy adapter (spec v6 §10): old short artifacts using long-form
    layout names must round-trip to short_* equivalents."""
    from video_agent.shorts import short_scene_builder
    out = short_scene_builder.normalize_short_scenes(
        {"scenes": [
            {"id": "s1", "layout": "hook", "duration_sec": 2.0, "narration": "n"},
            {"id": "s2", "layout": "subtitle", "duration_sec": 3.0, "narration": "n"},
            {"id": "s3", "layout": "warning", "duration_sec": 3.0, "narration": "n"},
            {"id": "s4", "layout": "cta", "duration_sec": 3.0, "narration": "n"},
        ]},
        {"narration": "x"},
    )
    layouts = [s["layout"] for s in out["scenes"]]
    assert layouts == ["short_hook", "short_tip", "short_pain", "short_cta"]


def test_qa_response_normalization_graphic_preference():
    from video_agent.shorts.qa import normalize_gemini_scenes_qa
    parsed_input = {
        "verdict": "FAIL",
        "issues": [
            {
                "type": "layout",
                "scene_id": "s03",
                "severity": "major",
                "detail": "s03 should be graphic_label_callout"
            },
            {
                "type": "layout",
                "scene_id": "s05",
                "severity": "major",
                "detail": "s05 could be graphic_comparison"
            }
        ],
        "required_changes": [
            "convert s03 to graphic_label_callout",
            "convert s05 to graphic_comparison"
        ],
        "warnings": [],
        "scores": {},
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 9,
            "visual_specificity": 9,
            "clarity": 9,
            "retention_pacing": 9,
            "natural_spanish": 9,
            "saveability": 8.5
        }
    }
    normalized = normalize_gemini_scenes_qa(parsed_input)
    assert normalized["verdict"] == "PASS"
    assert len(normalized["issues"]) == 0
    assert len(normalized["required_changes"]) == 0
    assert any("Downgraded Gemini issue: s03 should be" in w for w in normalized["warnings"])
    assert any("layout_optimization_downgraded_to_warning" in w for w in normalized["warnings"])


def test_missing_graphic_warning_only():
    from video_agent.shorts.validate_scenes import validate_scene_structure
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.5, "on_screen_text": "MARRÓN NO BASTA", "visual_prompt": "vertical bread package label", "narration": "El pan marrón no basta."},
        {"id": "s02", "layout": "graphic_checklist", "duration_sec": 4.0, "on_screen_text": "BUSCA INTEGRAL", "visual_prompt": "vertical label graphic", "narration": "Busca harina integral.", "layout_payload": {"title": "BUSCA INTEGRAL", "items": ["Harina integral", "Buena fibra"]}},
        {"id": "s03", "layout": "graphic_label_callout", "duration_sec": 4.5, "on_screen_text": "MIRA ETIQUETA", "visual_prompt": "vertical nutrition label close-up", "narration": "Mira la etiqueta.", "layout_payload": {"title": "MIRA ETIQUETA", "productLabel": "Pan", "callouts": [{"label": "Fibra", "value": "6 g"}, {"label": "Azúcar", "value": "3 g"}]}},
        {"id": "s04", "layout": "short_tip", "duration_sec": 4.0, "on_screen_text": "POR 100 G", "visual_prompt": "vertical hands comparing labels", "narration": "Compara fibra y azúcares por 100 g antes de elegir."},
        {"id": "s05", "layout": "short_cta", "duration_sec": 2.4, "on_screen_text": "GUARDA ESTA LISTA", "visual_prompt": "vertical shopping basket", "narration": "Guarda esta lista."},
    ]
    
    issues = validate_scene_structure(
        scenes, 
        scenes_doc={"total_duration_sec": 17.4, "scenes": scenes},
        script={"narration": " ".join(s["narration"] for s in scenes)}
    )
    
    warnings = [i for i in issues if i.severity == "warning"]
    blocking = [i for i in issues if i.severity in ("blocking_error", "repairable_error")]
    
    assert any(i.type == "missing_graphic_warning" for i in warnings)
    assert not any("graphic" in i.type for i in blocking)


def test_total_duration_sec_normalization():
    from video_agent.shorts.validate_scenes import validate_scene_structure
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.6},
        {"id": "s02", "layout": "short_tip", "duration_sec": 2.8},
        {"id": "s03", "layout": "short_cta", "duration_sec": 4.4},
    ]
    scenes_doc = {
        "total_duration_sec": 34.2,
        "scenes": scenes
    }
    issues = validate_scene_structure(scenes, scenes_doc=scenes_doc)
    assert scenes_doc["total_duration_sec"] == 9.8
    warnings = [i for i in issues if i.severity == "warning"]
    blocking = [i for i in issues if i.severity in ("blocking_error", "repairable_error")]
    assert any(i.type == "total_duration_normalized" for i in warnings)
    assert not any(i.type == "duration_sum" for i in blocking)


def test_five_error_accepted_duration_ranges_do_not_fail_scenes_qa(tmp_path: Path):
    from video_agent.shorts import paths, qa

    job = _long_job(tmp_path)
    short_id = "short-five-error-durations"
    short_dir = paths.short_dir(job, short_id)
    (short_dir / "json").mkdir(parents=True, exist_ok=True)
    script = {
        "short_id": short_id,
        "short_format": "mistake_list",
        "target_duration_sec": 28.2,
        "hook": "No es el pan.",
        "narration": (
            "No es el pan. Uno: comerlo de pie. Dos: sumarlo sin decidir. "
            "Tres: dejar la barra a la vista. Cuatro: cortar por cansancio. "
            "Cinco: cenar improvisando. Mejor: porción visible, plato pequeño, comida completa. Guárdalo."
        ),
        "cta": "Guárdalo.",
    }
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.8, "on_screen_text": "NO ES EL PAN", "caption": "Mira cómo lo usas", "visual_prompt": "Realistic bread on Spanish kitchen table", "narration": "No es el pan."},
        {"id": "s02", "layout": "short_pain", "duration_sec": 3.6, "on_screen_text": "DE PIE", "caption": "Sin plato", "visual_prompt": "Realistic person eating bread standing in kitchen", "narration": "Uno: comerlo de pie."},
        {"id": "s03", "layout": "short_pain", "duration_sec": 3.6, "on_screen_text": "SUMAR SIN DECIDIR", "caption": "Con arroz o pasta", "visual_prompt": "Realistic bread next to rice on Spanish table", "narration": "Dos: sumarlo sin decidir."},
        {"id": "s04", "layout": "short_pain", "duration_sec": 3.6, "on_screen_text": "BARRA A LA VISTA", "caption": "Demasiado a mano", "visual_prompt": "Realistic bread bar left on dining table", "narration": "Tres: dejar la barra a la vista."},
        {"id": "s05", "layout": "short_pain", "duration_sec": 3.6, "on_screen_text": "CANSANCIO", "caption": "Otro trozo", "visual_prompt": "Realistic tired adult cutting another bread slice", "narration": "Cuatro: cortar por cansancio."},
        {"id": "s06", "layout": "short_pain", "duration_sec": 3.6, "on_screen_text": "CENA IMPROVISADA", "caption": "A bocados", "visual_prompt": "Realistic bread and cheese dinner bites on plate", "narration": "Cinco: cenar improvisando."},
        {"id": "s07", "layout": "short_checklist", "duration_sec": 4.8, "on_screen_text": "MEJOR ASÍ", "caption": "Porción visible", "visual_prompt": "Realistic small plate with bread portion", "narration": "Mejor: porción visible, plato pequeño, comida completa.", "layout_payload": {"items": ["Porción visible", "Plato pequeño", "Comida completa"]}},
        {"id": "s08", "layout": "short_cta", "duration_sec": 2.6, "on_screen_text": "GUÁRDALO", "caption": "PARA TU PRÓXIMA CENA", "visual_prompt": "Realistic warm kitchen close-up", "narration": "Guárdalo."},
    ]
    scenes_doc = {"short_id": short_id, "total_duration_sec": 28.2, "scenes": scenes}
    (short_dir / "json" / paths.SHORT_SCRIPT_FILE).write_text(json.dumps(script), encoding="utf-8")
    (short_dir / "json" / paths.SHORT_SCENES_FILE).write_text(json.dumps(scenes_doc), encoding="utf-8")

    out = qa.run_short_scenes_qa(job, short_id, _cfg(), gemini_fn=None)

    assert out["verdict"] == "PASS"
    assert not any("duration" in str(issue).lower() for issue in out["issues"])


def test_audio_fit_failure_surfaces_without_regenerating_script(tmp_path: Path):
    import wave
    from unittest.mock import patch
    from video_agent.shorts import llm_history, paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    
    script_attempts = {"n": 0, "feedbacks": []}
    
    def llm_fn(kind, prompt):
        if kind == "script":
            script_attempts["n"] += 1
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            return json.dumps(_GOOD_SCENES)
        if kind == "seo":
            return json.dumps({"title": "t", "description": "d", "hashtags": [], "pinned_comment": "p"})
        return "{}"

    original_build_script = short_builder.short_script_builder.build_short_script
    def captured_build_script(*args, **kwargs):
        script_attempts["feedbacks"].append(kwargs.get("feedback", ""))
        return original_build_script(*args, **kwargs)

    with patch("video_agent.shorts.short_script_builder.build_short_script", captured_build_script):
        def tts_fn(short_dir, short_scenes, channel_config):
            calls.append("tts")
            audio_dir = short_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            wav_path = audio_dir / "short_narration.wav"
            with wave.open(str(wav_path), "w") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8000)
                handle.writeframes(b"\0\0" * int(30.0 * 8000))
            return wav_path

        io = _stub_io(calls)
        io["tts_fn"] = tts_fn

        cfg = _cfg()
        cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 1

        plan = {"short_id": "short-audio-fit-retry", "format": "pain_to_tip", "scene_ids": ["scene-09"], "music_track": "shorts_sleep_stress"}
        res = short_builder.build_short(
            job,
            plan,
            cfg,
            llm_fn=llm_fn,
            gemini_fn=lambda p: json.dumps({
                "verdict": "PASS",
                "issues": [],
                "required_changes": [],
                "product_scores": {
                    "audience_fit_45_plus": 9,
                    "hook_strength": 9,
                    "visual_specificity": 9,
                    "clarity": 9,
                    "retention_pacing": 9,
                    "natural_spanish": 9,
                    "saveability": 8.5
                }
            }),
            **io,
        )

        assert res["status"] == "needs_review"
        assert res["qa_verdict"] == "FAIL"
        assert script_attempts["n"] == 1
        assert calls == ["tts"]
        assert not any("AUDIO-FIT" in f or "narration audio exceeds" in f for f in script_attempts["feedbacks"])
        assert "audio_fit" in json.dumps(res).lower()

        short_dir = paths.short_dir(job, "short-audio-fit-retry")
        status_doc = json.loads((short_dir / "short_status.json").read_text(encoding="utf-8"))
        audio_stage = next(stage for stage in status_doc["stages"] if stage["name"] == "audio")
        assert audio_stage["status"] == "failed"
        assert "Narration audio" in audio_stage["error"]

        hist = llm_history.read_history(short_dir / "json" / paths.SHORT_LLM_HISTORY_FILE)
        stage_events = [
            h for h in hist
            if h.get("provider") == "deterministic" and h.get("kind") == "stage_status"
        ]
        audio_fail_events = [
            h for h in stage_events
            if h.get("payload", {}).get("stage") == "audio" and h.get("payload", {}).get("status") == "failed"
        ]
        assert len(audio_fail_events) == 1
        assert audio_fail_events[0]["payload"]["verdict"] == "FAIL"
        assert "Narration audio" in audio_fail_events[0]["payload"]["error"]


def test_repair_scene_duration_if_possible():
    from video_agent.shorts.validate_scenes import repair_scene_duration_if_possible
    
    # Fits within cap (hook cap is 3.0s, required is 1.4s)
    s1 = {"duration_sec": 1.0, "layout": "short_hook", "narration": "Abre fuerte."}
    res1 = repair_scene_duration_if_possible(s1)
    assert res1 == "auto_extended"
    assert s1["duration_sec"] == 1.4

    # Exceeds cap (hook cap is 3.0s, narration has 8 words -> required is 4.0s)
    s2 = {"duration_sec": 1.0, "layout": "short_hook", "narration": "Abre fuerte y mira esta increible etiqueta ahora mismo."}
    res2 = repair_scene_duration_if_possible(s2)
    assert res2 == "must_split_or_compress"
    assert s2["duration_sec"] == 1.0


def test_action_specific_repair_hints():
    from video_agent.shorts.validate_scenes import build_scene_repair_plan, SceneValidationIssue

    scenes = [
        {"id": "s01", "layout": "short_hook", "narration": "a"},
        {"id": "s06", "layout": "graphic_label_callout", "narration": "b"},
        {"id": "s09", "layout": "short_quote", "narration": "c"},
        {"id": "s10", "layout": "short_cta", "narration": "d"},
        {"id": "s02", "layout": "short_tip", "narration": "e"},
    ]
    issues = [
        SceneValidationIssue(type="scene_narration_fit", scene_id="s01", severity="repairable_error", detail="x"),
        SceneValidationIssue(type="scene_narration_fit", scene_id="s06", severity="repairable_error", detail="x"),
        SceneValidationIssue(type="scene_narration_fit", scene_id="s09", severity="repairable_error", detail="x"),
        SceneValidationIssue(type="scene_narration_fit", scene_id="s10", severity="repairable_error", detail="x"),
        SceneValidationIssue(type="scene_narration_fit", scene_id="s02", severity="repairable_error", detail="x"),
    ]
    plan = build_scene_repair_plan(scenes, issues)
    inst = "\n".join(plan["instructions"])
    assert "Hook narration is too long" in inst
    assert "Current narration is too long for a single graphic_label_callout" in inst
    assert "Quote narration is too long" in inst
    assert "CTA narration is too long" in inst
    assert "Condense narration or increase scene duration" in inst


def test_defensive_product_scores_validation():
    from video_agent.shorts.qa import normalize_gemini_scenes_qa
    
    # 1. All scores good (average >= 8.9, all >= 9.0 / saveability >= 8.5)
    parsed_good = {
        "verdict": "FAIL", # should get updated to PASS if only warnings/scores were failing (but wait, no issues are present here)
        "issues": [],
        "required_changes": [],
        "product_scores": {
            "audience_fit_45_plus": "9/10",
            "hook_strength": 9,
            "visual_specificity": 9.0,
            "clarity": "9",
            "retention_pacing": 9,
            "natural_spanish": "9",
            "saveability": 8.5
        }
    }
    res_good = normalize_gemini_scenes_qa(parsed_good)
    assert res_good["verdict"] == "PASS" # since parsed verdict was FAIL but no issues exist and scores are perfect
    assert not any(i["type"] == "product_quality_score_low" for i in res_good["issues"])

    # 2. Missing score key
    parsed_missing = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 9,
        }
    }
    res_missing = normalize_gemini_scenes_qa(parsed_missing)
    assert res_missing["verdict"] == "FAIL"
    assert any(i["type"] == "product_quality_scores_missing" for i in res_missing["issues"])

    # 3. Individual score too low (< 9.0)
    parsed_low = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 8, # < 9.0
            "visual_specificity": 9,
            "clarity": 9,
            "retention_pacing": 9,
            "natural_spanish": 9,
            "saveability": 8.5
        }
    }
    res_low = normalize_gemini_scenes_qa(parsed_low)
    assert res_low["verdict"] == "FAIL"
    assert any(i["type"] == "product_quality_score_low" for i in res_low["issues"])

    # 4. Average score too low (< 8.9)
    parsed_low_avg = {
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "product_scores": {
            "audience_fit_45_plus": 8.5,
            "hook_strength": 8.5,
            "visual_specificity": 8.5,
            "clarity": 8.5,
            "retention_pacing": 8.5,
            "natural_spanish": 8.5,
            "saveability": 8.5 # all 8.5s -> avg 8.5 < 8.9
        }
    }
    res_low_avg = normalize_gemini_scenes_qa(parsed_low_avg)
    assert res_low_avg["verdict"] == "FAIL"
    assert any(i["type"] == "product_quality_average_low" for i in res_low_avg["issues"])


def test_script_escalation_after_repeated_scene_failures(tmp_path: Path):
    from unittest.mock import patch
    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    
    script_attempts = {"n": 0, "feedbacks": []}
    
    def llm_fn(kind, prompt):
        if kind == "script":
            script_attempts["n"] += 1
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            # Hook duration 1.0s, but narration has 15 words -> requires 7.0s (exceeds 3.0s cap)
            bad_scenes = {
                "channel_id": "vida-plena-45",
                "short_id": "short-escalate",
                "total_duration_sec": 1.0,
                "scenes": [
                    {"id": "s1", "duration_sec": 1.0, "layout": "short_hook", "narration": "Abre fuerte y mira esta increible etiqueta ahora mismo con mucho cuidado y atencion.", "on_screen_text": "x", "caption": "c"}
                ]
            }
            return json.dumps(bad_scenes)
        return "{}"

    original_build_script = short_builder.short_script_builder.build_short_script
    def captured_build_script(*args, **kwargs):
        script_attempts["feedbacks"].append(kwargs.get("feedback", ""))
        return original_build_script(*args, **kwargs)

    with patch("video_agent.shorts.short_script_builder.build_short_script", captured_build_script):
        io = _stub_io(calls)
        cfg = _cfg()
        cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 2

        plan = {"short_id": "short-escalate", "format": "pain_to_tip", "scene_ids": ["scene-09"], "music_track": "shorts_sleep_stress"}
        res = short_builder.build_short(
            job,
            plan,
            cfg,
            llm_fn=llm_fn,
            gemini_fn=lambda p: json.dumps({"verdict": "PASS", "issues": [], "required_changes": []}),
            **io,
        )

        assert script_attempts["n"] >= 2
        assert any("SCRIPT COMPRESSION REQUIRED" in f for f in script_attempts["feedbacks"])
        assert res["status"] == "needs_review"


def test_best_candidate_fallback_blocked_by_low_scores(tmp_path: Path):
    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    
    def gemini_fn(prompt):
        return json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "product_scores": {
                "audience_fit_45_plus": 8,
                "hook_strength": 8,
                "visual_specificity": 8,
                "clarity": 5, # low score!
                "retention_pacing": 8,
                "natural_spanish": 8,
                "saveability": 8
            }
        })

    io = _stub_io(calls)
    cfg = _cfg()
    cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 0

    plan = {"short_id": "short-low-scores", "format": "pain_to_tip", "scene_ids": ["scene-09"], "music_track": "shorts_sleep_stress"}
    res = short_builder.build_short(
        job,
        plan,
        cfg,
        llm_fn=_llm_fn_factory(),
        gemini_fn=gemini_fn,
        **io,
    )

    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"


def test_best_candidate_fallback_blocked_by_gemini_audio_fit_issue(tmp_path: Path):
    from video_agent.shorts import short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    gemini_calls = {"n": 0}

    def gemini_fn(prompt):
        gemini_calls["n"] += 1
        if gemini_calls["n"] == 1:
            return json.dumps({"verdict": "PASS", "issues": [], "required_changes": []})
        return json.dumps({
            "verdict": "FAIL",
            "issues": [{
                "type": "audio_fit_risk",
                "scene_id": None,
                "severity": "major",
                "detail": "Narration density creates an audio_fit risk before rendering.",
            }],
            "required_changes": ["Shorten narration before render; audio_fit risk is high."],
            "product_scores": {
                "audience_fit_45_plus": 8,
                "hook_strength": 8,
                "visual_specificity": 8,
                "clarity": 8,
                "retention_pacing": 6,
                "natural_spanish": 8,
                "saveability": 8,
            },
        })

    cfg = _cfg()
    cfg["shorts"]["autopilot"]["max_regeneration_attempts"] = 0

    plan = {"short_id": "short-audio-fit-risk", "format": "pain_to_tip", "scene_ids": ["scene-09"], "music_track": "shorts_sleep_stress"}
    res = short_builder.build_short(
        job,
        plan,
        cfg,
        llm_fn=_llm_fn_factory(),
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "FAIL"
    assert gemini_calls["n"] >= 2
    assert "render" not in calls


# --------------------------------------------------------------------------
# Regression: graphic-count false positive + pacing simplification repair
# --------------------------------------------------------------------------

def test_two_graphics_not_failed_for_at_most_2_rule():
    """A candidate with exactly 2 graphics must not fail QA when Gemini
    incorrectly complains about an "at most 2 graphics" rule."""
    from video_agent.shorts.qa import normalize_gemini_scenes_qa

    parsed = {
        "verdict": "FAIL",
        "issues": [{
            "type": "graphic_count",
            "scene_id": None,
            "severity": "major",
            "detail": "Scene already has 2 graphics; at most 2 graphics allowed, remove one.",
        }],
        "required_changes": ["Remove one graphic — at most 2 graphics allowed."],
        "warnings": [],
        "scores": {},
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 9,
            "visual_specificity": 9,
            "clarity": 9,
            "retention_pacing": 9,
            "natural_spanish": 9,
            "saveability": 8.5
        }
    }

    res = normalize_gemini_scenes_qa(parsed, graphic_count=2, graphic_led=False)
    assert res["verdict"] == "PASS"
    assert not any(i.get("type") == "graphic_count" for i in res["issues"])
    assert not res["required_changes"]
    assert any("Downgraded graphic-count" in w for w in res["warnings"])

    # But a genuine over-cap (>=4 graphics) is still a real, blocking issue.
    res4 = normalize_gemini_scenes_qa(parsed, graphic_count=4, graphic_led=False)
    assert res4["verdict"] == "FAIL"
    assert any(i.get("type") == "graphic_count" for i in res4["issues"])


def test_two_graphics_not_failed_for_allowed_2_graphic_limit_phrase():
    from video_agent.shorts.qa import normalize_gemini_scenes_qa

    parsed = {
        "verdict": "FAIL",
        "issues": [{
            "type": "product_quality_score_low",
            "scene_id": None,
            "severity": "major",
            "detail": (
                "The scene flow exceeds the allowed 2 graphic scene limit "
                "(s04, s05 are graphics, but the structure creates unnecessary scene bloat)."
            ),
        }],
        "required_changes": ["Ensure only 2 scenes use graphic_* layout."],
        "warnings": [],
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 9,
            "visual_specificity": 9,
            "clarity": 9,
            "retention_pacing": 9,
            "natural_spanish": 9,
            "saveability": 8.5
        }
    }

    res = normalize_gemini_scenes_qa(parsed, graphic_count=2, graphic_led=False)

    assert res["verdict"] == "PASS"
    assert not res["issues"]
    assert not res["required_changes"]
    assert any("Downgraded graphic-count" in w for w in res["warnings"])


def _nine_scene_doc():
    scenes = [
        {"id": "s1", "duration_sec": 2.5, "layout": "short_hook", "on_screen_text": "ELIGE BIEN", "caption": "c", "visual_prompt": "v vertical", "narration": "¿Eliges bien el pan?"},
        {"id": "s2", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "REVISA ETIQUETA", "caption": "c", "visual_prompt": "v vertical", "narration": "Revisa la etiqueta primero."},
        {"id": "s3", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "HARINA INTEGRAL", "caption": "c", "visual_prompt": "v vertical", "narration": "Busca harina integral."},
        {"id": "s4", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "MIRA FIBRA", "caption": "c", "visual_prompt": "v vertical", "narration": "Mira la fibra."},
        {"id": "s5", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "MENOS AZUCAR", "caption": "c", "visual_prompt": "v vertical", "narration": "Compara los azúcares."},
        {"id": "s6", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "SIN ANADIDO", "caption": "c", "visual_prompt": "v vertical", "narration": "Evita azúcar añadido."},
        {"id": "s7", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "LISTA CORTA", "caption": "c", "visual_prompt": "v vertical", "narration": "Lee la lista corta."},
        {"id": "s8", "duration_sec": 3.0, "layout": "short_tip", "on_screen_text": "PAN DENSO", "caption": "c", "visual_prompt": "v vertical", "narration": "Elige pan denso."},
        {"id": "s9", "duration_sec": 2.5, "layout": "short_cta", "on_screen_text": "GUARDA ESTA LISTA", "caption": "c", "visual_prompt": "v vertical", "narration": "Guarda esta lista."},
    ]
    return {
        "channel_id": "vida-plena-45", "short_id": "short-pacing",
        "total_duration_sec": 26.0, "scenes": scenes,
        "qa": {"verdict": "PENDING_SHORTS_QA"},
    }


def test_nine_scenes_soft_pacing_triggers_simplification_not_max_regen(tmp_path: Path):
    """A 9-scene candidate with retention_pacing=6 (soft) should be rescued by
    deterministic simplification rather than failing after max regenerations."""
    from video_agent.shorts import short_builder, paths

    job = _long_job(tmp_path)
    calls: list[str] = []

    script9 = {
        **_GOOD_SCRIPT,
        "short_id": "short-pacing",
        "narration": "Revisa esta lista para elegir mejor pan. Mira la etiqueta y la fibra.",
        "cta": "Guarda esta lista.",
    }

    def gemini_fn(prompt):
        # Scene QA: structurally fine, but pacing is a soft 6 -> product FAIL.
        return json.dumps({
            "verdict": "PASS",
            "issues": [],
            "required_changes": [],
            "product_scores": {
                "audience_fit_45_plus": 8,
                "hook_strength": 8,
                "visual_specificity": 8,
                "clarity": 8,
                "retention_pacing": 6,   # soft pacing
                "natural_spanish": 8,
                "saveability": 8,
            },
        })

    plan = {"short_id": "short-pacing", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Revisa esta lista."}

    res = short_builder.build_short(
        job,
        plan,
        _cfg(),
        llm_fn=_llm_fn_factory(script=script9, scenes=_nine_scene_doc()),
        gemini_fn=gemini_fn,
        **_stub_io(calls),
    )

    # Under new strict thresholds, pacing=6 blocks render and results in needs_review
    assert res["status"] == "needs_review"
    assert "render" not in calls


# --------------------------------------------------------------------------
# Regression: 3-graphic normal Short must fail deterministically before Gemini
# --------------------------------------------------------------------------

def _three_graphic_scenes():
    """Mirrors the failing short-02_idea-02 candidate: a checklist Short with
    3 graphics (setup checklist + label callout + comparison)."""
    return [
        {"id": "s01", "duration_sec": 3.0, "layout": "short_hook", "on_screen_text": "MARRON NO BASTA", "caption": "c", "visual_prompt": "manos sostienen pan integral en el súper, vertical", "narration": "El pan marrón no es integral."},
        {"id": "s02", "duration_sec": 3.5, "layout": "short_tip", "on_screen_text": "REVISA RAPIDO", "caption": "c", "visual_prompt": "carrito de la compra en pasillo de panadería, vertical", "narration": "Haz esta revisión rápida."},
        {"id": "s03", "duration_sec": 4.0, "layout": "graphic_checklist", "on_screen_text": "TRES PASOS", "caption": "c", "visual_prompt": "checklist", "narration": "Tres comprobaciones rápidas.", "layout_payload": {"title": "TRES PASOS", "items": ["Color no basta", "Primer ingrediente", "Compara fibra"]}},
        {"id": "s04", "duration_sec": 4.5, "layout": "graphic_label_callout", "on_screen_text": "PRIMER INGREDIENTE", "caption": "c", "visual_prompt": "vertical nutrition label close-up", "narration": "Busca harina integral al principio.", "layout_payload": {"title": "PRIMER INGREDIENTE", "productLabel": "Pan integral", "callouts": [{"label": "Harina", "value": "integral"}, {"label": "Fibra", "value": "6 g"}]}},
        {"id": "s05", "duration_sec": 3.5, "layout": "short_tip", "on_screen_text": "EN EL SUPER", "caption": "c", "visual_prompt": "persona comparando dos panes en el supermercado, vertical", "narration": "Compáralo en el súper."},
        {"id": "s06", "duration_sec": 4.5, "layout": "graphic_comparison", "on_screen_text": "FIBRA Y AZUCAR", "caption": "c", "visual_prompt": "vertical two labels", "narration": "Compara fibra y azúcar.", "layout_payload": {"title": "EN EL SÚPER", "left": {"heading": "MEJOR", "text": "Más fibra"}, "right": {"heading": "CUIDADO", "text": "Más azúcar"}}},
        {"id": "s07", "duration_sec": 2.5, "layout": "short_cta", "on_screen_text": "GUARDA ESTA LISTA", "caption": "c", "visual_prompt": "pan en cesta de la compra, vertical", "narration": "Guarda esta lista."},
    ]


def test_three_graphics_fails_deterministic_validation_and_repair_converts_checklist():
    from video_agent.shorts import validate_scenes as v

    scenes = _three_graphic_scenes()
    script = {"short_format": "checklist", "narration": " ".join(s["narration"] for s in scenes), "cta": "Guarda esta lista."}
    doc = {"total_duration_sec": round(sum(s["duration_sec"] for s in scenes), 1), "scenes": scenes}

    issues = v.validate_scene_structure(scenes, scenes_doc=doc, script=script)

    # 3 graphics on a normal (non-graphic-led) checklist Short -> repairable error
    gc = [i for i in issues if i.type == "graphic_count"]
    assert gc, "expected a graphic_count issue"
    assert gc[0].severity == "repairable_error"
    assert v.has_blocking_or_repairable(issues)

    # Repair plan must keep label_callout + comparison and convert the checklist setup (s03)
    plan = v.build_scene_repair_plan(scenes, issues, script=script)
    blob = "\n".join(plan["instructions"])
    assert "s03" in blob
    assert "short_myth" in blob or "short_tip" in blob
    assert "s04" in blob and "s06" in blob  # the two kept high-value graphics


def test_two_graphics_realistic_base_passes_deterministic_validation():
    from video_agent.shorts import validate_scenes as v

    scenes = _three_graphic_scenes()
    # Convert the setup checklist (s03) into a realistic short_myth -> 2 graphics left.
    scenes[2] = {
        "id": "s03", "duration_sec": 3.0, "layout": "short_myth",
        "on_screen_text": "NO SOLO EL COLOR", "caption": "c",
        "visual_prompt": "primer plano de manos girando el envase de pan para leer la etiqueta, vertical",
        "narration": "No te fíes solo del color.",
    }
    script = {"short_format": "checklist", "narration": " ".join(s["narration"] for s in scenes), "cta": "Guarda esta lista."}
    doc = {"total_duration_sec": round(sum(s["duration_sec"] for s in scenes), 1), "scenes": scenes}

    issues = v.validate_scene_structure(scenes, scenes_doc=doc, script=script)

    assert v.count_graphic_scenes(scenes) == 2
    assert not any(i.type == "graphic_count" for i in issues)
    assert not v.has_blocking_or_repairable(issues)


def test_explicit_graphic_led_allows_three_graphics_as_warning():
    from video_agent.shorts import validate_scenes as v

    scenes = _three_graphic_scenes()
    script = {"short_format": "checklist", "graphic_led": True, "narration": " ".join(s["narration"] for s in scenes), "cta": "Guarda esta lista."}
    doc = {"total_duration_sec": round(sum(s["duration_sec"] for s in scenes), 1), "scenes": scenes}

    issues = v.validate_scene_structure(scenes, scenes_doc=doc, script=script)
    gc = [i for i in issues if i.type == "graphic_count"]
    assert gc and gc[0].severity == "warning"
    assert not v.has_blocking_or_repairable([i for i in issues if i.type == "graphic_count"])


# --------------------------------------------------------------------------
# Regression: ChatGPT provider-error robustness (spec §2-§5)
# --------------------------------------------------------------------------

_PROVIDER_ERROR_TEXT = (
    "Something went wrong. If this issue persists please contact us through our "
    "help center at help.openai.com."
)


def test_is_provider_error_text_and_payload_guards():
    from video_agent.shorts.short_scene_builder import (
        is_provider_error_text, is_valid_scene_payload,
    )
    assert is_provider_error_text(_PROVIDER_ERROR_TEXT)
    assert is_provider_error_text("An error occurred, try again later")
    assert not is_provider_error_text('{"scenes": [{"id": "s1"}]}')
    assert not is_provider_error_text("")
    assert not is_provider_error_text(None)

    assert is_valid_scene_payload({"scenes": [{"id": "s1"}]})
    assert not is_valid_scene_payload({"scenes": []})
    assert not is_valid_scene_payload({"scenes": "nope"})
    assert not is_valid_scene_payload("Something went wrong")
    assert not is_valid_scene_payload(None)


def test_build_short_scenes_raises_provider_error_not_empty_scenes(tmp_path: Path):
    from video_agent.shorts import short_scene_builder as ssb

    job = _long_job(tmp_path)

    def llm_fn(kind, prompt):
        return _PROVIDER_ERROR_TEXT

    try:
        ssb.build_short_scenes(
            job, {"short_id": "short-pe"}, _GOOD_SCRIPT, _cfg(), llm_fn,
        )
    except ssb.ChatGPTProviderError as exc:
        assert "help.openai.com" in (exc.snippet or "").lower() or exc.snippet
    else:
        raise AssertionError("expected ChatGPTProviderError")

    # Provider error must NOT have written an (empty) scenes artifact.
    from video_agent.shorts import paths
    sd = paths.short_dir(job, "short-pe")
    assert not (sd / "json" / paths.SHORT_SCENES_FILE).exists()


def test_build_short_provider_error_does_not_emit_scene_count_zero(tmp_path: Path):
    from video_agent.shorts import short_builder, paths
    import json as _json

    job = _long_job(tmp_path)
    calls: list[str] = []
    scene_calls = {"n": 0}

    def llm_fn(kind, prompt):
        if kind == "script":
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            scene_calls["n"] += 1
            return _PROVIDER_ERROR_TEXT  # provider error every time
        return "{}"

    def gemini_fn(prompt):
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": []})

    plan = {"short_id": "short-pe2", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "music_track": "shorts_sleep_stress", "narration_seed": "x"}
    res = short_builder.build_short(
        job, plan, _cfg(), llm_fn=llm_fn, gemini_fn=gemini_fn, **_stub_io(calls),
    )

    # Surfaced as a provider error, not a scene-QA / scene_count failure.
    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "PROVIDER_ERROR"
    assert res.get("failure_kind") == "chatgpt_provider_error"
    assert "render" not in calls

    # Retried on its own budget (default 2) -> 3 scene-generation attempts.
    assert scene_calls["n"] == 3

    # The failure report is a provider error, NOT "scene_count=0".
    sd = paths.short_dir(job, "short-pe2")
    fr = _json.loads((sd / "json" / paths.SHORT_FAILURE_REPORT_FILE).read_text(encoding="utf-8"))
    assert fr["type"] == "chatgpt_provider_error"
    # No empty/invalid scenes artifact, so no scene_count=0 creative feedback.
    assert not (sd / "json" / paths.SHORT_SCENES_QA_FILE).exists()


def test_chatgpt_send_with_recovery_clears_cookies_then_succeeds():
    import asyncio
    from video_agent.shorts import llm as shorts_llm

    class FakeClient:
        def __init__(self, responses):
            self._responses = list(responses)
            self.sends = 0
            self.cleared = 0

        async def chatgpt_send(self, prompt, *, response_timeout_ms=300_000):
            self.sends += 1
            return self._responses.pop(0)

        async def auth_clear_cookies(self, site):
            self.cleared += 1
            return {"ok": True, "site": site}

    good = '{"scenes": [{"id": "s1", "layout": "short_hook"}]}'
    client = FakeClient([_PROVIDER_ERROR_TEXT, good])
    out = asyncio.run(shorts_llm.chatgpt_send_with_recovery(client, "prompt"))
    assert out == good
    assert client.cleared == 1   # cookie reset before the successful retry
    assert client.sends == 2


def test_chatgpt_send_with_recovery_exhausts_and_returns_provider_text():
    import asyncio
    from video_agent.shorts import llm as shorts_llm

    class FakeClient:
        def __init__(self):
            self.sends = 0
            self.cleared = 0

        async def chatgpt_send(self, prompt, *, response_timeout_ms=300_000):
            self.sends += 1
            return _PROVIDER_ERROR_TEXT

        async def auth_clear_cookies(self, site):
            self.cleared += 1
            return {"ok": True}

    client = FakeClient()
    out = asyncio.run(
        shorts_llm.chatgpt_send_with_recovery(client, "prompt", max_provider_retries=2)
    )
    # Still provider text -> caller (build_short_scenes) will raise ChatGPTProviderError.
    from video_agent.shorts.short_scene_builder import is_provider_error_text
    assert is_provider_error_text(out)
    # 1 initial + 2 retries = 3 sends; cookies cleared on each retry.
    assert client.sends == 3
    assert client.cleared >= 2


def test_empty_scenes_json_gets_distinct_repair_message():
    from video_agent.shorts import validate_scenes as v
    issues = v.validate_scene_structure([], scenes_doc={"scenes": []}, script=_GOOD_SCRIPT)
    empty = [i for i in issues if i.type == "empty_scenes"]
    assert empty and empty[0].severity == "repairable_error"
    assert "empty scenes array" in empty[0].repair_hint.lower()
    # The misleading "scene_count … outside recommended range" is NOT used for 0.
    assert not any(i.type == "scene_count" for i in issues)


def test_short_scene_prompt_v6_layout_budget_ordering_and_selfcheck():
    from video_agent.shorts import prompts
    cfg = _cfg()
    plan = {"short_id": "short-order", "format": "checklist"}
    script = {**_GOOD_SCRIPT, "short_format": "checklist"}
    p = prompts.short_scene_prompt_v6(cfg, plan, script)

    i_budget = p.find("NON-NEGOTIABLE LAYOUT BUDGET")
    i_count = p.find("SCENE COUNT & TIMING")
    i_graphics = p.find("GRAPHIC SCENE LAYOUTS")
    i_schema = p.find("RETURN JSON SCHEMA")
    i_selfcheck = p.find("FINAL SELF-CHECK BEFORE RETURNING JSON")

    assert i_budget != -1
    assert i_budget < i_count < i_graphics < i_schema
    # self-check sits just before the schema
    assert i_selfcheck != -1 and i_selfcheck < i_schema
    # old verbatim-narration instruction is gone
    assert "Keep the narration faithful to the SCRIPT" not in p
    assert "do NOT copy long" in p


def _scene_qa_scores() -> dict:
    return {
        "audience_fit_45_plus": 10, "hook_strength": 10, "visual_specificity": 10,
        "clarity": 10, "retention_pacing": 9, "natural_spanish": 10, "saveability": 10,
    }


def test_soft_warning_does_not_trigger_excess_retry(tmp_path: Path):
    """Fix C4: deterministic scene validation PASS + LLM QA WARN must not loop."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_calls = {"n": 0}

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_calls["n"] += 1
            return json.dumps({
                "verdict": "WARN",
                "issues": [],
                "required_changes": [],
                "warnings": ["Mild pacing preference, not blocking."],
                "product_scores": _scene_qa_scores(),
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-warn-noretry", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), gemini_fn=gemini_fn, **_stub_io(calls))

    assert res["status"] == "rendered"
    assert qa_calls["n"] == 1, "WARN scene QA must not trigger a regeneration loop"


def test_short_scene_retry_cap_on_soft_issues(tmp_path: Path):
    """Fix C4/C6#3: repeated soft QA warnings must not exceed 2 scene attempts."""
    from video_agent.shorts import short_builder
    job = _long_job(tmp_path)
    calls: list[str] = []
    qa_calls = {"n": 0}

    def gemini_fn(prompt: str, **kwargs):
        if "Scenes QA reviewer" in prompt:
            qa_calls["n"] += 1
            return json.dumps({
                "verdict": "WARN",
                "issues": [],
                "required_changes": [],
                "warnings": ["Soft preference."],
                "product_scores": _scene_qa_scores(),
            })
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": [], "warnings": []})

    plan = {"short_id": "short-cap", "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "source_start_sec": 183.0, "source_end_sec": 199.0, "music_track": "shorts_sleep_stress",
            "narration_seed": "Marca una hora de cierre."}
    res = short_builder.build_short(job, plan, _cfg(), llm_fn=_llm_fn_factory(), gemini_fn=gemini_fn, **_stub_io(calls))

    assert res["status"] == "rendered"
    assert qa_calls["n"] <= 2
