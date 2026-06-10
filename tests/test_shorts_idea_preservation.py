from __future__ import annotations

import json
from pathlib import Path

from video_agent.shorts import prompts
from video_agent.shorts.validate_scenes import SceneValidationIssue


def _cfg() -> dict:
    return {
        "channel": {"id": "vida-plena-45"},
        "shorts": {
            "duration": {"min_sec": 20, "target_max_sec": 60},
            "funnel": {"cta_max_words": 8},
        },
    }


def _five_error_plan() -> dict:
    return {
        "short_id": "short-01",
        "title": "5 errores que hacen que el pan te desordene más",
        "hook_text": "Cinco errores con el pan",
        "format": "mistake_list",
        "viewer_pain": "el pan desordena la cena por gestos invisibles",
        "practical_payoff": "poner una porción visible en un plato pequeño",
        "key_points": [
            {"point": "comerlo de pie", "source_scene_ids": ["source_scene_01"]},
            {"point": "sumarlo a arroz o pasta", "source_scene_ids": ["source_scene_02"]},
            {"point": "dejar la barra en la mesa", "source_scene_ids": ["source_scene_03"]},
            {"point": "cortar otro trozo por cansancio", "source_scene_ids": ["source_scene_04"]},
            {"point": "cenar a bocados de pan y queso", "source_scene_ids": ["source_scene_05"]},
        ],
        "narration_seed": "Uno de pie. Dos con arroz. Tres barra en mesa. Cuatro por cansancio. Cinco a bocados.",
    }


def _five_error_script() -> dict:
    return {
        "short_id": "short-01",
        "short_format": "mistake_list",
        "target_duration_sec": 35,
        "hook": "No es el pan: son cinco gestos.",
        "narration": (
            "No es el pan: son cinco gestos. Uno: comerlo de pie. "
            "Dos: sumarlo a arroz o pasta. Tres: dejar la barra en la mesa. "
            "Cuatro: cortar otro trozo por cansancio. Cinco: cenar a bocados. "
            "Mejor: pon una porción visible en un plato pequeño."
        ),
        "cta": "Guárdalo para la cena.",
        "idea_contract": {
            "must_preserve_count": True,
            "count_mode": "exact",
            "original_count": 5,
            "final_count": 5,
            "count_label": "errores",
            "adaptation_allowed": False,
            "adaptation_used": False,
        },
        "idea_items": [
            {"item_id": 1, "label": "comerlo de pie", "source_support": ["key_point_1"], "required": True},
            {"item_id": 2, "label": "sumarlo a arroz o pasta", "source_support": ["key_point_2"], "required": True},
            {"item_id": 3, "label": "dejar la barra en la mesa", "source_support": ["key_point_3"], "required": True},
            {"item_id": 4, "label": "cortar otro trozo por cansancio", "source_support": ["key_point_4"], "required": True},
            {"item_id": 5, "label": "cenar a bocados", "source_support": ["key_point_5"], "required": True},
        ],
    }


def _scene(scene_id: str, covers_items, *, layout: str = "short_tip", duration: float = 3.0, payload_items=None) -> dict:
    return {
        "id": scene_id,
        "duration_sec": duration,
        "layout": layout,
        "narration": "Micro punto.",
        "on_screen_text": "MICRO PUNTO",
        "caption": "Detalle claro.",
        "visual_prompt": "Spain kitchen, vertical",
        "layout_payload": {"items": payload_items or []},
        "covers_items": covers_items,
        # The strict mapping validator requires source_scene_ids on any scene
        # that covers idea items (see validate_scene_structure).
        "source_scene_ids": [f"source_scene_{i:02d}" for i in (covers_items or [])],
    }


def test_extracts_numbered_promise_and_avoids_bare_number_false_positives():
    from video_agent.shorts.idea_preservation import derive_idea_contract

    contract = derive_idea_contract(_five_error_plan())

    assert contract["must_preserve_count"] is True
    assert contract["count_mode"] == "exact"
    assert contract["original_count"] == 5
    assert contract["count_label"] == "errores"
    assert contract["adaptation_allowed"] is False

    for title in ("Pan en 1 plato", "Fibra por 100 g", "A los 45 cambia esto", "1/2 plato saludable"):
        unlocked = derive_idea_contract({"title": title, "format": "pain_to_tip"})
        assert unlocked["must_preserve_count"] is False
        assert unlocked.get("original_count") is None


