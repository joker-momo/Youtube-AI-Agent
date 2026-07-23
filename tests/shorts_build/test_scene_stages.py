from __future__ import annotations

import pytest

from .conftest import *  # noqa: F401,F403


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


def test_scene_normalizer_backfills_source_scene_ids_from_idea_items():
    from video_agent.shorts.short_scene_builder import normalize_short_scenes

    doc = {
        "scenes": [
            {
                "scene_id": "s1",
                "layout": "short_tip",
                "narration": "Suelta la mandíbula.",
                "covers_items": [1],
            }
        ]
    }
    script = {
        "narration": "Suelta la mandíbula.",
        "idea_items": [
            {
                "item_id": 1,
                "source_support": ["scene-16"],
            }
        ],
    }

    out = normalize_short_scenes(doc, script)

    assert out["scenes"][0]["source_scene_ids"] == ["scene-16"]


def test_scene_normalizer_caps_short_cta_duration():
    from video_agent.shorts.short_scene_builder import normalize_short_scenes

    doc = {
        "scenes": [
            {
                "scene_id": "s1",
                "layout": "short_cta",
                "duration_sec": 4.8,
                "narration": "Pruébalo esta noche.",
            }
        ]
    }

    out = normalize_short_scenes(doc, {"narration": "Pruébalo esta noche."})

    assert out["scenes"][0]["duration_sec"] == 2.8
    assert out["total_duration_sec"] == 2.8


def test_scene_normalizer_restores_only_short_hook_not_full_script_beat():
    from video_agent.shorts.short_scene_builder import normalize_short_scenes
    from video_agent.shorts.validate_scenes import estimate_fits

    doc = {
        "scenes": [
            {
                "scene_id": "s01",
                "layout": "short_hook",
                "duration_sec": 2.4,
                "narration": "Tu mano te da una pista.",
                "on_screen_text": "TU MANO AYUDA",
            },
            {
                "scene_id": "s02",
                "layout": "short_tip",
                "duration_sec": 3.2,
                "narration": "Decide antes de empezar.",
            },
        ]
    }
    script = {
        "hook": "TU MANO AYUDA",
        "narration": "TU MANO AYUDA. ¿Repites pan por costumbre? No hay medida universal.",
        "beats": [
            {
                "purpose": "hook",
                "narration": "TU MANO AYUDA. ¿Repites pan por costumbre? No hay medida universal.",
            }
        ],
    }

    out = normalize_short_scenes(doc, script)
    first = out["scenes"][0]

    assert first["narration"] == "TU MANO AYUDA"
    assert "Repites pan" not in first["narration"]
    assert estimate_fits(first["narration"], first["duration_sec"])
    assert "first_hook_narration_restored_from_hook" in first["planner_warnings"]


def test_scene_normalizer_replaces_stale_food_payload_for_non_food_topic():
    from video_agent.shorts.short_scene_builder import normalize_short_scenes

    doc = {
        "scenes": [
            {
                "scene_id": "s07",
                "layout": "graphic_checklist",
                "duration_sec": 5.0,
                "on_screen_text": "MEJOR ASÍ",
                "caption": "Menos perfección. Más constancia. Ritual repetible",
                "narration": "Diez minutos, si se repiten.",
                "layout_payload": {
                    "title": "MEJOR ASÍ",
                    "items": ["Porción visible", "Plato pequeño", "Comida completa"],
                },
            }
        ]
    }
    script = {
        "title": "Pequeños límites que le dicen al cuerpo que ya no hay que correr",
        "hook": "BAJA LA CARGA",
        "narration": "Diez minutos repetibles bastan.",
    }

    out = normalize_short_scenes(doc, script)

    assert out["scenes"][0]["layout_payload"]["items"] == [
        "Menos perfección",
        "Más constancia",
        "Ritual repetible",
    ]
    assert "stale_food_payload_repaired" in out["scenes"][0]["planner_warnings"]


def test_scene_normalizer_keeps_food_payload_for_food_topic():
    from video_agent.shorts.short_scene_builder import normalize_short_scenes

    doc = {
        "scenes": [
            {
                "scene_id": "s07",
                "layout": "graphic_checklist",
                "narration": "Mejor: porción visible, plato pequeño, comida completa.",
                "layout_payload": {
                    "title": "MEJOR ASÍ",
                    "items": ["Porción visible", "Plato pequeño", "Comida completa"],
                },
            }
        ]
    }
    script = {
        "title": "Cómo comer pan sin pasarte después de los 45",
        "narration": "Mejor: porción visible, plato pequeño, comida completa.",
    }

    out = normalize_short_scenes(doc, script)

    assert out["scenes"][0]["layout_payload"]["items"] == [
        "Porción visible",
        "Plato pequeño",
        "Comida completa",
    ]


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


def test_graphic_comparison_allows_blank_optional_badges():
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scene = {
        "id": "s04",
        "layout": "graphic_comparison",
        "duration_sec": 4,
        "layout_payload": {
            "title": "DESAYUNO: 1 O 2",
            "left": {"heading": "1 REBANADA", "text": "Mira el tamaño", "badge": ""},
            "right": {"heading": "2 REBANADAS", "text": "Mira el acompañamiento", "badge": ""},
        },
    }

    warnings = validate_short_graphic_scenes([scene])

    assert warnings == []
    assert "badge" not in scene["layout_payload"]["left"]
    assert "badge" not in scene["layout_payload"]["right"]


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


