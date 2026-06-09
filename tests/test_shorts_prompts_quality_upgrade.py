from __future__ import annotations

import json
from video_agent.shorts import prompts
from video_agent.shorts.short_script_builder import ensure_script_idea_fields
from video_agent.shorts.short_scene_builder import normalize_short_scenes
from video_agent.shorts.retry_memory import generate_cumulative_feedback, RetryMemory, RetryIssue

def test_script_prompt_handles_mismatch_and_preserves_count_authority():
    # A. Script prompt handles mismatch
    short_plan = {
        "format": "pain_to_tip",
        "key_points": [{"point": "A"}, {"point": "B"}, {"point": "C"}, {"point": "D"}],
        "narration_seed": "Primero... Segundo... Tercero... Cuarto... Quinto..."
    }
    script_prompt = prompts.short_script_prompt({}, short_plan, {})
    assert 'The source_mapped_flow array must contain exactly as many items as defined in idea_contract.original_count' in script_prompt

def test_gemini_script_qa_treats_mismatch_as_warning():
    # B. Gemini Script QA treats mismatch as warning
    qa_prompt = prompts.gemini_script_qa_prompt({}, {})
    assert 'COUNT AUTHORITY' in qa_prompt
    assert 'it must NOT fail QA or produce a HARD_BLOCKER. It may only produce a WARN' in qa_prompt

def test_normalization_preserves_source_mapped_flow_and_transition():
    # C. Normalization preserves source_mapped_flow
    short_plan = {
        "key_points": [{"point": "A"}, {"point": "B"}, {"point": "C"}, {"point": "D"}],
    }
    fake_candidate = {
        "source_mapped_flow": [
            {"item_id": 1, "source_support": ["A"], "spoken_summary": "1", "visual_role": "narration"},
            {"item_id": 2, "source_support": ["B"], "spoken_summary": "2", "visual_role": "narration"},
            {"item_id": 3, "source_support": ["C"], "spoken_summary": "3", "visual_role": "narration"},
            {"item_id": 4, "source_support": ["D"], "spoken_summary": "4", "visual_role": "narration"}
        ],
        "idea_items": []
    }
    
    normalized_script = ensure_script_idea_fields(fake_candidate, short_plan)
    assert len(normalized_script["source_mapped_flow"]) == 4
    assert normalized_script["source_mapped_flow"][0]["item_id"] == 1

    # D. Normalization preserves transition_from_previous
    fake_scene_candidate = {
        "scenes": [
            {
                "scene_id": "s01",
                "transition_from_previous": "START",
                "covers_items": [],
                "retention_function": "hook"
            },
            {
                "scene_id": "s02",
                "transition_from_previous": "Cut to product",
                "covers_items": [1],
                "retention_function": "payoff"
            }
        ]
    }
    normalized_scene = normalize_short_scenes(fake_scene_candidate, normalized_script)
    assert normalized_scene["scenes"][0]["transition_from_previous"] == "START"
    assert normalized_scene["scenes"][1]["transition_from_previous"] == "Cut to product"
    assert normalized_scene["scenes"][1]["covers_items"] == [1]

def test_retry_feedback_accumulation():
    # E. Retry feedback accumulation
    memory = RetryMemory(stage="script")
    memory.active_issues["test"] = RetryIssue(
        id="test",
        stage="script",
        attempt=1,
        scene_id=None,
        type="missing_point",
        severity="HARD_BLOCKER",
        detail="Missing point 4",
        required_change="Fix it",
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1
    )
    
    exact_mapping_context = "1. Point A\n2. Point B\n3. Point C\n4. Point D"
    
    feedback = generate_cumulative_feedback(
        memory,
        attempt_number=2,
        candidate_summary="Missing point 4 again.",
        exact_mapping_context=exact_mapping_context
    )
    
    assert "EXACT ITEM MAPPING REQUIRED" in feedback
    assert "1. Point A" in feedback
    assert "Missing point 4" in feedback
