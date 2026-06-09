from video_agent.shorts.validate_scenes import repair_visual_only_unreadable

def test_repair_visual_only_unreadable_basic():
    scenes = [
        {"id": "1", "narration": "Hello world", "caption": "Hello world"},
        {"id": "2", "narration": "Second scene", "caption": "Second scene", "layout_payload": {"items": ["Some other item"]}},
    ]
    
    # Needs repair
    assert repair_visual_only_unreadable(scenes, "My new item") is True
    assert "My new item" in scenes[1]["narration"]

def test_repair_visual_only_unreadable_dict_item():
    scenes = [
        {"id": "1", "narration": "Hello world", "caption": "Hello world", "covers_items": ["1"]},
        {"id": "2", "narration": "Second scene", "caption": "Second scene", "covers_items": ["3"]},
    ]
    
    req = {"id": "3", "label": "Third item"}
    assert repair_visual_only_unreadable(scenes, req) is True
    
    # Should be injected into narration of scene 2 because it covers item 3
    assert "Prepara un pan base antes del hambre" in scenes[1]["narration"]
    assert "3" in scenes[1]["covers_items"]
    
def test_repair_visual_only_unreadable_already_covered():
    scenes = [
        {"id": "1", "narration": "Hello world", "caption": "Hello world", "covers_items": ["1"]},
        {"id": "2", "narration": "Second scene with Third item", "caption": "Second scene", "covers_items": ["3"]},
    ]
    
    req = {"id": "3", "label": "Third item"}
    # Already in narration, so it should just return False (no repair needed)
    assert repair_visual_only_unreadable(scenes, req) is False