def test_range_count_contract_uses_min_and_max():
    from video_agent.shorts.idea_preservation import derive_idea_contract

    contract = derive_idea_contract({"title": "3-4 errores al cenar", "format": "mistake_list"})

    assert contract["must_preserve_count"] is True
    assert contract["count_mode"] == "range"
    assert contract["idea_count_min"] == 3
    assert contract["idea_count_max"] == 4


def test_source_support_references_must_exist():
    from video_agent.shorts.idea_preservation import validate_script_idea_contract

    script = _five_error_script()
    script["idea_items"][0]["source_support"] = ["key_point_99"]

    issues = validate_script_idea_contract(script, original_idea=_five_error_plan())

    assert any(issue.type == "source_support" and issue.severity == "blocking_error" for issue in issues)


def test_exact_count_script_cannot_silently_reduce_five_to_two():
    from video_agent.shorts.idea_preservation import validate_script_idea_contract

    script = _five_error_script()
    script["idea_contract"]["final_count"] = 2
    script["idea_items"] = script["idea_items"][:2]

    issues = validate_script_idea_contract(script, original_idea=_five_error_plan())

    assert any(issue.type == "idea_fidelity" for issue in issues)


def test_normalizes_covers_items_without_crashing():
    from video_agent.shorts.idea_preservation import normalize_covers_items

    assert normalize_covers_items("1, 2") == ([1, 2], [])
    normalized, warnings = normalize_covers_items(["1", "bad", 3, 3])

    assert normalized == [1, 3]
    assert warnings


def test_scene_coverage_validator_fails_missing_item_and_unknown_item():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    scenes_doc = {"scenes": [_scene("s01", [1]), _scene("s02", [2, 3]), _scene("s03", [4, 99])]}

    issues = validate_scene_idea_coverage(scenes_doc, _five_error_script())

    assert any(issue.type == "missing_item_coverage" and "5" in issue.detail for issue in issues)
    assert any(issue.type == "unknown_item_coverage" and "99" in issue.detail for issue in issues)


def test_visual_only_item_with_short_dense_scene_is_unreadable():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    scenes_doc = {
        "scenes": [
            _scene("s01", [1], duration=2.0),
            _scene("s02", [2], duration=2.0),
            _scene("s03", [3], duration=2.0),
            _scene("s04", [4], duration=2.0),
            _scene("s05", [5], duration=1.0, layout="graphic_checklist", payload_items=["Uno", "Dos", "Tres", "Cuatro", "Cinco"]),
        ]
    }

    issues = validate_scene_idea_coverage(scenes_doc, _five_error_script())

    assert any(issue.type == "visual_only_unreadable" and issue.scene_id == "s05" for issue in issues)


def test_slideshow_risk_is_measurable():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    scenes_doc = {
        "scenes": [
            _scene("s01", [1], layout="short_hook"),
            _scene("s02", [2], layout="graphic_checklist", payload_items=["A", "B", "C"]),
            _scene("s03", [3], layout="graphic_checklist", payload_items=["A", "B", "C"]),
            _scene("s04", [4], layout="short_checklist", payload_items=["A", "B", "C"]),
            _scene("s05", [5], layout="short_checklist", payload_items=["A", "B", "C", "D"]),
        ]
    }

    issues = validate_scene_idea_coverage(scenes_doc, _five_error_script())

    assert any(issue.type == "slideshow_risk" for issue in issues)


def test_checklist_point_cap_respects_locked_exact_count():
    from video_agent.shorts.validate_scenes import validate_script_checklist_point_cap

    issue = validate_script_checklist_point_cap(_five_error_script())

    assert issue is None or issue.severity == "warning"
    if issue:
        assert "top 3-4" not in str(issue.repair_hint)


