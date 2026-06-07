# tests/test_shorts_retry_memory.py
from video_agent.shorts.retry_memory import make_stable_issue_id, RetryIssue

def test_stable_id_normalization():
    detail1 = "CTA scene s07 must be <= 2.8 sec"
    id1 = make_stable_issue_id("scene_validation", "s07", "duration", detail1)
    
    detail2 = "CTA scene s07 must be <= 5.2 sec"
    id2 = make_stable_issue_id("scene_validation", "s07", "duration", detail2)
    
    assert id1 == id2
    assert id1 == "scene_validation:s07:duration:cta_scene_scene_id_must_be_n_sec"

def test_retry_memory_feedback():
    from video_agent.shorts.retry_memory import RetryMemory, add_or_update_issue, generate_cumulative_feedback
    memory = RetryMemory(stage="scenes")
    memory.hard_invariants = ["Preserve source fidelity."]
    
    issue = RetryIssue(
        id="scene_validation:s07:duration:cta_too_long",
        stage="scene_validation",
        attempt=1,
        scene_id="s07",
        type="duration",
        severity="repairable_error",
        detail="CTA scene s07 duration exceeds 2.8s",
        required_change="Clamp CTA scene s07 to <= 2.8s",
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1
    )
    add_or_update_issue(memory, issue)
    
    feedback = generate_cumulative_feedback(memory, attempt_number=2)
    assert "ACTIVE ISSUES TO FIX NOW:" in feedback
    assert "1. [scene_validation][s07][duration] Clamp CTA scene s07 to <= 2.8s" in feedback
    assert "Preserve source fidelity." in feedback

def test_pipeline_state_assertions():
    from video_agent.shorts.retry_memory import ScenePipelineState
    state = ScenePipelineState()
    state.current_scenes_version = 1
    
    import pytest
    with pytest.raises(RuntimeError) as exc_info:
        from video_agent.shorts.short_builder import assert_latest_scenes_ready
        assert_latest_scenes_ready(state)
    assert "deterministic" in str(exc_info.value)

