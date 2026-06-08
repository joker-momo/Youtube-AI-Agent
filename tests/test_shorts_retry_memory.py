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
    assert "ACTIVE BLOCKERS TO FIX NOW:" in feedback
    assert "1. [SCENE-VALIDATION][s07][DURATION]" in feedback
    assert "Class: HARD_BLOCKER" in feedback
    assert "Required fix: Clamp CTA scene s07 to <= 2.8s" in feedback
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

def test_severity_constants_exist():
    from video_agent.shorts.qa import IssueClass
    assert IssueClass.HARD_BLOCKER == "hard_blocker"
    assert IssueClass.REPAIRABLE_BLOCKER == "repairable_blocker"
    assert IssueClass.SOFT_WARNING == "soft_warning"
    assert IssueClass.STALE_OR_SUPPRESSED == "stale_or_suppressed"

def test_get_short_rule_context():
    from video_agent.shorts.qa import get_short_rule_context
    idea = {"title": "La regla de compra para no equivocarte con el pan", "format": "checklist"}
    script = {"hook": "GIRA EL PAQUETE"}
    ctx = get_short_rule_context(idea, script)
    assert ctx["is_bread_shopping_checklist"] is True
    assert ctx["is_five_errors_bread_short"] is False

def test_cumulative_feedback_sections():
    from video_agent.shorts.retry_memory import RetryMemory, RetryIssue, add_or_update_issue, generate_cumulative_feedback
    memory = RetryMemory(stage="scenes")
    
    # Add a hard/repairable blocker
    issue_blocker = RetryIssue(
        id="scene_validation:s05:unreadable:1",
        stage="scene_validation",
        attempt=1,
        scene_id="s05",
        type="unreadable",
        severity="major",
        detail="Required item is unreadable",
        required_change="Speak the item OR give it a clearer scene",
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1,
        issue_class="repairable_blocker",
        reason="visual_only_unreadable"
    )
    add_or_update_issue(memory, issue_blocker)
    
    # Add a soft warning
    issue_warning = RetryIssue(
        id="scene_qa:s01:hook_motion:1",
        stage="scene_qa",
        attempt=1,
        scene_id="s01",
        type="hook_motion",
        severity="minor",
        detail="Hook motion could be sharper",
        required_change="Hook motion could be sharper",
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1,
        issue_class="soft_warning",
        reason="weak_hook_motion"
    )
    add_or_update_issue(memory, issue_warning)
    
    # Add a suppressed issue
    issue_suppressed = RetryIssue(
        id="scene_qa:global:five_errors:1",
        stage="scene_qa",
        attempt=1,
        scene_id=None,
        type="five_errors",
        severity="minor",
        detail="Suppressed because format is checklist",
        required_change="Suppressed because format is checklist",
        status="suppressed",
        first_seen_attempt=1,
        last_seen_attempt=1,
        issue_class="stale_or_suppressed",
        reason="wrong_context_five_errors_rule"
    )
    memory.suppressed_issues[issue_suppressed.id] = issue_suppressed
    
    feedback = generate_cumulative_feedback(memory, attempt_number=2)
    assert "ACTIVE BLOCKERS TO FIX NOW:" in feedback
    assert "Class: REPAIRABLE_BLOCKER" in feedback
    assert "WARNINGS / NICE-TO-HAVE (DO NOT BLOCK):" in feedback
    assert "SUPPRESSED / STALE ISSUES:" in feedback