def test_scene_duration_hard_cap_allows_up_to_sixty_seconds():
    from video_agent.shorts.validate_scenes import validate_scene_structure

    scenes = [
        _scene("s01", [], layout="short_hook", duration=3.0),
        _scene("s02", [1], duration=5.0),
        _scene("s03", [1], duration=5.0),
        _scene("s04", [2], duration=5.0),
        _scene("s05", [2], duration=5.0),
        _scene("s06", [3], duration=5.0),
        _scene("s07", [3], duration=5.0),
        _scene("s08", [4], duration=5.0),
        _scene("s09", [4], duration=5.0),
        _scene("s10", [5], duration=5.0),
        _scene("s11", [5], duration=5.0),
        _scene("s12", [], layout="short_cta", duration=2.5),
    ]
    scenes_doc = {"scenes": scenes, "total_duration_sec": 55.5}
    script = _five_error_script()

    issues = validate_scene_structure(scenes, scenes_doc=scenes_doc, script=script)

    assert not any(issue.type == "duration_range" for issue in issues)
    assert not any(issue.type == "scene_count" for issue in issues)


def test_scene_duration_above_sixty_seconds_still_fails():
    from video_agent.shorts.validate_scenes import validate_scene_structure

    scenes = [
        _scene("s01", [], layout="short_hook", duration=3.0),
        *[_scene(f"s{i:02d}", [((i - 2) % 5) + 1], duration=5.5) for i in range(2, 13)],
        _scene("s13", [], layout="short_cta", duration=3.0),
    ]
    scenes_doc = {"scenes": scenes, "total_duration_sec": 66.5}
    script = _five_error_script()

    issues = validate_scene_structure(scenes, scenes_doc=scenes_doc, script=script)

    assert any(issue.type == "duration_range" and "20-60" in issue.detail for issue in issues)


def test_rule_script_qa_routes_warning_severity_without_failing(tmp_path: Path, monkeypatch):
    from video_agent.shorts import paths, qa, validate_scenes

    long_job = tmp_path / "job"
    sd = paths.short_json_dir(long_job, "short-01")
    sd.mkdir(parents=True)
    (sd / paths.SHORT_SCRIPT_FILE).write_text(json.dumps(_five_error_script()), encoding="utf-8")
    (sd / paths.SHORT_SOURCE_MAP_FILE).write_text(json.dumps({"used_source_scenes": ["source_scene_01"]}), encoding="utf-8")

    monkeypatch.setattr(
        validate_scenes,
        "validate_script_word_budget",
        lambda script: SceneValidationIssue("script_word_budget", None, "warning", "old 38s preference"),
    )
    monkeypatch.setattr(validate_scenes, "validate_script_checklist_point_cap", lambda script: None)

    result = qa.run_short_script_qa(long_job, "short-01", _cfg(), music_track="track")

    assert result["verdict"] == "PASS"
    assert "old 38s preference" in result["warnings"]
    assert "script_word_budget" not in result["issues"]


def test_prompt_for_locked_five_item_idea_has_no_unconditional_four_max_contradiction():
    p = prompts.short_script_prompt(_cfg(), _five_error_plan(), {}).lower()

    assert "idea preservation contract" in p
    assert "do not reduce 5 errores to 3 or 4" in p
    assert "4 spoken checklist points = maximum" not in p
    assert "5 spoken checklist points = too dense" not in p


def test_gemini_script_qa_prompt_sees_original_idea_and_has_no_unconditional_more_than_four_fail():
    p = prompts.gemini_script_qa_prompt(
        _cfg(),
        _five_error_script(),
        {"used_source_scenes": ["source_scene_01"]},
        original_idea=_five_error_plan(),
    ).lower()

    assert "original idea" in p
    assert "idea_fidelity" in p
    assert "more than 4 checklist points" not in p
    assert "do not narrate five long steps" not in p


def test_scene_prompt_requires_covers_items():
    p = prompts.short_scene_prompt_v6(_cfg(), _five_error_plan(), _five_error_script()).lower()

    assert "covers_items" in p
    assert "do not drop items silently" in p


def test_seo_prompt_warns_against_count_mismatch():
    p = prompts.short_seo_prompt(_cfg(), _five_error_plan(), _five_error_script()).lower()

    assert "seo idea fidelity" in p
    assert "never publish title" in p
    assert "5 errores" in p


def test_short_seo_builder_rejects_count_mismatch(tmp_path: Path):
    from video_agent.shorts import short_seo_builder

    job = tmp_path / "long-job"
    job.mkdir()

    def llm_fn(kind, prompt):
        return json.dumps({
            "title": "2 errores con el pan después de los 45",
            "description": "Dos errores con el pan. #alimentacionsaludable #pan #shorts",
            "hashtags": ["#alimentacionsaludable", "#pan", "#shorts"],
            "pinned_comment": "¿Te pasa?",
        })

    try:
        short_seo_builder.build_short_seo(
            job,
            "short-01",
            _five_error_plan(),
            _five_error_script(),
            _cfg(),
            llm_fn,
        )
    except ValueError as exc:
        assert "SEO idea fidelity validation failed" in str(exc)
    else:
        raise AssertionError("Expected SEO count mismatch to fail")


