from __future__ import annotations

import json


def test_plan_first_frame_prefers_evidence_for_nutrition_label_topic():
    from video_agent.shorts.first_frame_planner import plan_first_frame

    plan = plan_first_frame(
        {
            "title": "Pan integral falso",
            "topic_family": "nutrition",
            "hook_text": "No mires el color. Mira el primer ingrediente.",
        },
        {
            "id": "s01",
            "layout": "short_hook",
            "visual_prompt": "close up supermarket bread package label",
        },
    )

    assert plan["strategy"] in {"evidence_closeup", "object_contrast", "graphic_proof"}
    assert plan["preferred_source"] == "pexels_photo"
    assert plan["roi_target"] == "ingredient label"
    assert "smiling person holding food" in plan["must_avoid"]
    assert "wide supermarket aisle" in plan["must_avoid"]
    assert plan["overlay_text"]


def test_plan_first_frame_does_not_treat_pantallas_as_bread_topic():
    from video_agent.shorts.first_frame_planner import plan_first_frame

    plan = plan_first_frame(
        {
            "title": "Pequeños límites para bajar la carga",
            "hook_text": "BAJA LA CARGA",
            "viewer_pain": "Sentir que incluso el descanso se llena de tareas, pantallas y prisa.",
        },
        {
            "id": "s01",
            "layout": "short_hook",
            "on_screen_text": "BAJA LA CARGA",
            "visual_prompt": "person sitting on a sofa with phone face down",
        },
    )

    assert plan["strategy"] == "human_reaction"
    assert plan["overlay_text"] == "BAJA LA CARGA"
    assert plan["roi_target"] == "face reaction"


def test_plan_first_frame_does_not_treat_bread_portion_as_label_topic():
    from video_agent.shorts.first_frame_planner import plan_first_frame

    plan = plan_first_frame(
        {
            "title": "La porción de pan sin contar gramos",
            "hook_text": "TU MANO AYUDA",
            "hook_angle": "La porción de pan sin contar gramos",
            "viewer_pain": "No saber cuánto pan comer y acabar repitiendo por costumbre.",
        },
        {
            "id": "s01",
            "layout": "short_hook",
            "narration": "TU MANO AYUDA.",
            "on_screen_text": "TU MANO AYUDA",
            "visual_prompt": "close-up of an open palm beside a bread slice for portion size comparison",
        },
    )

    assert plan["strategy"] == "object_contrast"
    assert plan["overlay_text"] == "TU MANO AYUDA"
    assert plan["roi_target"] == "specific object"
    assert "ingredient label" not in plan["must_show"]


def test_plan_first_frame_ignores_negative_supermarket_constraints_for_wellness_topic():
    from video_agent.shorts.first_frame_planner import plan_first_frame

    plan = plan_first_frame(
        {
            "title": "Pequeños límites que le dicen al cuerpo que ya no hay que correr",
            "hook_text": "BAJA LA CARGA",
            "viewer_pain": "Sentir que incluso el descanso se llena de tareas, pantallas y prisa.",
        },
        {
            "id": "s01",
            "layout": "short_hook",
            "on_screen_text": "BAJA LA CARGA",
            "narration": "Baja la carga.",
            "visual_prompt": (
                "Vertical 9:16 cinematic close-up in a warm Spanish bedroom at night: "
                "a 50-plus adult sits on the edge of the bed, bedside lamp on, "
                "phone face down on the nightstand. No food, no supermarket, no labels."
            ),
        },
    )

    assert plan["strategy"] == "human_reaction"
    assert plan["roi_target"] == "face reaction"


def test_apply_first_frame_plan_sets_scene_one_text_without_duplicate_callout():
    from video_agent.shorts.first_frame_planner import apply_first_frame_plan

    scenes_doc = {
        "scenes": [
            {
                "id": "s01",
                "layout": "short_hook",
                "on_screen_text": "OLD",
                "visual_prompt": "bread package label",
            },
            {"id": "s02", "layout": "short_tip", "on_screen_text": "KEEP"},
        ]
    }

    updated = apply_first_frame_plan(
        scenes_doc,
        {"title": "Pan integral falso", "topic_family": "nutrition"},
        {},
    )

    first = updated["scenes"][0]
    assert first["first_frame_plan"]["overlay_text"] == first["on_screen_text"]
    assert first["shorts_quality_debug"]["first_frame_overlay_source"] == "first_frame_plan.overlay_text"
    assert "callout_text" not in first
    assert updated["scenes"][1]["on_screen_text"] == "KEEP"


def test_graphic_scene_uses_graphic_proof_without_forcing_stock():
    from video_agent.shorts.first_frame_planner import plan_first_frame

    plan = plan_first_frame(
        {"title": "Pan integral falso", "topic_family": "nutrition"},
        {"id": "s01", "layout": "graphic_label_callout", "visual_prompt": ""},
    )

    assert plan["strategy"] == "graphic_proof"
    assert plan["preferred_source"] == "graphic"


def test_build_short_scenes_persists_first_frame_plan(tmp_path):
    from video_agent.shorts import paths
    from video_agent.shorts.short_scene_builder import build_short_scenes

    def fake_llm(prompt: str) -> str:
        return json.dumps(
            {
                "scenes": [
                    {
                        "scene_id": "s01",
                        "layout": "short_hook",
                        "duration_sec": 3,
                        "narration": "No mires el color.",
                        "on_screen_text": "PAN",
                        "visual_prompt": "close up supermarket bread package label",
                    }
                ]
            }
        )

    short_plan = {
        "short_id": "short-01",
        "title": "Pan integral falso",
        "topic_family": "nutrition",
        "hook_text": "No mires el color. Mira el primer ingrediente.",
    }
    script = {
        "hook": "No mires el color. Mira el primer ingrediente.",
        "narration": "No mires el color. Mira el primer ingrediente.",
    }

    scenes = build_short_scenes(tmp_path, short_plan, script, {"shorts": {}}, fake_llm)
    artifact = paths.short_json_dir(tmp_path, "short-01") / paths.SHORT_SCENES_FILE
    persisted = json.loads(artifact.read_text(encoding="utf-8"))

    assert scenes["scenes"][0]["first_frame_plan"]
    assert persisted["scenes"][0]["first_frame_plan"]
    assert persisted["scenes"][0]["on_screen_text"] == persisted["scenes"][0]["first_frame_plan"]["overlay_text"]
