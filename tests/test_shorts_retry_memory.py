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
    assert "1. [SCENE-VALIDATION][s07][DURATION] Clamp CTA scene s07 to <= 2.8s" in feedback
    assert "Preserve source fidelity." in feedback

def test_pipeline_state_assertions():
    from video_agent.shorts.retry_memory import ScenePipelineState
    state = ScenePipelineState()
    state.current_scenes_version = 1
    
    # Assert latest validation fails because validation version is None
    import pytest
    with pytest.raises(RuntimeError) as exc_info:
        from video_agent.shorts.short_builder import assert_latest_scenes_ready
        assert_latest_scenes_ready(state)
    assert "deterministic" in str(exc_info.value)

def test_cumulative_feedback_keeps_all_issues():
    from video_agent.shorts.retry_memory import RetryMemory, RetryIssue, add_or_update_issue, generate_cumulative_feedback
    memory = RetryMemory(stage="scenes")
    # Attempt 1: Issue A, B
    issue_a = RetryIssue(
        id="scene_qa:s06:layout:issue_a",
        stage="scene_qa",
        attempt=1,
        scene_id="s06",
        type="layout",
        severity="major",
        detail="Detail A",
        required_change="Fix A",
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1
    )
    issue_b = RetryIssue(
        id="scene_validation:s07:duration:issue_b",
        stage="scene_validation",
        attempt=1,
        scene_id="s07",
        type="duration",
        severity="repairable_error",
        detail="Detail B",
        required_change="Fix B",
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1
    )
    add_or_update_issue(memory, issue_a)
    add_or_update_issue(memory, issue_b)

    # Attempt 2: Issue C (A and B not proven fixed / not resolved)
    issue_c = RetryIssue(
        id="scene_qa:s06:payload:issue_c",
        stage="scene_qa",
        attempt=2,
        scene_id="s06",
        type="payload",
        severity="major",
        detail="Detail C",
        required_change="Fix C",
        status="active",
        first_seen_attempt=2,
        last_seen_attempt=2
    )
    add_or_update_issue(memory, issue_c)

    feedback = generate_cumulative_feedback(memory, attempt_number=3)
    # feedback must keep A, B, and C
    assert "Fix A" in feedback
    assert "Fix B" in feedback
    assert "Fix C" in feedback

def test_gemini_qa_fail_then_regenerated_must_rerun_qa():
    from video_agent.shorts.retry_memory import ScenePipelineState
    from video_agent.shorts.short_builder import assert_latest_scenes_ready
    import pytest

    state = ScenePipelineState()
    # scene_qa FAIL at version 4
    state.current_scenes_version = 4
    state.latest_scene_validation_ok = True
    state.latest_scene_validation_version = 4
    state.latest_scene_qa_ok = False
    state.latest_scene_qa_version = None

    # scenes regenerated to version 5
    state.current_scenes_version = 5
    state.latest_scene_validation_ok = True
    state.latest_scene_validation_version = 5
    # QA is not run yet, so it is stale or False
    state.latest_scene_qa_ok = False
    state.latest_scene_qa_version = None

    with pytest.raises(RuntimeError) as exc_info:
        assert_latest_scenes_ready(state)
    assert "Gemini scene QA" in str(exc_info.value)

def test_scene_validation_fail_blocks_all():
    from video_agent.shorts.retry_memory import ScenePipelineState
    from video_agent.shorts.short_builder import assert_latest_scenes_ready
    import pytest

    state = ScenePipelineState()
    state.current_scenes_version = 5
    state.latest_scene_validation_ok = False
    state.latest_scene_validation_version = None
    state.latest_scene_qa_ok = True
    state.latest_scene_qa_version = 5

    with pytest.raises(RuntimeError) as exc_info:
        assert_latest_scenes_ready(state)
    assert "deterministic scene_validation" in str(exc_info.value)

def test_stale_validation_result_blocks_render():
    from video_agent.shorts.retry_memory import ScenePipelineState
    from video_agent.shorts.short_builder import assert_latest_scenes_ready
    import pytest

    state = ScenePipelineState()
    state.current_scenes_version = 6
    state.latest_scene_validation_ok = True
    state.latest_scene_validation_version = 5  # stale!
    state.latest_scene_qa_ok = True
    state.latest_scene_qa_version = 6

    with pytest.raises(RuntimeError) as exc_info:
        assert_latest_scenes_ready(state)
    assert "scene_validation result is stale" in str(exc_info.value)

def test_audio_tail_ok_does_not_override_scene_validation_fail():
    from video_agent.shorts.retry_memory import ScenePipelineState
    from video_agent.shorts.short_builder import assert_latest_scenes_ready
    import pytest

    state = ScenePipelineState()
    state.current_scenes_version = 5
    # Validation failed
    state.latest_scene_validation_ok = False
    state.latest_scene_validation_version = None
    state.latest_scene_qa_ok = True
    state.latest_scene_qa_version = 5
    # Audio tail is OK
    state.latest_audio_tail_ok = True
    state.latest_audio_tail_version = 5

    with pytest.raises(RuntimeError) as exc_info:
        assert_latest_scenes_ready(state)
    assert "deterministic" in str(exc_info.value)

def test_mechanical_cta_clamp_must_revalidate():
    # Test that mechanical clamps increment current_scenes_version and clear validation flags.
    # In build_short, when duration repair happens, we increment current_scenes_version and reset validation flags.
    # Let's verify that resetting works.
    from video_agent.shorts.retry_memory import ScenePipelineState
    state = ScenePipelineState()
    state.current_scenes_version = 1
    state.latest_scene_validation_ok = True
    state.latest_scene_validation_version = 1
    state.latest_scene_qa_ok = True
    state.latest_scene_qa_version = 1

    # Simulate mechanical patch
    state.current_scenes_version += 1
    state.latest_scene_validation_ok = False
    state.latest_scene_validation_version = None

    import pytest
    from video_agent.shorts.short_builder import assert_latest_scenes_ready
    with pytest.raises(RuntimeError) as exc_info:
        assert_latest_scenes_ready(state)
    assert "deterministic scene_validation" in str(exc_info.value)