def test_builder_escalation_feedback_preserves_locked_count():
    from video_agent.shorts.short_builder import build_script_compression_feedback

    feedback = build_script_compression_feedback(_five_error_script())

    assert "Keep all 5 promised errores" in feedback
    assert "Keep 3 spoken checklist points max" not in feedback
    assert "Busca harina integral al principio" not in feedback
    assert "La etiqueta ayuda a elegir" not in feedback


def test_v19_derives_locked_count_from_key_points_and_narration_seed():
    from video_agent.shorts.idea_preservation import derive_idea_contract, derive_idea_items

    plan = {
        "format": "checklist",
        "title": "Cómo saber si un pan integral lo es de verdad",
        "key_points": [
            "El color marrón no garantiza que el pan sea integral.",
            "La primera línea debe empezar por harina integral.",
            "Comparar la fibra ayuda a elegir.",
            "Conviene revisar azúcar, jarabes e ingredientes dulces.",
            "Un pan de cereales puede llevar harina refinada.",
        ],
        "narration_seed": "Uno... Dos... Tres... Cuatro... Cinco...",
    }

    contract = derive_idea_contract(plan)
    items = derive_idea_items(plan, contract)

    assert contract["must_preserve_count"] is True
    assert contract["count_mode"] == "exact"
    assert contract["original_count"] == 5
    assert contract["final_count"] == 5
    assert contract["count_source"] == "key_points+narration_seed"
    assert len(items) == 5
    assert items[4]["source_support"] == ["key_point_5"]


def test_v19_spoken_item_coverage_suppresses_visual_only_unreadable():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    script = _five_error_script()
    script["idea_items"][0]["label"] = "rústico o multicereal no significa integral"
    scenes_doc = {
        "scenes": [
            {
                "id": "s03",
                "layout": "short_myth",
                "duration_sec": 1.0,
                "narration": "Rústico o multicereal no significa integral.",
                "on_screen_text": "",
                "caption": "",
                "layout_payload": {"items": ["Rústico", "Multicereal", "Integral", "Etiqueta", "Harina"]},
                "covers_items": [1],
            },
            _scene("s04", [2], duration=2.0),
            _scene("s05", [3], duration=2.0),
            _scene("s06", [4], duration=2.0),
            _scene("s07", [5], duration=2.0),
        ]
    }

    issues = validate_scene_idea_coverage(scenes_doc, script)

    assert not any(issue.type == "visual_only_unreadable" and issue.scene_id == "s03" for issue in issues)


def test_remaining_v19_picarlo_de_pie_spoken_coverage_suppresses_visual_only_unreadable():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    script = _five_error_script()
    script["idea_items"][0]["label"] = "tomarlo solo de pie con prisa"
    scenes_doc = {
        "scenes": [
            {
                "id": "s02",
                "layout": "short_checklist",
                "duration_sec": 1.0,
                "narration": "Uno: picarlo de pie, sin plato.",
                "on_screen_text": "",
                "caption": "",
                "visual_prompt": "Realistic Spanish kitchen footage, a person eating bread standing without a plate",
                "layout_payload": {"items": ["De pie", "Sin plato", "Con prisa", "Picoteo", "Pan"]},
                "covers_items": [1],
            },
            _scene("s03", [2], duration=2.0),
            _scene("s04", [3], duration=2.0),
            _scene("s05", [4], duration=2.0),
            _scene("s06", [5], duration=2.0),
        ]
    }

    issues = validate_scene_idea_coverage(scenes_doc, script)

    assert not any(issue.type == "visual_only_unreadable" and issue.scene_id == "s02" for issue in issues)