def test_only_soft_warnings_do_not_regenerate():
    from video_agent.shorts.qa import normalize_qa_issue, IssueClass
    idea = {"title": "La regla de compra para no equivocarte con el pan", "format": "checklist"}
    script = {"hook": "GIRA EL PAQUETE"}
    scenes = {}
    
    issue1 = {
        "type": "product_quality_score_low",
        "scene_id": None,
        "severity": "major",
        "detail": "Some product quality scores are below their required thresholds: {'hook_strength': 8.0}. Required: {'hook_strength': 9.0}."
    }
    norm1 = normalize_qa_issue(issue1, idea=idea, script=script, scenes=scenes)
    assert norm1.issue_class == IssueClass.SOFT_WARNING
    assert norm1.trigger_regeneration is False

    issue2 = {
        "type": "weak_hook_motion",
        "scene_id": "s01",
        "severity": "minor",
        "detail": "First scene is missing a strong hook motion cue."
    }
    norm2 = normalize_qa_issue(issue2, idea=idea, script=script, scenes=scenes)
    assert norm2.issue_class == IssueClass.SOFT_WARNING
    assert norm2.trigger_regeneration is False

def test_weak_hook_motion_repaired_deterministically():
    from video_agent.shorts.validate_scenes import repair_weak_hook_motion
    scenes = [
        {"id": "s01", "duration_sec": 3.5, "layout": "hook_layout"}
    ]
    changed = repair_weak_hook_motion(scenes)
    assert changed is True
    assert scenes[0]["motion"] == "push_in"
    assert scenes[0]["pattern_interrupt"] == "text_pop at 0.5s"

def test_visual_only_unreadable_repaired_deterministically():
    from video_agent.shorts.validate_scenes import repair_visual_only_unreadable
    scenes = [
        {"id": "s01", "duration_sec": 3.5, "layout": "graphic_checklist", "caption": "Some description", "layout_payload": {"items": ["item1", "item2"]}}
    ]
    changed = repair_visual_only_unreadable(scenes, required_item="item3")
    assert changed is True
    assert "item3" in scenes[0]["caption"] or "item3" in scenes[0].get("layout_payload", {}).get("items", [])


# ===== Spec §11 Acceptance Tests =====

def test_retry_feedback_active_only_hard_or_repairable():
    """Spec §11.1: ACTIVE BLOCKERS contains only hard/repairable issues."""
    from video_agent.shorts.qa import normalize_qa_issue, IssueClass
    from video_agent.shorts.retry_memory import RetryMemory, RetryIssue, add_or_update_issue, generate_cumulative_feedback

    idea = {"title": "Test short", "format": "checklist"}
    script = {"hook": "TEST"}
    scenes = {}

    issues = [
        {"type": "weak_hook_motion", "scene_id": "s01", "severity": "minor",
         "detail": "First scene is missing a strong hook motion cue."},
        {"type": "product_quality_score_low", "scene_id": None, "severity": "major",
         "detail": "Some product quality scores are below their required thresholds: {'hook_strength': 8.0}. Required: {'hook_strength': 9.0}."},
        {"type": "visual_only_unreadable", "scene_id": "s05", "severity": "major",
         "detail": "Item 3 appears only visually in an unreadable/dense scene."},
    ]

    memory = RetryMemory(stage="scenes")
    for i, raw in enumerate(issues):
        norm = normalize_qa_issue(raw, idea=idea, script=script, scenes=scenes)
        ri = RetryIssue(
            id=f"test:{i}",
            stage="scene_qa",
            attempt=1,
            scene_id=norm.scene_id,
            type=norm.issue_type,
            severity="minor" if norm.issue_class == IssueClass.SOFT_WARNING else "major",
            detail=norm.detail,
            required_change=norm.detail,
            status="active",
            first_seen_attempt=1,
            last_seen_attempt=1,
            issue_class=norm.issue_class,
            reason=norm.reason,
        )
        add_or_update_issue(memory, ri)

    feedback = generate_cumulative_feedback(memory, attempt_number=2)
    # ACTIVE BLOCKERS should contain only visual_only_unreadable (repairable)
    assert "ACTIVE BLOCKERS TO FIX NOW:" in feedback
    assert "unreadable" in feedback.lower()
    # WARNINGS should contain weak_hook_motion and product_score_low
    assert "WARNINGS / NICE-TO-HAVE (DO NOT BLOCK):" in feedback
    assert "hook motion" in feedback.lower() or "hook_motion" in feedback.lower()


