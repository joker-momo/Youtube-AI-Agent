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


# --- degenerate graphic payload repair (bug-486) -----------------------------

def _scene(layout: str, payload: dict, sid: str = "s02") -> dict:
    return {
        "id": sid,
        "layout": layout,
        "visual_type": "graphic",
        "duration_sec": 4.0,
        "narration": "x",
        "layout_payload": {
            "variant": "warm_olive",
            "visual_tone": "focus",
            "background_mode": "video_blur",
            "surface_style": "soft_card",
            **payload,
        },
    }


def test_one_item_checklist_downgrades_to_quote_instead_of_dying():
    """The real failure (short-02 idea-02): model produced graphic_checklist with
    a single item -> the validator raised and killed the whole job."""
    from video_agent.shorts.validation.graphic_checks import validate_short_graphic_scenes

    scene = _scene("graphic_checklist", {"title": "MIDE", "items": ["Mide tu cintura hoy"]})
    warnings = validate_short_graphic_scenes([scene])
    assert scene["layout"] == "graphic_quote_portrait"
    assert scene["layout_payload"]["title"] == "Mide tu cintura hoy"
    assert any("downgraded" in w.lower() for w in warnings)


def test_oversized_checklist_is_not_silently_truncated():
    from video_agent.shorts.validation.graphic_checks import validate_short_graphic_scenes

    items = [f"Paso {i}" for i in range(1, 8)]
    scene = _scene("graphic_checklist", {"title": "PASOS", "items": items})
    with pytest.raises(ValueError, match="requires 2-4 items"):
        validate_short_graphic_scenes([scene])
    assert scene["layout_payload"]["items"] == items


def test_one_step_step_list_downgrades_to_quote():
    from video_agent.shorts.validation.graphic_checks import validate_short_graphic_scenes

    scene = _scene("graphic_step_list", {"title": "HOY", "steps": [{"label": "1", "text": "Camina diez minutos"}]})
    validate_short_graphic_scenes([scene])
    assert scene["layout"] == "graphic_quote_portrait"
    assert scene["layout_payload"]["title"] == "Camina diez minutos"


def test_valid_checklist_untouched_and_empty_still_raises():
    from video_agent.shorts.validation.graphic_checks import validate_short_graphic_scenes

    ok = _scene("graphic_checklist", {"title": "PASOS", "items": ["Uno", "Dos", "Tres"]})
    validate_short_graphic_scenes([ok])
    assert ok["layout"] == "graphic_checklist"
    assert ok["layout_payload"]["items"] == ["Uno", "Dos", "Tres"]

    empty = _scene("graphic_checklist", {"title": "PASOS", "items": []})
    with pytest.raises(ValueError, match="requires 2-4 items"):
        validate_short_graphic_scenes([empty])


def test_scene_prompt_forbids_bare_label_compression():
    """bug-487 / bug-503: the scene builder gutted 'mide cuánto aceite usas realmente,
    sin cambiar nada' into 'Mide lo que usas.' — a bare label that lost the point and
    failed fidelity + product-quality QA. The prompt must forbid shortening and instead
    require verbatim preservation with clause-boundary splitting."""
    from video_agent.shorts.prompts import short_scene_prompt_v6

    p = short_scene_prompt_v6(
        {}, {"short_id": "s"}, {"short_id": "s", "narration": "n", "hook": "h", "cta": "c"}
    )
    assert "NEVER drop, summarize away, or reduce a beat" in p
    # the café/aceite gutting example is still called out as BAD
    assert "Mide lo que usas." in p
    # and the fix is SPLIT-at-clause-boundary, keeping every clause
    assert "clause boundary" in p
