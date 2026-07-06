"""Script generation, QA, anti-AI review & humanization stages."""

from __future__ import annotations

from video_agent.shorts import (
    anti_ai,
    humanization,
    paths,
    performance_memory,
    qa,
    short_script_builder,
    source_map,
    validate_scenes,
)
from video_agent.shorts.builder.context import BuildContext

# Backwards-compatible facade: tests and callers import/patch these via
# video_agent.shorts.short_builder.<name>.
from video_agent.shorts.builder.qa_gate import (
    _qa_blocker_details,
    check_and_apply_auto_pass,
    has_hard_fail,
)
from video_agent.shorts.builder.snapshots import (
    _normalized_script_hash,
)
from video_agent.shorts.builder.types import (
    _PROCEED,
    StageResult,
    StageSignal,
)
from video_agent.shorts.manifest import write_short_status
from video_agent.shorts.retry_memory import (
    RetryIssue,
    add_or_update_issue,
    generate_cumulative_feedback,
    make_stable_issue_id,
    resolve_issue_by_id,
    save_retry_memory,
    suppress_issue_by_id,
)
from video_agent.storage.atomic import atomic_write_json


def _stage_spoken_humanization(ctx: BuildContext) -> None:
    """Stage: spoken_humanization.

    Reads short_script + retention_plan from ctx.extras; writes
    spoken_humanization back to ctx.extras. Raises on failure (caller propagates).
    """
    short_id = ctx.short_plan["short_id"]
    short_script = ctx.extras["short_script"]
    retention_plan = ctx.extras.get("retention_plan", {})
    ctx.update_stage("spoken_humanization", "in_progress")
    try:
        ctx.check_stop()
        spoken_humanization = humanization.build_spoken_humanization(
            ctx.long_job_dir,
            short_id,
            short_script,
            retention_plan,
            ctx.channel_config,
            ctx.llm_fn,
        )
        ctx.update_stage(
            "spoken_humanization",
            "completed",
            generation_mode=spoken_humanization.get("generation_mode"),
            rewrite_discarded=spoken_humanization.get("rewrite_discarded", False),
        )
        ctx.extras["spoken_humanization"] = spoken_humanization
    except Exception as exc:
        ctx.update_stage("spoken_humanization", "failed", error=str(exc))
        ctx.status["status"] = "failed"
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc


def _stage_script(ctx: BuildContext) -> StageResult:
    """Stage: script generation.

    Reads from ctx.extras: script_attempts, script_feedback, prev_script_hash,
    retention_plan. Writes short_script, then delegates to _stage_qa_script for
    QA and retry-collapse handling.

    Signals: DONE (retry-collapse renderable -> outer break), RESTART_SCRIPT
    (qa FAIL -> outer continue with primed feedback), or a terminal status
    payload (retry-collapse hard fail). PROCEED falls through to humanization.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    _jd = ctx.json_dir
    status = ctx.status
    short_plan = ctx.short_plan
    plan_for_prompt = ctx.plan_for_prompt
    channel_config = ctx.channel_config
    update_stage = ctx.update_stage
    check_stop = ctx.check_stop
    _recorder = ctx.recorder
    llm_fn = ctx.llm_fn
    source_artifacts = ctx.source_artifacts

    script_attempts = ctx.extras["script_attempts"]
    script_feedback = ctx.extras["script_feedback"]
    ctx.extras["prev_script_hash"]
    retention_plan = ctx.extras.get("retention_plan", {})
    ctx.extras["script_retry_memory"]
    ctx.extras["script_memory_file"]

    # The parent long video's title carries the topic ("...aceite de oliva...")
    # even when the plan has no pillar/topic fields, so make it available to the
    # script prompt's topic-aware funnel CTA (bug-484).
    if not (source_artifacts or {}).get("source_video_title"):
        from video_agent.shorts.source_map import _long_title

        _title = _long_title(long_job_dir)
        if _title:
            source_artifacts = {**(source_artifacts or {}), "source_video_title": _title}

    # --- Stage 1: Script ---
    update_stage("script", "in_progress")
    try:
        check_stop()
        candidate = short_script_builder.build_short_script(
            long_job_dir,
            plan_for_prompt,
            channel_config,
            llm_fn,
            source_artifacts=source_artifacts,
            retention_plan=retention_plan,
            feedback=script_feedback,
            attempt=script_attempts,
            write_to_disk=False,
        )

        # Check for script completeness and structure
        from video_agent.shorts.validation.checks import (
            classify_script_validation,
            validate_full_short_script_candidate,
        )

        jd_test = paths.short_json_dir(long_job_dir, short_id)
        source_map_file = jd_test / paths.SHORT_SOURCE_MAP_FILE

        sm = None
        if source_map_file.exists():
            import json

            try:
                sm = json.loads(source_map_file.read_text())
            except Exception:
                pass

        # If candidate has target_duration_sec == 45 and count is unlocked, allow updating short_plan
        from video_agent.shorts.idea_preservation import derive_idea_contract

        contract = derive_idea_contract(short_plan)
        if not contract.get("must_preserve_count") and candidate.get("target_duration_sec") == 45:
            short_plan["target_duration_sec"] = 45
            plan_for_prompt["target_duration_sec"] = 45

        errors = validate_full_short_script_candidate(
            candidate,
            short_plan,
            sm,
            # Same topic inputs as the prompt, so the deterministic CTA gate and
            # the prompt can never disagree on the funnel CTA again (bug-484).
            channel_config=channel_config,
            long_video_title=str((source_artifacts or {}).get("source_video_title") or ""),
        )

        if errors:
            if "audio_fit_over_soft_budget" in errors:
                ctx.extras["script_feedback"] = (
                    script_feedback + "\n\nCRITICAL SYSTEM REJECTION: Audio fit failed. "
                    "Reduce narration to <= 65 words. "
                    "Keep 5 items. "
                    "Move details to visuals. "
                    "Do not change CTA."
                )
            else:
                ctx.extras["script_feedback"] = (
                    script_feedback
                    + "\n\nCRITICAL SYSTEM REJECTION: You returned a partial script or failed strict structural requirements! "
                    "You MUST return the FULL script from start to finish. "
                    "Errors detected: " + ", ".join(errors)
                )
            verdict = classify_script_validation(errors)
            _recorder.record_event(
                "deterministic", "script_validation", {"verdict": verdict, "errors": errors}
            )
            # Do NOT update stage or save the broken candidate
            return StageResult(StageSignal.RESTART_SCRIPT)

        short_script = candidate
        jd = paths.short_json_dir(long_job_dir, short_id)
        jd.mkdir(parents=True, exist_ok=True)
        atomic_write_json(jd / paths.SHORT_SCRIPT_FILE, short_script)

        update_stage("script", "completed")
    except Exception as exc:
        update_stage("script", "failed")
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    ctx.extras["short_script"] = short_script

    return _stage_qa_script(ctx)


def _stage_qa_script(ctx: BuildContext) -> StageResult:
    """Stage: qa_script.

    Reads short_script and script retry carry-over from ctx.extras. Handles
    normal script QA plus retry-collapse decisions, preserving the existing
    StageResult control-flow contract used by _build_short_impl.
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
    music_track = ctx.music_track
    max_regen = ctx.max_regen
    long_video_url = ctx.long_video_url

    short_script = ctx.extras["short_script"]
    script_attempts = ctx.extras["script_attempts"]
    script_feedback = ctx.extras["script_feedback"]
    prev_script_hash = ctx.extras["prev_script_hash"]
    script_retry_memory = ctx.extras["script_retry_memory"]
    script_memory_file = ctx.extras["script_memory_file"]

    cur_script_hash = _normalized_script_hash(short_script)
    if prev_script_hash is not None and cur_script_hash == prev_script_hash:
        script_qa_result = qa.run_short_script_qa(
            long_job_dir,
            short_id,
            channel_config,
            music_track=music_track,
            gemini_fn=gemini_fn,
            attempt=script_attempts,
        )
        check_and_apply_auto_pass(script_qa_result)
        atomic_write_json(_jd / paths.SHORT_SCRIPT_QA_FILE, script_qa_result)
        ctx.extras["script_qa_result"] = script_qa_result
        verdict = script_qa_result.get("verdict", "FAIL")
        update_stage(
            "qa_script",
            "completed" if verdict in ("PASS", "WARN") else "failed",
            qa_verdict=verdict,
        )

        renderable = not has_hard_fail(script_qa_result)
        _recorder.record_event(
            "deterministic",
            "retry_collapse",
            {
                "verdict": "WARN" if renderable else "FAIL",
                "retry_reason": "qa_retry",
                "retry_scope": "script_only",
                "attempt": script_attempts,
                "renderable": renderable,
                "detail": "Identical script output across retries; stopping loop.",
                "reason": "retry_collapse",
            },
            ok=False,
        )
        if renderable:
            script_qa_result["verdict"] = "WARN"
            script_qa_result["collapsed"] = True
            status["hook"] = str(short_script.get("hook") or "")
            write_short_status(long_job_dir, short_id, status)
            try:
                sm = source_map.build_source_map(
                    long_job_dir, short_plan, short_script, channel_config, long_video_url
                )
                atomic_write_json(_jd / paths.SHORT_SOURCE_MAP_FILE, sm)
            except Exception:
                pass
            return StageResult(StageSignal.DONE)
        else:
            blocker_details = _qa_blocker_details(script_qa_result)
            failure_reason = (
                "qa_script hard blocker (identical script across retries): "
                + "; ".join(blocker_details)
                if blocker_details
                else "qa_script produced an identical, non-renderable script across retries."
            )
            decision_summary = {
                "stage": "qa_script",
                "attempts_used": script_attempts,
                "max_attempts": max_regen + 1,
                "decision": "failed_hard_blocker",
                "renderable": False,
                "remaining_blockers": [{"detail": d} for d in blocker_details],
                "remaining_warnings": [],
                "continued_to_render": False,
            }
            atomic_write_json(_jd / paths.SHORT_QA_DECISION_SUMMARY_FILE, decision_summary)
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
                    "qa_verdict": "FAIL",
                    "failure_stage": "qa_script",
                    "failure_reason": failure_reason,
                    "regeneration_attempts": script_attempts,
                }
            )
            write_short_status(long_job_dir, short_id, status)
            return StageResult(StageSignal.PROCEED, returns=status)

    prev_script_hash = cur_script_hash
    ctx.extras["prev_script_hash"] = prev_script_hash

    # Update hook dynamically
    status["hook"] = str(short_script.get("hook") or "")
    write_short_status(long_job_dir, short_id, status)

    # Build Source Map early so Script QA can read it
    try:
        sm = source_map.build_source_map(
            long_job_dir, short_plan, short_script, channel_config, long_video_url
        )
        atomic_write_json(_jd / paths.SHORT_SOURCE_MAP_FILE, sm)
    except Exception:
        pass

    # --- Stage 2: QA Script ---
    update_stage("qa_script", "in_progress")
    try:
        check_stop()
        script_qa_result = qa.run_short_script_qa(
            long_job_dir,
            short_id,
            channel_config,
            music_track=music_track,
            gemini_fn=gemini_fn,
            attempt=script_attempts,
        )
        check_and_apply_auto_pass(script_qa_result)

        # Normalize script QA issues
        normalized_script_issues = []
        for item in script_qa_result.get("issues") or []:
            norm = qa.normalize_qa_issue(
                item, idea=short_plan, script=short_script, scenes={}, source="script_qa"
            )
            normalized_script_issues.append(norm)
        for item in script_qa_result.get("required_changes") or []:
            norm = qa.normalize_qa_issue(
                item, idea=short_plan, script=short_script, scenes={}, source="script_qa"
            )
            if not any(x.detail == norm.detail for x in normalized_script_issues):
                normalized_script_issues.append(norm)

        script_qa_result["normalized_issues"] = [n.to_dict() for n in normalized_script_issues]

        script_blockers = [
            n
            for n in normalized_script_issues
            if n.issue_class in {qa.IssueClass.HARD_BLOCKER, qa.IssueClass.REPAIRABLE_BLOCKER}
        ]
        script_warnings = [
            n for n in normalized_script_issues if n.issue_class == qa.IssueClass.SOFT_WARNING
        ]

        if not script_blockers:
            script_qa_result["verdict"] = "WARN" if script_warnings else "PASS"
        else:
            script_qa_result["verdict"] = "FAIL"

        atomic_write_json(_jd / paths.SHORT_SCRIPT_QA_FILE, script_qa_result)
        verdict = script_qa_result.get("verdict", "FAIL")
        update_stage(
            "qa_script",
            "completed" if verdict in ("PASS", "WARN") else "failed",
            qa_verdict=verdict,
        )

        # Record classification and wrong context suppression for script QA
        raw_gemini_verdict = script_qa_result.get("verdict")
        if raw_gemini_verdict == "FAIL" or verdict == "FAIL":
            classification_reason = "qa_hard_fail"
            if not script_blockers:
                classification_reason = "qa_soft_warn"
                has_wrong_context = any(
                    n.reason == "wrong_context_five_errors_rule" for n in normalized_script_issues
                )
                has_noncanonical = any(
                    n.reason == "noncanonical_count_inference" for n in normalized_script_issues
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
        for norm in normalized_script_issues:
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
        update_stage("qa_script", "failed")
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    ctx.extras["script_qa_result"] = script_qa_result
    ctx.extras["normalized_script_issues"] = normalized_script_issues

    if script_qa_result["verdict"] not in ("PASS", "WARN"):
        active_script_ids = set()
        suppressed_script_ids = set()
        for norm in normalized_script_issues:
            issue_id = make_stable_issue_id("script_qa", "global", norm.issue_type, norm.detail)
            if norm.issue_class == qa.IssueClass.STALE_OR_SUPPRESSED:
                suppressed_script_ids.add(issue_id)
            else:
                active_script_ids.add(issue_id)

            retry_issue = RetryIssue(
                id=issue_id,
                stage="script_qa",
                attempt=script_attempts,
                scene_id="global",
                type=norm.issue_type,
                severity="minor" if norm.issue_class == "soft_warning" else "major",
                detail=norm.detail,
                required_change=norm.repair_hint or norm.detail,
                status="active" if norm.issue_class != "stale_or_suppressed" else "suppressed",
                first_seen_attempt=script_attempts,
                last_seen_attempt=script_attempts,
                issue_class=norm.issue_class,
                reason=norm.reason,
            )
            if retry_issue.status == "suppressed":
                script_retry_memory.suppressed_issues[issue_id] = retry_issue
                suppress_issue_by_id(script_retry_memory, issue_id)
            else:
                add_or_update_issue(script_retry_memory, retry_issue)

        for issue_id in list(script_retry_memory.active_issues.keys()):
            if issue_id not in active_script_ids and issue_id not in suppressed_script_ids:
                resolve_issue_by_id(script_retry_memory, issue_id)

        save_retry_memory(script_retry_memory, script_memory_file)
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
        script_feedback = generate_cumulative_feedback(
            script_retry_memory, script_attempts + 1, exact_mapping_context=exact_mapping_context
        )
        ctx.extras["script_feedback"] = script_feedback
        return StageResult(StageSignal.RESTART_SCRIPT)

    return _PROCEED


def _stage_anti_ai_review(ctx: BuildContext) -> StageResult:
    """Stage: graphic validation -> performance_memory(scenes_ready) -> anti_ai_review.

    Reads from ctx.extras: short_script, short_scenes, retention_plan,
    script_retry_memory, script_memory_file, script_attempts,
    anti_ai_regeneration_attempts.

    On anti-AI FAIL with budget left, primes script_feedback in ctx.extras and
    returns RESTART_SCRIPT (outer-loop continue). On exhausted budget returns a
    terminal status payload. Otherwise PROCEED. Raises on unexpected errors.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    status = ctx.status
    short_plan = ctx.short_plan
    plan_for_prompt = ctx.plan_for_prompt
    channel_config = ctx.channel_config
    update_stage = ctx.update_stage
    gemini_fn = ctx.gemini_fn
    max_regen = ctx.max_regen

    short_script = ctx.extras["short_script"]
    short_scenes = ctx.extras["short_scenes"]
    retention_plan = ctx.extras.get("retention_plan", {})
    script_retry_memory = ctx.extras["script_retry_memory"]
    script_memory_file = ctx.extras["script_memory_file"]
    script_attempts = ctx.extras["script_attempts"]
    anti_ai_regeneration_attempts = ctx.extras["anti_ai_regeneration_attempts"]

    # Scenes passed! Run graphic validator
    try:
        graphic_warnings = validate_scenes.validate_short_graphic_scenes(
            short_scenes.get("scenes") or []
        )
        if graphic_warnings:
            status["graphic_warnings"] = graphic_warnings
    except ValueError as exc:
        update_stage("render", "failed", error=str(exc))
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise

    update_stage("performance_memory", "in_progress")
    try:
        performance_memory.write_performance_memory(
            long_job_dir,
            short_id,
            plan_for_prompt,
            short_script,
            short_scenes,
            retention_plan,
            status="scenes_ready",
        )
        update_stage("performance_memory", "completed", memory_status="scenes_ready")
    except Exception as exc:
        update_stage("performance_memory", "failed", error=str(exc))
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    update_stage("anti_ai_review", "in_progress")
    try:
        anti_ai_review = anti_ai.run_anti_ai_review(
            long_job_dir,
            short_id,
            short_script,
            short_scenes,
            retention_plan,
            channel_config,
            gemini_fn=gemini_fn,
        )
        anti_verdict = anti_ai_review.get("verdict", "FAIL")
        update_stage(
            "anti_ai_review",
            "completed" if anti_verdict in {"PASS", "WARN"} else "failed",
            qa_verdict=anti_verdict,
        )
    except Exception as exc:
        update_stage("anti_ai_review", "failed", error=str(exc))
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    ctx.extras["anti_ai_review"] = anti_ai_review

    if anti_ai_review.get("verdict") == "FAIL":
        anti_ai_regeneration_attempts += 1
        ctx.extras["anti_ai_regeneration_attempts"] = anti_ai_regeneration_attempts
        status["anti_ai_regeneration_attempts"] = anti_ai_regeneration_attempts
        issue_text = "; ".join(
            str(item)
            for item in (
                anti_ai_review.get("recommended_changes")
                or anti_ai_review.get("robotic_patterns")
                or anti_ai_review.get("generic_phrases")
                or ["anti_ai_review_failed"]
            )
        )
        issue_id = make_stable_issue_id("anti_ai_review", "global", "anti_ai_issue", issue_text)
        add_or_update_issue(
            script_retry_memory,
            RetryIssue(
                id=issue_id,
                stage="anti_ai_review",
                attempt=script_attempts,
                scene_id="global",
                type="anti_ai_issue",
                severity="major",
                detail=issue_text,
                required_change=issue_text,
                status="active",
                first_seen_attempt=script_attempts,
                last_seen_attempt=script_attempts,
            ),
        )
        save_retry_memory(script_retry_memory, script_memory_file)
        if anti_ai_regeneration_attempts <= 1 and script_attempts < max_regen + 1:
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
            script_feedback = generate_cumulative_feedback(
                script_retry_memory,
                script_attempts + 1,
                exact_mapping_context=exact_mapping_context,
            )
            ctx.extras["script_feedback"] = script_feedback
            for stage_name in (
                "script",
                "qa_script",
                "spoken_humanization",
                "scenes",
                "visual_rhythm_plan",
                "qa_scenes",
                "anti_ai_review",
            ):
                update_stage(stage_name, "pending")
            return StageResult(StageSignal.RESTART_SCRIPT)

        performance_memory.write_performance_memory(
            long_job_dir,
            short_id,
            plan_for_prompt,
            short_script,
            short_scenes,
            retention_plan,
            status="failed",
            failure_stage="anti_ai_review",
            failure_reason=issue_text,
        )
        update_stage("performance_memory", "completed", memory_status="failed")
        status.update(
            {
                "status": "failed",
                "rendered": False,
                "uploaded": False,
                "youtube_url": "",
                "requires_user_review": True,
                "qa_verdict": "FAIL",
                "failure_stage": "anti_ai_review",
                "failure_reason": issue_text,
                "anti_ai_regeneration_attempts": anti_ai_regeneration_attempts,
            }
        )
        write_short_status(long_job_dir, short_id, status)
        return StageResult(StageSignal.PROCEED, returns=status)

    return _PROCEED