def test_v19_item_coverage_field_marks_spoken_mode():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    script = _five_error_script()
    scene = _scene("s03", [1], duration=1.0, payload_items=["Uno", "Dos", "Tres", "Cuatro", "Cinco"])
    scene["narration"] = ""
    scene["item_coverage"] = [{"item_id": 1, "mode": "spoken", "evidence": "scene.narration"}]
    scenes_doc = {"scenes": [scene, _scene("s04", [2]), _scene("s05", [3]), _scene("s06", [4]), _scene("s07", [5])]}

    issues = validate_scene_idea_coverage(scenes_doc, script)

    assert not any(issue.type == "visual_only_unreadable" and issue.scene_id == "s03" for issue in issues)


def test_v19_footage_led_two_graphics_and_one_short_checklist_is_not_repairable_slideshow():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    scenes_doc = {
        "scenes": [
            _scene("s01", [], layout="short_hook"),
            _scene("s02", [1], layout="short_myth"),
            _scene("s03", [2], layout="graphic_label_callout", payload_items=["Harina integral"]),
            _scene("s04", [3], layout="short_tip"),
            _scene("s05", [4], layout="graphic_comparison", payload_items=["Fibra", "Azúcar"]),
            _scene("s06", [5], layout="short_checklist", payload_items=["Etiqueta", "Fibra", "Azúcar"]),
            _scene("s07", [], layout="short_cta"),
        ]
    }

    issues = validate_scene_idea_coverage(scenes_doc, _five_error_script())

    assert not any(issue.type == "slideshow_risk" and issue.severity == "repairable_error" for issue in issues)


def test_remaining_v19_footage_led_two_short_checklists_are_not_repairable_slideshow():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    scenes_doc = {
        "scenes": [
            _scene("s01", [], layout="short_hook"),
            {
                **_scene("s02", [1], layout="short_checklist", payload_items=["De pie"]),
                "visual_prompt": "Realistic Spanish kitchen footage, person standing by counter with bread",
            },
            _scene("s03", [2], layout="short_tip"),
            {
                **_scene("s04", [3], layout="short_checklist", payload_items=["Barra en mesa"]),
                "visual_prompt": "Realistic Spain dining table footage with bread on a plate",
            },
            _scene("s05", [4], layout="short_tip"),
            _scene("s06", [5], layout="short_tip"),
            _scene("s07", [], layout="short_cta"),
        ]
    }

    issues = validate_scene_idea_coverage(scenes_doc, _five_error_script())

    assert not any(issue.type == "slideshow_risk" and issue.severity == "repairable_error" for issue in issues)


def test_v19_dense_slideshow_still_repairable():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    scenes_doc = {
        "scenes": [
            _scene("s01", [1], layout="graphic_checklist", payload_items=["A", "B", "C", "D", "E"]),
            _scene("s02", [2], layout="graphic_checklist", payload_items=["A", "B", "C"]),
            _scene("s03", [3], layout="short_checklist", payload_items=["A", "B", "C", "D"]),
            _scene("s04", [4], layout="short_checklist", payload_items=["A", "B", "C"]),
            _scene("s05", [5], layout="short_checklist", payload_items=["A", "B", "C"]),
        ]
    }

    issues = validate_scene_idea_coverage(scenes_doc, _five_error_script())

    assert any(issue.type == "slideshow_risk" and issue.severity == "repairable_error" for issue in issues)


def test_remaining_v19_four_short_checklists_and_six_checklist_like_is_repairable():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    scenes_doc = {
        "scenes": [
            _scene("s01", [1], layout="short_checklist", payload_items=["A", "B", "C", "D"]),
            _scene("s02", [2], layout="short_checklist", payload_items=["A", "B", "C", "D"]),
            _scene("s03", [3], layout="short_checklist", payload_items=["A", "B", "C", "D"]),
            _scene("s04", [4], layout="short_checklist", payload_items=["A", "B", "C", "D"]),
            _scene("s05", [5], layout="graphic_step_list", payload_items=["A", "B", "C"]),
            _scene("s06", [], layout="graphic_checklist", payload_items=["A", "B", "C"]),
        ]
    }

    issues = validate_scene_idea_coverage(scenes_doc, _five_error_script())

    assert any(issue.type == "slideshow_risk" and issue.severity == "repairable_error" for issue in issues)


def test_remaining_v19_unknown_item_coverage_repair_hint_mentions_payoff_cta_empty_items():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    scenes_doc = {"scenes": [_scene("s08", [6], layout="short_cta")]}

    issues = validate_scene_idea_coverage(scenes_doc, _five_error_script())
    issue = next(issue for issue in issues if issue.type == "unknown_item_coverage")

    assert issue.severity == "repairable_error"
    assert "covers_items=[]" in str(issue.repair_hint)
    assert "payoff/CTA" in str(issue.repair_hint)


