from __future__ import annotations


def test_planner_prompt_requests_quality_fields_and_preserves_candidate_ids():
    from video_agent.shorts import prompts

    prompt = prompts.planner_prompt(
        {},
        [{"candidate_id": "candidate-01", "scene_ids": ["s1"], "narration": "Pan oscuro."}],
        {"job_id": "job-1", "title": "Pan integral", "pillar": "nutrition"},
        ["pain_to_tip"],
    ).lower()

    for expected in ("hook_pattern", "viewer_pain", "curiosity_gap", "comment_trigger_type", "identity_angle"):
        assert expected in prompt
    assert "only select candidate_id values present" in prompt
    assert "raw json" in prompt


def test_script_prompt_includes_retention_plan_fields_and_raw_json_only():
    from video_agent.shorts import prompts

    prompt = prompts.short_script_prompt(
        {},
        {"short_id": "short-01", "format": "pain_to_tip", "narration_seed": "Mira la etiqueta."},
        {},
        retention_plan={"hook_pattern": "common_mistake", "curiosity_gap": "color no basta"},
    ).lower()

    for expected in ("retention plan", "hook_pattern", "curiosity_gap", "micro_tension_lines", "identity_line", "comment_trigger"):
        assert expected in prompt
    assert "return exactly one raw valid json object" in prompt
    assert "no markdown fences" in prompt


def test_scene_prompt_includes_retention_humanization_and_rhythm_fields():
    from video_agent.shorts import prompts

    prompt = prompts.short_scene_prompt_v6(
        {},
        {"short_id": "short-01"},
        {"narration": "Pan oscuro no basta."},
        retention_plan={"retention_beats": [{"function": "hook"}]},
        spoken_humanization={"delivery_style": "warm_direct"},
    ).lower()

    for expected in ("retention plan", "spoken humanization", "retention_function", "rhythm_tag", "pattern_interrupt", "avoid slide-deck feel"):
        assert expected in prompt
    assert "return exactly one raw valid json object" in prompt


def test_gemini_qa_prompts_include_new_quality_scores():
    from video_agent.shorts import prompts

    script_prompt = prompts.gemini_script_qa_prompt({}, {"narration": "Pan oscuro no basta."}).lower()
    scene_prompt = prompts.gemini_scenes_qa_prompt({}, {"narration": "Pan oscuro no basta."}, {"scenes": []}).lower()

    for expected in ("hook_specificity", "micro_tension", "human_naturalness", "visual_rhythm", "identity_resonance", "commentability"):
        assert expected in script_prompt
        assert expected in scene_prompt