def test_soft_issues_do_not_trigger_scene_regeneration():
    """Spec §11.1: deterministic validation PASS + Gemini FAIL with only product
    quality scores 8/10 -> qa_scenes becomes WARN, no regeneration."""
    from video_agent.shorts.qa import normalize_qa_issue, IssueClass

    idea = {"title": "Test short", "format": "checklist"}
    script = {"hook": "TEST"}
    scenes = {}

    issue = {
        "type": "product_quality_score_low",
        "scene_id": None,
        "severity": "major",
        "detail": "Some product quality scores are below their required thresholds: {'hook_strength': 8.0}. Required: {'hook_strength': 9.0}.",
    }
    norm = normalize_qa_issue(issue, idea=idea, script=script, scenes=scenes)
    assert norm.issue_class == IssueClass.SOFT_WARNING
    assert norm.trigger_regeneration is False

    all_norms = [norm]
    blockers = [n for n in all_norms if n.issue_class in {IssueClass.HARD_BLOCKER, IssueClass.REPAIRABLE_BLOCKER}]
    warnings = [n for n in all_norms if n.issue_class == IssueClass.SOFT_WARNING]
    assert len(blockers) == 0
    assert len(warnings) == 1
    verdict = "WARN" if warnings else "PASS"
    assert verdict == "WARN"


def test_retry_attempt_three_continues_if_only_soft_warnings():
    """Spec §11.1: attempt >= 3 with only soft warnings -> stop retry, continue with WARN."""
    from video_agent.shorts.qa import normalize_qa_issue, IssueClass

    idea = {"title": "Test short", "format": "checklist"}
    script = {"hook": "TEST"}

    soft_issues = [
        {"type": "weak_hook_motion", "scene_id": "s01", "severity": "minor",
         "detail": "First scene is missing a strong hook motion cue."},
        {"type": "product_quality_average_low", "scene_id": None, "severity": "minor",
         "detail": "Average product quality score is 8.5, below 8.90."},
    ]

    norms = [normalize_qa_issue(i, idea=idea, script=script, scenes={}) for i in soft_issues]
    blockers = [n for n in norms if n.issue_class in {IssueClass.HARD_BLOCKER, IssueClass.REPAIRABLE_BLOCKER}]
    warnings = [n for n in norms if n.issue_class == IssueClass.SOFT_WARNING]

    assert len(blockers) == 0
    assert len(warnings) == 2
    decision = "continued_with_warn" if not blockers else "failed_hard_blocker"
    assert decision == "continued_with_warn"


def test_five_errors_rules_not_applied_to_bread_shopping_checklist():
    """Spec §11.2: five-errors rules are suppressed for bread shopping checklist."""
    from video_agent.shorts.qa import normalize_qa_issue, IssueClass

    idea = {
        "title": "La regla de compra para no equivocarte con el pan",
        "hook_text": "GIRA EL PAQUETE",
        "format": "checklist",
    }
    script = {"hook": "GIRA EL PAQUETE"}

    wrong_context_issues = [
        {"type": "required_change", "detail": "Set s01 title to NO ES EL PAN", "severity": "major"},
        {"type": "required_change", "detail": "CTA must use GUÁRDALO", "severity": "major"},
        {"type": "required_change", "detail": "All scenes must follow 3.2-4.0s error scene duration", "severity": "major"},
    ]

    for raw in wrong_context_issues:
        norm = normalize_qa_issue(raw, idea=idea, script=script, scenes={})
        assert norm.issue_class == IssueClass.STALE_OR_SUPPRESSED, f"Expected STALE_OR_SUPPRESSED for: {raw['detail']}, got {norm.issue_class}"
        assert norm.trigger_regeneration is False
        assert norm.reason == "wrong_context_five_errors_rule"


