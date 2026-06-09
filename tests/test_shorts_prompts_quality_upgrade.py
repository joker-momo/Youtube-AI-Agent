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


def test_scene_prompt_includes_source_mapped_flow_even_for_long_script():
    # F. scene prompt must contain source_mapped_flow even when narration is very long
    # (guards against the old [:2000] truncation that silently dropped source_mapped_flow)
    short_script = {
        "short_id": "short-05_idea-06_test",
        "short_format": "checklist",
        "hook": "¿Sabes qué pan comprar en el super?",
        "narration": "x" * 5000,  # simulate a very dense narration that would have eaten the 2000-char budget
        "cta": "Guarda esto para el súper.",
        "idea_contract": {
            "preserved": True,
            "original_count": 4,
            "final_count": 4,
            "adaptation_used": False,
            "adaptation_reason": ""
        },
        "idea_items": [
            {"item_id": 1, "label": "harina integral", "spoken_or_visual_role": "narration", "source_support": ["kp1"], "required": True},
            {"item_id": 2, "label": "gusto", "spoken_or_visual_role": "narration", "source_support": ["kp2"], "required": True},
            {"item_id": 3, "label": "estrategia semanal", "spoken_or_visual_role": "narration", "source_support": ["kp3"], "required": True},
            {"item_id": 4, "label": "frontal", "spoken_or_visual_role": "on_screen_text", "source_support": ["kp4"], "required": True},
        ],
        "source_mapped_flow": [
            {"item_id": 1, "source_support": ["kp1"], "spoken_summary": "Busca harina integral como primer ingrediente.", "visual_role": "narration"},
            {"item_id": 2, "source_support": ["kp2"], "spoken_summary": "Confía en el gusto, no en el color.", "visual_role": "narration"},
            {"item_id": 3, "source_support": ["kp3"], "spoken_summary": "Planifica tu estrategia semanal.", "visual_role": "narration"},
            {"item_id": 4, "source_support": ["kp4"], "spoken_summary": "Lee siempre el frontal del paquete.", "visual_role": "on_screen_text"},
        ],
    }

    prompt = prompts.short_scene_prompt_v6({}, {}, short_script)

    assert "source_mapped_flow" in prompt, "source_mapped_flow must appear in scene prompt"
    assert "harina integral" in prompt, "item label 'harina integral' must survive in scene prompt"
    assert "gusto" in prompt, "item label 'gusto' must survive in scene prompt"
    assert "estrategia semanal" in prompt, "item label 'estrategia semanal' must survive in scene prompt"
    assert "frontal" in prompt, "item label 'frontal' must survive in scene prompt"