def test_checklist_requires_at_least_one_graphic_candidate():
    from video_agent.shorts.validate_scenes import validate_scene_structure

    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.4, "on_screen_text": "TU MANO AYUDA", "narration": "Tu mano ayuda."},
        {"id": "s02", "layout": "short_tip", "duration_sec": 4.0, "on_screen_text": "MIRA TU DÍA", "narration": "Uno: depende de apetito, actividad, sueño, objetivos y resto del plato."},
        {"id": "s03", "layout": "short_tip", "duration_sec": 4.0, "on_screen_text": "DESAYUNO FLEXIBLE", "narration": "Dos: una o dos rebanadas según tamaño y acompañamiento."},
        {"id": "s04", "layout": "short_tip", "duration_sec": 4.0, "on_screen_text": "TAMAÑO PALMA", "narration": "Tres: una rebanada del tamaño de tu palma puede ser referencia inicial."},
        {"id": "s05", "layout": "short_cta", "duration_sec": 2.4, "on_screen_text": "GUÁRDALO", "narration": "Guárdalo."},
    ]

    issues = validate_scene_structure(
        scenes,
        scenes_doc={"total_duration_sec": 16.8, "scenes": scenes},
        script={"short_format": "checklist", "narration": " ".join(s["narration"] for s in scenes)},
    )

    blocking = [i for i in issues if i.severity in ("blocking_error", "repairable_error")]
    assert any(i.type == "missing_graphic_required" for i in blocking), issues
    assert any("graphic_checklist" in (i.repair_hint or "") for i in blocking)


def test_missing_graphic_repair_promotes_structured_short_checklist():
    from video_agent.shorts.validate_scenes import (
        repair_missing_graphic_checklist_scene,
        validate_scene_structure,
    )

    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.4, "on_screen_text": "TU MANO AYUDA", "narration": "TU MANO AYUDA."},
        {
            "id": "s02",
            "layout": "short_tip",
            "duration_sec": 4.9,
            "on_screen_text": "TU CONTEXTO",
            "narration": "La porción depende de apetito, actividad, sueño, objetivos y plato.",
            "layout_payload": {
                "title": "TU CONTEXTO",
                "items": ["Apetito y actividad", "Sueño y objetivos", "Resto del plato"],
            },
        },
        {"id": "s03", "layout": "graphic_checklist", "duration_sec": 4.2, "on_screen_text": "MEJOR ASÍ", "narration": "Prueba una rebanada del tamaño de tu palma.", "layout_payload": {"title": "MEJOR ASÍ", "items": ["Una rebanada", "Tamaño palma"]}},
        {"id": "s04", "layout": "short_cta", "duration_sec": 2.4, "on_screen_text": "GUÁRDALO", "narration": "Guárdalo."},
    ]
    script = {
        "short_format": "checklist",
        "narration": " ".join(s["narration"] for s in scenes),
        "idea_contract": {"must_preserve_count": True, "original_count": 2, "final_count": 2},
    }

    assert repair_missing_graphic_checklist_scene(scenes, script) is True

    assert scenes[1]["layout"] == "graphic_checklist"
    assert scenes[1]["visual_type"] == "graphic"
    assert scenes[1]["asset_strategy"] == "ai_image_preferred"
    issues = validate_scene_structure(
        scenes,
        scenes_doc={"total_duration_sec": 13.9, "scenes": scenes},
        script=script,
    )
    assert not any(i.type == "missing_graphic_required" for i in issues), issues


def test_missing_graphic_repair_promotes_compact_portion_tip_without_items():
    from video_agent.shorts.validate_scenes import (
        repair_missing_graphic_checklist_scene,
        validate_scene_structure,
    )

    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.4, "on_screen_text": "TU MANO AYUDA", "narration": "TU MANO AYUDA."},
        {"id": "s02", "layout": "short_tip", "duration_sec": 4.0, "on_screen_text": "DESAYUNO: 1 O 2", "narration": "En el desayuno, una o dos rebanadas pueden encajar.", "visual_prompt": "Spanish breakfast table with one or two bread slices clearly compared.", "layout_payload": {"title": "DESAYUNO: 1 O 2", "items": []}},
        {"id": "s03", "layout": "graphic_comparison", "duration_sec": 4.0, "on_screen_text": "USA TU PALMA", "narration": "Prueba una rebanada del tamaño de tu palma.", "layout_payload": {"title": "USA TU PALMA", "left": {"heading": "REFERENCIA", "text": "Tu palma"}, "right": {"heading": "PORCIÓN", "text": "Una rebanada"}}},
        {"id": "s04", "layout": "short_cta", "duration_sec": 2.4, "on_screen_text": "GUÁRDALO", "narration": "Guárdalo."},
    ]
    script = {
        "short_format": "checklist",
        "narration": " ".join(s["narration"] for s in scenes),
        "idea_contract": {"must_preserve_count": True, "original_count": 2, "final_count": 2},
    }

    assert repair_missing_graphic_checklist_scene(scenes, script) is True

    assert scenes[1]["layout"] == "graphic_comparison"
    assert scenes[1]["visual_type"] == "graphic"
    assert scenes[1]["asset_strategy"] == "ai_image_preferred"
    assert scenes[1]["layout_payload"]["left"]["text"] == "1 rebanada"
    assert scenes[1]["layout_payload"]["right"]["text"] == "2 rebanadas"
    issues = validate_scene_structure(
        scenes,
        scenes_doc={"total_duration_sec": 12.8, "scenes": scenes},
        script=script,
    )
    assert not any(i.type == "missing_graphic_required" for i in issues), issues


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
    from video_agent.shorts import paths, qa
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
    from video_agent.shorts import paths, qa
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
    from video_agent.shorts import paths, qa
    job = _good_short_dir(tmp_path)
    sp = paths.short_dir(job, "short-01") / "short_script.json"
    d = json.loads(sp.read_text())
    d["narration"] = "Esta rutina cura el insomnio para siempre, garantizado."
    sp.write_text(json.dumps(d), encoding="utf-8")
    out = qa.run_short_qa(job, "short-01", _cfg(), music_track="shorts_sleep_stress")
    assert out["verdict"] == "FAIL"
    assert any("overclaim" in i or "medical" in i for i in out["issues"])