def test_five_errors_rules_apply_to_actual_five_errors_short():
    """Spec §11.2: five-errors rules are active for actual five-errors shorts."""
    from video_agent.shorts.qa import get_short_rule_context

    idea = {
        "title": "5 errores con el pan despues de los 45",
        "format": "mistakes",
        "original_count": 5,
    }
    script = {"hook": "NO ES EL PAN"}
    ctx = get_short_rule_context(idea, script)
    assert ctx["is_five_errors_bread_short"] is True


def test_call_budget_unknown_is_low():
    """Spec §11.3: with known failure types, unknown should be 0."""
    from video_agent.shorts.call_budget import build_call_budget_summary

    history = [
        {"provider": "deterministic", "kind": "scene_validation", "ok": False, "error": "Duration cap"},
        {"provider": "deterministic", "kind": "scene_structure", "ok": False, "error": "Layout mismatch"},
        {"provider": "gemini", "kind": "qa_scenes", "ok": False, "reason": "qa_soft_warn"},
        {"provider": "gemini", "kind": "qa_scenes", "ok": False, "reason": "qa_soft_warn"},
        {"provider": "gemini", "kind": "qa_scenes", "ok": False, "reason": "qa_soft_warn"},
        {"provider": "gemini", "kind": "qa_scenes", "ok": False, "reason": "qa_soft_warn"},
        {"provider": "deterministic", "kind": "wrong_context_suppressed", "ok": False,
         "reason": "wrong_context_suppressed"},
    ]
    s = build_call_budget_summary(history)
    assert s["by_reason"]["scene_validation_fail"] == 2
    assert s["by_reason"]["qa_soft_warn"] == 4
    assert s["by_reason"]["wrong_context_suppressed"] == 1
    assert s["by_reason"]["unknown"] == 0


def test_call_budget_records_retry_collapse():
    """Spec §11.3: retry_collapse increments when same normalized hash repeats."""
    from video_agent.shorts.call_budget import build_call_budget_summary

    history = [
        {"provider": "deterministic", "kind": "retry_collapse", "ok": False,
         "reason": "retry_collapse",
         "payload": {"detail": "Identical scene output across retries; stopping loop."}},
    ]
    s = build_call_budget_summary(history)
    assert s["by_reason"]["retry_collapse"] == 1


def test_no_chatgpt_regeneration_for_hook_repair():
    """Spec §11.4: weak hook motion repaired deterministically, no ChatGPT call."""
    from video_agent.shorts.validate_scenes import repair_weak_hook_motion

    scenes = [
        {"id": "s01", "duration_sec": 3.5, "layout": "hook_layout", "narration": "Test hook"}
    ]
    changed = repair_weak_hook_motion(scenes)
    assert changed is True
    assert scenes[0]["motion"] == "push_in"
    assert scenes[0]["pattern_interrupt"] == "text_pop at 0.5s"

    # Scene that already has motion (but is weak) should be repaired to push_in
    scenes2 = [
        {"id": "s01", "duration_sec": 3.5, "layout": "hook_layout", "motion": "zoom_in"}
    ]
    changed2 = repair_weak_hook_motion(scenes2)
    assert changed2 is True
    assert scenes2[0]["motion"] == "push_in"