def _near_valid_five_error_scene_plan(cta_duration: float = 2.8) -> tuple[dict, dict]:
    script = _five_error_script()
    script["cta"] = "Guárdalo."
    scenes = [
        {
            **_scene("s01", [], layout="short_hook", duration=2.5),
            "narration": "Cinco gestos con pan.",
            "visual_prompt": "Realistic Spanish kitchen footage, warm morning light",
        },
        {
            **_scene("s02", [1], layout="short_pain", duration=3.5),
            "narration": "Uno: picarlo de pie, sin plato.",
            "visual_prompt": "Realistic Spanish kitchen footage, person eating bread standing without a plate",
        },
        {
            **_scene("s03", [2], layout="short_pain", duration=3.5),
            "narration": "Dos: sumarlo encima de arroz o pasta.",
            "visual_prompt": "Realistic Spain dining table footage, plate with bread next to rice",
        },
        {
            **_scene("s04", [3], layout="short_pain", duration=3.5),
            "narration": "Tres: dejar la barra a mano.",
            "visual_prompt": "Realistic Spanish table footage, bread bar left within reach",
        },
        {
            **_scene("s05", [4], layout="short_pain", duration=3.5),
            "narration": "Cuatro: cortar otro trozo por cansancio.",
            "visual_prompt": "Realistic kitchen footage, tired adult cutting another bread slice",
        },
        {
            **_scene("s06", [5], layout="short_pain", duration=3.5),
            "narration": "Cinco: cenar a bocados.",
            "visual_prompt": "Realistic quiet dinner footage, small bread and cheese bites on plate",
        },
        {
            **_scene("s07", [], layout="graphic_checklist", duration=4.5),
            "narration": "Mejor: pon una porción visible.",
            "visual_prompt": "Realistic Spanish kitchen footage, small plate with a visible bread portion",
            "layout_payload": {
                "title": "MEJOR ASÍ",
                "items": ["Porción visible", "Plato pequeño", "Comida completa"],
            },
        },
        {
            **_scene("s08", [], layout="short_cta", duration=cta_duration),
            "narration": "Guárdalo.",
            "visual_prompt": "Realistic warm close-up, person looking at camera in kitchen",
        },
    ]
    scenes_doc = {"scenes": scenes, "total_duration_sec": round(sum(s["duration_sec"] for s in scenes), 1)}
    return script, scenes_doc


def test_latest_loop_near_valid_five_error_scene_plan_passes_deterministic_validation():
    from video_agent.shorts.validate_scenes import validate_scene_structure

    script, scenes_doc = _near_valid_five_error_scene_plan(cta_duration=2.8)

    issues = validate_scene_structure(scenes_doc["scenes"], scenes_doc=scenes_doc, script=script)

    assert not any(issue.severity in {"blocking_error", "repairable_error"} for issue in issues)


def test_latest_loop_one_payoff_short_checklist_two_checklist_like_not_repairable_slideshow():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    script, scenes_doc = _near_valid_five_error_scene_plan(cta_duration=2.8)

    issues = validate_scene_idea_coverage(scenes_doc, script)

    assert not any(issue.type == "slideshow_risk" and issue.severity == "repairable_error" for issue in issues)


def test_latest_loop_dejar_barra_spoken_coverage_suppresses_visual_only_unreadable():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    script = _five_error_script()
    script["idea_items"][2]["label"] = "dejar la barra en la mesa y repetir por inercia"
    scenes_doc = {
        "scenes": [
            _scene("s02", [1], duration=2.0),
            _scene("s03", [2], duration=2.0),
            {
                **_scene("s04", [3], layout="short_tip", duration=1.0, payload_items=["Barra", "Mesa", "Mano", "Repetir", "Inercia"]),
                "narration": "Tres: dejar la barra a mano.",
                "visual_prompt": "Realistic Spanish dining table footage, bread bar within reach",
            },
            _scene("s05", [4], duration=2.0),
            _scene("s06", [5], duration=2.0),
        ]
    }

    issues = validate_scene_idea_coverage(scenes_doc, script)

    assert not any(issue.type == "visual_only_unreadable" and issue.scene_id == "s04" for issue in issues)


