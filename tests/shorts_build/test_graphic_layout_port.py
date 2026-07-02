"""Regression tests for the long-form graphic-card port into the Shorts pipeline.

The long pipeline's graphic vocabulary (stat/myth/do_dont/recipe_snapshot/
quote_portrait/evidence_nugget/warning — see
``orchestrator/stages/graphic_images.py``) is now first-class in Shorts:
planning prompts, validators, and the ChatGPT image-prompt builder all know the
ported layouts, and the image prompt carries the long-form brand-style +
anatomy/typography guard.
"""

from __future__ import annotations

import pytest

PORTED_LAYOUTS = (
    "graphic_stat",
    "graphic_myth",
    "graphic_do_dont",
    "graphic_recipe_snapshot",
    "graphic_quote_portrait",
    "graphic_evidence_nugget",
    "graphic_warning",
)


def _valid_scene(layout: str) -> dict:
    payloads = {
        "graphic_stat": {"title": "80%", "body": "del pan de súper no es integral"},
        "graphic_myth": {
            "title": "El pan integral adelgaza",
            "body": "Sacia más, pero las calorías cuentan",
        },
        "graphic_do_dont": {
            "title": "EN EL DESAYUNO",
            "bad": "Pan blanco solo",
            "good": "Integral con proteína",
        },
        "graphic_recipe_snapshot": {
            "title": "MERIENDA REAL",
            "items": ["Yogur natural", "Avena", "Fruta"],
        },
        "graphic_quote_portrait": {"title": "Comer bien no es comer menos"},
        "graphic_evidence_nugget": {
            "title": "Después de los 60",
            "body": "la masa muscular cae más rápido",
        },
        "graphic_warning": {
            "title": "EVITA ESTO",
            "items": ["Comprar con hambre", "Fiarte del color"],
        },
    }
    return {
        "id": "s1",
        "layout": layout,
        "duration_sec": 3.5,
        "layout_payload": payloads[layout],
    }


def test_ported_layouts_registered_everywhere():
    from video_agent.shorts.short_scene_builder import SUPPORTED_GRAPHIC_LAYOUTS as builder_layouts
    from video_agent.shorts.validation._constants import GRAPHIC_LAYOUT_DURATION_TARGETS
    from video_agent.shorts.validation.issues import (
        LAYOUT_DURATION_TARGETS,
        SUPPORTED_GRAPHIC_LAYOUTS,
    )

    for layout in PORTED_LAYOUTS:
        assert layout in SUPPORTED_GRAPHIC_LAYOUTS
        assert layout in builder_layouts
        assert layout in LAYOUT_DURATION_TARGETS
        assert layout in GRAPHIC_LAYOUT_DURATION_TARGETS


def test_scene_builder_map_layout_preserves_ported_layouts():
    from video_agent.shorts.short_scene_builder import _map_layout

    for layout in PORTED_LAYOUTS:
        assert _map_layout(layout) == layout


@pytest.mark.parametrize("layout", PORTED_LAYOUTS)
def test_validator_accepts_valid_ported_payloads(layout):
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scene = _valid_scene(layout)
    validate_short_graphic_scenes([scene])
    assert scene["visual_type"] == "graphic"
    assert scene["on_screen_text"] == scene["layout_payload"]["title"]


@pytest.mark.parametrize(
    ("layout", "mutation", "fragment"),
    [
        ("graphic_stat", {"body": ""}, "non-empty body"),
        ("graphic_myth", {"body": None}, "non-empty body"),
        ("graphic_do_dont", {"good": ""}, "good"),
        ("graphic_recipe_snapshot", {"items": ["Solo uno"]}, "2-3"),
        ("graphic_warning", {"items": ["Esto es veneno", "Otra cosa"]}, "veneno"),
    ],
)
def test_validator_rejects_malformed_ported_payloads(layout, mutation, fragment):
    from video_agent.shorts.validate_scenes import validate_short_graphic_scenes

    scene = _valid_scene(layout)
    scene["layout_payload"].update(mutation)
    with pytest.raises(ValueError) as exc:
        validate_short_graphic_scenes([scene])
    assert fragment.lower() in str(exc.value).lower()


@pytest.mark.parametrize("layout", PORTED_LAYOUTS)
def test_image_prompt_has_layout_contract_for_ported_layouts(layout):
    from video_agent.shorts.assets.image_prompt import build_scene_image_prompt

    scene = _valid_scene(layout)
    scene["visual_prompt"] = "mature woman reading a bread label in a supermarket"
    prompt = build_scene_image_prompt(scene, "bread label")
    assert f"Layout contract: {layout}." in prompt
    # The payload teaching content must be baked into the prompt.
    assert scene["layout_payload"]["title"] in prompt
    # Long-form anatomy/typography guard is always present on graphic scenes.
    assert "Montserrat" in prompt
    assert "malformed hands" in prompt


def test_image_prompt_injects_channel_brand_style():
    from video_agent.shorts.assets.image_prompt import build_scene_image_prompt, load_brand_style

    brand = load_brand_style("vida-plena-45")
    assert "Brand style" in brand
    assert "#2F6B57" in brand  # vida-plena-45 primary from style-dna.json

    scene = _valid_scene("graphic_stat")
    prompt = build_scene_image_prompt(scene, "bread", brand_style=brand)
    assert "#2F6B57" in prompt

    # Missing channel → neutral fallback, never a crash.
    neutral = load_brand_style("no-such-channel")
    assert "Brand style" in neutral


def test_scene_prompt_v6_teaches_ported_layouts():
    import inspect

    from video_agent.shorts.prompts import short_scene_prompt_v6

    src = inspect.getsource(short_scene_prompt_v6)
    for layout in PORTED_LAYOUTS:
        assert layout in src, f"short_scene_prompt_v6 must teach {layout}"
