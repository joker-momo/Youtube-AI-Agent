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

    # Injected into the caption of scene 2 because it covers item 3
    assert "Prepara un pan base" in scenes[1]["caption"]
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
    # Injects into scene s02's caption; narration untouched.
    assert "Prepara un pan base" in scenes[1].get("caption", "")
    assert scenes[1]["narration"] == "Second scene"
    assert 3 in scenes[1]["covers_items"]
