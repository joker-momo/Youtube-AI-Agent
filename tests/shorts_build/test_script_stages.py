from __future__ import annotations

from .conftest import *  # noqa: F401,F403

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


