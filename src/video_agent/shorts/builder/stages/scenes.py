"""Scene-generation stage cluster for the Short builder.

This cluster is mutually recursive: _stage_scenes drives the _scenes_*
repair helpers, which call back into _stage_visual_rhythm/_stage_qa_scenes.
It therefore lives in one module to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from video_agent.shorts import (
    paths,
    qa,
    short_scene_builder,
    validate_scenes,
    visual_rhythm,
)
from video_agent.shorts.builder.context import BuildContext

# Backwards-compatible facade: tests and callers import/patch these via
# video_agent.shorts.short_builder.<name>.
from video_agent.shorts.builder.qa_gate import (
    HARD_SCENE_VALIDATION_TYPES,
    build_script_compression_feedback,
    check_and_apply_auto_pass,
    should_fallback_to_gemini_scene_qa,
)
from video_agent.shorts.builder.snapshots import (
    _normalized_scene_hash,
)
from video_agent.shorts.builder.types import (
    _PROCEED,
    StageResult,
    StageSignal,
)
from video_agent.shorts.manifest import write_short_status
from video_agent.shorts.retry_memory import (
    RetryIssue,
    RetryMemory,
    ScenePipelineState,
    add_or_update_issue,
    generate_cumulative_feedback,
    load_retry_memory,
    make_stable_issue_id,
    resolve_issue_by_id,
    save_retry_memory,
    suppress_issue_by_id,
)
from video_agent.storage.atomic import atomic_write_json


class _LoopAction(Enum):
    """Control signal returned by `_scenes_*` loop-body helpers.

    CONTINUE -> the caller's `while` loop does `continue`.
    BREAK    -> the caller's `while` loop does `break`.
    FALLTHROUGH -> proceed to the next block in the loop body.
    A returned StageResult instead means: bubble up out of `_stage_scenes`.
    """

    CONTINUE = "continue"
    BREAK = "break"
    FALLTHROUGH = "fallthrough"


@dataclass
class _SceneLoopState:
    """Mutable state shared across one `_stage_scenes` inner-loop iteration.

    Holds every local that the loop body reads or writes across helper
    boundaries, so the extracted `_scenes_*` helpers can mutate it in place
    rather than threading dozens of return values.
    """

    # Counters mirrored into ctx.extras at the loop boundaries.
    scenes_attempts: int = 0
    structural_attempts: int = 0
    product_attempts: int = 0
    total_regeneration_attempts: int = 0
    # Internal loop bookkeeping.
    scene_fit_failures: int = 0
    fit_failure_counted_this_attempt: bool = False
    provider_error_attempts: int = 0
    attempt_1_failed_layout_schema: bool = False
    prev_scene_hash: str | None = None
    skip_generation: bool = False
    scenes_passed: bool = False
    escalate_to_script: bool = False
    scene_collapsed: bool = False
    # Working objects.
    short_scenes: Any = None
    scenes_qa_result: Any = None
    visual_rhythm_plan: Any = None
    state: Any = None
    scenes_feedback: str = ""
    best_scene_candidate: Any = None
    best_scene_candidate_qa: Any = None
    scene_retry_memory: Any = None
    scene_memory_file: Any = None
    visual_repair_tracker: Any = None
    # Iteration-local working values reused by later blocks.
    scenes: Any = None
    structure_issues: Any = None
    structure_blocked: bool = False
    normalized_scene_issues: Any = None


def _stage_visual_rhythm(
    ctx: BuildContext,
    short_scenes: dict[str, Any],
    scenes_attempts: int,
    state: ScenePipelineState,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Stage: visual_rhythm_plan.

    Returns the possibly updated scenes document, scene list, and rhythm plan.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    short_script = ctx.extras["short_script"]
    retention_plan = ctx.extras.get("retention_plan", {})

    ctx.update_stage("visual_rhythm_plan", "in_progress")
    try:
        ctx.check_stop()
        visual_rhythm_plan = visual_rhythm.build_visual_rhythm_plan(
            long_job_dir,
            short_id,
            short_scenes,
            retention_plan,
            ctx.channel_config,
        )
        rhythm_candidate = visual_rhythm.apply_visual_rhythm_to_scenes(
            short_scenes, visual_rhythm_plan
        )
        rhythm_issues = validate_scenes.validate_scene_structure(
            rhythm_candidate.get("scenes") or [],
            scenes_doc=rhythm_candidate,
            script=short_script,
            attempt=scenes_attempts,
        )
        if validate_scenes.has_blocking_or_repairable(rhythm_issues):
            ctx.update_stage("visual_rhythm_plan", "completed", qa_verdict="WARN", discarded=True)
        else:
            short_scenes = rhythm_candidate
            state.current_scenes_version += 1
            ctx.update_stage(
                "visual_rhythm_plan",
                "completed",
                qa_verdict="PASS",
                generation_mode=visual_rhythm_plan.get("generation_mode"),
            )
        return short_scenes, short_scenes.get("scenes") or [], visual_rhythm_plan
    except Exception as exc:
        ctx.update_stage("visual_rhythm_plan", "failed", error=str(exc))
        ctx.status["status"] = "failed"
        write_short_status(long_job_dir, short_id, ctx.status)
        raise exc


def _stage_qa_scenes(
    ctx: BuildContext,
    short_scenes: dict[str, Any],
    scenes_attempts: int,
) -> tuple[dict[str, Any], list[Any]]:
    """Stage: qa_scenes.

    Runs scene QA, normalizes issues, writes the QA artifact, and updates the
    qa_scenes stage status. Retry-budget decisions remain in _stage_scenes.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    _jd = ctx.json_dir
    status = ctx.status
    short_plan = ctx.short_plan
    channel_config = ctx.channel_config
    update_stage = ctx.update_stage
    check_stop = ctx.check_stop
    _recorder = ctx.recorder
    gemini_fn = ctx.gemini_fn
    short_script = ctx.extras["short_script"]

    # --- Stage 4: QA Scenes ---
    update_stage("qa_scenes", "in_progress")
    try:
        check_stop()
        scenes_qa_result = qa.run_short_scenes_qa(
            long_job_dir,
            short_id,
            channel_config,
            gemini_fn=gemini_fn,
            attempt=scenes_attempts,
        )
        check_and_apply_auto_pass(scenes_qa_result)

        # Normalize scenes QA issues
        normalized_scene_issues = []
        for item in scenes_qa_result.get("issues") or []:
            norm = qa.normalize_qa_issue(
                item,
                idea=short_plan,
                script=short_script,
                scenes=short_scenes,
                source="gemini_scene_qa"
                if scenes_qa_result.get("provider") == "gemini"
                else "scene_validation",
            )
            normalized_scene_issues.append(norm)
        for item in scenes_qa_result.get("required_changes") or []:
            norm = qa.normalize_qa_issue(
                item,
                idea=short_plan,
                script=short_script,
                scenes=short_scenes,
                source="gemini_scene_qa"
                if scenes_qa_result.get("provider") == "gemini"
                else "scene_validation",
            )
            if not any(x.detail == norm.detail for x in normalized_scene_issues):
                normalized_scene_issues.append(norm)

        scenes_qa_result["normalized_issues"] = [n.to_dict() for n in normalized_scene_issues]

        scene_blockers = [
            n
            for n in normalized_scene_issues
            if n.issue_class in {qa.IssueClass.HARD_BLOCKER, qa.IssueClass.REPAIRABLE_BLOCKER}
        ]
        scene_warnings = [
            n for n in normalized_scene_issues if n.issue_class == qa.IssueClass.SOFT_WARNING
        ]
        [n for n in normalized_scene_issues if n.issue_class == qa.IssueClass.STALE_OR_SUPPRESSED]

        if not scene_blockers:
            scenes_qa_result["verdict"] = "WARN" if scene_warnings else "PASS"
        else:
            scenes_qa_result["verdict"] = "FAIL"

        verdict = scenes_qa_result.get("verdict", "FAIL")
        atomic_write_json(_jd / paths.SHORT_SCENES_QA_FILE, scenes_qa_result)
        update_stage(
            "qa_scenes",
            "completed" if verdict in ("PASS", "WARN") else "failed",
            qa_verdict=verdict,
        )

        # Record classification and wrong context suppression for scene QA
        raw_gemini_verdict = scenes_qa_result.get("verdict")
        if raw_gemini_verdict == "FAIL" or verdict == "FAIL":
            classification_reason = "qa_hard_fail"
            if not scene_blockers:
                classification_reason = "qa_soft_warn"
                has_wrong_context = any(
                    n.reason == "wrong_context_five_errors_rule" for n in normalized_scene_issues
                )
                has_noncanonical = any(
                    n.reason == "noncanonical_count_inference" for n in normalized_scene_issues
                )
                if has_noncanonical:
                    classification_reason = "noncanonical_count_inference"
                elif has_wrong_context:
                    classification_reason = "wrong_context_suppressed"

            _recorder.record_event(
                "deterministic",
                "qa_classification",
                {
                    "reason": classification_reason,
                },
                ok=True,
            )
        for norm in normalized_scene_issues:
            if norm.reason == "wrong_context_five_errors_rule":
                _recorder.record_event(
                    "deterministic",
                    "wrong_context_suppressed",
                    {
                        "reason": "wrong_context_suppressed",
                        "detail": norm.detail,
                    },
                    ok=False,
                )
            elif norm.reason == "noncanonical_count_inference":
                _recorder.record_event(
                    "deterministic",
                    "noncanonical_count_inference",
                    {
                        "reason": "noncanonical_count_inference",
                        "detail": norm.detail,
                    },
                    ok=False,
                )
    except Exception as exc:
        update_stage("qa_scenes", "failed")
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    return scenes_qa_result, normalized_scene_issues


