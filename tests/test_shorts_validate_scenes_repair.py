from video_agent.shorts.validate_scenes import repair_visual_only_unreadable

# repair_visual_only_unreadable injects the missing item's text into the scene
# CAPTION, never the narration: narration drives the TTS duration estimate, and
# injecting there created false scene_narration_fit hard blockers.

def test_repair_visual_only_unreadable_basic():
    scenes = [
        {"id": "1", "narration": "Hello world", "caption": "Hello world"},
        {"id": "2", "narration": "Second scene", "caption": "Second scene", "layout_payload": {"items": ["Some other item"]}},
    ]

    # Needs repair
    assert repair_visual_only_unreadable(scenes, "My new item") is True
    assert "My new item" in scenes[1]["caption"]
    # Narration must stay untouched (spoken-duration estimate must not change).
    assert scenes[1]["narration"] == "Second scene"

def test_repair_visual_only_unreadable_dict_item():
    scenes = [
        {"id": "1", "narration": "Hello world", "caption": "Hello world", "covers_items": ["1"]},
        {"id": "2", "narration": "Second scene", "caption": "Second scene", "covers_items": ["3"]},
    ]

    req = {"id": "3", "label": "Third item"}
    assert repair_visual_only_unreadable(scenes, req) is True

    # The actual item label is injected into the caption of scene 2 (covers item 3).
    # Never a hardcoded per-item string: a mismatched caption would make repair
    # return True while validation keeps failing the same item -> false hard blocker.
    assert "Third item" in scenes[1]["caption"]
    assert scenes[1]["narration"] == "Second scene"
    assert "3" in scenes[1]["covers_items"]

def test_repair_visual_only_unreadable_already_covered():
    scenes = [
        {"id": "1", "narration": "Hello world", "caption": "Hello world", "covers_items": ["1"]},
        {"id": "2", "narration": "Second scene with Third item", "caption": "Second scene", "covers_items": ["3"]},
    ]

    req = {"id": "3", "label": "Third item"}
    # Already in narration, so it should just return False (no repair needed)
    assert repair_visual_only_unreadable(scenes, req) is False

def test_repair_visual_only_unreadable_item_id_and_integer_covers():
    scenes = [
        {"id": "s01", "duration_sec": 3.0, "narration": "First scene", "covers_items": [1]},
        {"id": "s02", "duration_sec": 3.0, "narration": "Second scene", "covers_items": [3]},
    ]
    req = {"item_id": 3, "label": "Third item"}
    # Should locate scene s02 which covers item 3 (integer) using the item_id (integer -> string '3')
    assert repair_visual_only_unreadable(scenes, req) is True
    # Injects the actual label into scene s02's caption; narration untouched.
    assert "Third item" in scenes[1].get("caption", "")
    assert scenes[1]["narration"] == "Second scene"
    assert 3 in scenes[1]["covers_items"]


def test_repair_visual_only_unreadable_item_4_uses_actual_label_for_validation():
    from video_agent.shorts.idea_preservation import validate_scene_idea_coverage

    scenes = [
        {
            "id": "s04",
            "duration_sec": 4.2,
            "narration": "El móvil no siempre relaja.",
            "caption": "Puede abrir más estímulos. Vuelve a harina, fibra e ingredientes.",
            "covers_items": [3, 4],
            "layout_payload": {
                "title": "Atención activada",
                "items": ["Noticias", "Discusiones", "Comparaciones"],
            },
        }
    ]
    req = {"item_id": 4, "label": "Noticias, discusiones y comparaciones activan la atención"}

    assert repair_visual_only_unreadable(scenes, req) is True

    script = {
        "idea_contract": {"must_preserve_count": True},
        "idea_items": [
            {"item_id": 4, "label": "Noticias, discusiones y comparaciones activan la atención", "required": True},
        ],
    }
    issues = validate_scene_idea_coverage({"scenes": scenes}, script)

    assert scenes[0]["narration"] == "El móvil no siempre relaja."
    assert "Vuelve a harina" not in scenes[0]["caption"]
    assert "Noticias, discusiones" in scenes[0]["caption"]
    assert not any(issue.type == "visual_only_unreadable" for issue in issues)


# repair_excess_graphic_scenes demotes the lowest-value graphics back to realistic
# scenes when a normal Short exceeds the 2-graphic cap, so the over-cap repairable
# error converges deterministically instead of looping to a hard blocker.


def _three_graphic_scenes():
    return [
        {"id": "s01", "layout": "graphic_label_callout", "narration": "Mira la etiqueta.",
         "on_screen_text": "ETIQUETA", "duration_sec": 3.0,
         "layout_payload": {"title": "ETIQUETA", "productLabel": "x", "callouts": []}},
        {"id": "s02", "layout": "graphic_comparison", "narration": "Compara fibra.",
         "on_screen_text": "FIBRA", "duration_sec": 3.0,
         "layout_payload": {"title": "FIBRA", "left": {}, "right": {}}},
        {"id": "s03", "layout": "graphic_checklist", "narration": "Resumen.",
         "on_screen_text": "RESUMEN", "duration_sec": 3.0, "visual_type": "graphic",
         "layout_payload": {"title": "RESUMEN", "items": ["uno", "dos"]}},
    ]


def test_repair_excess_graphic_scenes_demotes_to_cap():
    from video_agent.shorts.validate_scenes import (
        count_graphic_scenes,
        repair_excess_graphic_scenes,
    )

    scenes = _three_graphic_scenes()
    assert repair_excess_graphic_scenes(scenes, {"short_format": "checklist"}) is True
    # Now at or under the 2-graphic cap.
    assert count_graphic_scenes(scenes) <= 2
    # The demoted scene became a realistic short_tip with no graphic markers.
    demoted = [s for s in scenes if not str(s["layout"]).startswith("graphic_")]
    assert demoted, "expected at least one demoted scene"
    d = demoted[0]
    assert d["layout"] == "short_tip"
    assert d.get("visual_type") != "graphic"
    assert "layout_payload" not in d
    assert d["visual_prompt"].strip()
    # Narration / id preserved.
    assert d["narration"]
    assert d["id"]


def test_repair_excess_graphic_scenes_noop_under_cap():
    from video_agent.shorts.validate_scenes import repair_excess_graphic_scenes

    scenes = _three_graphic_scenes()[:2]
    assert repair_excess_graphic_scenes(scenes, {"short_format": "checklist"}) is False


def test_repair_excess_graphic_scenes_respects_explicit_graphic_led():
    from video_agent.shorts.validate_scenes import repair_excess_graphic_scenes

    scenes = _three_graphic_scenes()
    # Explicit opt-in must NOT be demoted.
    assert repair_excess_graphic_scenes(scenes, {"graphic_led": True}) is False