def test_script_rule_qa_rejects_over_word_budget_before_scenes(tmp_path: Path):
    from video_agent.shorts import paths, qa

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
    from video_agent.shorts import paths, qa

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


def test_normalize_short_scenes_restores_hook_without_copying_full_beat():
    from video_agent.shorts import short_scene_builder

    scenes_doc = {
        "scenes": [
            {
                "scene_id": "s01",
                "layout": "short_hook",
                "narration": "¿Repites pan por costumbre?",
                "on_screen_text": "TU MANO AYUDA",
                "duration_sec": 2.6,
            },
            {
                "scene_id": "s02",
                "layout": "short_tip",
                "narration": "Depende de tu apetito.",
                "duration_sec": 4.0,
            },
        ]
    }
    script = {
        "hook": "TU MANO AYUDA",
        "narration": "TU MANO AYUDA. ¿Repites pan por costumbre? Depende de tu apetito.",
        "beats": [
            {
                "time_sec": "0-3",
                "purpose": "hook",
                "narration": "TU MANO AYUDA. ¿Repites pan por costumbre?",
            }
        ],
    }

    out = short_scene_builder.normalize_short_scenes(scenes_doc, script)

    assert out["scenes"][0]["narration"] == "TU MANO AYUDA"
    assert "Repites pan" not in out["scenes"][0]["narration"]
    assert out["scenes"][1]["narration"] == "Depende de tu apetito."


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


def test_qa_response_keeps_missing_graphic_as_required_change():
    from video_agent.shorts.qa import normalize_gemini_scenes_qa

    parsed_input = {
        "verdict": "FAIL",
        "issues": [
            {
                "type": "missing_graphic_required",
                "scene_id": "s03",
                "severity": "major",
                "detail": "Missing graphic: checklist scene should become graphic_checklist.",
            }
        ],
        "required_changes": ["Missing graphic: convert s03 to graphic_checklist."],
        "warnings": [],
        "product_scores": {
            "audience_fit_45_plus": 9,
            "hook_strength": 9,
            "visual_specificity": 9,
            "clarity": 9,
            "retention_pacing": 9,
            "natural_spanish": 9,
            "saveability": 8.5,
        },
    }

    normalized = normalize_gemini_scenes_qa(parsed_input)

    assert normalized["verdict"] == "FAIL"
    assert any(issue.get("type") == "missing_graphic_required" for issue in normalized["issues"])
    assert normalized["required_changes"] == ["Missing graphic: convert s03 to graphic_checklist."]


def test_checklist_missing_graphic_is_repairable_error():
    from video_agent.shorts.validate_scenes import validate_scene_structure
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.5, "on_screen_text": "MARRÓN NO BASTA", "visual_prompt": "vertical bread package label", "narration": "El pan marrón no basta."},
        {"id": "s02", "layout": "short_tip", "duration_sec": 4.0, "on_screen_text": "POR 100 G", "visual_prompt": "vertical hands comparing labels", "narration": "Compara fibra y azúcares por 100 g antes de elegir."},
        {"id": "s03", "layout": "short_cta", "duration_sec": 2.4, "on_screen_text": "GUARDA ESTA LISTA", "visual_prompt": "vertical shopping basket", "narration": "Guarda esta lista."},
    ]

    issues = validate_scene_structure(
        scenes,
        scenes_doc={"total_duration_sec": 8.9, "scenes": scenes},
        script={"short_format": "checklist", "narration": " ".join(s["narration"] for s in scenes)}
    )

    blocking = [i for i in issues if i.severity in ("blocking_error", "repairable_error")]

    assert any(i.type == "missing_graphic_required" for i in blocking)
    assert any("graphic_label_callout" in (i.repair_hint or "") for i in blocking)


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
        {"id": "s03", "layout": "graphic_comparison", "duration_sec": 3.6, "on_screen_text": "SUMAR SIN DECIDIR", "caption": "Con arroz o pasta", "visual_prompt": "Graphic comparison card: bread alone vs bread added to rice or pasta", "narration": "Dos: sumarlo sin decidir.", "layout_payload": {"title": "DECIDE PRIMERO", "left": {"heading": "MEJOR", "text": "Elige una porción"}, "right": {"heading": "CUIDADO", "text": "Pan encima de arroz"}}},
        {"id": "s04", "layout": "short_pain", "duration_sec": 3.6, "on_screen_text": "BARRA A LA VISTA", "caption": "Demasiado a mano", "visual_prompt": "Realistic bread bar left on dining table", "narration": "Tres: dejar la barra a la vista."},
        {"id": "s05", "layout": "short_pain", "duration_sec": 3.6, "on_screen_text": "CANSANCIO", "caption": "Otro trozo", "visual_prompt": "Realistic tired adult cutting another bread slice", "narration": "Cuatro: cortar por cansancio."},
        {"id": "s06", "layout": "short_pain", "duration_sec": 3.6, "on_screen_text": "CENA IMPROVISADA", "caption": "A bocados", "visual_prompt": "Realistic bread and cheese dinner bites on plate", "narration": "Cinco: cenar improvisando."},
        {"id": "s07", "layout": "graphic_checklist", "duration_sec": 4.8, "on_screen_text": "MEJOR ASÍ", "caption": "Porción visible", "visual_prompt": "Graphic checklist card: visible bread portion, small plate, complete meal", "narration": "Mejor: porción visible, plato pequeño, comida completa.", "layout_payload": {"title": "MEJOR ASÍ", "items": ["Porción visible", "Plato pequeño", "Comida completa"]}},
        {"id": "s08", "layout": "short_cta", "duration_sec": 2.6, "on_screen_text": "GUÁRDALO", "caption": "PARA TU PRÓXIMA CENA", "visual_prompt": "Realistic warm kitchen close-up", "narration": "Guárdalo."},
    ]
    scenes_doc = {"short_id": short_id, "total_duration_sec": 28.2, "scenes": scenes}
    (short_dir / "json" / paths.SHORT_SCRIPT_FILE).write_text(json.dumps(script), encoding="utf-8")
    (short_dir / "json" / paths.SHORT_SCENES_FILE).write_text(json.dumps(scenes_doc), encoding="utf-8")

    out = qa.run_short_scenes_qa(job, short_id, _cfg(), gemini_fn=None)

    assert out["verdict"] == "PASS"
    assert not any("duration" in str(issue).lower() for issue in out["issues"])