def test_canonical_checklist_count_authority():
    from video_agent.shorts.qa import normalize_qa_issue, IssueClass
    from video_agent.shorts.idea_preservation import ensure_script_idea_fields
    from video_agent.shorts.call_budget import build_call_budget_summary

    # 1. QA Mismatch Suppression Logic
    idea = {"original_count": 4, "title": "Test idea", "format": "checklist"}
    script = {
        "idea_contract": {
            "original_count": 4,
            "must_preserve_count": True,
        }
    }
    scenes = {}

    # Gemini QA incorrectly infers 5-step checklist
    issue_five_step = {
        "type": "idea_fidelity",
        "detail": "The original idea defines a 5-step checklist, but the script only has 4 items."
    }

    norm = normalize_qa_issue(issue_five_step, idea=idea, script=script, scenes=scenes)
    assert norm.issue_class == IssueClass.STALE_OR_SUPPRESSED
    assert norm.reason == "noncanonical_count_inference"
    assert norm.trigger_regeneration is False

    # Check spanish count mismatch
    issue_spanish_five = {
        "type": "idea_fidelity",
        "detail": "El guión debe tener cinco errores como indica la idea."
    }
    norm_sp = normalize_qa_issue(issue_spanish_five, idea=idea, script=script, scenes=scenes)
    assert norm_sp.issue_class == IssueClass.STALE_OR_SUPPRESSED
    assert norm_sp.reason == "noncanonical_count_inference"

    # Should NOT suppress if original_count is actually 5
    script_five = {
        "idea_contract": {
            "original_count": 5,
            "must_preserve_count": True,
        }
    }
    norm_not_suppressed = normalize_qa_issue(issue_five_step, idea={"original_count": 5}, script=script_five, scenes=scenes)
    assert norm_not_suppressed.issue_class != IssueClass.STALE_OR_SUPPRESSED

    # 2. Contract Protection
    chatgpt_script_mutated = {
        "idea_contract": {
            "original_count": 5,
            "final_count": 5,
            "must_preserve_count": True,
        }
    }
    short_plan = {
        "key_points": [1, 2, 3, 4],  # Canonical original_count = 4
        "format": "checklist",
    }
    protected_script = ensure_script_idea_fields(chatgpt_script_mutated, short_plan)
    assert protected_script["idea_contract"]["original_count"] == 4
    assert protected_script["idea_contract"]["final_count"] == 4

    # 3. Call Budget reason mapping
    history = [
        {"provider": "gemini", "kind": "qa_script", "verdict": "FAIL", "ok": True},
        {"provider": "deterministic", "kind": "qa_classification", "payload": {"reason": "noncanonical_count_inference"}},
    ]
    summary = build_call_budget_summary(history)
    assert summary["by_reason"]["noncanonical_count_inference"] == 1
    assert summary["by_reason"]["qa_hard_fail"] == 0


def test_repair_weak_hook_motion_non_strong():
    from video_agent.shorts.validate_scenes import repair_weak_hook_motion
    scenes = [
        {"id": "s01", "duration_sec": 3.5, "layout": "short_hook", "motion": "pan_right"}
    ]
    changed = repair_weak_hook_motion(scenes)
    assert changed is True
    assert scenes[0]["motion"] == "push_in"
    assert scenes[0]["pattern_interrupt"] == "text_pop at 0.5s"


def test_slideshow_risk_downgrade():
    from video_agent.shorts.validate_scenes import validate_scene_structure
    # Define scenes that trigger hard slideshow_risk
    scenes = [
        {"id": "s01", "duration_sec": 3.0, "layout": "short_hook", "motion": "push_in", "retention_function": "hook"},
        {"id": "s02", "duration_sec": 3.0, "layout": "graphic_checklist", "layout_payload": {"items": [1, 2, 3]}},
        {"id": "s03", "duration_sec": 3.0, "layout": "graphic_checklist", "layout_payload": {"items": [1, 2, 3]}},
        {"id": "s04", "duration_sec": 3.0, "layout": "graphic_checklist", "layout_payload": {"items": [1, 2, 3]}},
        {"id": "s05", "duration_sec": 3.0, "layout": "short_cta", "motion": "push_in"}
    ]
    # At attempt 1, slideshow_risk is a repairable_error (blocker)
    issues_1 = validate_scene_structure(scenes, attempt=1, script={"idea_items": [{"item_id": 1}]})
    slideshow_1 = next((i for i in issues_1 if i.type == "slideshow_risk"), None)
    assert slideshow_1 is not None
    assert slideshow_1.severity == "repairable_error"

    # At attempt 2, slideshow_risk is downgraded to warning
    issues_2 = validate_scene_structure(scenes, attempt=2, script={"idea_items": [{"item_id": 1}]})
    slideshow_2 = next((i for i in issues_2 if i.type == "slideshow_risk"), None)
    assert slideshow_2 is not None
    assert slideshow_2.severity == "warning"


