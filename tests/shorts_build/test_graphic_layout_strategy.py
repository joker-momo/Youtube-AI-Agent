from __future__ import annotations

import re

import pytest

from video_agent.shorts.assets.image_prompt import (
    build_scene_image_prompt,
    load_brand_style,
)
from video_agent.shorts.prompts import short_scene_prompt_v6
from video_agent.shorts.validation.graphic_checks import validate_short_graphic_scenes

PLANNER_GRAPHIC_LAYOUTS = {
    "graphic_plate_ratio",
    "graphic_checklist",
    "graphic_step_list",
    "graphic_label_callout",
    "graphic_comparison",
    "graphic_stat",
    "graphic_myth",
    "graphic_do_dont",
    "graphic_recipe_snapshot",
    "graphic_warning",
}

LEGACY_INTERNAL_LAYOUTS = {
    "graphic_routine_split",
    "graphic_evidence_nugget",
    "graphic_quote_portrait",
}


def _scene(layout: str, payload: dict, *, duration: float = 4.0) -> dict:
    return {
        "id": "s02",
        "layout": layout,
        "visual_type": "graphic",
        "duration_sec": duration,
        "narration": "Elige una opción fácil de recordar.",
        "visual_prompt": "real Spanish foods photographed with consistent warm light",
        "layout_payload": payload,
    }


def _planner_layout_section() -> str:
    prompt = short_scene_prompt_v6(
        {},
        {"short_id": "s"},
        {"short_id": "s", "narration": "n", "hook": "h", "cta": "c"},
    )
    return prompt.split("GRAPHIC SCENE LAYOUTS", 1)[1].split(
        "GRAPHIC layout_payload SHAPES", 1
    )[0]


def test_scene_planner_exposes_exactly_ten_semantic_graphic_layouts() -> None:
    section = _planner_layout_section()
    offered = set(re.findall(r"^- (graphic_[a-z_]+)\s+->", section, flags=re.MULTILINE))

    assert offered == PLANNER_GRAPHIC_LAYOUTS
    assert offered.isdisjoint(LEGACY_INTERNAL_LAYOUTS)


def test_scene_planner_teaches_legacy_layout_migrations_and_surface_families() -> None:
    prompt = short_scene_prompt_v6(
        {},
        {"short_id": "s"},
        {"short_id": "s", "narration": "n", "hook": "h", "cta": "c"},
    )

    assert "evidence" in prompt.lower() and "graphic_stat" in prompt
    assert "time" in prompt.lower() and "graphic_step_list" in prompt
    assert "repair" in prompt.lower() and "graphic_quote_portrait" in prompt
    for style in {
        "hero_stat",
        "binary_split",
        "numbered_photo_bands",
        "annotated_object",
        "photo_tiles",
    }:
        assert style in prompt


def test_five_item_checklist_is_rejected_without_silent_truncation() -> None:
    items = ["Uno", "Dos", "Tres", "Cuatro", "Cinco"]
    scene = _scene(
        "graphic_checklist",
        {"title": "CINCO PASOS", "items": items},
    )

    with pytest.raises(ValueError, match="requires 2-4 items"):
        validate_short_graphic_scenes([scene])

    assert scene["layout_payload"]["items"] == items


def test_step_list_accepts_an_optional_short_time_prefix() -> None:
    scene = _scene(
        "graphic_step_list",
        {
            "title": "RUTINA FÁCIL",
            "steps": [
                {"label": "1", "time": "10 min", "text": "Preparar dormitorio"},
                {"label": "2", "time": "5 min", "text": "Respirar despacio"},
            ],
        },
    )

    validate_short_graphic_scenes([scene])


def test_step_list_rejects_an_empty_time_prefix() -> None:
    scene = _scene(
        "graphic_step_list",
        {
            "title": "RUTINA FÁCIL",
            "steps": [
                {"label": "1", "time": "", "text": "Preparar dormitorio"},
                {"label": "2", "time": "5 min", "text": "Respirar despacio"},
            ],
        },
    )

    with pytest.raises(ValueError, match="step.time"):
        validate_short_graphic_scenes([scene])


def test_numbered_photo_bands_is_valid_only_for_list_semantics() -> None:
    checklist = _scene(
        "graphic_checklist",
        {
            "title": "MEJOR ASÍ",
            "items": ["Porción visible", "Plato pequeño", "Comida completa"],
            "surface_style": "numbered_photo_bands",
        },
    )
    validate_short_graphic_scenes([checklist])

    stat = _scene(
        "graphic_stat",
        {
            "title": "80%",
            "body": "Mira la etiqueta",
            "surface_style": "numbered_photo_bands",
        },
        duration=3.0,
    )
    with pytest.raises(ValueError, match="numbered_photo_bands"):
        validate_short_graphic_scenes([stat])


@pytest.mark.parametrize(
    ("layout", "surface_style", "payload"),
    [
        ("graphic_stat", "hero_stat", {"title": "80%", "body": "Mira la etiqueta"}),
        (
            "graphic_comparison",
            "binary_split",
            {
                "title": "DOS OPCIONES",
                "left": {"heading": "A", "text": "Opción uno"},
                "right": {"heading": "B", "text": "Opción dos"},
            },
        ),
        (
            "graphic_label_callout",
            "annotated_object",
            {
                "title": "MIRA PRIMERO",
                "callouts": [
                    {"label": "Fibra", "value": "6 g"},
                    {"label": "Azúcar", "value": "3 g"},
                ],
            },
        ),
        (
            "graphic_recipe_snapshot",
            "photo_tiles",
            {"title": "MERIENDA REAL", "items": ["Yogur", "Avena", "Fruta"]},
        ),
    ],
)
def test_new_surface_families_validate_on_compatible_semantics(
    layout: str, surface_style: str, payload: dict
) -> None:
    scene = _scene(layout, {**payload, "surface_style": surface_style})

    validate_short_graphic_scenes([scene])


def test_graphic_prompt_is_content_first_and_uses_brand_as_accent_only() -> None:
    brand = load_brand_style("vida-plena-45")
    prompt = build_scene_image_prompt(
        _scene(
            "graphic_checklist",
            {
                "title": "FRUTOS SECOS",
                "items": ["Pasas", "Albaricoque", "Higos"],
                "surface_style": "numbered_photo_bands",
            },
        ),
        "dried fruit benefits",
        brand_style=brand,
    )
    lower = prompt.lower()

    assert "content-first" in lower
    assert "brand" in lower and "accent" in lower
    assert "full-bleed" in lower or "edge-to-edge" in lower
    assert "horizontal" in lower and "numbered" in lower and "circular" in lower
    assert "use only this brand palette" not in lower
    assert "soft panel/card" not in lower
    assert "wellness-magazine card" not in lower


def test_legacy_layouts_remain_valid_for_stored_scene_compatibility() -> None:
    scenes = [
        _scene(
            "graphic_evidence_nugget",
            {"title": "DESPUÉS DE 60", "body": "Cuida tu fuerza"},
            duration=3.0,
        ),
        _scene(
            "graphic_routine_split",
            {
                "title": "RUTINA 20 MIN",
                "blocks": [
                    {"time": "10 min", "text": "Preparar dormitorio"},
                    {"time": "10 min", "text": "Respirar despacio"},
                ],
            },
        ),
        _scene(
            "graphic_quote_portrait",
            {"title": "Comer bien no es comer menos"},
        ),
    ]
    for index, scene in enumerate(scenes, start=1):
        scene["id"] = f"s{index:02d}"

    warnings = validate_short_graphic_scenes(scenes)

    assert any("3 graphic scenes" in warning for warning in warnings)