def test_latest_loop_short_cta_duration_is_mechanically_repaired():
    from video_agent.shorts.validate_scenes import repair_scene_duration_if_possible

    scene = {
        "id": "s08",
        "layout": "short_cta",
        "duration_sec": 3.9,
        "narration": "Guárdalo.",
    }

    result = repair_scene_duration_if_possible(scene)

    assert result == "auto_shortened_cta"
    assert scene["duration_sec"] <= 2.8


def test_latest_loop_cta_only_repair_plan_is_mechanical_and_specific():
    from video_agent.shorts.validate_scenes import SceneValidationIssue, build_scene_repair_plan

    scenes = [
        {"id": "s08", "layout": "short_cta", "duration_sec": 3.9, "narration": "Guárdalo."}
    ]
    issues = [
        SceneValidationIssue(
            type="duration_cap",
            scene_id="s08",
            severity="repairable_error",
            detail="Scene s08 (short_cta) duration 3.9s exceeds hard max 2.8s.",
        )
    ]

    plan = build_scene_repair_plan(scenes, issues, script=_five_error_script())
    instructions = "\n".join(plan["instructions"])

    assert "Set s08 duration_sec to 2.6-2.8" in instructions
    assert "Keep s02-s06 as realistic short_tip/short_pain scenes, not short_checklist." not in instructions
    assert "Convert one checklist-like scene" not in instructions


def test_v19_scene_validation_fallback_allows_only_soft_issues():
    from video_agent.shorts.short_builder import should_fallback_to_gemini_scene_qa

    issues = [
        SceneValidationIssue("slideshow_risk", None, "warning", "Footage-led candidate has mild list density."),
        SceneValidationIssue("visual_only_unreadable", "s03", "warning", "Suppressed false positive; spoken coverage exists."),
    ]

    assert should_fallback_to_gemini_scene_qa(issues) is True


def test_v19_slideshow_repair_plan_keeps_item_scenes_footage_led():
    from video_agent.shorts.validate_scenes import build_scene_repair_plan

    issue = SceneValidationIssue(
        "slideshow_risk",
        None,
        "repairable_error",
        "Short is too text/list heavy: short_checklist=4, checklist_like=5.",
        "Reduce the exact dense checklist/graphic scene carrying too many text chunks.",
    )

    script, scenes_doc = _near_valid_five_error_scene_plan()
    plan = build_scene_repair_plan(scenes_doc["scenes"], [issue], script=script)
    instructions = "\n".join(plan["instructions"])

    assert "Keep s02-s06 as realistic short_tip/short_pain scenes, not short_checklist." in instructions
    assert "Do not convert good footage-led item scenes into short_checklist scenes." in instructions
    assert "Convert one checklist-like scene" not in instructions


def test_v19_scene_validation_fallback_blocks_hard_issues():
    from video_agent.shorts.short_builder import should_fallback_to_gemini_scene_qa

    hard_types = [
        "missing_item_coverage",
        "layout",
        "payload",
        "audio_fit",
        "source_support",
        "safety",
    ]
    for issue_type in hard_types:
        issue = SceneValidationIssue(issue_type, None, "repairable_error", "hard issue")
        assert should_fallback_to_gemini_scene_qa([issue]) is False


# -- goal: fix latest scene_validation loop -------------------------------

def test_goal_derives_fifth_item_from_narration_seed_when_keypoints_short():
    from video_agent.shorts.idea_preservation import derive_idea_items

    plan = {
        "format": "mistake_list",
        "key_points": [
            {"point": "comerlo de pie", "source_scene_ids": ["source_scene_10"]},
            {"point": "sumarlo a arroz o pasta", "source_scene_ids": ["source_scene_20"]},
            {"point": "dejar la barra en la mesa", "source_scene_ids": ["source_scene_30"]},
            {"point": "cortar otro trozo por cansancio", "source_scene_ids": ["source_scene_35"]},
        ],
        "narration_seed": (
            "Uno: comerlo de pie. Dos: sumarlo a arroz. Tres: dejar la barra. "
            "Cuatro: otro trozo. Y quinto: cena de bocados de pan."
        ),
    }
    contract = {"must_preserve_count": True, "count_mode": "exact", "original_count": 5}

    items = derive_idea_items(plan, contract)

    assert len(items) == 5
    fifth = [it for it in items if it["item_id"] == 5][0]
    assert "cena de bocados" in fifth["label"].lower()
    assert fifth["source_support"]  # non-empty + verifiable reference