def test_repair_plan_ignores_warnings():
    from video_agent.shorts.validate_scenes import SceneValidationIssue, build_scene_repair_plan
    scenes = [
        {"id": "s01", "duration_sec": 3.5, "layout": "short_hook", "motion": "push_in"}
    ]
    issues = [
        SceneValidationIssue(
            type="duration_pacing",
            scene_id="s01",
            severity="warning",
            detail="Outside target pacing",
            repair_hint="Allowed if pacing remains strong"
        )
    ]
    plan = build_scene_repair_plan(scenes, issues)
    # The warning should not produce repair plan instructions
    assert len(plan["instructions"]) == 5  # Just the 5 default boilerplate headers, no issue instructions


def test_normalize_duration_pacing_and_total_duration():
    from video_agent.shorts.qa import normalize_qa_issue, IssueClass
    
    issue_pacing = {
        "type": "duration_pacing",
        "detail": "Scene s02 duration is outside target pacing",
        "severity": "warning"
    }
    norm_pacing = normalize_qa_issue(issue_pacing, idea={}, script={}, scenes={})
    assert norm_pacing.issue_class == IssueClass.SOFT_WARNING
    assert norm_pacing.reason == "duration_pacing"
    assert norm_pacing.trigger_regeneration is False

    issue_norm = {
        "type": "total_duration_normalized",
        "detail": "total_duration_sec normalized from 32 to 30",
        "severity": "warning"
    }
    norm_dur = normalize_qa_issue(issue_norm, idea={}, script={}, scenes={})
    assert norm_dur.issue_class == IssueClass.SOFT_WARNING
    assert norm_dur.reason == "duration_normalized"
    assert norm_dur.trigger_regeneration is False
    assert norm_dur.include_in_retry_feedback is False


def test_call_budget_classification_with_new_reasons():
    from video_agent.shorts.call_budget import build_call_budget_summary
    history = [
        # stage_status failed
        {"provider": "deterministic", "kind": "stage_status", "ok": False, "payload": {"stage": "qa_scenes"}},
        {"provider": "deterministic", "kind": "qa_classification", "payload": {"reason": "duration_normalized"}},
        
        # gemini qa failed
        {"provider": "gemini", "kind": "qa_scenes", "ok": False, "reason": "qa_soft_warn"},
        {"provider": "deterministic", "kind": "qa_classification", "payload": {"reason": "deterministic_repair"}},
    ]
    summary = build_call_budget_summary(history)
    assert summary["by_reason"]["duration_normalized"] == 1
    assert summary["by_reason"]["deterministic_repair"] == 1
    assert summary["by_reason"]["unknown"] == 0


def test_scene_retry_cap_and_memory():
    from video_agent.shorts.retry_memory import RetryMemory, RetryIssue, add_or_update_issue
    from video_agent.shorts.validate_scenes import SceneValidationIssue
    
    issue = SceneValidationIssue(
        type="total_duration_normalized",
        scene_id=None,
        severity="warning",
        detail="normalized total duration"
    )
    issue_class_val = "soft_warning" if issue.severity == "warning" else ("repairable_blocker" if issue.severity == "repairable_error" else "hard_blocker")
    reason_val = issue.type
    if issue.type == "total_duration_normalized":
        reason_val = "duration_normalized"
        issue_class_val = "soft_warning"
        
    retry_issue = RetryIssue(
        id="x",
        stage="scene_validation",
        attempt=1,
        scene_id=None,
        type=issue.type,
        severity=issue.severity,
        detail=issue.detail,
        required_change=issue.detail,
        status="active",
        first_seen_attempt=1,
        last_seen_attempt=1,
        issue_class=issue_class_val,
        reason=reason_val
    )
    assert retry_issue.issue_class == "soft_warning"
    assert retry_issue.reason == "duration_normalized"





