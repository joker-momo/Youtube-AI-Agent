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


def test_pattern_break_does_not_fabricate_overlay_content():
    """Spec §"Python planner must not": never invent bullets/quotes from on_screen_text.

    Long runs of subtitle without ChatGPT-proposed payload must stay subtitle and
    emit a planner warning instead of being promoted with derived junk content.
    """
    scenes = [
        _scene(
            id=f"scene-{idx:02d}",
            layout="subtitle",
            on_screen_text="",
            caption=f"Frase breve {idx}.",
            narration=f"Frase breve {idx}.",
        )
        for idx in range(1, 16)
    ]

    planned = apply_retention_layouts(scenes)
    layouts = [scene["layout"] for scene in planned]

    # All scenes stay subtitle — planner must not fabricate checklist/quote payloads.
    assert all(layout == "subtitle" for layout in layouts)
    # At least one scene in the long subtitle run carries the pattern-break warning.
    assert any(
        "Could not insert safe pattern break" in w
        for s in planned
        for w in s.get("planner_warnings", [])
    )


def test_pattern_break_promotes_proposed_checklist_with_valid_payload():
    """Spec line 617: promote a scene that ALREADY shipped valid checklist payload."""
    scenes = [
        _scene(id=f"scene-{idx:02d}", layout="subtitle")
        for idx in range(1, 16)
    ]
    scenes[8]["layout"] = "checklist"
    scenes[8]["narration"] = "Empieza con un plato simple: proteína, verduras y agua."
    scenes[8]["caption"] = "Empieza con un plato simple."
    scenes[8]["on_screen_text"] = "TU PLATO BASE"
    scenes[8]["layout_payload"] = {
        "title": "TU PLATO BASE",
        "body": "",
        "bullets": ["Proteína", "Verduras", "Agua"],
        "cta": "",
    }

    planned = apply_retention_layouts(scenes)

    assert planned[8]["layout"] == "checklist"
    assert planned[8]["layout_payload"]["bullets"] == ["Proteína", "Verduras", "Agua"]


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


def test_stat_requires_supported_number():
    good = _scene(
        layout="stat",
        narration="Solo necesitas 3 pasos para armar un buen desayuno.",
        layout_payload={"title": "3 pasos", "body": "para el desayuno"},
    )
    [planned] = apply_retention_layouts([good])
    assert planned["layout"] == "stat"

    bad = _scene(layout="stat", narration="Un desayuno equilibrado.", layout_payload={"title": "999 cosas", "body": "x"})
    [planned_bad] = apply_retention_layouts([bad])
    assert planned_bad["layout"] == "subtitle"


def test_steps_requires_supported_ordered_items():
    good = _scene(
        layout="steps",
        narration="Empieza con un plato simple: proteína, verduras y agua.",
        layout_payload={"title": "Tu plato", "bullets": ["Proteína", "Verduras", "Agua"]},
    )
    [planned] = apply_retention_layouts([good])
    assert planned["layout"] == "steps"


def test_comparison_requires_two_supported_sides():
    good = _scene(
        layout="comparison",
        narration="Compara una cena ligera con una cena abundante antes de dormir.",
        layout_payload={"title": "Cena", "bullets": ["cena ligera", "cena abundante"]},
    )
    [planned] = apply_retention_layouts([good])
    assert planned["layout"] == "comparison"

    bad = _scene(layout="comparison", narration="Solo una idea.", layout_payload={"title": "x", "bullets": ["una"]})
    [planned_bad] = apply_retention_layouts([bad])
    assert planned_bad["layout"] == "subtitle"


def test_myth_requires_supported_myth_and_reality():
    good = _scene(
        layout="myth",
        narration="El mito de saltarse comidas frente a la realidad de comer equilibrado.",
        layout_payload={"title": "saltarse comidas", "body": "comer equilibrado"},
    )
    [planned] = apply_retention_layouts([good])
    assert planned["layout"] == "myth"


def test_duplicate_graphic_headline_downgraded():
    a = _scene(
        id="scene-01",
        layout="warning",
        narration="Evita los dos extremos al comer de noche.",
        layout_payload={"title": "Evita los dos extremos", "bullets": ["Evita los dos extremos al comer de noche", "Evita los dos extremos al comer de noche"]},
    )
    mid = _scene(id="scene-02", layout="subtitle")
    c = _scene(
        id="scene-03",
        layout="warning",
        narration="Evita los dos extremos otra vez al cenar.",
        layout_payload={"title": "Evita los dos extremos", "bullets": ["Evita los dos extremos otra vez al cenar", "Evita los dos extremos otra vez al cenar"]},
    )
    planned = apply_retention_layouts([a, mid, c])
    assert planned[0]["layout"] == "warning"
    assert planned[2]["layout"] == "subtitle"  # duplicate headline downgraded