def test_goal_overlong_short_checklist_payoff_is_clamped():
    from video_agent.shorts.validate_scenes import repair_scene_duration_if_possible

    scene = {
        "id": "s07",
        "layout": "short_checklist",
        "duration_sec": 7.4,
        "narration": "Mejora: plato pequeño y compañía.",
        "layout_payload": {"items": ["Plato pequeño", "Compañía", "Porción visible"]},
    }

    result = repair_scene_duration_if_possible(scene)

    assert scene["duration_sec"] <= 5.0
    assert result == "auto_shortened"


def test_goal_ordinal_narration_suppresses_visual_only_item_1():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    script = _five_error_script()
    dense = {
        **_scene("s02", [1], layout="short_checklist", duration=1.0,
                 payload_items=["a", "b", "c", "d", "e"]),
        "narration": "Uno: picoteo sin darte cuenta.",
    }

    issues = validate_scene_idea_coverage({"scenes": [dense]}, script)

    assert not any(i.type == "visual_only_unreadable" for i in issues)


def test_goal_ordinal_narration_suppresses_visual_only_item_4():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    script = _five_error_script()
    dense = {
        **_scene("s05", [4], layout="short_checklist", duration=1.0,
                 payload_items=["a", "b", "c", "d", "e"]),
        "narration": "Cuatro: otro trozo por cansancio.",
    }

    issues = validate_scene_idea_coverage({"scenes": [dense]}, script)

    assert not any(i.type == "visual_only_unreadable" for i in issues)


def test_goal_candidate_with_overlong_payoff_passes_after_repair():
    from video_agent.shorts import validate_scenes

    script, scenes_doc = _near_valid_five_error_scene_plan(cta_duration=2.8)
    scenes_doc["scenes"][6]["duration_sec"] = 7.4  # s07 payoff over hard max
    for scene in scenes_doc["scenes"]:
        validate_scenes.repair_scene_duration_if_possible(scene)
    scenes_doc["total_duration_sec"] = round(
        sum(s["duration_sec"] for s in scenes_doc["scenes"]), 1
    )

    issues = validate_scenes.validate_scene_structure(
        scenes_doc["scenes"], scenes_doc=scenes_doc, script=script
    )

    assert scenes_doc["scenes"][6]["duration_sec"] <= 5.0
    assert not validate_scenes.has_blocking_or_repairable(issues)


def test_goal_script_checklist_point_cap_with_locked_five_count():
    from video_agent.shorts.validate_scenes import validate_script_checklist_point_cap

    script = {
        "short_format": "mistake_list",
        "narration": "Uno: uno. Dos: dos. Tres: tres. Cuatro: cuatro. Cinco: cinco.",
        "idea_contract": {
            "must_preserve_count": True,
            "count_mode": "exact",
            "original_count": 5
        }
    }

    issue = validate_script_checklist_point_cap(script)
    assert issue is None


def test_goal_rule_based_qa_passes_when_checklist_point_cap_satisfied(tmp_path):
    from video_agent.shorts import qa, paths

    job_dir = tmp_path / "job"
    sd = paths.short_json_dir(job_dir, "short-01")
    sd.mkdir(parents=True)

    script = {
        "short_id": "short-01",
        "short_format": "mistake_list",
        "narration": "Uno: uno. Dos: dos. Tres: tres. Cuatro: cuatro. Cinco: cinco.",
        "cta": "Guárda.",
        "idea_contract": {
            "must_preserve_count": True,
            "count_mode": "exact",
            "original_count": 5
        }
    }
    
    # Save script and source map
    (sd / paths.SHORT_SCRIPT_FILE).write_text(json.dumps(script), encoding="utf-8")
    (sd / paths.SHORT_SOURCE_MAP_FILE).write_text(json.dumps({"used_source_scenes": ["scene-01"]}), encoding="utf-8")

    res = qa.run_short_script_qa(job_dir, "short-01", {"shorts": {"funnel": {"cta_max_words": 8}}}, music_track="track")
    assert res["verdict"] == "PASS"
    assert "script_checklist_point_cap" not in res["issues"]

