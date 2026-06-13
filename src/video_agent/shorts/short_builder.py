"""Build one Short end to end: generate → QA (regen loop) → audio → mix → render.

All side-effecting steps (LLM, Kokoro TTS, ffmpeg mix, Remotion render, cover)
are injected so the orchestration is unit-testable; real implementations are the
defaults used by the autopilot.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import (
    anti_ai,
    call_budget,
    humanization,
    llm_history,
    performance_memory,
    paths,
    qa,
    retention_plan as retention_plan_builder,
    short_scene_builder,
    short_script_builder,
    short_seo_builder,
    source_map,
    validate_scenes,
    visual_rhythm,
)
from video_agent.shorts.idea_preservation import allowed_spoken_points_from_contract
from video_agent.shorts.manifest import write_short_status
from video_agent.storage.atomic import atomic_write_json
from video_agent.shorts.retry_memory import (
    ScenePipelineState,
    assert_latest_scenes_ready,
    RetryMemory,
    RetryIssue,
    add_or_update_issue,
    resolve_issue_by_id,
    suppress_issue_by_id,
    generate_cumulative_feedback,
    make_stable_issue_id,
    save_retry_memory,
    load_retry_memory,
)

# Backwards-compatible facade: tests and callers import/patch these via
# video_agent.shorts.short_builder.<name>.
from video_agent.shorts.builder.defaults import (
    _default_llm_fn, _default_background_fn, _default_tts_fn,
    _default_mix_fn, _default_render_fn, _default_cover_fn,
)
from video_agent.shorts.builder.qa_gate import (
    HARD_SCENE_VALIDATION_TYPES,
    _HARD_QA_ISSUE_MARKERS,
    _scene_qa_has_hard_fail, has_hard_fail, _qa_blocker_details,
    check_and_apply_auto_pass, should_fallback_to_gemini_scene_qa,
    build_script_compression_feedback,
)
from video_agent.shorts.builder.retry import (
    MAX_PROVIDER_RETRIES_PER_CALL,
    record_retry_event, wrap_llm_with_provider_retries,
)
from video_agent.shorts.builder.snapshots import (
    _parse, _cover_text, _normalized_script_hash, _normalized_scene_hash,
    _scene_duration_sum, _snapshot_scene_durations, _restore_scene_durations,
)
from video_agent.shorts.builder.status import _update_short_stage
from video_agent.shorts.builder.render_props import _write_render_props
from video_agent.shorts.builder.context import BuildContext

# Average product score (0-10) at/above which a soft-only scene-QA FAIL is
# auto-passed as WARN instead of regenerated (spec §6).
SCORE_AUTOPASS_AVERAGE = 8.5

MAX_QA_RETRIES_PER_STAGE = 1
MAX_SCENE_REGEN_ATTEMPTS = 2
MAX_SCRIPT_REGEN_ATTEMPTS = 1


# ---------------------------------------------------------------------------
# Signal-based dispatch for stages extracted from _build_short_impl. A stage
# returns a StageResult; the outer loop inspects `.returns` (a terminal status
# payload the caller must `return`) and `.signal` (control flow when `.returns`
# is None): PROCEED falls through, RESTART_SCRIPT is the outer-loop `continue`,
# DONE is the outer-loop `break`.
# ---------------------------------------------------------------------------
from enum import Enum
from dataclasses import dataclass


class StageSignal(Enum):
    PROCEED = "proceed"
    RESTART_SCRIPT = "restart"
    DONE = "done"


@dataclass
class StageResult:
    signal: StageSignal = StageSignal.PROCEED
    returns: dict[str, Any] | None = None


_PROCEED = StageResult(StageSignal.PROCEED)


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


# ---------------------------------------------------------------------------
# Per-stage functions extracted from _build_short_impl
# ---------------------------------------------------------------------------

def _stage_retention_plan(ctx: BuildContext) -> dict[str, Any] | None:
    """Stage: retention_plan.

    Returns None to continue; returns a terminal failure dict to abort.
    On unexpected exception, re-raises (caller must propagate).
    """
    short_id = ctx.short_plan["short_id"]
    ctx.update_stage("retention_plan", "in_progress")
    try:
        ctx.check_stop()
        retention_plan = retention_plan_builder.build_retention_plan(
            ctx.long_job_dir,
            ctx.plan_for_prompt,
            ctx.channel_config,
            ctx.llm_fn,
            source_artifacts=ctx.source_artifacts,
        )
        ctx.update_stage(
            "retention_plan",
            "completed",
            generation_mode=retention_plan.get("generation_mode"),
            input_hash=retention_plan.get("input_hash"),
        )
        ctx.extras["retention_plan"] = retention_plan
        return None
    except Exception as exc:
        ctx.update_stage("retention_plan", "failed", error=str(exc))
        ctx.status["status"] = "failed"
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc


def _stage_render(ctx: BuildContext) -> None:
    """Stage: render (video + cover).

    On success, writes video_path / cover_path to ctx.extras.
    On failure, updates status and re-raises.
    """
    short_id = ctx.short_plan["short_id"]
    sd = ctx.short_dir
    ctx.update_stage("render", "in_progress")
    try:
        ctx.check_stop()
        stop_file = ctx.long_job_dir / ".stop_requested"
        try:
            video_path = ctx.render_fn(sd, ctx.channel_config, stop_request_path=stop_file)
        except TypeError:
            # Injected render_fn without the kwarg (back-compat).
            video_path = ctx.render_fn(sd, ctx.channel_config)
        ctx.check_stop()
        cover_path = ctx.cover_fn(sd, ctx.channel_config)
        ctx.update_stage("render", "completed")
        ctx.extras["video_path"] = video_path
        ctx.extras["cover_path"] = cover_path
    except Exception as exc:
        ctx.update_stage("render", "failed", error=str(exc))
        ctx.status["status"] = "failed"
        ctx.status["error"] = str(exc)
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc


def _stage_performance_memory(ctx: BuildContext) -> None:
    """Stage: performance_memory (final, after render).

    Updates the performance memory record to 'rendered' status.
    """
    short_id = ctx.short_plan["short_id"]
    anti_ai_regeneration_attempts = ctx.extras.get("anti_ai_regeneration_attempts", 0)
    short_script = ctx.extras.get("short_script", {})
    short_scenes = ctx.extras.get("short_scenes", {})
    retention_plan = ctx.extras.get("retention_plan", {})
    ctx.status.update({
        "status": "rendered",
        "rendered": True,
        "uploaded": False,
        "youtube_url": "",
        "requires_user_review": False,
        "requires_render_confirmation": False,
        "video_path": f"shorts/{short_id}/{paths.SHORT_OUTPUTS_SUBDIR}/{paths.SHORT_VIDEO_FILE}",
        "cover_path": f"shorts/{short_id}/{paths.SHORT_OUTPUTS_SUBDIR}/{paths.SHORT_COVER_FILE}",
        "anti_ai_regeneration_attempts": anti_ai_regeneration_attempts,
    })
    write_short_status(ctx.long_job_dir, short_id, ctx.status)
    ctx.update_stage("performance_memory", "in_progress")
    performance_memory.write_performance_memory(
        ctx.long_job_dir,
        short_id,
        ctx.plan_for_prompt,
        short_script,
        short_scenes,
        retention_plan,
        thumbnail_meta={
            "status": "completed",
            "mode": "disabled_for_shorts_render",
            "image_generation_called": False,
            "thumbnail_path": None,
        },
        status="rendered",
    )
    ctx.update_stage("performance_memory", "completed", memory_status="rendered")


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


def _stage_background(ctx: BuildContext) -> None:
    """Stage: background assets — resolve each scene's background source.

    Reads short_scenes + scene_pipeline_state from ctx.extras. Raises on failure.
    """
    short_id = ctx.short_plan["short_id"]
    sd = ctx.short_dir
    short_scenes = ctx.extras["short_scenes"]
    state = ctx.extras["scene_pipeline_state"]
    ctx.update_stage("background", "in_progress")
    try:
        ctx.check_stop()
        assert_latest_scenes_ready(state)
        bg_sources: list[dict[str, Any]] = []

        def _on_scene_bg(info: dict[str, Any]) -> None:
            phase = info.get("phase")
            # Record the per-scene source only once acquisition resolved.
            if phase == "resolved":
                bg_sources.append({
                    "scene_id": info.get("scene_id"),
                    "background_source": info.get("background_source"),
                })
            ctx.update_stage(
                "background",
                "in_progress",
                current_scene=(int(info.get("index", 0)) + 1),
                total_scenes=info.get("total"),
                last_scene_id=info.get("scene_id"),
                # While fetching show "…", then the resolved source label.
                last_source=(info.get("background_source") if phase == "resolved" else "fetching…"),
                scene_phase=phase,
            )

        ctx.background_fn(sd, short_scenes, ctx.channel_config, on_scene_resolved=_on_scene_bg)
        ctx.update_stage("background", "completed", per_scene=bg_sources)
    except Exception as exc:
        ctx.update_stage("background", "failed", error=str(exc))
        ctx.status["status"] = "failed"
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc


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
        rhythm_candidate = visual_rhythm.apply_visual_rhythm_to_scenes(short_scenes, visual_rhythm_plan)
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
            long_job_dir, short_id, channel_config,
            gemini_fn=gemini_fn, attempt=scenes_attempts,
        )
        check_and_apply_auto_pass(scenes_qa_result)

        # Normalize scenes QA issues
        normalized_scene_issues = []
        for item in scenes_qa_result.get("issues") or []:
            norm = qa.normalize_qa_issue(
                item, idea=short_plan, script=short_script, scenes=short_scenes,
                source="gemini_scene_qa" if scenes_qa_result.get("provider") == "gemini" else "scene_validation"
            )
            normalized_scene_issues.append(norm)
        for item in scenes_qa_result.get("required_changes") or []:
            norm = qa.normalize_qa_issue(
                item, idea=short_plan, script=short_script, scenes=short_scenes,
                source="gemini_scene_qa" if scenes_qa_result.get("provider") == "gemini" else "scene_validation"
            )
            if not any(x.detail == norm.detail for x in normalized_scene_issues):
                normalized_scene_issues.append(norm)

        scenes_qa_result["normalized_issues"] = [n.to_dict() for n in normalized_scene_issues]

        scene_blockers = [n for n in normalized_scene_issues if n.issue_class in {qa.IssueClass.HARD_BLOCKER, qa.IssueClass.REPAIRABLE_BLOCKER}]
        scene_warnings = [n for n in normalized_scene_issues if n.issue_class == qa.IssueClass.SOFT_WARNING]
        scene_suppressed = [n for n in normalized_scene_issues if n.issue_class == qa.IssueClass.STALE_OR_SUPPRESSED]

        if not scene_blockers:
            scenes_qa_result["verdict"] = "WARN" if scene_warnings else "PASS"
        else:
            scenes_qa_result["verdict"] = "FAIL"

        verdict = scenes_qa_result.get("verdict", "FAIL")
        atomic_write_json(_jd / paths.SHORT_SCENES_QA_FILE, scenes_qa_result)
        update_stage("qa_scenes", "completed" if verdict in ("PASS", "WARN") else "failed", qa_verdict=verdict)

        # Record classification and wrong context suppression for scene QA
        raw_gemini_verdict = scenes_qa_result.get("verdict")
        if raw_gemini_verdict == "FAIL" or verdict == "FAIL":
            classification_reason = "qa_hard_fail"
            if not scene_blockers:
                classification_reason = "qa_soft_warn"
                has_wrong_context = any(n.reason == "wrong_context_five_errors_rule" for n in normalized_scene_issues)
                has_noncanonical = any(n.reason == "noncanonical_count_inference" for n in normalized_scene_issues)
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
                ok=True
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
                    ok=False
                )
            elif norm.reason == "noncanonical_count_inference":
                _recorder.record_event(
                    "deterministic",
                    "noncanonical_count_inference",
                    {
                        "reason": "noncanonical_count_inference",
                        "detail": norm.detail,
                    },
                    ok=False
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
                long_job_dir, plan_for_prompt, short_script, channel_config, llm_fn,
                retention_plan=retention_plan,
                spoken_humanization=spoken_humanization,
                feedback=loop.scenes_feedback, attempt=scenes_attempts,
            )
            update_stage("scenes", "completed")
        else:
            loop.skip_generation = False
            update_stage("scenes", "completed")

        short_scenes = loop.short_scenes

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
                issue for issue in scene_retry_memory.active_issues.values()
                if issue.issue_class in {"hard_blocker", "repairable_blocker"}
                and issue.type != "slideshow_risk"
            ]
            active_memory_warnings = [
                issue for issue in scene_retry_memory.active_issues.values()
                if issue.issue_class == "soft_warning"
                or issue.type == "slideshow_risk"
            ]

            collapse_blockers = [
                i for i in collapse_issues
                if i.severity in ("blocking_error", "repairable_error")
                and i.type != "slideshow_risk"
            ]
            collapse_warnings = [
                i for i in collapse_issues
                if i.severity == "warning"
                or i.type == "slideshow_risk"
            ]

            remaining_blockers = active_memory_blockers + [
                {
                    "type": b.type,
                    "scene_id": b.scene_id,
                    "detail": b.detail,
                    "severity": b.severity,
                    "issue_class": "repairable_blocker" if b.severity == "repairable_error" else "hard_blocker",
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
                "remaining_blockers": [b.to_dict() if hasattr(b, "to_dict") else b for b in remaining_blockers],
                "remaining_warnings": [w.to_dict() if hasattr(w, "to_dict") else w for w in remaining_warnings] + [
                    {"type": "retry_collapse", "detail": "Identical scene output across retries; stopping loop."}
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
                status.update({
                    "status": "needs_review",
                    "rendered": False,
                    "uploaded": False,
                    "youtube_url": "",
                    "requires_user_review": True,
                    "qa_verdict": "FAIL",
                    "failure_stage": "qa_scenes",
                    "failure_reason": f"Scene QA retry collapse failed hard blocker: {[b.get('detail') if isinstance(b, dict) else getattr(b, 'detail', '') for b in remaining_blockers]}",
                    "regeneration_attempts": loop.total_regeneration_attempts,
                })
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
        update_stage("scenes", "failed", error="chatgpt_provider_error")
        _recorder.record_event(
            "chatgpt",
            "provider_error",
            {
                "event": "chatgpt_provider_error",
                "stage": "scene_generation",
                "action": "clear_browser_state_and_retry",
                "attempt": loop.provider_error_attempts,
                "error_snippet": snippet,
            },
            ok=False,
        )
        atomic_write_json(_jd / paths.SHORT_FAILURE_REPORT_FILE, {
            "stage": "scene_generation",
            "type": "chatgpt_provider_error",
            "attempt": loop.provider_error_attempts,
            "detail": "ChatGPT returned provider-error text instead of scene JSON.",
            "error_snippet": snippet,
        })
        if loop.provider_error_attempts > max_chatgpt_provider_retries:
            for s in status["stages"]:
                if s["status"] == "pending":
                    s["status"] = "skipped"
            status.update({
                "status": "needs_review",
                "rendered": False,
                "uploaded": False,
                "youtube_url": "",
                "requires_user_review": True,
                "qa_verdict": "PROVIDER_ERROR",
                "failure_kind": "chatgpt_provider_error",
                "failure_message": (
                    "ChatGPT provider error persisted after browser/session "
                    "cleanup and retry. This is not a scene QA failure."
                ),
                "regeneration_attempts": loop.total_regeneration_attempts,
            })
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
        validate_scenes.repair_scene_duration_if_possible(scene)

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

    structure_issues = validate_scenes.validate_scene_structure(
        scenes,
        scenes_doc=short_scenes,
        script=short_script,
        attempt=scenes_attempts,
    )

    # Auto-repair duration/narration-fit if it's the only class of hard issues remaining
    hard_errors = [
        i for i in structure_issues
        if i.severity in ("blocking_error", "repairable_error")
        and i.type in HARD_SCENE_VALIDATION_TYPES
    ]
    # Run deterministic fit-repair whenever ANY duration/narration-fit
    # hard error exists — even alongside other hard errors (e.g.
    # missing_item_coverage). Repairing the fixable fit issues here keeps
    # them from blocking the run; remaining errors still drive regen.
    if any(i.type in ("duration_cap", "scene_narration_fit") for i in hard_errors):
        repaired_any = False
        # First try simple per-scene duration clamp/extend (handles duration_cap).
        for issue in hard_errors:
            if issue.scene_id:
                scene_to_fix = next((s for s in scenes if str(s.get("id") or s.get("scene_id") or "") == issue.scene_id), None)
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
                "deterministic", "scene_narration_fit_repair", entry,
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
                _idea_items = short_script.get("idea_items") or short_script.get("points") or short_script.get("checklist") or []
                item_str = item_id
                for it in _idea_items:
                    if isinstance(it, dict) and str(it.get("item_id") or it.get("id") or "") == item_id:
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
                        ok=True
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
                        ok=True
                    )

    if did_visual_repair:
        atomic_write_json(_jd / paths.SHORT_SCENES_FILE, short_scenes)
        loop.skip_generation = True
        return _LoopAction.CONTINUE

    loop.short_scenes = short_scenes
    loop.scenes = scenes
    loop.structure_issues = structure_issues
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
    plan_for_prompt = ctx.plan_for_prompt
    channel_config = ctx.channel_config
    update_stage = ctx.update_stage
    check_stop = ctx.check_stop
    _recorder = ctx.recorder
    llm_fn = ctx.llm_fn
    gemini_fn = ctx.gemini_fn
    max_regen = ctx.max_regen
    max_structural_attempts = ctx.max_structural_attempts
    max_product_attempts = ctx.max_product_attempts
    max_chatgpt_provider_retries = ctx.max_chatgpt_provider_retries

    short_script = ctx.extras["short_script"]
    retention_plan = ctx.extras.get("retention_plan", {})
    spoken_humanization = ctx.extras["spoken_humanization"]
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
            "- If scenes are regenerated after Gemini QA, Gemini QA must run again."
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

        # Check for scene_narration_fit failures
        has_fit_failure = any(
            issue.type == "scene_narration_fit" and issue.severity in ("blocking_error", "repairable_error")
            for issue in structure_issues
        )
        if has_fit_failure:
            scene_fit_failures += 1

        structure_blocked = (
            validate_scenes.has_blocking_or_repairable(structure_issues)
            and not should_fallback_to_gemini_scene_qa(structure_issues)
        )

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

        if structure_blocked:
            if scenes_attempts == 1:
                attempt_1_failed_layout_schema = True

            # Check for scene regeneration cap
            stop_scene_retries = False
            max_limit = min(2, max_regen + 1)
            if scenes_attempts >= max_limit:
                if scenes_attempts == 2 and attempt_1_failed_layout_schema and max_regen >= 2:
                    pass
                else:
                    stop_scene_retries = True

            if stop_scene_retries:
                active_memory_blockers = [
                    issue for issue in scene_retry_memory.active_issues.values()
                    if issue.issue_class in {"hard_blocker", "repairable_blocker"}
                    and issue.type != "slideshow_risk"
                ]
                active_memory_warnings = [
                    issue for issue in scene_retry_memory.active_issues.values()
                    if issue.issue_class == "soft_warning"
                    or issue.type == "slideshow_risk"
                ]
                remaining_blockers = [
                    i for i in structure_issues
                    if i.severity in ("blocking_error", "repairable_error")
                    and i.type != "slideshow_risk"
                ] + active_memory_blockers
                remaining_warnings = [
                    i for i in structure_issues
                    if i.severity == "warning" or i.type == "slideshow_risk"
                ] + active_memory_warnings
                decision = "continued_with_warn" if not remaining_blockers else "failed_hard_blocker"
                renderable = not remaining_blockers

                max_allowed_attempts = max_limit
                if attempt_1_failed_layout_schema and max_regen >= 2:
                    max_allowed_attempts = 3
                decision_summary = {
                    "stage": "qa_scenes",
                    "attempts_used": scenes_attempts,
                    "max_attempts": max_allowed_attempts,
                    "decision": decision,
                    "renderable": renderable,
                    "remaining_blockers": [b.to_dict() if hasattr(b, "to_dict") else b for b in remaining_blockers],
                    "remaining_warnings": [w.to_dict() if hasattr(w, "to_dict") else w for w in remaining_warnings],
                    "continued_to_render": decision == "continued_with_warn",
                }
                atomic_write_json(_jd / paths.SHORT_QA_DECISION_SUMMARY_FILE, decision_summary)

                if not remaining_blockers:
                    # Downgrade slideshow_risk and warnings to WARN and continue
                    structure_blocked = False
                    scenes_passed = True
                    state.latest_scene_validation_ok = True
                    state.latest_scene_validation_version = state.current_scenes_version
                    scenes_qa_result = {
                        "verdict": "WARN",
                        "issues": validate_scenes.issues_to_dicts(structure_issues),
                        "required_changes": [],
                        "warnings": [i.detail for i in structure_issues],
                        "provider": "deterministic",
                    }
                    atomic_write_json(_jd / paths.SHORT_SCENES_QA_FILE, scenes_qa_result)
                    update_stage("qa_scenes", "completed", qa_verdict="WARN")

                    _recorder.record_event(
                        "deterministic",
                        "qa_classification",
                        {
                            "reason": "qa_soft_warn",
                        },
                        ok=True
                    )
                    save_retry_memory(scene_retry_memory, scene_memory_file)
                    break
                else:
                    scenes_passed = False
                    update_stage("qa_scenes", "failed", qa_verdict="FAIL")
                    status.update({
                        "status": "needs_review",
                        "rendered": False,
                        "uploaded": False,
                        "youtube_url": "",
                        "requires_user_review": True,
                        "qa_verdict": "FAIL",
                        "failure_stage": "qa_scenes",
                        "failure_reason": f"Scene validation failed hard blocker: {[b.get('detail') if isinstance(b, dict) else getattr(b, 'detail', '') for b in remaining_blockers]}",
                    })
                    write_short_status(long_job_dir, short_id, status)
                    save_retry_memory(scene_retry_memory, scene_memory_file)

                    _recorder.record_event(
                        "deterministic",
                        "qa_classification",
                        {
                            "reason": "scene_validation_fail",
                        },
                        ok=True
                    )
                    return StageResult(StageSignal.PROCEED, returns=status)

            repair_plan = validate_scenes.build_scene_repair_plan(
                scenes,
                structure_issues,
                script=short_script,
            )
            atomic_write_json(_jd / paths.SHORT_SCENE_REPAIR_FILE, {
                "attempt": scenes_attempts,
                **repair_plan,
            })
            _recorder.record_event(
                "deterministic",
                "scene_repair_plan",
                {"attempt": scenes_attempts, **repair_plan},
            )
            scenes_qa_result = {
                "verdict": "FAIL",
                "issues": validate_scenes.issues_to_dicts(structure_issues),
                "required_changes": repair_plan["instructions"],
                "warnings": [
                    issue.detail for issue in structure_issues
                    if issue.severity == "warning"
                ],
                "provider": "deterministic",
                "repair_plan": repair_plan,
            }
            atomic_write_json(_jd / paths.SHORT_SCENES_QA_FILE, scenes_qa_result)
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
                ok=True
            )

            # Track deterministic issues in retry memory
            active_validation_ids = set()
            for issue in structure_issues:
                issue_id = make_stable_issue_id("scene_validation", issue.scene_id, issue.type, issue.detail)
                active_validation_ids.add(issue_id)
                required_change = "\n".join(issue.instructions) if getattr(issue, "instructions", None) else (issue.repair_hint or issue.detail)

                issue_class_val = "soft_warning" if issue.severity == "warning" else ("repairable_blocker" if issue.severity == "repairable_error" else "hard_blocker")
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
                    reason=reason_val
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
                    scene = next((s for s in scenes if str(s.get("id") or s.get("scene_id") or "") == issue.scene_id), None)
                    if scene:
                        covers = scene.get("covers_items") or []
                        narration = str(scene.get("narration") or "").lower()
                        suppress = False
                        for cid in covers:
                            if str(cid) in narration or "cinco" in narration or "cuatro" in narration or "tres" in narration or "dos" in narration or "uno" in narration:
                                suppress = True
                        if suppress:
                            suppress_issue_by_id(scene_retry_memory, issue_id)

            structural_attempts += 1
            status["qa_scenes_structural_attempts"] = structural_attempts
            write_short_status(long_job_dir, short_id, status)
            if scene_fit_failures >= 2:
                escalate_to_script = True
                break
            if structural_attempts >= max_structural_attempts:
                save_retry_memory(scene_retry_memory, scene_memory_file)
                from video_agent.shorts.idea_preservation import derive_idea_items
                exact_mapping_items = short_plan.get("idea_items") or derive_idea_items(short_plan)
                exact_mapping_context = "\n".join(f"{i+1}. {item.get('label') or item.get('topic') or item}" for i, item in enumerate(exact_mapping_items)) if exact_mapping_items else ""
                scenes_feedback = generate_cumulative_feedback(
                    scene_retry_memory, scenes_attempts + 1,
                    candidate_summary=f"Scenes attempt {scenes_attempts} failed deterministic validation.",
                    exact_mapping_context=exact_mapping_context
                )
                break

            save_retry_memory(scene_retry_memory, scene_memory_file)
            from video_agent.shorts.idea_preservation import derive_idea_items
            exact_mapping_items = short_plan.get("idea_items") or derive_idea_items(short_plan)
            exact_mapping_context = "\n".join(f"{i+1}. {item.get('label') or item.get('topic') or item}" for i, item in enumerate(exact_mapping_items)) if exact_mapping_items else ""
            scenes_feedback = generate_cumulative_feedback(
                scene_retry_memory, scenes_attempts + 1,
                candidate_summary=f"Scenes attempt {scenes_attempts} failed deterministic validation.",
                exact_mapping_context=exact_mapping_context
            )
            continue

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

            scene_warnings = [n for n in normalized_scene_issues if n.issue_class == qa.IssueClass.SOFT_WARNING]
            decision = "continued_with_warn" if scenes_qa_result.get("verdict") == "WARN" else "passed"

            decision_summary = {
                "stage": "qa_scenes",
                "attempts_used": scenes_attempts,
                "max_attempts": max_allowed_attempts,
                "decision": decision,
                "renderable": True,
                "remaining_blockers": [],
                "remaining_warnings": [w.to_dict() if hasattr(w, "to_dict") else w for w in scene_warnings],
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
                reason=norm.reason
            )
            if retry_issue.status == "suppressed":
                scene_retry_memory.suppressed_issues[issue_id] = retry_issue
                suppress_issue_by_id(scene_retry_memory, issue_id)
            else:
                add_or_update_issue(scene_retry_memory, retry_issue)

        for issue_id in list(scene_retry_memory.active_issues.keys()):
            issue = scene_retry_memory.active_issues[issue_id]
            if issue.stage == "scene_qa" and issue_id not in active_qa_ids and issue_id not in suppressed_qa_ids:
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
            if scenes_attempts == 2 and attempt_1_failed_layout_schema and not renderable and max_regen >= 2:
                pass
            else:
                stop_scene_retries = True

        if stop_scene_retries:
            remaining_blockers = [
                n for n in normalized_scene_issues
                if n.issue_class in {qa.IssueClass.HARD_BLOCKER, qa.IssueClass.REPAIRABLE_BLOCKER}
                and n.issue_type != "slideshow_risk"
            ]
            remaining_warnings = [
                n for n in normalized_scene_issues
                if n.issue_class == qa.IssueClass.SOFT_WARNING
                or n.issue_type == "slideshow_risk"
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

            print(f"Scene QA stopped after {scenes_attempts} attempts.\n"
                  f"Remaining blockers: {[b.detail for b in remaining_blockers]}\n"
                  f"Remaining warnings: {[w.detail for w in remaining_warnings]}\n"
                  f"Renderable: {renderable}\n"
                  f"Decision: {decision}")

            if decision == "failed_hard_blocker":
                status.update({
                    "status": "needs_review",
                    "rendered": False,
                    "uploaded": False,
                    "youtube_url": "",
                    "requires_user_review": True,
                    "qa_verdict": "FAIL",
                    "failure_stage": "qa_scenes",
                    "failure_reason": f"Scene QA failed hard blocker: {[b.detail for b in remaining_blockers]}",
                })
                write_short_status(long_job_dir, short_id, status)
                save_retry_memory(scene_retry_memory, scene_memory_file)
                return StageResult(StageSignal.PROCEED, returns=status)
            else:
                save_retry_memory(scene_retry_memory, scene_memory_file)
                break

        # --- Product quality repair strategy ---
        summary = qa.summarize_product_scores(
            scenes_qa_result.get("product_scores") or {}
        )
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
        exact_mapping_context = "\n".join(f"{i+1}. {item.get('label') or item.get('topic') or item}" for i, item in enumerate(exact_mapping_items)) if exact_mapping_items else ""
        scenes_feedback = generate_cumulative_feedback(
            scene_retry_memory, scenes_attempts + 1, candidate_summary=candidate_summary, exact_mapping_context=exact_mapping_context
        )


    if escalate_to_script:
        script_feedback = build_script_compression_feedback(short_script)
        atomic_write_json(_jd / paths.SHORT_FAILURE_REPORT_FILE, {
            "stage": "scenes",
            "attempt": scenes_attempts,
            "detail": "Escalating to script compression due to repeated scene_narration_fit failures.",
            "feedback": script_feedback,
        })
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
            atomic_write_json(_jd / paths.SHORT_FAILURE_REPORT_FILE, {
                "stage": "qa_scenes",
                "best_candidate_available": True,
                "deterministic_issues": validate_scenes.issues_to_dicts(candidate_issues),
                "llm_qa": best_scene_candidate_qa,
                "latest_scene_qa_ok": state.latest_scene_qa_ok,
                "latest_scene_qa_version": state.latest_scene_qa_version,
                "detail": "Gemini scene QA verdict was FAIL; audio, SEO, and render are blocked.",
            })

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
    gemini_fn = ctx.gemini_fn
    music_track = ctx.music_track
    max_regen = ctx.max_regen
    long_video_url = ctx.long_video_url
    source_artifacts = ctx.source_artifacts

    script_attempts = ctx.extras["script_attempts"]
    script_feedback = ctx.extras["script_feedback"]
    prev_script_hash = ctx.extras["prev_script_hash"]
    retention_plan = ctx.extras.get("retention_plan", {})
    script_retry_memory = ctx.extras["script_retry_memory"]
    script_memory_file = ctx.extras["script_memory_file"]

    # --- Stage 1: Script ---
    update_stage("script", "in_progress")
    try:
        check_stop()
        candidate = short_script_builder.build_short_script(
            long_job_dir, plan_for_prompt, channel_config, llm_fn,
            source_artifacts=source_artifacts,
            retention_plan=retention_plan,
            feedback=script_feedback, attempt=script_attempts,
            write_to_disk=False,
        )

        # Check for script completeness and structure
        from video_agent.shorts.validation.checks import validate_full_short_script_candidate, classify_script_validation
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

        errors = validate_full_short_script_candidate(candidate, short_plan, sm)

        if errors:
            if "audio_fit_over_soft_budget" in errors:
                ctx.extras["script_feedback"] = (
                    script_feedback +
                    "\n\nCRITICAL SYSTEM REJECTION: Audio fit failed. "
                    "Reduce narration to <= 65 words. "
                    "Keep 5 items. "
                    "Move details to visuals. "
                    "Do not change CTA."
                )
            else:
                ctx.extras["script_feedback"] = (
                    script_feedback +
                    "\n\nCRITICAL SYSTEM REJECTION: You returned a partial script or failed strict structural requirements! "
                    "You MUST return the FULL script from start to finish. "
                    "Errors detected: " + ", ".join(errors)
                )
            verdict = classify_script_validation(errors)
            _recorder.record_event("deterministic", "script_validation", {"verdict": verdict, "errors": errors})
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
            long_job_dir, short_id, channel_config,
            music_track=music_track, gemini_fn=gemini_fn, attempt=script_attempts,
        )
        check_and_apply_auto_pass(script_qa_result)
        atomic_write_json(_jd / paths.SHORT_SCRIPT_QA_FILE, script_qa_result)
        ctx.extras["script_qa_result"] = script_qa_result
        verdict = script_qa_result.get("verdict", "FAIL")
        update_stage("qa_script", "completed" if verdict in ("PASS", "WARN") else "failed", qa_verdict=verdict)

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
                sm = source_map.build_source_map(long_job_dir, short_plan, short_script, channel_config, long_video_url)
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
            status.update({
                "status": "needs_review",
                "rendered": False,
                "uploaded": False,
                "youtube_url": "",
                "requires_user_review": True,
                "qa_verdict": "FAIL",
                "failure_stage": "qa_script",
                "failure_reason": failure_reason,
                "regeneration_attempts": script_attempts,
            })
            write_short_status(long_job_dir, short_id, status)
            return StageResult(StageSignal.PROCEED, returns=status)

    prev_script_hash = cur_script_hash
    ctx.extras["prev_script_hash"] = prev_script_hash

    # Update hook dynamically
    status["hook"] = str(short_script.get("hook") or "")
    write_short_status(long_job_dir, short_id, status)

    # Build Source Map early so Script QA can read it
    try:
        sm = source_map.build_source_map(long_job_dir, short_plan, short_script, channel_config, long_video_url)
        atomic_write_json(_jd / paths.SHORT_SOURCE_MAP_FILE, sm)
    except Exception:
        pass

    # --- Stage 2: QA Script ---
    update_stage("qa_script", "in_progress")
    try:
        check_stop()
        script_qa_result = qa.run_short_script_qa(
            long_job_dir, short_id, channel_config,
            music_track=music_track, gemini_fn=gemini_fn, attempt=script_attempts,
        )
        check_and_apply_auto_pass(script_qa_result)

        # Normalize script QA issues
        normalized_script_issues = []
        for item in script_qa_result.get("issues") or []:
            norm = qa.normalize_qa_issue(item, idea=short_plan, script=short_script, scenes={}, source="script_qa")
            normalized_script_issues.append(norm)
        for item in script_qa_result.get("required_changes") or []:
            norm = qa.normalize_qa_issue(item, idea=short_plan, script=short_script, scenes={}, source="script_qa")
            if not any(x.detail == norm.detail for x in normalized_script_issues):
                normalized_script_issues.append(norm)

        script_qa_result["normalized_issues"] = [n.to_dict() for n in normalized_script_issues]

        script_blockers = [n for n in normalized_script_issues if n.issue_class in {qa.IssueClass.HARD_BLOCKER, qa.IssueClass.REPAIRABLE_BLOCKER}]
        script_warnings = [n for n in normalized_script_issues if n.issue_class == qa.IssueClass.SOFT_WARNING]

        if not script_blockers:
            script_qa_result["verdict"] = "WARN" if script_warnings else "PASS"
        else:
            script_qa_result["verdict"] = "FAIL"

        atomic_write_json(_jd / paths.SHORT_SCRIPT_QA_FILE, script_qa_result)
        verdict = script_qa_result.get("verdict", "FAIL")
        update_stage("qa_script", "completed" if verdict in ("PASS", "WARN") else "failed", qa_verdict=verdict)

        # Record classification and wrong context suppression for script QA
        raw_gemini_verdict = script_qa_result.get("verdict")
        if raw_gemini_verdict == "FAIL" or verdict == "FAIL":
            classification_reason = "qa_hard_fail"
            if not script_blockers:
                classification_reason = "qa_soft_warn"
                has_wrong_context = any(n.reason == "wrong_context_five_errors_rule" for n in normalized_script_issues)
                has_noncanonical = any(n.reason == "noncanonical_count_inference" for n in normalized_script_issues)
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
                ok=True
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
                    ok=False
                )
            elif norm.reason == "noncanonical_count_inference":
                _recorder.record_event(
                    "deterministic",
                    "noncanonical_count_inference",
                    {
                        "reason": "noncanonical_count_inference",
                        "detail": norm.detail,
                    },
                    ok=False
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
                reason=norm.reason
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
        exact_mapping_context = "\n".join(f"{i+1}. {item.get('label') or item.get('topic') or item}" for i, item in enumerate(exact_mapping_items)) if exact_mapping_items else ""
        script_feedback = generate_cumulative_feedback(script_retry_memory, script_attempts + 1, exact_mapping_context=exact_mapping_context)
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
        add_or_update_issue(script_retry_memory, RetryIssue(
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
        ))
        save_retry_memory(script_retry_memory, script_memory_file)
        if anti_ai_regeneration_attempts <= 1 and script_attempts < max_regen + 1:
            from video_agent.shorts.idea_preservation import derive_idea_items
            exact_mapping_items = short_plan.get("idea_items") or derive_idea_items(short_plan)
            exact_mapping_context = "\n".join(f"{i+1}. {item.get('label') or item.get('topic') or item}" for i, item in enumerate(exact_mapping_items)) if exact_mapping_items else ""
            script_feedback = generate_cumulative_feedback(
                script_retry_memory, script_attempts + 1, exact_mapping_context=exact_mapping_context
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
        status.update({
            "status": "failed",
            "rendered": False,
            "uploaded": False,
            "youtube_url": "",
            "requires_user_review": True,
            "qa_verdict": "FAIL",
            "failure_stage": "anti_ai_review",
            "failure_reason": issue_text,
            "anti_ai_regeneration_attempts": anti_ai_regeneration_attempts,
        })
        write_short_status(long_job_dir, short_id, status)
        return StageResult(StageSignal.PROCEED, returns=status)

    return _PROCEED


def _stage_audio(ctx: BuildContext) -> StageResult:
    """Stage: audio TTS + audio_fit check.

    Reads from ctx.extras: short_scenes, scene_pipeline_state, short_script, and
    the counters total_regeneration_attempts / scenes_attempts /
    structural_attempts / product_attempts (for the failure payload). Writes
    narration_wav + duration_sec back to ctx.extras. Returns a terminal status
    payload when audio_fit fails; otherwise PROCEED. Raises on unexpected errors.
    """
    short_id = ctx.short_plan["short_id"]
    long_job_dir = ctx.long_job_dir
    sd = ctx.short_dir
    _jd = ctx.json_dir
    status = ctx.status
    channel_config = ctx.channel_config
    update_stage = ctx.update_stage
    check_stop = ctx.check_stop
    _recorder = ctx.recorder
    tts_fn = ctx.tts_fn

    short_scenes = ctx.extras["short_scenes"]
    short_script = ctx.extras["short_script"]
    state = ctx.extras["scene_pipeline_state"]
    total_regeneration_attempts = ctx.extras["total_regeneration_attempts"]
    scenes_attempts = ctx.extras["scenes_attempts"]
    structural_attempts = ctx.extras["structural_attempts"]
    product_attempts = ctx.extras["product_attempts"]

    update_stage("audio", "in_progress")
    try:
        check_stop()
        assert_latest_scenes_ready(state)
        # Shorts TTS runs with dynamic_sync=False (see shorts.audio): each
        # scene's audio is padded to its planned duration_sec, so the single
        # narration track stays aligned with the per-scene visual sequences.
        # Keep the planned scene durations the renderer already uses — do NOT
        # overwrite them with raw speech lengths, which desyncs audio/video.
        narration_wav = tts_fn(sd, short_scenes, channel_config)
        duration_sec = float(_scene_duration_sum(short_scenes) or short_scenes.get("total_duration_sec") or 0.0)
        short_scenes["total_duration_sec"] = round(duration_sec, 1)
        narration_audio_sec = validate_scenes.probe_audio_duration_sec(narration_wav)
        tail_added_total = 0.0
        tail_distribution: list[dict[str, Any]] = []
        if narration_audio_sec is not None:
            tail_repair = validate_scenes.extend_scene_durations_for_audio_tail(
                short_scenes,
                narration_audio_sec
            )
            tail_added_total = float(tail_repair.get("added_sec") or 0.0)
            tail_distribution = list(tail_repair.get("tail_repair_distribution") or [])
            if tail_repair.get("changed"):
                duration_sec = float(_scene_duration_sum(short_scenes) or short_scenes.get("total_duration_sec") or duration_sec)
                short_scenes["total_duration_sec"] = round(duration_sec, 1)

                state.current_scenes_version += 1
                state.latest_scene_validation_ok = False
                state.latest_scene_validation_version = None

                re_issues = validate_scenes.validate_scene_structure(
                    short_scenes.get("scenes") or [],
                    scenes_doc=short_scenes,
                    script=short_script,
                )
                if not validate_scenes.has_blocking_or_repairable(re_issues):
                    state.latest_scene_validation_ok = True
                    state.latest_scene_validation_version = state.current_scenes_version
                    if state.latest_scene_qa_ok:
                        state.latest_scene_qa_version = state.current_scenes_version
                duration_sec = float(_scene_duration_sum(short_scenes) or short_scenes.get("total_duration_sec") or duration_sec)
                short_scenes["total_duration_sec"] = round(duration_sec, 1)

                atomic_write_json(_jd / paths.SHORT_SCENES_FILE, short_scenes)
                _recorder.record_event(
                    "deterministic",
                    "audio_tail_repair",
                    {
                        "verdict": "PASS",
                        "render_duration_sec": duration_sec,
                        "narration_audio_sec": round(narration_audio_sec, 3),
                        **tail_repair,
                    },
                    ok=True,
                )

        if narration_audio_sec is not None:
            sync_summary = validate_scenes.audio_sync_summary(
                render_duration_sec=duration_sec,
                narration_audio_sec=narration_audio_sec,
                tail_added_sec=tail_added_total,
                tail_repair_distribution=tail_distribution,
            )
            atomic_write_json(_jd / paths.SHORT_AUDIO_SYNC_SUMMARY_FILE, sync_summary)
            _recorder.record_event(
                "deterministic",
                "audio_sync_summary",
                sync_summary,
                ok=sync_summary["verdict"] != "FAIL",
            )

        audio_fit_passed = True
        audio_issue = None
        if narration_audio_sec is not None:
            audio_issue = validate_scenes.validate_audio_fit(duration_sec, narration_audio_sec)
            if audio_issue:
                audio_fit_passed = False

        if audio_fit_passed:
            state.latest_audio_tail_ok = True
            state.latest_audio_tail_version = state.current_scenes_version
        else:
            state.latest_audio_tail_ok = False
            state.latest_audio_tail_version = None

        if not audio_fit_passed:
            repair_plan = validate_scenes.build_scene_repair_plan(
                short_scenes.get("scenes") or [],
                [audio_issue],
                script=short_script,
            )
            _recorder.record_event(
                "deterministic",
                "audio_fit",
                {
                    "verdict": "FAIL",
                    "issue": audio_issue.to_dict(),
                    "render_duration_sec": duration_sec,
                    "narration_audio_sec": round(narration_audio_sec, 3),
                    "repair_plan": repair_plan,
                },
                ok=False,
            )
            update_stage("audio", "failed", error=audio_issue.detail)
            atomic_write_json(_jd / paths.SHORT_FAILURE_REPORT_FILE, {
                "stage": "audio",
                "issues": [audio_issue.to_dict()],
                "render_duration_sec": duration_sec,
                "narration_audio_sec": round(narration_audio_sec, 3),
                "repair_plan": repair_plan,
            })
            status.update({
                "status": "needs_review",
                "rendered": False,
                "uploaded": False,
                "youtube_url": "",
                "requires_user_review": True,
                "qa_verdict": "FAIL",
                "duration_sec": round(duration_sec, 1),
                "audio_fit_issue": audio_issue.to_dict(),
                "regeneration_attempts": total_regeneration_attempts,
                "qa_scenes_attempts": scenes_attempts,
                "qa_scenes_structural_attempts": structural_attempts,
                "qa_scenes_product_attempts": product_attempts,
            })
            write_short_status(long_job_dir, short_id, status)
            ctx.extras["narration_wav"] = narration_wav
            ctx.extras["duration_sec"] = duration_sec
            return StageResult(StageSignal.PROCEED, returns=status)
        else:
            audio_fit_passed = True
            update_stage(
                "audio",
                "completed",
                qa_verdict="PASS",
                render_duration_sec=round(duration_sec, 1),
                narration_audio_sec=round(narration_audio_sec, 3) if narration_audio_sec is not None else None,
            )
    except Exception as exc:
        update_stage("audio", "failed")
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    ctx.extras["narration_wav"] = narration_wav
    ctx.extras["duration_sec"] = duration_sec
    return _PROCEED


def _stage_seo(ctx: BuildContext) -> None:
    """Stage: SEO. Runs only after audio_fit passes. Raises on failure."""
    short_id = ctx.short_plan["short_id"]
    short_script = ctx.extras["short_script"]
    retention_plan = ctx.extras.get("retention_plan", {})
    ctx.update_stage("seo", "in_progress")
    try:
        ctx.check_stop()
        short_seo_builder.build_short_seo(
            ctx.long_job_dir, short_id, ctx.plan_for_prompt, short_script,
            ctx.channel_config, ctx.llm_fn, ctx.long_video_url,
            retention_plan=retention_plan,
            history_recorder=ctx.recorder,
        )
        ctx.update_stage("seo", "completed")
    except Exception as exc:
        ctx.update_stage("seo", "failed")
        ctx.status["status"] = "failed"
        write_short_status(ctx.long_job_dir, short_id, ctx.status)
        raise exc


def build_short(
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    *,
    llm_fn: Callable[..., str] = _default_llm_fn,
    gemini_fn: Callable[[str], str] | None = None,
    background_fn: Callable[..., None] = _default_background_fn,
    tts_fn: Callable[..., Path] = _default_tts_fn,
    mix_fn: Callable[..., Path] = _default_mix_fn,
    render_fn: Callable[..., Path] = _default_render_fn,
    cover_fn: Callable[..., Path] = _default_cover_fn,
    long_video_url: str = "",
    require_render_confirmation: bool = False,
    source_artifacts: dict | None = None,
) -> dict[str, Any]:
    short_id = short_plan["short_id"]
    _jd = paths.short_json_dir(long_job_dir, short_id)
    _jd.mkdir(parents=True, exist_ok=True)
    try:
        return _build_short_impl(
            long_job_dir,
            short_plan,
            channel_config,
            llm_fn=llm_fn,
            gemini_fn=gemini_fn,
            background_fn=background_fn,
            tts_fn=tts_fn,
            mix_fn=mix_fn,
            render_fn=render_fn,
            cover_fn=cover_fn,
            long_video_url=long_video_url,
            require_render_confirmation=require_render_confirmation,
            source_artifacts=source_artifacts,
        )
    finally:
        history_file = _jd / paths.SHORT_LLM_HISTORY_FILE
        if history_file.exists():
            try:
                budget_summary = call_budget.build_call_budget_summary(
                    llm_history.read_history(history_file)
                )
                atomic_write_json(_jd / paths.SHORT_CALL_BUDGET_SUMMARY_FILE, budget_summary)
                recorder = llm_history.LLMHistoryRecorder(history_file)
                recorder.record_event(
                    "deterministic",
                    "call_budget_summary",
                    budget_summary,
                    ok=budget_summary["verdict"] != "FAIL",
                )
            except Exception:
                pass


def _build_short_impl(
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    *,
    llm_fn: Callable[..., str] = _default_llm_fn,
    gemini_fn: Callable[[str], str] | None = None,
    background_fn: Callable[..., None] = _default_background_fn,
    tts_fn: Callable[..., Path] = _default_tts_fn,
    mix_fn: Callable[..., Path] = _default_mix_fn,
    render_fn: Callable[..., Path] = _default_render_fn,
    cover_fn: Callable[..., Path] = _default_cover_fn,
    long_video_url: str = "",
    require_render_confirmation: bool = False,
    source_artifacts: dict | None = None,
) -> dict[str, Any]:
    short_id = short_plan["short_id"]
    sd = paths.short_dir(long_job_dir, short_id)
    sd.mkdir(parents=True, exist_ok=True)
    paths.short_tmp_dir(long_job_dir, short_id).mkdir(parents=True, exist_ok=True)
    _jd = paths.short_json_dir(long_job_dir, short_id)
    _jd.mkdir(parents=True, exist_ok=True)
    _od = paths.short_outputs_dir(long_job_dir, short_id)
    _od.mkdir(parents=True, exist_ok=True)

    # Record every ChatGPT + Gemini prompt/response for this Short — including
    # failed QA verdicts and every regeneration retry — to one JSONL file.
    _recorder = llm_history.LLMHistoryRecorder(_jd / paths.SHORT_LLM_HISTORY_FILE)
    llm_fn = _recorder.wrap(llm_fn, "chatgpt")
    llm_fn = wrap_llm_with_provider_retries(llm_fn, _recorder, "chatgpt")
    if gemini_fn is not None:
        gemini_fn = _recorder.wrap(gemini_fn, "gemini", default_kind="qa")
        gemini_fn = wrap_llm_with_provider_retries(gemini_fn, _recorder, "gemini")

    ap = (channel_config.get("shorts") or {}).get("autopilot") or {}
    max_regen = int(ap.get("max_regeneration_attempts", 4))
    # Separate retry budgets so deterministic structural failures and Gemini
    # product-quality failures do not starve each other inside one shared loop.
    max_structural_attempts = int(ap.get("max_structural_attempts", max_regen + 1))
    max_product_attempts = int(ap.get("max_product_repair_attempts", max_regen + 1))
    # Provider errors (ChatGPT "Something went wrong…") get their own retry budget
    # so a browser failure never consumes a creative scene-regeneration attempt.
    max_chatgpt_provider_retries = 0
    music_track = short_plan.get("music_track")
    cover_words = int(((channel_config.get("shorts") or {}).get("cover") or {}).get("text_max_words", 5))

    atomic_write_json(_jd / paths.SHORT_IDEA_FILE, short_plan)

    # Initialize basic info and stages
    base = {
        "short_id": short_id,
        "source_long_job_id": long_job_dir.name,
        "format": short_plan.get("format"),
        "idea_id": short_plan.get("idea_id"),
        "hook": "",
        "cover_text": "",
        "duration_sec": 0.0,
        "score": short_plan.get("score"),
        "qa_verdict": "PENDING",
        "regeneration_attempts": 0,
        "qa_scenes_attempts": 0,
        "qa_scenes_structural_attempts": 0,
        "qa_scenes_product_attempts": 0,
        "music_track": music_track,
        "source_scene_ids": short_plan.get("source_scene_ids") or short_plan.get("scene_ids") or [],
        "voice": {
            "provider": (channel_config.get("shorts") or {}).get("tts", {}).get("provider", "kokoro"),
            "voice_id": (channel_config.get("shorts") or {}).get("tts", {}).get("voice_id", "ef_dora"),
            "speed": (channel_config.get("shorts") or {}).get("tts", {}).get("speed", 1.07),
        },
    }

    stages = [
        {"name": "retention_plan", "label": "Retention Plan", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "script", "label": "Short Script", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "qa_script", "label": "QA Script", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "spoken_humanization", "label": "Spoken Humanization", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "scenes", "label": "Short Scenes", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "visual_rhythm_plan", "label": "Visual Rhythm Plan", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "qa_scenes", "label": "QA Scenes", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "anti_ai_review", "label": "Anti-AI Review", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "background", "label": "Background Assets", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "audio", "label": "Audio TTS & Mix", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "seo", "label": "Short SEO", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "render", "label": "Video & Cover Render", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "performance_memory", "label": "Performance Memory", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
    ]

    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status = {
        **base,
        "status": "generating",
        "rendered": False,
        "uploaded": False,
        "stages": stages,
        "created_at": started_at,
        "updated_at": started_at,
        # Liveness signal consumed by shorts.status orphan recovery: refreshed on
        # every update_stage so a build that dies mid-stage goes stale.
        "heartbeat_at": started_at,
    }

    def update_stage(stage_name: str, new_status: str, **kwargs):
        _update_short_stage(status, stage_name, new_status, **kwargs)
        write_short_status(long_job_dir, short_id, status)
        if new_status in {"completed", "failed", "skipped"}:
            payload = {"stage": stage_name, "status": new_status, **kwargs}
            if "verdict" not in payload:
                qa_verdict = payload.get("qa_verdict")
                if qa_verdict:
                    payload["verdict"] = qa_verdict
                elif new_status == "completed":
                    payload["verdict"] = "PASS"
                elif new_status == "failed":
                    payload["verdict"] = "FAIL"
            _recorder.record_event(
                "deterministic",
                "stage_status",
                payload,
                ok=new_status != "failed",
            )

    def check_stop():
        if (long_job_dir / ".stop_requested").exists() or (sd / ".stop_requested").exists():
            from fastapi import HTTPException
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Stop requested by operator.",
                    "stop_requested": True,
                }
            )

    script_qa_result: dict[str, Any] = {"verdict": "FAIL", "issues": ["not_generated"]}
    short_script: dict[str, Any] = {}
    normalized_script_issues: list[Any] = []
    normalized_scene_issues: list[Any] = []
    script_feedback = ""
    script_attempts = 0
    scenes_attempts = 0
    structural_attempts = 0
    product_attempts = 0
    total_regeneration_attempts = 0

    plan_for_prompt = {**short_plan, "source_long_job_id": long_job_dir.name}
    script_qa_result: dict[str, Any] = {"verdict": "FAIL", "issues": ["not_generated"]}
    normalized_script_issues: list[dict[str, Any]] = []
    scenes_qa_result: dict[str, Any] = {"verdict": "FAIL", "issues": ["not_generated"]}
    short_scenes: dict[str, Any] = {}
    scenes_feedback = ""
    best_scene_candidate = None
    best_scene_candidate_qa = None
    retention_plan: dict[str, Any] = {}
    spoken_humanization: dict[str, Any] = {}
    visual_rhythm_plan: dict[str, Any] = {}
    anti_ai_review: dict[str, Any] = {}
    anti_ai_regeneration_attempts = 0

    narration_wav = None
    duration_sec = 0.0

    script_memory_file = _jd / "script_retry_memory.json"
    script_retry_memory = load_retry_memory(script_memory_file)
    if script_retry_memory is None:
        script_retry_memory = RetryMemory(stage="script")
        script_retry_memory.hard_invariants = [
            "- Preserve source fidelity.",
            "- Preserve idea_contract.original_count when must_preserve_count=true.",
            "- Do not invent unsupported claims.",
            "- Do not use unsafe/medical fear framing."
        ]

    # Build the shared context object for per-stage functions.
    _ctx = BuildContext(
        long_job_dir=long_job_dir,
        short_dir=sd,
        json_dir=_jd,
        outputs_dir=_od,
        short_plan=short_plan,
        plan_for_prompt=plan_for_prompt,
        channel_config=channel_config,
        llm_fn=llm_fn,
        gemini_fn=gemini_fn,
        background_fn=background_fn,
        tts_fn=tts_fn,
        mix_fn=mix_fn,
        render_fn=render_fn,
        cover_fn=cover_fn,
        status=status,
        recorder=_recorder,
        update_stage=update_stage,
        check_stop=check_stop,
        max_regen=max_regen,
        max_structural_attempts=max_structural_attempts,
        max_product_attempts=max_product_attempts,
        max_chatgpt_provider_retries=max_chatgpt_provider_retries,
        music_track=music_track,
        cover_words=cover_words,
        long_video_url=long_video_url,
        require_render_confirmation=require_render_confirmation,
        source_artifacts=source_artifacts,
    )

    # --- Stage: retention_plan ---
    _stage_retention_plan(_ctx)
    retention_plan = _ctx.extras["retention_plan"]

    prev_script_hash = None
    # We use a while loop for script generation, allowing loop back on audio_fit failure
    while script_attempts < max_regen + 1:
        script_attempts += 1
        total_regeneration_attempts = (script_attempts - 1) + max(0, scenes_attempts - 1)

        # --- Stages 1+2: Script + QA Script ---
        _ctx.extras["script_attempts"] = script_attempts
        _ctx.extras["script_feedback"] = script_feedback
        _ctx.extras["prev_script_hash"] = prev_script_hash
        _ctx.extras["retention_plan"] = retention_plan
        _ctx.extras["script_retry_memory"] = script_retry_memory
        _ctx.extras["script_memory_file"] = script_memory_file
        _script_result = _stage_script(_ctx)
        
        if _script_result.returns is not None:
            return _script_result.returns
        if _script_result.signal is StageSignal.DONE:
            short_script = _ctx.extras["short_script"]
            script_qa_result = _ctx.extras.get("script_qa_result", script_qa_result)
            normalized_script_issues = _ctx.extras.get("normalized_script_issues", normalized_script_issues)
            prev_script_hash = _ctx.extras.get("prev_script_hash")
            break
        if _script_result.signal is StageSignal.RESTART_SCRIPT:
            script_feedback = _ctx.extras["script_feedback"]
            prev_script_hash = _ctx.extras.get("prev_script_hash")
            continue

        short_script = _ctx.extras["short_script"]
        script_qa_result = _ctx.extras.get("script_qa_result", script_qa_result)
        normalized_script_issues = _ctx.extras.get("normalized_script_issues", normalized_script_issues)
        prev_script_hash = _ctx.extras.get("prev_script_hash")

        _ctx.extras["short_script"] = short_script
        _ctx.extras["retention_plan"] = retention_plan
        _stage_spoken_humanization(_ctx)
        spoken_humanization = _ctx.extras["spoken_humanization"]

        # --- Stages: scenes -> visual_rhythm -> qa_scenes (inner regen loop) ---
        _ctx.extras["short_script"] = short_script
        _ctx.extras["retention_plan"] = retention_plan
        _ctx.extras["spoken_humanization"] = spoken_humanization
        _ctx.extras["script_attempts"] = script_attempts
        _ctx.extras["script_feedback"] = script_feedback
        _scenes_result = _stage_scenes(_ctx)
        if _scenes_result.returns is not None:
            return _scenes_result.returns
        # PROCEED / RESTART_SCRIPT / DONE all publish the carry-over values.
        short_scenes = _ctx.extras["short_scenes"]
        scenes_qa_result = _ctx.extras["scenes_qa_result"]
        state = _ctx.extras["scene_pipeline_state"]
        scenes_attempts = _ctx.extras["scenes_attempts"]
        structural_attempts = _ctx.extras["structural_attempts"]
        product_attempts = _ctx.extras["product_attempts"]
        total_regeneration_attempts = _ctx.extras["total_regeneration_attempts"]
        if _scenes_result.signal is StageSignal.RESTART_SCRIPT:
            script_feedback = _ctx.extras["script_feedback"]
            continue
        if _scenes_result.signal is StageSignal.DONE:
            break

        # Scenes passed! Graphic validation -> performance_memory -> anti_ai_review.
        _ctx.extras["short_script"] = short_script
        _ctx.extras["short_scenes"] = short_scenes
        _ctx.extras["retention_plan"] = retention_plan
        _ctx.extras["script_retry_memory"] = script_retry_memory
        _ctx.extras["script_memory_file"] = script_memory_file
        _ctx.extras["script_attempts"] = script_attempts
        _ctx.extras["anti_ai_regeneration_attempts"] = anti_ai_regeneration_attempts
        _anti_ai_result = _stage_anti_ai_review(_ctx)
        anti_ai_review = _ctx.extras.get("anti_ai_review", {})
        anti_ai_regeneration_attempts = _ctx.extras["anti_ai_regeneration_attempts"]
        if _anti_ai_result.returns is not None:
            return _anti_ai_result.returns
        if _anti_ai_result.signal is StageSignal.RESTART_SCRIPT:
            script_feedback = _ctx.extras["script_feedback"]
            continue

        # Stage: Background assets — resolve each scene's background (Pexels
        # video/photo, ChatGPT image, or placeholder) BEFORE audio, reporting the
        # source scene-by-scene so the UI shows exactly where each came from.
        _ctx.extras["short_scenes"] = short_scenes
        _ctx.extras["scene_pipeline_state"] = state
        _stage_background(_ctx)

        # Stage 6: Audio TTS & exact audio_fit check
        _ctx.extras["short_scenes"] = short_scenes
        _ctx.extras["short_script"] = short_script
        _ctx.extras["scene_pipeline_state"] = state
        _ctx.extras["total_regeneration_attempts"] = total_regeneration_attempts
        _ctx.extras["scenes_attempts"] = scenes_attempts
        _ctx.extras["structural_attempts"] = structural_attempts
        _ctx.extras["product_attempts"] = product_attempts
        _audio_result = _stage_audio(_ctx)
        narration_wav = _ctx.extras["narration_wav"]
        duration_sec = _ctx.extras["duration_sec"]
        if _audio_result.returns is not None:
            return _audio_result.returns

        # Stage 5: SEO (Only runs after audio_fit passes!)
        _ctx.extras["short_script"] = short_script
        _ctx.extras["retention_plan"] = retention_plan
        _stage_seo(_ctx)

        hook = str(short_script.get("hook") or "")
        cover_text = _cover_text(hook, cover_words)
        duration_sec = float(
            short_scenes.get("total_duration_sec")
            or sum(float(s.get("duration_sec") or 0.0) for s in (short_scenes.get("scenes") or []))
            or short_script.get("target_duration_sec")
            or 0.0
        )

        # Save finalized metadata to status
        status.update({
            "hook": hook,
            "cover_text": cover_text,
            "duration_sec": round(duration_sec, 1),
            "qa_verdict": "PASS",
            "regeneration_attempts": total_regeneration_attempts,
            "qa_scenes_attempts": scenes_attempts,
            "qa_scenes_structural_attempts": structural_attempts,
            "qa_scenes_product_attempts": product_attempts,
        })
        write_short_status(long_job_dir, short_id, status)

        if require_render_confirmation:
            _write_render_props(sd, short_scenes, channel_config, music_track)
            update_stage("audio", "pending")
            update_stage("render", "pending")
            status.update({
                "status": "ready_for_render",
                "rendered": False,
                "uploaded": False,
                "youtube_url": "",
                "requires_user_review": False,
                "requires_render_confirmation": True,
                "video_path": None,
                "cover_path": None,
            })
            write_short_status(long_job_dir, short_id, status)
            return status

        # Mix and finalize audio
        try:
            check_stop()
            mix_fn(sd, narration_wav, music_track, channel_config, duration_sec)
            _write_render_props(sd, short_scenes, channel_config, music_track)
            update_stage("audio", "completed")
        except Exception as exc:
            update_stage("audio", "failed")
            status["status"] = "failed"
            write_short_status(long_job_dir, short_id, status)
            raise exc

        # All stages completed successfully! Break out of outer loop.
        break

    # If loops finished but script/scene QA didn't pass, handle review status.
    # Fix C4: a soft scene-QA WARN (deterministic validation already passed) is
    # acceptable for render — only FAIL blocks.
    #
    # "Max attempts reached" is NOT automatically fatal: this terminal gate must
    # separate a genuine hard blocker (safety / source / schema / render
    # contract) from leftover warnings or capped repairable quality issues. Only
    # a hard blocker stops the pipeline with an explicit, reviewable reason; the
    # generic "QA failed after max regeneration attempts" message is never the
    # source of truth — the structured decision below is.
    if script_qa_result["verdict"] not in ("PASS", "WARN") or scenes_qa_result["verdict"] not in ("PASS", "WARN"):
        if script_qa_result["verdict"] not in ("PASS", "WARN"):
            fail_stage = "qa_script"
            blocker_details = _qa_blocker_details(script_qa_result)
        else:
            fail_stage = "qa_scenes"
            blocker_details = _qa_blocker_details(scenes_qa_result)

        decision = "failed_hard_blocker"
        failure_reason = (
            f"{fail_stage} hard blocker after {total_regeneration_attempts + 1} attempt(s): "
            + "; ".join(blocker_details)
            if blocker_details
            else f"{fail_stage} did not pass after the maximum regeneration attempts."
        )

        decision_summary = {
            "stage": fail_stage,
            "attempts_used": total_regeneration_attempts + 1,
            "max_attempts": max_regen + 1,
            "decision": decision,
            "renderable": False,
            "remaining_blockers": [{"detail": d} for d in blocker_details],
            "remaining_warnings": [],
            "continued_to_render": False,
        }
        atomic_write_json(_jd / paths.SHORT_QA_DECISION_SUMMARY_FILE, decision_summary)

        # Mark remaining pending stages as skipped
        for s in status["stages"]:
            if s["status"] == "pending":
                s["status"] = "skipped"
        status.update({
            "status": "needs_review",
            "rendered": False,
            "uploaded": False,
            "youtube_url": "",
            "requires_user_review": True,
            "qa_verdict": "FAIL",
            "failure_stage": fail_stage,
            "failure_reason": failure_reason,
            "duration_sec": round(duration_sec, 1),
            "regeneration_attempts": total_regeneration_attempts,
            "qa_scenes_attempts": scenes_attempts,
            "qa_scenes_structural_attempts": structural_attempts,
            "qa_scenes_product_attempts": product_attempts,
        })
        write_short_status(long_job_dir, short_id, status)
        return status

    # --- Stage: render ---
    _stage_render(_ctx)

    # --- Stage: performance_memory (final, after render) ---
    _ctx.extras.update({
        "short_script": short_script,
        "short_scenes": short_scenes,
        "retention_plan": retention_plan,
        "anti_ai_regeneration_attempts": anti_ai_regeneration_attempts,
    })
    _stage_performance_memory(_ctx)

    return status