def _scenes_generate_and_normalize(ctx, loop):
    """Generate scenes, apply deterministic repairs + visual rhythm, validate.

    Covers Stage-3 generation (with provider/generic exception handling),
    retry-collapse protection, post-generation duration/hook repairs, the
    visual-rhythm pass, payoff-layout repair, scene-structure validation, the
    deterministic scene-fit repair, and visual_only_unreadable repair.

    Returns a `_LoopAction` (CONTINUE / BREAK / FALLTHROUGH) or a `StageResult`
    to bubble up out of `_stage_scenes`. Mutates `loop` in place.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    _jd = ctx.json_dir
    status = ctx.status
    plan_for_prompt = ctx.plan_for_prompt
    channel_config = ctx.channel_config
    update_stage = ctx.update_stage
    check_stop = ctx.check_stop
    _recorder = ctx.recorder
    llm_fn = ctx.llm_fn
    max_regen = ctx.max_regen
    max_chatgpt_provider_retries = ctx.max_chatgpt_provider_retries

    short_script = ctx.extras["short_script"]
    retention_plan = ctx.extras.get("retention_plan", {})
    spoken_humanization = ctx.extras["spoken_humanization"]

    state = loop.state
    scene_retry_memory = loop.scene_retry_memory
    scene_memory_file = loop.scene_memory_file
    scenes_attempts = loop.scenes_attempts

    # --- Stage 3: Scenes ---
    try:
        if not loop.skip_generation:
            update_stage("scenes", "in_progress")
            check_stop()
            loop.short_scenes = short_scene_builder.build_short_scenes(
                long_job_dir,
                plan_for_prompt,
                short_script,
                channel_config,
                llm_fn,
                retention_plan=retention_plan,
                spoken_humanization=spoken_humanization,
                feedback=loop.scenes_feedback,
                attempt=scenes_attempts,
            )
            update_stage("scenes", "completed")
        else:
            loop.skip_generation = False
            update_stage("scenes", "completed")

        short_scenes = loop.short_scenes

        # Deterministic overlay cleanup before any validation/QA: strip "N." list
        # numbering from on_screen_text (forbidden in polished Shorts; LLM keeps
        # emitting it and bounded regen does not reliably fix it).
        validate_scenes.strip_numbered_on_screen_text(short_scenes.get("scenes") or [])

        state.current_scenes_version += 1
        state.latest_scene_validation_ok = False
        state.latest_scene_validation_version = None
        state.latest_scene_qa_ok = False
        state.latest_scene_qa_version = None
        state.latest_audio_tail_ok = False
        state.latest_audio_tail_version = None

        # Spec §8: retry-collapse protection. If a regeneration produced
        # the same normalized scenes as the previous attempt, the
        # generator is stuck — stop looping. Accept with WARN when the
        # output is renderable + safe, otherwise let the loop end and
        # report a clear failure.
        cur_scene_hash = _normalized_scene_hash(short_scenes)
        if loop.prev_scene_hash is not None and cur_scene_hash == loop.prev_scene_hash:
            # Run deterministic repairs first so they are present in final scenes
            scenes = short_scenes.get("scenes") or []
            for scene in scenes:
                validate_scenes.repair_scene_duration_if_possible(scene)
            validate_scenes.repair_weak_hook_motion(scenes)

            collapse_issues = validate_scenes.validate_scene_structure(
                scenes,
                scenes_doc=short_scenes,
                script=short_script,
                attempt=scenes_attempts,
            )

            # Also check for active hard blockers in retry memory
            active_memory_blockers = [
                issue
                for issue in scene_retry_memory.active_issues.values()
                if issue.issue_class in {"hard_blocker", "repairable_blocker"}
                and issue.type != "slideshow_risk"
            ]
            active_memory_warnings = [
                issue
                for issue in scene_retry_memory.active_issues.values()
                if issue.issue_class == "soft_warning" or issue.type == "slideshow_risk"
            ]

            collapse_blockers = [
                i
                for i in collapse_issues
                if i.severity in ("blocking_error", "repairable_error")
                and i.type != "slideshow_risk"
            ]
            collapse_warnings = [
                i for i in collapse_issues if i.severity == "warning" or i.type == "slideshow_risk"
            ]

            remaining_blockers = active_memory_blockers + [
                {
                    "type": b.type,
                    "scene_id": b.scene_id,
                    "detail": b.detail,
                    "severity": b.severity,
                    "issue_class": "repairable_blocker"
                    if b.severity == "repairable_error"
                    else "hard_blocker",
                }
                for b in collapse_blockers
            ]
            remaining_warnings = active_memory_warnings + [
                {
                    "type": w.type,
                    "scene_id": w.scene_id,
                    "detail": w.detail,
                    "severity": w.severity,
                    "issue_class": "soft_warning",
                }
                for w in collapse_warnings
            ]

            renderable = len(remaining_blockers) == 0
            decision = "continued_with_warn" if renderable else "failed_hard_blocker"

            max_limit = min(2, max_regen + 1)
            max_allowed_attempts = max_limit
            if loop.attempt_1_failed_layout_schema and max_regen >= 2:
                max_allowed_attempts = 3

            decision_summary = {
                "stage": "qa_scenes",
                "attempts_used": scenes_attempts,
                "max_attempts": max_allowed_attempts,
                "decision": decision,
                "renderable": renderable,
                "remaining_blockers": [
                    b.to_dict() if hasattr(b, "to_dict") else b for b in remaining_blockers
                ],
                "remaining_warnings": [
                    w.to_dict() if hasattr(w, "to_dict") else w for w in remaining_warnings
                ]
                + [
                    {
                        "type": "retry_collapse",
                        "detail": "Identical scene output across retries; stopping loop.",
                    }
                ],
                "continued_to_render": renderable,
            }
            atomic_write_json(_jd / paths.SHORT_QA_DECISION_SUMMARY_FILE, decision_summary)

            _recorder.record_event(
                "deterministic",
                "retry_collapse",
                {
                    "verdict": "WARN" if renderable else "FAIL",
                    "retry_reason": "scene_validation_fail",
                    "retry_scope": "scenes_only",
                    "attempt": scenes_attempts,
                    "renderable": renderable,
                    "detail": "Identical scene output across retries; stopping loop.",
                    "reason": "retry_collapse",
                },
                ok=renderable,
            )

            if renderable:
                loop.best_scene_candidate = dict(short_scenes)
                loop.best_scene_candidate_qa = {"verdict": "WARN", "collapsed": True}
                loop.scenes_qa_result = {"verdict": "WARN", "collapsed": True, "qa_pass": True}
                loop.scenes_passed = True
                state.latest_scene_validation_ok = True
                state.latest_scene_validation_version = state.current_scenes_version
                state.latest_scene_qa_ok = True
                state.latest_scene_qa_version = state.current_scenes_version
                atomic_write_json(_jd / paths.SHORT_SCENES_FILE, short_scenes)
                update_stage("qa_scenes", "completed", qa_verdict="WARN")
                loop.scene_collapsed = True
                save_retry_memory(scene_retry_memory, scene_memory_file)
                return _LoopAction.BREAK
            else:
                loop.scenes_passed = False
                update_stage("qa_scenes", "failed", qa_verdict="FAIL")
                status.update(
                    {
                        "status": "needs_review",
                        "rendered": False,
                        "uploaded": False,
                        "youtube_url": "",
                        "requires_user_review": True,
                        "qa_verdict": "FAIL",
                        "failure_stage": "qa_scenes",
                        "failure_reason": f"Scene QA retry collapse failed hard blocker: {[b.get('detail') if isinstance(b, dict) else getattr(b, 'detail', '') for b in remaining_blockers]}",
                        "regeneration_attempts": loop.total_regeneration_attempts,
                    }
                )
                write_short_status(long_job_dir, short_id, status)
                save_retry_memory(scene_retry_memory, scene_memory_file)
                return StageResult(StageSignal.PROCEED, returns=status)
        loop.prev_scene_hash = cur_scene_hash
    except short_scene_builder.ChatGPTProviderError as exc:
        # Provider/browser failure — NOT a creative scene-QA failure.
        # The recovery-wrapped llm_fn already cleared cookies + reopened a
        # fresh temp chat; here we just keep it off the creative budget and
        # surface a non-QA failure if the provider keeps erroring.
        loop.provider_error_attempts += 1
        snippet = getattr(exc, "snippet", "")
        failure_kind = getattr(exc, "failure_kind", "chatgpt_provider_error")
        update_stage("scenes", "failed", error=failure_kind)
        _recorder.record_event(
            "chatgpt",
            "provider_error",
            {
                "event": failure_kind,
                "stage": "scene_generation",
                "action": "clear_browser_state_and_retry",
                "attempt": loop.provider_error_attempts,
                "error_snippet": snippet,
            },
            ok=False,
        )
        atomic_write_json(
            _jd / paths.SHORT_FAILURE_REPORT_FILE,
            {
                "stage": "scene_generation",
                "type": failure_kind,
                "attempt": loop.provider_error_attempts,
                "detail": str(exc) or "ChatGPT returned provider-error text instead of scene JSON.",
                "error_snippet": snippet,
            },
        )
        if loop.provider_error_attempts > max_chatgpt_provider_retries:
            for s in status["stages"]:
                if s["status"] == "pending":
                    s["status"] = "skipped"
            status.update(
                {
                    "status": "needs_review",
                    "rendered": False,
                    "uploaded": False,
                    "youtube_url": "",
                    "requires_user_review": True,
                    "qa_verdict": "PROVIDER_ERROR",
                    "failure_kind": failure_kind,
                    "failure_message": (
                        "ChatGPT refused the scenes JSON for response size even "
                        "after a compact-output retry. This is not a scene QA "
                        "failure; regenerate the short."
                        if failure_kind == "chatgpt_size_refusal"
                        else "ChatGPT provider error persisted after browser/session "
                        "cleanup and retry. This is not a scene QA failure."
                    ),
                    "regeneration_attempts": loop.total_regeneration_attempts,
                }
            )
            write_short_status(long_job_dir, short_id, status)
            return StageResult(StageSignal.PROCEED, returns=status)
        # Do NOT consume the scenes/creative budget for a provider error.
        loop.scenes_attempts -= 1
        return _LoopAction.CONTINUE
    except Exception as exc:
        update_stage("scenes", "failed")
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    short_scenes = loop.short_scenes
    scenes = short_scenes.get("scenes") or []
    for scene in scenes:
        duration_repair = validate_scenes.repair_scene_duration_if_possible(scene)
        if (
            duration_repair == "must_split_or_compress"
            and not loop.fit_failure_counted_this_attempt
        ):
            # This is the earliest signal that the scene narration cannot fit
            # its layout cap. Later deterministic repairs may rewrite the
            # candidate into a different structural failure, but repeated
            # original fit failures should still trigger script compression.
            loop.scene_fit_failures += 1
            loop.fit_failure_counted_this_attempt = True

    # Deterministic repair: weak hook motion (spec §10.1)
    if validate_scenes.repair_weak_hook_motion(scenes):
        state.current_scenes_version += 1
        state.latest_scene_validation_ok = False
        state.latest_scene_validation_version = None

    short_scenes, scenes, loop.visual_rhythm_plan = _stage_visual_rhythm(
        ctx,
        short_scenes,
        scenes_attempts,
        state,
    )
    loop.short_scenes = short_scenes

    if validate_scenes.repair_five_error_bread_payoff_layout(scenes, short_script):
        short_scenes["scenes"] = scenes
        short_scenes["total_duration_sec"] = round(
            sum(float(scene.get("duration_sec") or 0.0) for scene in scenes),
            1,
        )
        state.current_scenes_version += 1
        state.latest_scene_validation_ok = False
        state.latest_scene_validation_version = None
        _recorder.record_event(
            "deterministic",
            "payoff_layout_repair",
            {"attempt": scenes_attempts, "layout": "graphic_checklist"},
            ok=True,
        )

    if validate_scenes.repair_missing_graphic_checklist_scene(scenes, short_script):
        short_scenes["scenes"] = scenes
        state.current_scenes_version += 1
        state.latest_scene_validation_ok = False
        state.latest_scene_validation_version = None
        _recorder.record_event(
            "deterministic",
            "missing_graphic_checklist_repair",
            {"attempt": scenes_attempts, "layout": "graphic_checklist"},
            ok=True,
        )

    # Demote the lowest-value excess graphics back to realistic scenes when a
    # normal Short is over the 2-graphic cap. Prevents the over-cap repairable
    # error from looping to a hard blocker when the LLM keeps re-emitting graphics.
    if validate_scenes.repair_excess_graphic_scenes(scenes, short_script):
        short_scenes["scenes"] = scenes
        state.current_scenes_version += 1
        state.latest_scene_validation_ok = False
        state.latest_scene_validation_version = None
        _recorder.record_event(
            "deterministic",
            "excess_graphic_demote_repair",
            {"attempt": scenes_attempts},
            ok=True,
        )

    # Repair resumed artifacts created by the older CTA-relocation path even
    # when no narration-fit error remains to trigger the fit-repair block.
    if validate_scenes.normalize_pre_cta_scene_metadata(scenes):
        short_scenes["scenes"] = scenes
        state.current_scenes_version += 1
        state.latest_scene_validation_ok = False
        state.latest_scene_validation_version = None
        _recorder.record_event(
            "deterministic",
            "cta_metadata_normalized",
            {"attempt": scenes_attempts},
            ok=True,
        )

    structure_issues = validate_scenes.validate_scene_structure(
        scenes,
        scenes_doc=short_scenes,
        script=short_script,
        attempt=scenes_attempts,
    )

    # Auto-repair duration/narration-fit if it's the only class of hard issues remaining
    hard_errors = [
        i
        for i in structure_issues
        if i.severity in ("blocking_error", "repairable_error")
        and i.type in HARD_SCENE_VALIDATION_TYPES
    ]
    if (
        any(i.type == "scene_narration_fit" for i in hard_errors)
        and not loop.fit_failure_counted_this_attempt
    ):
        # Count the original LLM scene fit failure before deterministic repair.
        # Repair may split/condense the scene and remove the fit issue while
        # still leaving an invalid scene plan; repeated originals should still
        # escalate to script compression instead of burning scene retries.
        # MUST honor the per-attempt guard like the other two counting sites:
        # unguarded, a single attempt was double-counted (build-time must_split
        # signal + this validation hit) and instantly tripped the >=2
        # escalation, so EVERY scenes roll burned a script-compression attempt
        # instead of using its scene-repair budget (bug-510, run-10).
        loop.scene_fit_failures += 1
        loop.fit_failure_counted_this_attempt = True
    # Run deterministic fit-repair whenever ANY duration/narration-fit
    # hard error exists — even alongside other hard errors (e.g.
    # missing_item_coverage). Repairing the fixable fit issues here keeps
    # them from blocking the run; remaining errors still drive regen.
    if any(i.type in ("duration_cap", "scene_narration_fit") for i in hard_errors):
        repaired_any = False
        # First try simple per-scene duration clamp/extend (handles duration_cap).
        for issue in hard_errors:
            if issue.scene_id:
                scene_to_fix = next(
                    (
                        s
                        for s in scenes
                        if str(s.get("id") or s.get("scene_id") or "") == issue.scene_id
                    ),
                    None,
                )
                if scene_to_fix:
                    res = validate_scenes.repair_scene_duration_if_possible(scene_to_fix)
                    if res in ("auto_shortened", "auto_extended", "auto_shortened_cta"):
                        repaired_any = True
        # Then run deterministic scene-fit repair (extend -> split ->
        # micro-condense) for any remaining narration overflow, BEFORE
        # spending an LLM regeneration. No regen_fn: an unfixable scene
        # falls through to the existing regeneration path below.
        fit_result = validate_scenes.deterministic_scene_fit_repair(scenes, short_script)
        if any(mode != "regen_required" for mode in fit_result["modes"]):
            repaired_any = True
            short_scenes["scenes"] = fit_result["scenes"]
            scenes = fit_result["scenes"]
            short_scenes["total_duration_sec"] = fit_result["total_duration_sec"]
        for entry in fit_result["logs"]:
            _recorder.record_event(
                "deterministic",
                "scene_narration_fit_repair",
                entry,
                ok=entry["repair_mode_attempted"] != "regen_required",
            )
        if repaired_any:
            state.current_scenes_version += 1
            state.latest_scene_validation_ok = False
            state.latest_scene_validation_version = None
            # Re-run validation with repaired durations
            structure_issues = validate_scenes.validate_scene_structure(
                scenes,
                scenes_doc=short_scenes,
                script=short_script,
                attempt=scenes_attempts,
            )

    # A footage-led tip can inherit a 3+ item card payload from generation or
    # a narration split. That makes it count as another checklist and can leave
    # slideshow_risk as the only blocker until the outer retry budget expires.
    # Normalize only the exact validator target, preserving spoken content and
    # source coverage, then revalidate before spending another LLM call.
    slideshow_issues = [
        issue
        for issue in structure_issues
        if issue.type == "slideshow_risk" and issue.severity == "repairable_error"
    ]
    if validate_scenes.repair_slideshow_density(scenes, slideshow_issues):
        short_scenes["scenes"] = scenes
        short_scenes["total_duration_sec"] = round(
            sum(float(scene.get("duration_sec") or 0.0) for scene in scenes),
            1,
        )
        state.current_scenes_version += 1
        state.latest_scene_validation_ok = False
        state.latest_scene_validation_version = None
        _recorder.record_event(
            "deterministic",
            "slideshow_density_repair",
            {
                "attempt": scenes_attempts,
                "scene_ids": [issue.scene_id for issue in slideshow_issues if issue.scene_id],
            },
            ok=True,
        )
        structure_issues = validate_scenes.validate_scene_structure(
            scenes,
            scenes_doc=short_scenes,
            script=short_script,
            attempt=scenes_attempts,
        )

    atomic_write_json(_jd / paths.SHORT_SCENES_FILE, short_scenes)

    # Intercept visual_only_unreadable for deterministic repair and downgrade logic
    visual_issues = [i for i in structure_issues if i.type == "visual_only_unreadable"]
    did_visual_repair = False
    for vi in visual_issues:
        import re

        m = re.match(r"Item (\w+) appears", str(vi.detail))
        if m:
            item_id = m.group(1)
            tracker = loop.visual_repair_tracker.get(item_id, {"repairs": 0, "regens": 0})

            if tracker["repairs"] < 1:
                # Attempt deterministic repair
                _idea_items = (
                    short_script.get("idea_items")
                    or short_script.get("points")
                    or short_script.get("checklist")
                    or []
                )
                item_str = item_id
                for it in _idea_items:
                    if (
                        isinstance(it, dict)
                        and str(it.get("item_id") or it.get("id") or "") == item_id
                    ):
                        item_str = it
                        break

                if validate_scenes.repair_visual_only_unreadable(scenes, item_str):
                    did_visual_repair = True
                    tracker["repairs"] += 1
                    loop.visual_repair_tracker[item_id] = tracker

                    _recorder.record_event(
                        "deterministic",
                        "qa_classification",
                        {"reason": "deterministic_repair", "item_id": item_id},
                        ok=True,
                    )
            else:
                # We already tried deterministic repair. This is a ChatGPT regen attempt.
                tracker["regens"] += 1
                loop.visual_repair_tracker[item_id] = tracker
                if tracker["regens"] > 1:
                    # Already regenerated once, give up and downgrade
                    vi.severity = "warning"
                    vi.detail = f"(Downgraded) {vi.detail}"
                    _recorder.record_event(
                        "deterministic",
                        "qa_classification",
                        {"reason": "visual_repair_downgraded", "item_id": item_id},
                        ok=True,
                    )

    if did_visual_repair:
        atomic_write_json(_jd / paths.SHORT_SCENES_FILE, short_scenes)
        loop.skip_generation = True
        return _LoopAction.CONTINUE

    loop.short_scenes = short_scenes
    loop.scenes = scenes
    loop.structure_issues = structure_issues
    return _LoopAction.FALLTHROUGH


def _scenes_run_structure_validation(ctx, loop):
    """Track narration-fit failures, compute structure_blocked, persist the
    scene-validation doc, and update validation-ok bookkeeping.

    Always returns `_LoopAction.FALLTHROUGH`. Mutates `loop` in place.
    """
    _jd = ctx.json_dir
    _recorder = ctx.recorder
    scenes_attempts = loop.scenes_attempts
    state = loop.state
    scene_retry_memory = loop.scene_retry_memory
    structure_issues = loop.structure_issues

    # Check for scene_narration_fit failures
    has_fit_failure = any(
        issue.type == "scene_narration_fit"
        and issue.severity in ("blocking_error", "repairable_error")
        for issue in structure_issues
    )
    if has_fit_failure:
        if not loop.fit_failure_counted_this_attempt:
            loop.scene_fit_failures += 1
            loop.fit_failure_counted_this_attempt = True

    structure_blocked = validate_scenes.has_blocking_or_repairable(
        structure_issues
    ) and not should_fallback_to_gemini_scene_qa(structure_issues)

    structure_doc = {
        "attempt": scenes_attempts,
        "verdict": "FAIL" if structure_blocked else "PASS",
        "issues": validate_scenes.issues_to_dicts(structure_issues),
    }
    atomic_write_json(_jd / paths.SHORT_SCENE_STRUCTURE_FILE, structure_doc)
    _recorder.record_event(
        "deterministic",
        "scene_validation",
        structure_doc,
        ok=structure_doc["verdict"] == "PASS",
    )

    if not structure_blocked:
        state.latest_scene_validation_ok = True
        state.latest_scene_validation_version = state.current_scenes_version
        if state.latest_scene_qa_ok:
            state.latest_scene_qa_version = state.current_scenes_version
        for issue_id in list(scene_retry_memory.active_issues.keys()):
            issue = scene_retry_memory.active_issues[issue_id]
            if issue.stage == "scene_validation":
                resolve_issue_by_id(scene_retry_memory, issue_id)
    else:
        state.latest_scene_validation_ok = False
        state.latest_scene_validation_version = None

    loop.structure_blocked = structure_blocked
    return _LoopAction.FALLTHROUGH


def _scenes_structural_repair(ctx, loop):
    """Handle the deterministic structural-repair branch (structure_blocked).

    Caps regeneration, downgrades-to-WARN-or-fails when stopping, builds the
    repair plan, records retry-memory issues, and either escalates to script
    compression or feeds back for another scene regeneration.

    Returns a `_LoopAction` or a `StageResult`. Mutates `loop` in place.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    _jd = ctx.json_dir
    status = ctx.status
    short_plan = ctx.short_plan
    update_stage = ctx.update_stage
    _recorder = ctx.recorder
    max_regen = ctx.max_regen
    max_structural_attempts = ctx.max_structural_attempts

    short_script = ctx.extras["short_script"]

    state = loop.state
    scene_retry_memory = loop.scene_retry_memory
    scene_memory_file = loop.scene_memory_file
    scenes_attempts = loop.scenes_attempts
    scenes = loop.scenes
    structure_issues = loop.structure_issues

    if not loop.structure_blocked:
        return _LoopAction.FALLTHROUGH

    if scenes_attempts == 1:
        loop.attempt_1_failed_layout_schema = True

    # Check for scene regeneration cap
    stop_scene_retries = False
    max_limit = min(2, max_regen + 1)
    if scenes_attempts >= max_limit:
        if scenes_attempts == 2 and loop.attempt_1_failed_layout_schema and max_regen >= 2:
            pass
        else:
            stop_scene_retries = True
    # Script regeneration resets scenes_attempts to 1. If the combined outer
    # budget is already exhausted, waiting for local attempt 2 is impossible;
    # enter the same terminal classifier now. It will WARN-and-continue when
    # only slideshow_risk remains and still FAIL for every real blocker.
    if loop.total_regeneration_attempts >= max_regen:
        stop_scene_retries = True

    if stop_scene_retries:
        active_memory_blockers = [
            issue
            for issue in scene_retry_memory.active_issues.values()
            if issue.issue_class in {"hard_blocker", "repairable_blocker"}
            and issue.type != "slideshow_risk"
        ]
        active_memory_warnings = [
            issue
            for issue in scene_retry_memory.active_issues.values()
            if issue.issue_class == "soft_warning" or issue.type == "slideshow_risk"
        ]
        remaining_blockers = [
            i
            for i in structure_issues
            if i.severity in ("blocking_error", "repairable_error") and i.type != "slideshow_risk"
        ] + active_memory_blockers
        remaining_warnings = [
            i for i in structure_issues if i.severity == "warning" or i.type == "slideshow_risk"
        ] + active_memory_warnings
        decision = "continued_with_warn" if not remaining_blockers else "failed_hard_blocker"
        renderable = not remaining_blockers

        max_allowed_attempts = max_limit
        if loop.attempt_1_failed_layout_schema and max_regen >= 2:
            max_allowed_attempts = 3
        decision_summary = {
            "stage": "qa_scenes",
            "attempts_used": scenes_attempts,
            "max_attempts": max_allowed_attempts,
            "decision": decision,
            "renderable": renderable,
            "remaining_blockers": [
                b.to_dict() if hasattr(b, "to_dict") else b for b in remaining_blockers
            ],
            "remaining_warnings": [
                w.to_dict() if hasattr(w, "to_dict") else w for w in remaining_warnings
            ],
            "continued_to_render": decision == "continued_with_warn",
        }
        atomic_write_json(_jd / paths.SHORT_QA_DECISION_SUMMARY_FILE, decision_summary)

        if not remaining_blockers:
            # Downgrade slideshow_risk and warnings to WARN and continue
            loop.structure_blocked = False
            loop.scenes_passed = True
            state.latest_scene_validation_ok = True
            state.latest_scene_validation_version = state.current_scenes_version
            loop.scenes_qa_result = {
                "verdict": "WARN",
                "issues": validate_scenes.issues_to_dicts(structure_issues),
                "required_changes": [],
                "warnings": [i.detail for i in structure_issues],
                "provider": "deterministic",
            }
            atomic_write_json(_jd / paths.SHORT_SCENES_QA_FILE, loop.scenes_qa_result)
            update_stage("qa_scenes", "completed", qa_verdict="WARN")

            _recorder.record_event(
                "deterministic",
                "qa_classification",
                {
                    "reason": "qa_soft_warn",
                },
                ok=True,
            )
            save_retry_memory(scene_retry_memory, scene_memory_file)
            return _LoopAction.BREAK
        else:
            loop.scenes_passed = False
            update_stage("qa_scenes", "failed", qa_verdict="FAIL")
            status.update(
                {
                    "status": "needs_review",
                    "rendered": False,
                    "uploaded": False,
                    "youtube_url": "",
                    "requires_user_review": True,
                    "qa_verdict": "FAIL",
                    "failure_stage": "qa_scenes",
                    "failure_reason": f"Scene validation failed hard blocker: {[b.get('detail') if isinstance(b, dict) else getattr(b, 'detail', '') for b in remaining_blockers]}",
                }
            )
            write_short_status(long_job_dir, short_id, status)
            save_retry_memory(scene_retry_memory, scene_memory_file)

            _recorder.record_event(
                "deterministic",
                "qa_classification",
                {
                    "reason": "scene_validation_fail",
                },
                ok=True,
            )
            return StageResult(StageSignal.PROCEED, returns=status)

    repair_plan = validate_scenes.build_scene_repair_plan(
        scenes,
        structure_issues,
        script=short_script,
    )
    atomic_write_json(
        _jd / paths.SHORT_SCENE_REPAIR_FILE,
        {
            "attempt": scenes_attempts,
            **repair_plan,
        },
    )
    _recorder.record_event(
        "deterministic",
        "scene_repair_plan",
        {"attempt": scenes_attempts, **repair_plan},
    )
    loop.scenes_qa_result = {
        "verdict": "FAIL",
        "issues": validate_scenes.issues_to_dicts(structure_issues),
        "required_changes": repair_plan["instructions"],
        "warnings": [issue.detail for issue in structure_issues if issue.severity == "warning"],
        "provider": "deterministic",
        "repair_plan": repair_plan,
    }
    atomic_write_json(_jd / paths.SHORT_SCENES_QA_FILE, loop.scenes_qa_result)
    update_stage("qa_scenes", "failed", qa_verdict="FAIL")

    # Log qa_classification event
    classification_reason = "scene_validation_fail"
    if any(i.type == "slideshow_risk" for i in structure_issues):
        if scenes_attempts >= 2:
            classification_reason = "retry_collapse"
        else:
            classification_reason = "scene_validation_fail"
    elif any(i.type == "duration_pacing" for i in structure_issues):
        classification_reason = "qa_soft_warn"
    elif any(i.type == "total_duration_normalized" for i in structure_issues):
        classification_reason = "duration_normalized"

    _recorder.record_event(
        "deterministic",
        "qa_classification",
        {
            "reason": classification_reason,
        },
        ok=True,
    )

    # Track deterministic issues in retry memory
    active_validation_ids = set()
    for issue in structure_issues:
        issue_id = make_stable_issue_id(
            "scene_validation", issue.scene_id, issue.type, issue.detail
        )
        active_validation_ids.add(issue_id)
        required_change = (
            "\n".join(issue.instructions)
            if getattr(issue, "instructions", None)
            else (issue.repair_hint or issue.detail)
        )

        issue_class_val = (
            "soft_warning"
            if issue.severity == "warning"
            else ("repairable_blocker" if issue.severity == "repairable_error" else "hard_blocker")
        )
        reason_val = issue.type
        if issue.type == "total_duration_normalized":
            reason_val = "duration_normalized"
            issue_class_val = "soft_warning"
        elif issue.type == "duration_pacing":
            reason_val = "duration_pacing"
            issue_class_val = "soft_warning"
        elif issue.type == "weak_hook_motion":
            reason_val = "weak_hook_motion"
            issue_class_val = "soft_warning"

        retry_issue = RetryIssue(
            id=issue_id,
            stage="scene_validation",
            attempt=scenes_attempts,
            scene_id=issue.scene_id,
            type=issue.type,
            severity=issue.severity,
            detail=issue.detail,
            required_change=required_change,
            status="active",
            first_seen_attempt=scenes_attempts,
            last_seen_attempt=scenes_attempts,
            issue_class=issue_class_val,
            reason=reason_val,
        )
        add_or_update_issue(scene_retry_memory, retry_issue)

    # Resolve issues no longer present
    for issue_id in list(scene_retry_memory.active_issues.keys()):
        issue = scene_retry_memory.active_issues[issue_id]
        if issue.stage == "scene_validation" and issue_id not in active_validation_ids:
            resolve_issue_by_id(scene_retry_memory, issue_id)

    # Suppress visual_only_unreadable false positives
    for issue_id in list(scene_retry_memory.active_issues.keys()):
        issue = scene_retry_memory.active_issues[issue_id]
        if issue.type == "visual_only_unreadable" and issue.scene_id:
            scene = next(
                (
                    s
                    for s in scenes
                    if str(s.get("id") or s.get("scene_id") or "") == issue.scene_id
                ),
                None,
            )
            if scene:
                covers = scene.get("covers_items") or []
                narration = str(scene.get("narration") or "").lower()
                suppress = False
                for cid in covers:
                    if (
                        str(cid) in narration
                        or "cinco" in narration
                        or "cuatro" in narration
                        or "tres" in narration
                        or "dos" in narration
                        or "uno" in narration
                    ):
                        suppress = True
                if suppress:
                    suppress_issue_by_id(scene_retry_memory, issue_id)

    loop.structural_attempts += 1
    status["qa_scenes_structural_attempts"] = loop.structural_attempts
    write_short_status(long_job_dir, short_id, status)
    if loop.scene_fit_failures >= 2:
        loop.escalate_to_script = True
        return _LoopAction.BREAK
    if loop.structural_attempts >= max_structural_attempts:
        save_retry_memory(scene_retry_memory, scene_memory_file)
        from video_agent.shorts.idea_preservation import derive_idea_items

        exact_mapping_items = short_plan.get("idea_items") or derive_idea_items(short_plan)
        exact_mapping_context = (
            "\n".join(
                f"{i + 1}. {item.get('label') or item.get('topic') or item}"
                for i, item in enumerate(exact_mapping_items)
            )
            if exact_mapping_items
            else ""
        )
        loop.scenes_feedback = generate_cumulative_feedback(
            scene_retry_memory,
            scenes_attempts + 1,
            candidate_summary=f"Scenes attempt {scenes_attempts} failed deterministic validation.",
            exact_mapping_context=exact_mapping_context,
        )
        return _LoopAction.BREAK

    save_retry_memory(scene_retry_memory, scene_memory_file)
    from video_agent.shorts.idea_preservation import derive_idea_items

    exact_mapping_items = short_plan.get("idea_items") or derive_idea_items(short_plan)
    exact_mapping_context = (
        "\n".join(
            f"{i + 1}. {item.get('label') or item.get('topic') or item}"
            for i, item in enumerate(exact_mapping_items)
        )
        if exact_mapping_items
        else ""
    )
    loop.scenes_feedback = generate_cumulative_feedback(
        scene_retry_memory,
        scenes_attempts + 1,
        candidate_summary=f"Scenes attempt {scenes_attempts} failed deterministic validation.",
        exact_mapping_context=exact_mapping_context,
    )
    return _LoopAction.CONTINUE


