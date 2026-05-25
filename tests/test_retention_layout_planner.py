from __future__ import annotations

from video_agent.retention.layout_planner import apply_retention_layouts


def _scene(**overrides):
    scene = {
        "id": "scene-01",
        "narration": "Empieza con un plato simple: proteína, verduras y agua.",
        "caption": "Empieza con un plato simple.",
        "on_screen_text": "TU PLATO BASE",
        "layout": "subtitle",
        "layout_payload": {"title": "", "body": "", "bullets": [], "cta": ""},
        "planner_warnings": [],
    }
    scene.update(overrides)
    return scene


def test_invalid_layout_downgrades_to_subtitle():
    [scene] = apply_retention_layouts([_scene(layout="sparkle")])
    assert scene["layout"] == "subtitle"
    assert scene["planner_warnings"]


def test_checklist_requires_supported_bullets():
    scene = _scene(
        layout="checklist",
        layout_payload={"title": "TU PLATO BASE", "body": "", "bullets": ["Proteína", "Verduras", "Agua"], "cta": ""},
    )
    [planned] = apply_retention_layouts([scene])
    assert planned["layout"] == "checklist"

    bad = _scene(
        layout="checklist",
        layout_payload={"title": "TU PLATO BASE", "body": "", "bullets": ["Proteína", "Magnesio"], "cta": ""},
    )
    [planned_bad] = apply_retention_layouts([bad])
    assert planned_bad["layout"] == "subtitle"
    assert "Checklist downgraded" in planned_bad["planner_warnings"][0]


def test_checklist_allows_safe_shortened_bullets_without_inventing_content():
    scene = _scene(
        layout="checklist",
        narration="Empieza con un plato simple: proteína, verduras y agua.",
        layout_payload={
            "title": "TU PLATO BASE",
            "body": "",
            "bullets": ["Proteína", "Verduras", "Agua"],
            "cta": "",
        },
    )

    [planned] = apply_retention_layouts([scene])

    assert planned["layout"] == "checklist"
    assert planned["layout_payload"]["bullets"] == ["Proteína", "Verduras", "Agua"]

    unsafe = _scene(
        layout="checklist",
        narration="Caminar diez minutos después de comer puede ayudarte.",
        layout_payload={
            "title": "TU PLATO BASE",
            "body": "",
            "bullets": ["Proteína", "Verduras"],
            "cta": "",
        },
    )

    [planned_unsafe] = apply_retention_layouts([unsafe])

    assert planned_unsafe["layout"] == "subtitle"
    assert planned_unsafe["layout_payload"]["bullets"] == ["Proteína", "Verduras"]


def test_warning_requires_warning_intent():
    scene = _scene(layout="warning", narration="Evita llegar a la cena con hambre extrema.")
    [planned] = apply_retention_layouts([scene])
    assert planned["layout"] == "warning"

    calm = _scene(layout="warning", narration="Camina con calma despues de comer.")
    [planned_calm] = apply_retention_layouts([calm])
    assert planned_calm["layout"] == "subtitle"


def test_cta_only_final_scene():
    first = _scene(id="scene-01", layout="cta", layout_payload={"cta": "Suscríbete hoy"})
    last = _scene(id="scene-02", layout="cta", layout_payload={"cta": "Suscríbete hoy"})
    planned = apply_retention_layouts([first, last])
    assert planned[0]["layout"] == "subtitle"
    assert planned[1]["layout"] == "cta"


def test_first_scene_promotes_to_hook_only_with_safe_text():
    [planned] = apply_retention_layouts([_scene(layout="subtitle", on_screen_text="NO ES TU EDAD")])
    assert planned["layout"] == "hook"


def test_pattern_break_warning_when_no_safe_candidate():
    scenes = [
        _scene(id=f"scene-{idx:02d}", layout="subtitle", on_screen_text="", caption="", narration="")
        for idx in range(1, 8)
    ]
    planned = apply_retention_layouts(scenes)
    assert any("Could not insert safe pattern break" in warning for warning in planned[0]["planner_warnings"])


def test_pattern_break_promotes_only_eligible_existing_payload():
    scenes = [
        _scene(id=f"scene-{idx:02d}", layout="subtitle", on_screen_text="")
        for idx in range(1, 8)
    ]
    scenes[3]["layout"] = "checklist"
    scenes[3]["layout_payload"] = {
        "title": "TU PLATO BASE",
        "body": "",
        "bullets": ["Proteína", "Verduras", "Agua"],
        "cta": "",
    }

    planned = apply_retention_layouts(scenes)

    assert planned[3]["layout"] == "checklist"
    assert not any(
        "Could not insert safe pattern break" in warning
        for warning in planned[0]["planner_warnings"]
    )


def test_pattern_break_derives_safe_templates_from_existing_scene_text():
    scenes = [
        _scene(
            id=f"scene-{idx:02d}",
            layout="subtitle",
            on_screen_text=f"ESCENA {idx}",
            caption=f"Frase breve {idx}.",
            narration=f"Frase breve {idx}.",
        )
        for idx in range(1, 16)
    ]
    scenes[3]["narration"] = "Evita forzar el sueño cuando aparece ansiedad."
    scenes[3]["caption"] = "Evita forzar el sueño."
    scenes[3]["on_screen_text"] = "EVITA FORZAR"
    scenes[8]["caption"] = "Respira y baja el ritmo."
    scenes[8]["on_screen_text"] = "RESPIRA"
    scenes[8]["narration"] = "Respira y baja el ritmo."
    scenes[13]["caption"] = "Dormir mejor empieza antes."
    scenes[13]["on_screen_text"] = "ANTES DE DORMIR"
    scenes[13]["narration"] = "Dormir mejor empieza antes."

    planned = apply_retention_layouts(scenes)
    layouts = {scene["layout"] for scene in planned}

    assert "warning" in layouts
    assert "checklist" in layouts
    assert "quote" in layouts
    for scene in planned:
        if scene["layout"] == "checklist":
            assert scene["layout_payload"]["bullets"]
        if scene["layout"] == "quote":
            assert scene["layout_payload"]["body"]


def test_final_scene_promotes_to_cta_from_script_without_mid_video_cta():
    first = _scene(id="scene-01", layout="subtitle")
    last = _scene(id="scene-02", layout="subtitle")

    planned = apply_retention_layouts(
        [first, last],
        script={"cta": "Prueba esta rutina esta noche."},
    )

    assert planned[0]["layout"] != "cta"
    assert planned[1]["layout"] == "cta"
    assert planned[1]["layout_payload"]["cta"] == "Prueba esta rutina esta noche."