def test_five_error_bread_payoff_layout_repairs_to_graphic_checklist():
    from video_agent.shorts import validate_scenes as v

    script = {
        "short_format": "mistake_list",
        "hook": "No es el pan.",
        "narration": (
            "No es el pan. Uno: comerlo de pie. Dos: sumarlo sin decidir. "
            "Tres: dejar la barra a la vista. Cuatro: cortar por cansancio. "
            "Cinco: cenar improvisando con pan. Mejor: porción visible, plato pequeño, comida completa. Guárdalo."
        ),
        "idea_contract": {"original_count": 5, "final_count": 5},
    }
    scenes = [
        {"id": "s01", "layout": "short_hook", "duration_sec": 2.8, "narration": "No es el pan."},
        {"id": "s02", "layout": "short_pain", "duration_sec": 3.6, "narration": "Uno: comerlo de pie."},
        {"id": "s03", "layout": "short_pain", "duration_sec": 3.6, "narration": "Dos: sumarlo sin decidir."},
        {"id": "s04", "layout": "short_pain", "duration_sec": 3.6, "narration": "Tres: dejar la barra a la vista."},
        {"id": "s05", "layout": "short_pain", "duration_sec": 3.6, "narration": "Cuatro: cortar por cansancio."},
        {"id": "s06", "layout": "short_pain", "duration_sec": 3.6, "narration": "Cinco: cenar improvisando con pan."},
        {
            "id": "s07",
            "layout": "graphic_routine_split",
            "duration_sec": 4.6,
            "on_screen_text": "MEJOR ASÍ",
            "narration": "Mejor: porción visible, plato pequeño, comida completa.",
            "layout_payload": {
                "title": "RUTINA",
                "blocks": [{"time": "1", "text": "Porción visible"}],
            },
        },
        {"id": "s08", "layout": "short_cta", "duration_sec": 2.6, "narration": "Guárdalo."},
    ]
    doc = {"total_duration_sec": 28.0, "scenes": scenes}
    issues = v.validate_scene_structure(scenes, scenes_doc=doc, script=script)
    assert any(i.type == "payoff_layout" for i in issues)

    assert v.repair_five_error_bread_payoff_layout(scenes, script)

    assert scenes[-2]["layout"] == "graphic_checklist"
    assert scenes[-2]["on_screen_text"] == "MEJOR ASÍ"
    assert scenes[-2]["layout_payload"] == {
        "title": "MEJOR ASÍ",
        "items": ["Porción visible", "Plato pequeño", "Comida completa"],
    }
    repaired_issues = v.validate_scene_structure(scenes, scenes_doc=doc, script=script)
    assert not any(i.type == "payoff_layout" for i in repaired_issues)




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
        is_provider_error_text,
        is_valid_scene_payload,
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
    import json as _json

    from video_agent.shorts import paths, short_builder

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
    # the builder now PRESERVES script content and SPLITS long beats rather than
    # compressing them (the old "do NOT copy long ... verbatim" rule caused
    # source-fidelity QA hard-blocks and was removed)
    assert "do NOT copy long" not in p
    assert "Preserve ALL of the SCRIPT's content" in p
    assert "SPLIT triggers" in p


def _scene_qa_scores() -> dict:
    return {
        "audience_fit_45_plus": 10, "hook_strength": 10, "visual_specificity": 10,
        "clarity": 10, "retention_pacing": 9, "natural_spanish": 10, "saveability": 10,
    }


# --- bug: ChatGPT response-size refusal treated as empty scenes (bridge
# 20260705-141239). Two refusal shapes observed in production:
#   (a) valid JSON: {"error": "...exceeds the maximum response size...", "scenes": []}
#   (b) plain text: "I can't generate the requested JSON ... exceed the response limits"
# Neither is a creative scene-QA failure; both must be caught BEFORE validation.

