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
        _scene(id=f"scene-{idx:02d}", layout="subtitle", on_screen_text="")
        for idx in range(1, 8)
    ]
    planned = apply_retention_layouts(scenes)
    assert any("Could not insert safe pattern break" in warning for warning in planned[0]["planner_warnings"])
