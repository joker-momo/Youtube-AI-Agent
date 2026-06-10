import pytest
from video_agent.shorts.validate_scenes import validate_scene_structure

def test_source_scene_ids_covers_items_validator():
    script = {
        "source_mapped_flow": [
            {"source_scene_id": "vid01_01", "concept": "first item"},
            {"source_scene_id": "vid01_02", "concept": "second item"}
        ],
        "idea_items": [
            {"item_id": 1},
            {"item_id": 2}
        ]
    }
    
    # 1. Missing source_scene_ids when covers_items exists
    scenes_missing = [
        {"id": "s01", "layout": "short_hook"},
        {"id": "s02", "layout": "short_tip", "covers_items": [1], "source_scene_ids": []},
        {"id": "s03", "layout": "short_cta"}
    ]
    issues = validate_scene_structure(scenes_missing, script=script)
    missing_issues = [i for i in issues if i.type == "missing_source_scene_ids"]
    assert len(missing_issues) == 1
    assert "Scene s02 covers items but has empty source_scene_ids" in missing_issues[0].detail
    
    # 2. Invalid source_scene_ids
    scenes_invalid = [
        {"id": "s01", "layout": "short_hook"},
        {"id": "s02", "layout": "short_tip", "covers_items": [1], "source_scene_ids": ["vid99_99"]},
        {"id": "s03", "layout": "short_cta"}
    ]
    issues = validate_scene_structure(scenes_invalid, script=script)
    invalid_issues = [i for i in issues if i.type == "invalid_source_scene_ids"]
    assert len(invalid_issues) == 1
    assert "vid99_99" in invalid_issues[0].detail
    
    # 3. Valid source_scene_ids
    scenes_valid = [
        {"id": "s01", "layout": "short_hook"},
        {"id": "s02", "layout": "short_tip", "covers_items": [1], "source_scene_ids": ["vid01_01"]},
        {"id": "s03", "layout": "short_cta"}
    ]
    issues = validate_scene_structure(scenes_valid, script=script)
    assert not any(i.type in ("missing_source_scene_ids", "invalid_source_scene_ids") for i in issues)