_SIZE_REFUSAL_JSON = (
    '{"error": "The requested output exceeds the maximum response size I can '
    'produce in a single message. The required JSON schema with 5–8 fully '
    'populated scenes and all mandated fields is too large to fit within the '
    'response limit. Split the task (for example, request scenes 1–3 and '
    'then scenes 4–6, or reduce the required fields), and I can return '
    'valid raw JSON for each part.", "scenes": [], "total_duration_sec": 0}'
)

_SIZE_REFUSAL_TEXT = (
    "I can't generate the requested JSON because it appears to be intended for "
    "an external generation pipeline with a very large, strict schema, and "
    "producing it reliably in-chat would exceed the response limits. Any "
    "truncation would make the JSON invalid."
)


def test_is_size_refusal_response_detects_both_shapes():
    from video_agent.shorts.short_scene_builder import is_size_refusal_response

    # Production shapes.
    assert is_size_refusal_response(_SIZE_REFUSAL_JSON)
    assert is_size_refusal_response(_SIZE_REFUSAL_TEXT)
    # bug 20260705 REOPEN: a non-size error object is NOT a size refusal anymore.
    assert not is_size_refusal_response('{"error": "cannot comply", "scenes": []}')
    # Valid scenes payloads are never a refusal, even if a string contains "error".
    assert not is_size_refusal_response(
        '{"scenes": [{"id": "s01", "narration": "Sin error, todo bien."}]}'
    )
    assert not is_size_refusal_response(
        '{"error": "minor note", "scenes": [{"id": "s01", "narration": "ok"}]}'
    )
    assert not is_size_refusal_response("")
    assert not is_size_refusal_response(None)


def test_classify_refusal_distinguishes_causes():
    """bug 20260705 reopen: quota / auth / policy / generic error objects must
    each be classified distinctly, not blanket-labelled as a size refusal."""
    from video_agent.shorts.short_scene_builder import classify_refusal

    assert classify_refusal(_SIZE_REFUSAL_JSON) == "size_refusal"
    assert classify_refusal('{"error": "You have reached your usage limit for GPT-4.", "scenes": []}') == "quota"
    assert classify_refusal('{"error": "Your session has expired, please log in.", "scenes": []}') == "auth"
    assert classify_refusal('{"error": "I can\'t help with that content policy.", "scenes": []}') == "policy"
    # An error object with no recognised wording is a distinct provider failure,
    # NOT a size refusal.
    assert classify_refusal('{"error": "cannot comply", "scenes": []}') == "error_object"
    # Valid scenes / empty input are never a refusal.
    assert classify_refusal('{"scenes": [{"id": "s01", "narration": "ok"}]}') is None
    assert classify_refusal("") is None
    assert classify_refusal(None) is None


def test_build_short_scenes_size_refusal_recovers_on_compact_retry(tmp_path: Path):
    from video_agent.shorts import paths
    from video_agent.shorts import short_scene_builder as ssb

    job = _long_job(tmp_path)
    prompts_seen: list[str] = []
    good = json.dumps({
        "scenes": [
            {"id": "s01", "layout": "short_hook", "narration": "Hola.", "duration_sec": 4},
            {"id": "s02", "layout": "short_tip", "narration": "Consejo.", "duration_sec": 5},
        ],
        "total_duration_sec": 9,
    })

    def llm_fn(kind, prompt):
        prompts_seen.append(prompt)
        return _SIZE_REFUSAL_JSON if len(prompts_seen) == 1 else good

    out = ssb.build_short_scenes(
        job, {"short_id": "short-sz1"}, _GOOD_SCRIPT, _cfg(), llm_fn,
    )
    assert len(prompts_seen) == 2
    # Retry prompt must carry the compact-output correction.
    assert "SIZE CORRECTION" in prompts_seen[1]
    assert prompts_seen[1].startswith(prompts_seen[0][:200])
    assert out["scenes"], "compact retry must yield non-empty scenes"
    sd = paths.short_dir(job, "short-sz1")
    assert (sd / "json" / paths.SHORT_SCENES_FILE).exists()


def test_build_short_scenes_size_refusal_twice_raises_size_error(tmp_path: Path):
    from video_agent.shorts import paths
    from video_agent.shorts import short_scene_builder as ssb

    job = _long_job(tmp_path)
    calls = {"n": 0}

    def llm_fn(kind, prompt):
        calls["n"] += 1
        # Alternate refusal shapes across the two attempts.
        return _SIZE_REFUSAL_JSON if calls["n"] == 1 else _SIZE_REFUSAL_TEXT

    try:
        ssb.build_short_scenes(
            job, {"short_id": "short-sz2"}, _GOOD_SCRIPT, _cfg(), llm_fn,
        )
    except ssb.ChatGPTSizeRefusalError as exc:
        assert isinstance(exc, ssb.ChatGPTProviderError)
        assert exc.failure_kind == "chatgpt_size_refusal"
        assert exc.snippet
    else:
        raise AssertionError("expected ChatGPTSizeRefusalError")

    assert calls["n"] == 2
    # Refusal must NOT have written an (empty) scenes artifact.
    sd = paths.short_dir(job, "short-sz2")
    assert not (sd / "json" / paths.SHORT_SCENES_FILE).exists()


