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

def test_scene_validation_fail_s08_duration_in_feedback():
    from video_agent.shorts.retry_memory import RetryMemory, RetryIssue, add_or_update_issue, generate_cumulative_feedback
    from video_agent.shorts.validate_scenes import SceneValidationIssue, build_scene_repair_plan
    
    # Given scene_validation FAIL with issue s08 duration > max
    issue = SceneValidationIssue(
        type="duration_cap",
        scene_id="s08",
        severity="repairable_error",
        detail="Scene s08 duration 5.2s exceeds layout cap 4.5s",
        repair_hint="Shorten s08 duration"
    )
    
    scenes = [
        {"id": "s08", "duration_sec": 5.2, "layout": "graphic_checklist"}
    ]
    
    # Build repair plan to attach instructions
    repair_plan = build_scene_repair_plan(scenes, [issue])
    assert issue.instructions is not None
    assert len(issue.instructions) > 0
    
    memory = RetryMemory(stage="scenes")
    
    required_change = "\n".join(issue.instructions)
    retry_issue = RetryIssue(
        id="scene_validation:s08:duration_cap:x",
        stage="scene_validation",
        attempt=1,
        scene_id=issue.scene_id,
        type=issue.type,
        severity=issue.severity,
        detail=issue.detail,
        required_change=required_change,
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1
    )
    add_or_update_issue(memory, retry_issue)
    
    feedback = generate_cumulative_feedback(memory, attempt_number=2)
    assert "[SCENE-VALIDATION][s08][DURATION-CAP]" in feedback
    assert "s08" in feedback
    assert "ACTIVE ISSUES: None" not in feedback

def test_scene_validation_fail_slideshow_risk_in_feedback():
    from video_agent.shorts.retry_memory import RetryMemory, RetryIssue, add_or_update_issue, generate_cumulative_feedback
    from video_agent.shorts.validate_scenes import SceneValidationIssue, build_scene_repair_plan
    
    # Given scene_validation FAIL with slideshow_risk
    issue = SceneValidationIssue(
        type="slideshow_risk",
        scene_id=None,
        severity="warning",
        detail="Slideshow risk: consecutive static layouts without visual motion",
        repair_hint="Introduce more graphics or switch layouts"
    )
    
    scenes = []
    repair_plan = build_scene_repair_plan(scenes, [issue])
    
    memory = RetryMemory(stage="scenes")
    required_change = issue.repair_hint or issue.detail
    retry_issue = RetryIssue(
        id="scene_validation:global:slideshow_risk:x",
        stage="scene_validation",
        attempt=1,
        scene_id=issue.scene_id,
        type=issue.type,
        severity=issue.severity,
        detail=issue.detail,
        required_change=required_change,
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1
    )
    add_or_update_issue(memory, retry_issue)
    
    feedback = generate_cumulative_feedback(memory, attempt_number=2)
    assert "SLIDESHOW-RISK" in feedback
    assert "Slideshow risk" in feedback
    assert "ACTIVE ISSUES: None" not in feedback

def test_scene_validation_fail_active_issues_length():
    from video_agent.shorts.retry_memory import RetryMemory, RetryIssue, add_or_update_issue
    from video_agent.shorts.validate_scenes import SceneValidationIssue
    
    memory = RetryMemory(stage="scenes")
    
    # 1. Validation FAIL with real issues
    issue1 = SceneValidationIssue(
        type="duration_cap",
        scene_id="s08",
        severity="repairable_error",
        detail="Scene s08 duration exceeds cap"
    )
    
    issue_id1 = "scene_validation:s08:duration_cap:x"
    retry_issue1 = RetryIssue(
        id=issue_id1,
        stage="scene_validation",
        attempt=1,
        scene_id=issue1.scene_id,
        type=issue1.type,
        severity=issue1.severity,
        detail=issue1.detail,
        required_change=issue1.detail,
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1
    )
    add_or_update_issue(memory, retry_issue1)
    assert len(memory.active_issues) > 0
    
    # 2. If all issues were warning-level suppressed issues
    from video_agent.shorts.retry_memory import suppress_issue_by_id
    suppress_issue_by_id(memory, issue_id1)
    assert len(memory.active_issues) == 0