def _scenes_run_qa(ctx, loop):
    """Run Gemini scene QA, record its verdict, and enforce the QA hard gate.

    On PASS/WARN it records the best candidate and stops the loop; otherwise it
    tracks QA issues in retry memory, caps regeneration, and either downgrades
    to WARN, fails hard, or continues for another product-quality repair.

    Returns a `_LoopAction` or a `StageResult`. Mutates `loop` in place and
    stores the normalized QA issues on `loop.normalized_scene_issues`.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    _jd = ctx.json_dir
    status = ctx.status
    update_stage = ctx.update_stage
    max_regen = ctx.max_regen

    state = loop.state
    scene_retry_memory = loop.scene_retry_memory
    scene_memory_file = loop.scene_memory_file
    scenes_attempts = loop.scenes_attempts
    short_scenes = loop.short_scenes

    scenes_qa_result, normalized_scene_issues = _stage_qa_scenes(
        ctx,
        short_scenes,
        scenes_attempts,
    )
    loop.scenes_qa_result = scenes_qa_result
    loop.normalized_scene_issues = normalized_scene_issues

    qa_pass = scenes_qa_result.get("verdict") in ("PASS", "WARN")
    scenes_qa_result["qa_pass"] = qa_pass
    scenes_qa_result["provider_call_ok"] = bool(
        scenes_qa_result.get("provider_call_ok")
        or scenes_qa_result.get("provider") in {"gemini", "rule_based"}
    )

    if qa_pass:
        for issue_id in list(scene_retry_memory.active_issues.keys()):
            issue = scene_retry_memory.active_issues[issue_id]
            if issue.stage == "scene_qa":
                resolve_issue_by_id(scene_retry_memory, issue_id)
        loop.best_scene_candidate = dict(short_scenes)
        loop.best_scene_candidate_qa = dict(scenes_qa_result)
        loop.scenes_passed = True

        state.latest_scene_qa_ok = True
        state.latest_scene_qa_version = state.current_scenes_version

        max_limit = min(2, max_regen + 1)
        max_allowed_attempts = max_limit
        if loop.attempt_1_failed_layout_schema and max_regen >= 2:
            max_allowed_attempts = 3

        scene_warnings = [
            n for n in normalized_scene_issues if n.issue_class == qa.IssueClass.SOFT_WARNING
        ]
        decision = "continued_with_warn" if scenes_qa_result.get("verdict") == "WARN" else "passed"

        decision_summary = {
            "stage": "qa_scenes",
            "attempts_used": scenes_attempts,
            "max_attempts": max_allowed_attempts,
            "decision": decision,
            "renderable": True,
            "remaining_blockers": [],
            "remaining_warnings": [
                w.to_dict() if hasattr(w, "to_dict") else w for w in scene_warnings
            ],
            "continued_to_render": True,
        }
        atomic_write_json(_jd / paths.SHORT_QA_DECISION_SUMMARY_FILE, decision_summary)

        save_retry_memory(scene_retry_memory, scene_memory_file)
        return _LoopAction.BREAK

    loop.best_scene_candidate = dict(short_scenes)
    loop.best_scene_candidate_qa = dict(scenes_qa_result)
    loop.product_attempts += 1
    status["qa_scenes_product_attempts"] = loop.product_attempts
    write_short_status(long_job_dir, short_id, status)

    # Track Gemini QA issues in retry memory
    active_qa_ids = set()
    suppressed_qa_ids = set()
    for norm in normalized_scene_issues:
        issue_id = make_stable_issue_id("scene_qa", norm.scene_id, norm.issue_type, norm.detail)
        if norm.issue_class == qa.IssueClass.STALE_OR_SUPPRESSED:
            suppressed_qa_ids.add(issue_id)
        else:
            active_qa_ids.add(issue_id)

        retry_issue = RetryIssue(
            id=issue_id,
            stage="scene_qa",
            attempt=scenes_attempts,
            scene_id=norm.scene_id,
            type=norm.issue_type,
            severity="minor" if norm.issue_class == "soft_warning" else "major",
            detail=norm.detail,
            required_change=norm.repair_hint or norm.detail,
            status="active" if norm.issue_class != "stale_or_suppressed" else "suppressed",
            first_seen_attempt=scenes_attempts,
            last_seen_attempt=scenes_attempts,
            issue_class=norm.issue_class,
            reason=norm.reason,
        )
        if retry_issue.status == "suppressed":
            scene_retry_memory.suppressed_issues[issue_id] = retry_issue
            suppress_issue_by_id(scene_retry_memory, issue_id)
        else:
            add_or_update_issue(scene_retry_memory, retry_issue)

    for issue_id in list(scene_retry_memory.active_issues.keys()):
        issue = scene_retry_memory.active_issues[issue_id]
        if (
            issue.stage == "scene_qa"
            and issue_id not in active_qa_ids
            and issue_id not in suppressed_qa_ids
        ):
            resolve_issue_by_id(scene_retry_memory, issue_id)

    # Max attempts check: MAX_SCENE_REGEN_ATTEMPTS = 2
    # Allow attempt 3 only if:
    # - attempt 1 failed schema/layout hard blocker;
    # - and current candidate is not renderable.
    stop_scene_retries = False
    renderable = not loop.structure_blocked and not any(
        n.issue_class == qa.IssueClass.HARD_BLOCKER for n in normalized_scene_issues
    )

    max_limit = min(2, max_regen + 1)
    if scenes_attempts >= max_limit:
        if (
            scenes_attempts == 2
            and loop.attempt_1_failed_layout_schema
            and not renderable
            and max_regen >= 2
        ):
            pass
        else:
            stop_scene_retries = True

    if stop_scene_retries:
        remaining_blockers = [
            n
            for n in normalized_scene_issues
            if n.issue_class in {qa.IssueClass.HARD_BLOCKER, qa.IssueClass.REPAIRABLE_BLOCKER}
            and n.issue_type != "slideshow_risk"
        ]
        remaining_warnings = [
            n
            for n in normalized_scene_issues
            if n.issue_class == qa.IssueClass.SOFT_WARNING or n.issue_type == "slideshow_risk"
        ]

        decision = ""
        if not remaining_blockers:
            # Downgrade to WARN and continue
            scenes_qa_result["verdict"] = "WARN"
            scenes_qa_result["qa_pass"] = True
            loop.scenes_passed = True
            loop.best_scene_candidate = dict(short_scenes)
            loop.best_scene_candidate_qa = dict(scenes_qa_result)

            state.latest_scene_qa_ok = True
            state.latest_scene_qa_version = state.current_scenes_version
            decision = "continued_with_warn"
            update_stage("qa_scenes", "completed", qa_verdict="WARN")
        else:
            loop.scenes_passed = False
            decision = "failed_hard_blocker"
            update_stage("qa_scenes", "failed", qa_verdict="FAIL")

        summary_report = {
            "stage": "qa_scenes",
            "scenes_attempts": scenes_attempts,
            "remaining_blockers": [b.to_dict() for b in remaining_blockers],
            "remaining_warnings": [w.to_dict() for w in remaining_warnings],
            "renderable": renderable,
            "decision": decision,
            "latest_scene_qa_ok": state.latest_scene_qa_ok,
            "latest_scene_qa_version": state.latest_scene_qa_version,
            "best_candidate_available": loop.best_scene_candidate is not None,
        }
        atomic_write_json(_jd / paths.SHORT_FAILURE_REPORT_FILE, summary_report)

        # Write qa_decision_summary.json
        max_allowed_attempts = max_limit
        if loop.attempt_1_failed_layout_schema and not renderable and max_regen >= 2:
            max_allowed_attempts = 3
        decision_summary = {
            "stage": "qa_scenes",
            "attempts_used": scenes_attempts,
            "max_attempts": max_allowed_attempts,
            "decision": decision,
            "renderable": renderable,
            "remaining_blockers": [b.to_dict() for b in remaining_blockers],
            "remaining_warnings": [w.to_dict() for w in remaining_warnings],
            "continued_to_render": decision == "continued_with_warn",
        }
        atomic_write_json(_jd / paths.SHORT_QA_DECISION_SUMMARY_FILE, decision_summary)

        print(
            f"Scene QA stopped after {scenes_attempts} attempts.\n"
            f"Remaining blockers: {[b.detail for b in remaining_blockers]}\n"
            f"Remaining warnings: {[w.detail for w in remaining_warnings]}\n"
            f"Renderable: {renderable}\n"
            f"Decision: {decision}"
        )

        if decision == "failed_hard_blocker":
            status.update(
                {
                    "status": "needs_review",
                    "rendered": False,
                    "uploaded": False,
                    "youtube_url": "",
                    "requires_user_review": True,
                    "qa_verdict": "FAIL",
                    "failure_stage": "qa_scenes",
                    "failure_reason": f"Scene QA failed hard blocker: {[b.detail for b in remaining_blockers]}",
                }
            )
            write_short_status(long_job_dir, short_id, status)
            save_retry_memory(scene_retry_memory, scene_memory_file)
            return StageResult(StageSignal.PROCEED, returns=status)
        else:
            save_retry_memory(scene_retry_memory, scene_memory_file)
            return _LoopAction.BREAK

    return _LoopAction.FALLTHROUGH


def _scenes_product_quality_repair(ctx, loop):
    """Build the cumulative feedback for the next scene regeneration after a
    Gemini product-quality miss (optionally requesting a pacing simplify).

    Always returns `_LoopAction.FALLTHROUGH`. Mutates `loop` in place.
    """
    short_plan = ctx.short_plan
    scene_retry_memory = loop.scene_retry_memory
    scene_memory_file = loop.scene_memory_file
    scenes_attempts = loop.scenes_attempts
    short_scenes = loop.short_scenes
    scenes_qa_result = loop.scenes_qa_result

    # --- Product quality repair strategy ---
    summary = qa.summarize_product_scores(scenes_qa_result.get("product_scores") or {})
    scene_count = len(short_scenes.get("scenes") or [])
    candidate_summary = ""
    if summary["needs_pacing_simplify"] and scene_count >= qa.SIMPLIFY_SCENE_COUNT_THRESHOLD:
        candidate_summary = (
            "SIMPLIFY FOR PACING:\n"
            f"- retention_pacing is weak ({summary['retention_pacing']}) with {scene_count} scenes.\n"
            "- Remove the redundant late summary scene.\n"
            "- Merge the final tip/quote into the CTA scene.\n"
            "- Target 7-8 scenes total.\n"
            "- Do NOT add more graphics."
        )

    save_retry_memory(scene_retry_memory, scene_memory_file)
    from video_agent.shorts.idea_preservation import derive_idea_items

    exact_mapping_items = short_plan.get("idea_items") or derive_idea_items(short_plan)
    exact_mapping_context = (
        "\n".join(
            f"{i + 1}. {item.get('label') or item.get('topic') or item}"
            for i, item in enumerate(exact_mapping_items)
        )
        if exact_mapping_items
        else ""
    )
    loop.scenes_feedback = generate_cumulative_feedback(
        scene_retry_memory,
        scenes_attempts + 1,
        candidate_summary=candidate_summary,
        exact_mapping_context=exact_mapping_context,
    )
    return _LoopAction.FALLTHROUGH


def _stage_scenes(ctx: BuildContext) -> StageResult:
    """Stage: scenes -> visual_rhythm_plan -> qa_scenes inner regen loop.

    Self-contained inner loop preserved whole. Reads from ctx.extras:
    short_script, retention_plan, spoken_humanization, script_attempts,
    script_feedback. Writes short_scenes, scenes_qa_result, visual_rhythm_plan,
    scene_pipeline_state, and the counters scenes_attempts / structural_attempts
    / product_attempts / total_regeneration_attempts back to ctx.extras.

    Signals: a terminal status payload (hard fail), DONE (scenes did not pass ->
    outer break), RESTART_SCRIPT (escalate_to_script -> outer continue), or
    PROCEED (scenes passed). Raises on unexpected errors.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    _jd = ctx.json_dir
    status = ctx.status
    short_plan = ctx.short_plan
    update_stage = ctx.update_stage
    _recorder = ctx.recorder
    max_regen = ctx.max_regen
    max_structural_attempts = ctx.max_structural_attempts
    max_product_attempts = ctx.max_product_attempts

    short_script = ctx.extras["short_script"]
    ctx.extras.get("retention_plan", {})
    ctx.extras["spoken_humanization"]
    script_attempts = ctx.extras["script_attempts"]
    script_feedback = ctx.extras["script_feedback"]

    state = ScenePipelineState()
    scene_memory_file = _jd / "scene_retry_memory.json"
    scene_retry_memory = load_retry_memory(scene_memory_file)
    if scene_retry_memory is None:
        scene_retry_memory = RetryMemory(stage="scenes")
        scene_retry_memory.hard_invariants = [
            "- Preserve source fidelity.",
            "- Preserve idea_contract.original_count when must_preserve_count=true.",
            "- Do not invent unsupported claims.",
            "- Do not use unsafe/medical fear framing.",
            "- Latest scene_validation and latest Gemini scene QA must pass before audio/SEO/render.",
            "- If scenes are regenerated after Gemini QA, Gemini QA must run again.",
        ]

    scenes_attempts = 0
    scene_fit_failures = 0
    # Separate budgets: deterministic structural repairs vs. Gemini product
    # quality repairs. Each failure class consumes only its own budget.
    structural_attempts = 0
    product_attempts = 0
    provider_error_attempts = 0
    attempt_1_failed_layout_schema = False
    status["qa_scenes_attempts"] = 0
    status["qa_scenes_structural_attempts"] = 0
    status["qa_scenes_product_attempts"] = 0
    write_short_status(long_job_dir, short_id, status)
    scenes_qa_result = {"verdict": "FAIL", "issues": ["not_generated"]}
    short_scenes = {}
    scenes_feedback = ""
    best_scene_candidate = None
    best_scene_candidate_qa = None

    scenes_passed = False
    escalate_to_script = False
    prev_scene_hash: str | None = None
    scene_collapsed = False
    # Hard ceiling guards against a pathological loop where neither budget
    # increments; in practice every iteration consumes structural or product.
    _scenes_loop_ceiling = max_structural_attempts + max_product_attempts + 2
    visual_repair_tracker = {}
    skip_generation = False
    visual_rhythm_plan = None

    # Shared mutable loop state, mutated in place by the `_scenes_*` helpers.
    loop = _SceneLoopState(
        scenes_attempts=scenes_attempts,
        structural_attempts=structural_attempts,
        product_attempts=product_attempts,
        total_regeneration_attempts=0,
        scene_fit_failures=scene_fit_failures,
        provider_error_attempts=provider_error_attempts,
        attempt_1_failed_layout_schema=attempt_1_failed_layout_schema,
        prev_scene_hash=prev_scene_hash,
        skip_generation=skip_generation,
        scenes_passed=scenes_passed,
        escalate_to_script=escalate_to_script,
        scene_collapsed=scene_collapsed,
        short_scenes=short_scenes,
        scenes_qa_result=scenes_qa_result,
        visual_rhythm_plan=None,
        state=state,
        scenes_feedback=scenes_feedback,
        best_scene_candidate=best_scene_candidate,
        best_scene_candidate_qa=best_scene_candidate_qa,
        scene_retry_memory=scene_retry_memory,
        scene_memory_file=scene_memory_file,
        visual_repair_tracker=visual_repair_tracker,
    )

    while loop.scenes_attempts < _scenes_loop_ceiling:
        # Capture any mutations the not-yet-extracted blocks below made to the
        # local mirror variables on the previous iteration before advancing.
        loop.scenes_attempts = scenes_attempts
        loop.structural_attempts = structural_attempts
        loop.product_attempts = product_attempts
        loop.scene_fit_failures = scene_fit_failures
        loop.provider_error_attempts = provider_error_attempts
        loop.attempt_1_failed_layout_schema = attempt_1_failed_layout_schema
        loop.prev_scene_hash = prev_scene_hash
        loop.skip_generation = skip_generation
        loop.scenes_passed = scenes_passed
        loop.escalate_to_script = escalate_to_script
        loop.scene_collapsed = scene_collapsed
        loop.short_scenes = short_scenes
        loop.scenes_qa_result = scenes_qa_result
        loop.visual_rhythm_plan = visual_rhythm_plan
        loop.scenes_feedback = scenes_feedback
        loop.best_scene_candidate = best_scene_candidate
        loop.best_scene_candidate_qa = best_scene_candidate_qa

        loop.scenes_attempts += 1
        loop.fit_failure_counted_this_attempt = False
        loop.total_regeneration_attempts = (script_attempts - 1) + (loop.scenes_attempts - 1)
        # Mirror loop state into the locals used by the not-yet-extracted
        # blocks below.
        scenes_attempts = loop.scenes_attempts
        total_regeneration_attempts = loop.total_regeneration_attempts
        status["qa_scenes_attempts"] = scenes_attempts
        write_short_status(long_job_dir, short_id, status)

        _r = _scenes_generate_and_normalize(ctx, loop)
        # Sync mutated loop state back into the local mirror variables.
        scenes_attempts = loop.scenes_attempts
        structural_attempts = loop.structural_attempts
        product_attempts = loop.product_attempts
        total_regeneration_attempts = loop.total_regeneration_attempts
        scene_fit_failures = loop.scene_fit_failures
        provider_error_attempts = loop.provider_error_attempts
        attempt_1_failed_layout_schema = loop.attempt_1_failed_layout_schema
        prev_scene_hash = loop.prev_scene_hash
        skip_generation = loop.skip_generation
        scenes_passed = loop.scenes_passed
        escalate_to_script = loop.escalate_to_script
        scene_collapsed = loop.scene_collapsed
        short_scenes = loop.short_scenes
        scenes_qa_result = loop.scenes_qa_result
        visual_rhythm_plan = loop.visual_rhythm_plan
        scenes_feedback = loop.scenes_feedback
        best_scene_candidate = loop.best_scene_candidate
        best_scene_candidate_qa = loop.best_scene_candidate_qa
        scenes = loop.scenes
        structure_issues = loop.structure_issues
        if isinstance(_r, StageResult):
            return _r
        if _r is _LoopAction.CONTINUE:
            continue
        if _r is _LoopAction.BREAK:
            break

        loop.scenes = scenes
        loop.structure_issues = structure_issues
        _scenes_run_structure_validation(ctx, loop)
        scene_fit_failures = loop.scene_fit_failures
        structure_blocked = loop.structure_blocked

        _r = _scenes_structural_repair(ctx, loop)
        structure_blocked = loop.structure_blocked
        scenes_passed = loop.scenes_passed
        scenes_qa_result = loop.scenes_qa_result
        structural_attempts = loop.structural_attempts
        scenes_feedback = loop.scenes_feedback
        attempt_1_failed_layout_schema = loop.attempt_1_failed_layout_schema
        escalate_to_script = loop.escalate_to_script
        if isinstance(_r, StageResult):
            return _r
        if _r is _LoopAction.CONTINUE:
            continue
        if _r is _LoopAction.BREAK:
            break

        scenes_qa_result, normalized_scene_issues = _stage_qa_scenes(
            ctx,
            short_scenes,
            scenes_attempts,
        )

        qa_pass = scenes_qa_result.get("verdict") in ("PASS", "WARN")
        scenes_qa_result["qa_pass"] = qa_pass
        scenes_qa_result["provider_call_ok"] = bool(
            scenes_qa_result.get("provider_call_ok")
            or scenes_qa_result.get("provider") in {"gemini", "rule_based"}
        )

        if qa_pass:
            for issue_id in list(scene_retry_memory.active_issues.keys()):
                issue = scene_retry_memory.active_issues[issue_id]
                if issue.stage == "scene_qa":
                    resolve_issue_by_id(scene_retry_memory, issue_id)
            best_scene_candidate = dict(short_scenes)
            best_scene_candidate_qa = dict(scenes_qa_result)
            scenes_passed = True

            state.latest_scene_qa_ok = True
            state.latest_scene_qa_version = state.current_scenes_version

            max_limit = min(2, max_regen + 1)
            max_allowed_attempts = max_limit
            if attempt_1_failed_layout_schema and max_regen >= 2:
                max_allowed_attempts = 3

            scene_warnings = [
                n for n in normalized_scene_issues if n.issue_class == qa.IssueClass.SOFT_WARNING
            ]
            decision = (
                "continued_with_warn" if scenes_qa_result.get("verdict") == "WARN" else "passed"
            )

            decision_summary = {
                "stage": "qa_scenes",
                "attempts_used": scenes_attempts,
                "max_attempts": max_allowed_attempts,
                "decision": decision,
                "renderable": True,
                "remaining_blockers": [],
                "remaining_warnings": [
                    w.to_dict() if hasattr(w, "to_dict") else w for w in scene_warnings
                ],
                "continued_to_render": True,
            }
            atomic_write_json(_jd / paths.SHORT_QA_DECISION_SUMMARY_FILE, decision_summary)

            save_retry_memory(scene_retry_memory, scene_memory_file)
            break

        best_scene_candidate = dict(short_scenes)
        best_scene_candidate_qa = dict(scenes_qa_result)
        product_attempts += 1
        status["qa_scenes_product_attempts"] = product_attempts
        write_short_status(long_job_dir, short_id, status)

        # Track Gemini QA issues in retry memory
        active_qa_ids = set()
        suppressed_qa_ids = set()
        for norm in normalized_scene_issues:
            issue_id = make_stable_issue_id("scene_qa", norm.scene_id, norm.issue_type, norm.detail)
            if norm.issue_class == qa.IssueClass.STALE_OR_SUPPRESSED:
                suppressed_qa_ids.add(issue_id)
            else:
                active_qa_ids.add(issue_id)

            retry_issue = RetryIssue(
                id=issue_id,
                stage="scene_qa",
                attempt=scenes_attempts,
                scene_id=norm.scene_id,
                type=norm.issue_type,
                severity="minor" if norm.issue_class == "soft_warning" else "major",
                detail=norm.detail,
                required_change=norm.repair_hint or norm.detail,
                status="active" if norm.issue_class != "stale_or_suppressed" else "suppressed",
                first_seen_attempt=scenes_attempts,
                last_seen_attempt=scenes_attempts,
                issue_class=norm.issue_class,
                reason=norm.reason,
            )
            if retry_issue.status == "suppressed":
                scene_retry_memory.suppressed_issues[issue_id] = retry_issue
                suppress_issue_by_id(scene_retry_memory, issue_id)
            else:
                add_or_update_issue(scene_retry_memory, retry_issue)

        for issue_id in list(scene_retry_memory.active_issues.keys()):
            issue = scene_retry_memory.active_issues[issue_id]
            if (
                issue.stage == "scene_qa"
                and issue_id not in active_qa_ids
                and issue_id not in suppressed_qa_ids
            ):
                resolve_issue_by_id(scene_retry_memory, issue_id)

        # Max attempts check: MAX_SCENE_REGEN_ATTEMPTS = 2
        # Allow attempt 3 only if:
        # - attempt 1 failed schema/layout hard blocker;
        # - and current candidate is not renderable.
        stop_scene_retries = False
        renderable = not structure_blocked and not any(
            n.issue_class == qa.IssueClass.HARD_BLOCKER for n in normalized_scene_issues
        )

        max_limit = min(2, max_regen + 1)
        if scenes_attempts >= max_limit:
            if (
                scenes_attempts == 2
                and attempt_1_failed_layout_schema
                and not renderable
                and max_regen >= 2
            ):
                pass
            else:
                stop_scene_retries = True

        if stop_scene_retries:
            remaining_blockers = [
                n
                for n in normalized_scene_issues
                if n.issue_class in {qa.IssueClass.HARD_BLOCKER, qa.IssueClass.REPAIRABLE_BLOCKER}
                and n.issue_type != "slideshow_risk"
            ]
            remaining_warnings = [
                n
                for n in normalized_scene_issues
                if n.issue_class == qa.IssueClass.SOFT_WARNING or n.issue_type == "slideshow_risk"
            ]

            decision = ""
            if not remaining_blockers:
                # Downgrade to WARN and continue
                scenes_qa_result["verdict"] = "WARN"
                scenes_qa_result["qa_pass"] = True
                scenes_passed = True
                best_scene_candidate = dict(short_scenes)
                best_scene_candidate_qa = dict(scenes_qa_result)

                state.latest_scene_qa_ok = True
                state.latest_scene_qa_version = state.current_scenes_version
                decision = "continued_with_warn"
                update_stage("qa_scenes", "completed", qa_verdict="WARN")
            else:
                scenes_passed = False
                decision = "failed_hard_blocker"
                update_stage("qa_scenes", "failed", qa_verdict="FAIL")

            summary_report = {
                "stage": "qa_scenes",
                "scenes_attempts": scenes_attempts,
                "remaining_blockers": [b.to_dict() for b in remaining_blockers],
                "remaining_warnings": [w.to_dict() for w in remaining_warnings],
                "renderable": renderable,
                "decision": decision,
                "latest_scene_qa_ok": state.latest_scene_qa_ok,
                "latest_scene_qa_version": state.latest_scene_qa_version,
                "best_candidate_available": best_scene_candidate is not None,
            }
            atomic_write_json(_jd / paths.SHORT_FAILURE_REPORT_FILE, summary_report)

            # Write qa_decision_summary.json
            max_allowed_attempts = max_limit
            if attempt_1_failed_layout_schema and not renderable and max_regen >= 2:
                max_allowed_attempts = 3
            decision_summary = {
                "stage": "qa_scenes",
                "attempts_used": scenes_attempts,
                "max_attempts": max_allowed_attempts,
                "decision": decision,
                "renderable": renderable,
                "remaining_blockers": [b.to_dict() for b in remaining_blockers],
                "remaining_warnings": [w.to_dict() for w in remaining_warnings],
                "continued_to_render": decision == "continued_with_warn",
            }
            atomic_write_json(_jd / paths.SHORT_QA_DECISION_SUMMARY_FILE, decision_summary)

            print(
                f"Scene QA stopped after {scenes_attempts} attempts.\n"
                f"Remaining blockers: {[b.detail for b in remaining_blockers]}\n"
                f"Remaining warnings: {[w.detail for w in remaining_warnings]}\n"
                f"Renderable: {renderable}\n"
                f"Decision: {decision}"
            )

            if decision == "failed_hard_blocker":
                status.update(
                    {
                        "status": "needs_review",
                        "rendered": False,
                        "uploaded": False,
                        "youtube_url": "",
                        "requires_user_review": True,
                        "qa_verdict": "FAIL",
                        "failure_stage": "qa_scenes",
                        "failure_reason": f"Scene QA failed hard blocker: {[b.detail for b in remaining_blockers]}",
                    }
                )
                write_short_status(long_job_dir, short_id, status)
                save_retry_memory(scene_retry_memory, scene_memory_file)
                return StageResult(StageSignal.PROCEED, returns=status)
            else:
                save_retry_memory(scene_retry_memory, scene_memory_file)
                break

        # --- Product quality repair strategy ---
        summary = qa.summarize_product_scores(scenes_qa_result.get("product_scores") or {})
        scene_count = len(short_scenes.get("scenes") or [])
        candidate_summary = ""
        if summary["needs_pacing_simplify"] and scene_count >= qa.SIMPLIFY_SCENE_COUNT_THRESHOLD:
            candidate_summary = (
                "SIMPLIFY FOR PACING:\n"
                f"- retention_pacing is weak ({summary['retention_pacing']}) with {scene_count} scenes.\n"
                "- Remove the redundant late summary scene.\n"
                "- Merge the final tip/quote into the CTA scene.\n"
                "- Target 7-8 scenes total.\n"
                "- Do NOT add more graphics."
            )

        save_retry_memory(scene_retry_memory, scene_memory_file)
        from video_agent.shorts.idea_preservation import derive_idea_items

        exact_mapping_items = short_plan.get("idea_items") or derive_idea_items(short_plan)
        exact_mapping_context = (
            "\n".join(
                f"{i + 1}. {item.get('label') or item.get('topic') or item}"
                for i, item in enumerate(exact_mapping_items)
            )
            if exact_mapping_items
            else ""
        )
        scenes_feedback = generate_cumulative_feedback(
            scene_retry_memory,
            scenes_attempts + 1,
            candidate_summary=candidate_summary,
            exact_mapping_context=exact_mapping_context,
        )

    if escalate_to_script:
        script_feedback = build_script_compression_feedback(short_script)
        atomic_write_json(
            _jd / paths.SHORT_FAILURE_REPORT_FILE,
            {
                "stage": "scenes",
                "attempt": scenes_attempts,
                "detail": "Escalating to script compression due to repeated scene_narration_fit failures.",
                "feedback": script_feedback,
            },
        )
        ctx.extras["script_feedback"] = script_feedback
        ctx.extras["short_scenes"] = short_scenes
        ctx.extras["scenes_qa_result"] = scenes_qa_result
        ctx.extras["scene_pipeline_state"] = state
        ctx.extras["scenes_attempts"] = scenes_attempts
        ctx.extras["structural_attempts"] = structural_attempts
        ctx.extras["product_attempts"] = product_attempts
        ctx.extras["total_regeneration_attempts"] = total_regeneration_attempts
        return StageResult(StageSignal.RESTART_SCRIPT)

    # Final hard gate: a provider call that returned JSON is not the same
    # as a passed QA verdict. If Gemini scene QA says FAIL, do not rescue it
    # as a best candidate; retry scenes while budget remains, otherwise stop
    # before audio tail repair, SEO, or render.
    if not scenes_passed and scenes_qa_result["verdict"] != "PASS":
        if best_scene_candidate is not None and best_scene_candidate_qa is not None:
            candidate_issues = validate_scenes.validate_scene_structure(
                best_scene_candidate.get("scenes") or [],
                scenes_doc=best_scene_candidate,
                script=short_script,
                attempt=scenes_attempts,
            )
            state.latest_scene_qa_ok = False
            state.latest_scene_qa_version = None
            atomic_write_json(
                _jd / paths.SHORT_FAILURE_REPORT_FILE,
                {
                    "stage": "qa_scenes",
                    "best_candidate_available": True,
                    "deterministic_issues": validate_scenes.issues_to_dicts(candidate_issues),
                    "llm_qa": best_scene_candidate_qa,
                    "latest_scene_qa_ok": state.latest_scene_qa_ok,
                    "latest_scene_qa_version": state.latest_scene_qa_version,
                    "detail": "Gemini scene QA verdict was FAIL; audio, SEO, and render are blocked.",
                },
            )

    if not scenes_passed:
        ctx.extras["short_scenes"] = short_scenes
        ctx.extras["scenes_qa_result"] = scenes_qa_result
        ctx.extras["scene_pipeline_state"] = state
        ctx.extras["scenes_attempts"] = scenes_attempts
        ctx.extras["structural_attempts"] = structural_attempts
        ctx.extras["product_attempts"] = product_attempts
        ctx.extras["total_regeneration_attempts"] = total_regeneration_attempts
        return StageResult(StageSignal.DONE)

    ctx.extras["short_scenes"] = short_scenes
    ctx.extras["scenes_qa_result"] = scenes_qa_result
    ctx.extras["visual_rhythm_plan"] = visual_rhythm_plan
    ctx.extras["scene_pipeline_state"] = state
    ctx.extras["scenes_attempts"] = scenes_attempts
    ctx.extras["structural_attempts"] = structural_attempts
    ctx.extras["product_attempts"] = product_attempts
    ctx.extras["total_regeneration_attempts"] = total_regeneration_attempts
    return _PROCEED