def test_short_scene_prompt_v6_forbids_size_refusal_and_requires_minified_json():
    from video_agent.shorts import prompts

    p = prompts.short_scene_prompt_v6(
        _cfg(), {"short_id": "short-min"}, {**_GOOD_SCRIPT, "short_format": "checklist"},
    )
    low = p.lower()
    assert "one single line" in low or "single line" in low
    assert "never refuse" in low
    assert "empty scenes array" in low


def test_short_scene_prompt_v6_enforces_verbatim_preserve_split_not_compress():
    """Regression: the builder used to compress script narration to per-scene word
    caps and shorten the CTA to 3-5 words, which scene_qa then hard-blocked for
    source-fidelity (real incident: café short dropped the hook's 2nd half, a whole
    sentence, and the CTA's topic, landing 23.2s < 35s). The prompt must now teach
    VERBATIM preservation + SPLIT (not compression), and must NOT tell the model to
    shorten/compress narration or the CTA to fit caps/timing."""
    from video_agent.shorts import prompts

    p = prompts.short_scene_prompt_v6(
        _cfg(), {"short_id": "short-fid"}, {**_GOOD_SCRIPT, "short_format": "checklist"},
    )
    low = p.lower()
    # Teaches content preservation + splitting (not compression).
    assert "preserve all of the script's content" in low
    assert "split" in low
    assert "clause boundary" in low
    # Faithful paraphrase is allowed; only dropping/changing content fails.
    assert "faithful paraphrase" in low
    # The CTA scene must carry the FULL script CTA incl. its topic (no 3-5 word cap).
    assert "full script cta" in low
    # The old compression permissions are gone.
    assert "you may shorten scene.narration" not in low
    assert "3–5 word cta" not in low
    assert "do not copy long script narration beats verbatim" not in low


def test_build_short_scenes_quota_error_raises_immediately_without_compact_retry(tmp_path: Path):
    """bug 20260705 reopen: a quota error is NOT a size refusal — it must raise a
    typed quota error on the FIRST reply, never burning a compact retry."""
    from video_agent.shorts import short_scene_builder as ssb

    job = _long_job(tmp_path)
    prompts_seen: list[str] = []

    def llm_fn(kind, prompt):
        prompts_seen.append(prompt)
        return '{"error": "You have reached your usage limit for GPT-4.", "scenes": []}'

    try:
        ssb.build_short_scenes(job, {"short_id": "short-q1"}, _GOOD_SCRIPT, _cfg(), llm_fn)
        raise AssertionError("expected ChatGPTQuotaError")
    except ssb.ChatGPTQuotaError as exc:
        assert exc.failure_kind == "chatgpt_quota"
    assert len(prompts_seen) == 1, "quota must not trigger the compact-size retry"
    assert not any("SIZE CORRECTION" in p for p in prompts_seen)


def test_build_short_scenes_policy_refusal_raises_typed_policy_error(tmp_path: Path):
    from video_agent.shorts import short_scene_builder as ssb

    job = _long_job(tmp_path)
    calls = {"n": 0}

    def llm_fn(kind, prompt):
        calls["n"] += 1
        return '{"error": "I can\'t help with that content policy.", "scenes": []}'

    try:
        ssb.build_short_scenes(job, {"short_id": "short-p1"}, _GOOD_SCRIPT, _cfg(), llm_fn)
        raise AssertionError("expected ChatGPTPolicyRefusalError")
    except ssb.ChatGPTPolicyRefusalError as exc:
        assert exc.failure_kind == "chatgpt_policy"
    assert calls["n"] == 1


def test_build_short_scenes_unclassified_error_object_is_labelled_distinctly(tmp_path: Path):
    from video_agent.shorts import short_scene_builder as ssb

    job = _long_job(tmp_path)

    def llm_fn(kind, prompt):
        return '{"error": "cannot comply", "scenes": []}'

    try:
        ssb.build_short_scenes(job, {"short_id": "short-e1"}, _GOOD_SCRIPT, _cfg(), llm_fn)
        raise AssertionError("expected ChatGPTErrorObjectError")
    except ssb.ChatGPTErrorObjectError as exc:
        assert exc.failure_kind == "chatgpt_error_object"
        # must NOT be mislabelled as a size refusal
        assert not isinstance(exc, ssb.ChatGPTSizeRefusalError)


def _drive_build_short_with_scene_reply(tmp_path, short_id, scene_reply):
    """Run the full scene stage via build_short with a fixed scenes reply, return
    (res, scene_calls, calls, short_dir)."""
    from video_agent.shorts import paths, short_builder

    job = _long_job(tmp_path)
    calls: list[str] = []
    scene_calls = {"n": 0}

    def llm_fn(kind, prompt):
        if kind == "script":
            return json.dumps(_GOOD_SCRIPT)
        if kind == "scenes":
            scene_calls["n"] += 1
            return scene_reply
        return "{}"

    def gemini_fn(prompt):
        return json.dumps({"verdict": "PASS", "issues": [], "required_changes": []})

    plan = {"short_id": short_id, "format": "pain_to_tip", "scene_ids": ["scene-09"],
            "music_track": "shorts_sleep_stress", "narration_seed": "x"}
    res = short_builder.build_short(
        job, plan, _cfg(), llm_fn=llm_fn, gemini_fn=gemini_fn, **_stub_io(calls),
    )
    return res, scene_calls, calls, paths.short_dir(job, short_id)


def test_end_to_end_quota_refusal_surfaces_specific_kind_and_spares_creative_budget(tmp_path: Path):
    """bug 20260705 r3: a quota refusal must reach short status as chatgpt_quota
    (operator sees the real cause), never touch the creative scene-QA budget, and
    never render."""
    from video_agent.shorts import paths

    res, scene_calls, calls, sd = _drive_build_short_with_scene_reply(
        tmp_path, "short-e2e-q",
        '{"error": "You have reached your usage limit for GPT-4.", "scenes": []}',
    )
    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "PROVIDER_ERROR"
    assert res.get("failure_kind") == "chatgpt_quota", res.get("failure_kind")
    assert "render" not in calls
    # Creative budget untouched: no scene-QA artifact, so no scene_count=0 feedback.
    assert not (sd / "json" / paths.SHORT_SCENES_QA_FILE).exists()
    import json as _json
    fr = _json.loads((sd / "json" / paths.SHORT_FAILURE_REPORT_FILE).read_text(encoding="utf-8"))
    assert fr["type"] == "chatgpt_quota"


def test_end_to_end_policy_refusal_surfaces_specific_kind(tmp_path: Path):
    from video_agent.shorts import paths

    res, scene_calls, calls, sd = _drive_build_short_with_scene_reply(
        tmp_path, "short-e2e-p",
        '{"error": "I can\'t help with that content policy.", "scenes": []}',
    )
    assert res.get("failure_kind") == "chatgpt_policy", res.get("failure_kind")
    assert res["qa_verdict"] == "PROVIDER_ERROR"
    assert "render" not in calls
    assert not (sd / "json" / paths.SHORT_SCENES_QA_FILE).exists()


def test_end_to_end_auth_refusal_surfaces_specific_kind(tmp_path: Path):
    """bug 20260705 r4: every representative refusal class through the full
    persisted flow — auth."""
    from video_agent.shorts import paths

    res, _sc, calls, sd = _drive_build_short_with_scene_reply(
        tmp_path, "short-e2e-a",
        '{"error": "Your session has expired, please log in to continue.", "scenes": []}',
    )
    assert res.get("failure_kind") == "chatgpt_auth", res.get("failure_kind")
    assert res["qa_verdict"] == "PROVIDER_ERROR"
    assert "render" not in calls
    assert not (sd / "json" / paths.SHORT_SCENES_QA_FILE).exists()
    import json as _json
    fr = _json.loads((sd / "json" / paths.SHORT_FAILURE_REPORT_FILE).read_text(encoding="utf-8"))
    assert fr["type"] == "chatgpt_auth"


def test_end_to_end_unclassified_error_object_surfaces_specific_kind(tmp_path: Path):
    """bug 20260705 r4: unclassified error object -> chatgpt_error_object, NOT
    a size refusal, through the full flow."""
    from video_agent.shorts import paths

    res, _sc, calls, sd = _drive_build_short_with_scene_reply(
        tmp_path, "short-e2e-e", '{"error": "cannot comply", "scenes": []}',
    )
    assert res.get("failure_kind") == "chatgpt_error_object", res.get("failure_kind")
    assert res["qa_verdict"] == "PROVIDER_ERROR"
    assert not (sd / "json" / paths.SHORT_SCENES_QA_FILE).exists()
    import json as _json
    fr = _json.loads((sd / "json" / paths.SHORT_FAILURE_REPORT_FILE).read_text(encoding="utf-8"))
    assert fr["type"] == "chatgpt_error_object"


def test_end_to_end_size_refusal_twice_surfaces_size_kind_through_full_flow(tmp_path: Path):
    """bug 20260705 r4: a size refusal that persists past the compact retry
    reaches short status as chatgpt_size_refusal, still sparing the creative
    budget and never rendering."""
    from video_agent.shorts import paths

    res, scene_calls, calls, sd = _drive_build_short_with_scene_reply(
        tmp_path, "short-e2e-s", _SIZE_REFUSAL_JSON,
    )
    assert res.get("failure_kind") == "chatgpt_size_refusal", res.get("failure_kind")
    assert res["qa_verdict"] == "PROVIDER_ERROR"
    assert "render" not in calls
    assert not (sd / "json" / paths.SHORT_SCENES_QA_FILE).exists()
    # A size refusal gets EXACTLY its one in-place compact retry (2 scene calls).
    assert scene_calls["n"] == 2


def test_every_refusal_class_persists_kind_and_spends_zero_creative_budget(tmp_path: Path):
    """bug 20260705 r5: for EVERY refusal class, read the PERSISTED short_status.json
    off disk (full-flow contract) and assert the creative scene-QA budget counters
    are DIRECTLY zero — not merely that a QA file is absent."""
    from video_agent.shorts import paths
    from video_agent.shorts.manifest import read_short_status

    cases = {
        "short-r5-size": (_SIZE_REFUSAL_JSON, "chatgpt_size_refusal"),
        "short-r5-quota": ('{"error": "You have reached your usage limit.", "scenes": []}', "chatgpt_quota"),
        "short-r5-auth": ('{"error": "Your session has expired, log in.", "scenes": []}', "chatgpt_auth"),
        "short-r5-policy": ('{"error": "I can\'t help with that content policy.", "scenes": []}', "chatgpt_policy"),
        "short-r5-err": ('{"error": "cannot comply", "scenes": []}', "chatgpt_error_object"),
    }
    for short_id, (reply, kind) in cases.items():
        case_root = tmp_path / short_id
        case_root.mkdir(parents=True, exist_ok=True)
        job = _long_job(case_root)  # isolated job per case
        calls: list[str] = []

        def llm_fn(k, prompt, _reply=reply):
            if k == "script":
                return json.dumps(_GOOD_SCRIPT)
            if k == "scenes":
                return _reply
            return "{}"

        from video_agent.shorts import short_builder
        plan = {"short_id": short_id, "format": "pain_to_tip", "scene_ids": ["scene-09"],
                "music_track": "shorts_sleep_stress", "narration_seed": "x"}
        short_builder.build_short(
            job, plan, _cfg(),
            llm_fn=llm_fn,
            gemini_fn=lambda p: json.dumps({"verdict": "PASS", "issues": [], "required_changes": []}),
            **_stub_io(calls),
        )

        # PERSISTED full-flow contract: read short_status.json from disk.
        persisted = read_short_status(job, short_id)
        assert persisted["status"] == "needs_review", (short_id, persisted.get("status"))
        assert persisted["qa_verdict"] == "PROVIDER_ERROR", (short_id, persisted.get("qa_verdict"))
        assert persisted["failure_kind"] == kind, (short_id, persisted.get("failure_kind"))

        # DIRECT creative-budget assertion: a provider refusal must consume ZERO
        # creative REGENERATION budget. qa_scenes_attempts is the raw
        # scene-generation attempt counter (legitimately >=1 — one attempt was
        # made), so the budget-consumed metrics are the RETRY counters:
        # regeneration_attempts and the structural/product creative-retry sub-counts.
        for counter in ("regeneration_attempts", "qa_scenes_structural_attempts",
                        "qa_scenes_product_attempts"):
            assert persisted.get(counter, 0) == 0, (short_id, counter, persisted.get(counter))

        # And the persisted scenes artifact never became a valid empty-scenes doc
        # that could have entered scene QA.
        assert not paths.short_scenes_qa_path(job, short_id).exists() if hasattr(
            paths, "short_scenes_qa_path"
        ) else not (paths.short_dir(job, short_id) / "json" / paths.SHORT_SCENES_QA_FILE).exists()
        assert "render" not in calls


# bug 20260705 r6: ONE parameterized contract asserting BOTH the returned dict
# AND the persisted short_status.json for EVERY refusal class.
# (label, reply, kind, exact_scene_calls). Only a genuine SIZE refusal earns the
# in-place compact retry (2 scene LLM calls); quota/auth/policy/error_object raise
# their typed error on the first reply (exactly 1). Verified by probe, not assumed.
_REFUSAL_CONTRACT_CASES = [
    ("size", _SIZE_REFUSAL_JSON, "chatgpt_size_refusal", 2),
    ("quota", '{"error": "You have reached your usage limit.", "scenes": []}', "chatgpt_quota", 1),
    ("auth", '{"error": "Your session has expired, log in.", "scenes": []}', "chatgpt_auth", 1),
    ("policy", '{"error": "I can\'t help with that content policy.", "scenes": []}', "chatgpt_policy", 1),
    ("error_object", '{"error": "cannot comply", "scenes": []}', "chatgpt_error_object", 1),
]


@pytest.mark.parametrize(
    "label,reply,kind,expected_calls", _REFUSAL_CONTRACT_CASES,
    ids=[c[0] for c in _REFUSAL_CONTRACT_CASES],
)
def test_refusal_operator_contract_returned_and_persisted(tmp_path: Path, label, reply, kind, expected_calls):
    """Full operator contract per class: the RETURNED dict and the PERSISTED
    short_status.json + failure report must agree on status/qa_verdict/failure_kind,
    the raw provider detail is preserved, the exact scene-call count is honoured
    (size retries once, others do not), no creative regeneration budget is spent,
    no scene-QA artifact exists, and nothing renders."""
    import json as _json

    from video_agent.shorts import paths

    res, scene_calls, calls, sd = _drive_build_short_with_scene_reply(tmp_path, f"short-c-{label}", reply)

    # GATE 1 — exact scene LLM call count: only size gets the compact retry.
    assert scene_calls["n"] == expected_calls, (label, scene_calls["n"], expected_calls)

    # RETURNED contract.
    assert res["status"] == "needs_review"
    assert res["qa_verdict"] == "PROVIDER_ERROR"
    assert res.get("failure_kind") == kind

    # PERSISTED contract (read short_status.json off disk) agrees with the returned dict.
    persisted = _json.loads((sd / paths.SHORT_STATUS_FILE).read_text(encoding="utf-8"))
    assert persisted["status"] == res["status"]
    assert persisted["qa_verdict"] == res["qa_verdict"]
    assert persisted["failure_kind"] == res.get("failure_kind") == kind

    # Persisted failure report: type AND GATE 2 — the raw provider detail/snippet
    # is preserved (not flattened to just the kind), so an operator sees what the
    # model actually returned.
    fr = _json.loads((sd / "json" / paths.SHORT_FAILURE_REPORT_FILE).read_text(encoding="utf-8"))
    assert fr["type"] == kind
    assert fr.get("detail"), (label, "missing provider detail")
    assert reply.strip()[:30] in fr.get("error_snippet", ""), (label, fr.get("error_snippet"))

    # DIRECT creative-budget: zero regeneration/structural/product retries.
    for counter in ("regeneration_attempts", "qa_scenes_structural_attempts", "qa_scenes_product_attempts"):
        assert persisted.get(counter, 0) == 0

    # No scene-QA artifact, no render.
    assert not (sd / "json" / paths.SHORT_SCENES_QA_FILE).exists()
    assert "render" not in calls
