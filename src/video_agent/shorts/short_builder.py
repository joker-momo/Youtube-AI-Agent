"""Build one Short end to end: generate → QA (regen loop) → audio → mix → render.

All side-effecting steps (LLM, Kokoro TTS, ffmpeg mix, Remotion render, cover)
are injected so the orchestration is unit-testable; real implementations are the
defaults used by the autopilot.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_agent.shorts import (
    anti_ai,
    call_budget,
    humanization,
    llm_history,
    paths,
    performance_memory,
    qa,
    short_scene_builder,
    short_script_builder,
    short_seo_builder,
    source_map,
    validate_scenes,
    visual_rhythm,
)
from video_agent.shorts import (
    retention_plan as retention_plan_builder,
)
from video_agent.shorts.builder.context import BuildContext

# Backwards-compatible facade: tests and callers import/patch these via
# video_agent.shorts.short_builder.<name>.
from video_agent.shorts.builder.defaults import (
    _default_background_fn,
    _default_cover_fn,
    _default_llm_fn,
    _default_mix_fn,
    _default_render_fn,
    _default_tts_fn,
)
from video_agent.shorts.builder.qa_gate import (
    _HARD_QA_ISSUE_MARKERS,
    HARD_SCENE_VALIDATION_TYPES,
    _qa_blocker_details,
    _scene_qa_has_hard_fail,
    build_script_compression_feedback,
    check_and_apply_auto_pass,
    has_hard_fail,
    should_fallback_to_gemini_scene_qa,
)
from video_agent.shorts.builder.render_props import _write_render_props
from video_agent.shorts.builder.retry import (
    MAX_PROVIDER_RETRIES_PER_CALL,
    record_retry_event,
    wrap_llm_with_provider_retries,
)
from video_agent.shorts.builder.snapshots import (
    _cover_text,
    _normalized_scene_hash,
    _normalized_script_hash,
    _parse,
    _restore_scene_durations,
    _scene_duration_sum,
    _snapshot_scene_durations,
)
from video_agent.shorts.builder.stages.media import (
    _stage_audio,
    _stage_background,
    _stage_performance_memory,
    _stage_render,
    _stage_retention_plan,
    _stage_seo,
)
from video_agent.shorts.builder.stages.scenes import (
    _LoopAction,
    _SceneLoopState,
    _scenes_generate_and_normalize,
    _scenes_product_quality_repair,
    _scenes_run_qa,
    _scenes_run_structure_validation,
    _scenes_structural_repair,
    _stage_qa_scenes,
    _stage_scenes,
    _stage_visual_rhythm,
)
from video_agent.shorts.builder.stages.script import (
    _stage_anti_ai_review,
    _stage_qa_script,
    _stage_script,
    _stage_spoken_humanization,
)
from video_agent.shorts.builder.stages.visual_acquisition import (
    _stage_visual_acquisition,
)
from video_agent.shorts.builder.stages.visual_beats import (
    _stage_visual_beats,
)
from video_agent.shorts.builder.stages.visual_local_qa import (
    _stage_visual_local_qa,
)
from video_agent.shorts.builder.stages.visual_performance import (
    _stage_visual_performance,
)
from video_agent.shorts.builder.stages.visual_schedule import (
    _stage_visual_schedule,
)
from video_agent.shorts.builder.stages.visual_spans import (
    _stage_visual_spans,
)
from video_agent.shorts.builder.status import _update_short_stage

# Back-compat facade: stages were extracted into builder.stages.* and the
# orchestration kernel into builder.types. Re-exported here so existing
# callers/tests that patch or import via video_agent.shorts.short_builder.*
# keep working unchanged.
from video_agent.shorts.builder.types import (
    _PROCEED,
    MAX_QA_RETRIES_PER_STAGE,
    MAX_SCENE_REGEN_ATTEMPTS,
    MAX_SCRIPT_REGEN_ATTEMPTS,
    SCORE_AUTOPASS_AVERAGE,
    StageResult,
    StageSignal,
)
from video_agent.shorts.idea_preservation import allowed_spoken_points_from_contract
from video_agent.shorts.manifest import write_short_status
from video_agent.shorts.retry_memory import (
    RetryIssue,
    RetryMemory,
    ScenePipelineState,
    add_or_update_issue,
    assert_latest_scenes_ready,
    generate_cumulative_feedback,
    load_retry_memory,
    make_stable_issue_id,
    resolve_issue_by_id,
    save_retry_memory,
    suppress_issue_by_id,
)
from video_agent.storage.atomic import atomic_write_json

__all__ = [
    "anti_ai",
    "call_budget",
    "humanization",
    "llm_history",
    "performance_memory",
    "paths",
    "qa",
    "retention_plan_builder",
    "short_scene_builder",
    "short_script_builder",
    "short_seo_builder",
    "source_map",
    "validate_scenes",
    "visual_rhythm",
    "allowed_spoken_points_from_contract",
    "write_short_status",
    "atomic_write_json",
    "ScenePipelineState",
    "assert_latest_scenes_ready",
    "RetryMemory",
    "RetryIssue",
    "add_or_update_issue",
    "resolve_issue_by_id",
    "suppress_issue_by_id",
    "generate_cumulative_feedback",
    "make_stable_issue_id",
    "save_retry_memory",
    "load_retry_memory",
    "_default_llm_fn",
    "_default_background_fn",
    "_default_tts_fn",
    "_default_mix_fn",
    "_default_render_fn",
    "_default_cover_fn",
    "HARD_SCENE_VALIDATION_TYPES",
    "_HARD_QA_ISSUE_MARKERS",
    "_scene_qa_has_hard_fail",
    "has_hard_fail",
    "_qa_blocker_details",
    "check_and_apply_auto_pass",
    "should_fallback_to_gemini_scene_qa",
    "build_script_compression_feedback",
    "MAX_PROVIDER_RETRIES_PER_CALL",
    "record_retry_event",
    "wrap_llm_with_provider_retries",
    "_parse",
    "_cover_text",
    "_normalized_script_hash",
    "_normalized_scene_hash",
    "_scene_duration_sum",
    "_snapshot_scene_durations",
    "_restore_scene_durations",
    "_update_short_stage",
    "_write_render_props",
    "BuildContext",
    "StageSignal",
    "StageResult",
    "_PROCEED",
    "SCORE_AUTOPASS_AVERAGE",
    "MAX_QA_RETRIES_PER_STAGE",
    "MAX_SCENE_REGEN_ATTEMPTS",
    "MAX_SCRIPT_REGEN_ATTEMPTS",
    "_LoopAction",
    "_SceneLoopState",
    "_stage_visual_rhythm",
    "_stage_qa_scenes",
    "_scenes_generate_and_normalize",
    "_scenes_run_structure_validation",
    "_scenes_structural_repair",
    "_scenes_run_qa",
    "_scenes_product_quality_repair",
    "_stage_scenes",
    "_stage_spoken_humanization",
    "_stage_script",
    "_stage_qa_script",
    "_stage_anti_ai_review",
    "_stage_visual_spans",
    "_stage_visual_beats",
    "_stage_visual_local_qa",
    "_stage_visual_performance",
    "_stage_visual_schedule",
    "_stage_retention_plan",
    "_stage_render",
    "_stage_performance_memory",
    "_stage_background",
    "_stage_audio",
    "_stage_seo",
    "build_short",
    "_build_short_impl",
]


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
    cover_words = int(
        ((channel_config.get("shorts") or {}).get("cover") or {}).get("text_max_words", 5)
    )

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
            "provider": (channel_config.get("shorts") or {})
            .get("tts", {})
            .get("provider", "kokoro"),
            "voice_id": (channel_config.get("shorts") or {})
            .get("tts", {})
            .get("voice_id", "ef_dora"),
            "speed": (channel_config.get("shorts") or {}).get("tts", {}).get("speed", 1.07),
        },
    }

    stages = [
        {
            "name": "retention_plan",
            "label": "Retention Plan",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "script",
            "label": "Short Script",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "qa_script",
            "label": "QA Script",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "spoken_humanization",
            "label": "Spoken Humanization",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "scenes",
            "label": "Short Scenes",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "visual_rhythm_plan",
            "label": "Visual Rhythm Plan",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "qa_scenes",
            "label": "QA Scenes",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "anti_ai_review",
            "label": "Anti-AI Review",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "visual_spans",
            "label": "Visual Spans",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "visual_acquisition",
            "label": "Visual Acquisition",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "background",
            "label": "Background Assets",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "audio",
            "label": "Audio TTS & Mix",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "visual_local_qa",
            "label": "Visual Local QA",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "visual_beats",
            "label": "Visual Beats",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "visual_schedule",
            "label": "Visual Schedule",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "seo",
            "label": "Short SEO",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "render",
            "label": "Video & Cover Render",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "performance_memory",
            "label": "Performance Memory",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
        {
            "name": "visual_performance",
            "label": "Visual Performance",
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "actual_seconds": None,
        },
    ]

    started_at = datetime.datetime.now(datetime.UTC).isoformat()
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
                },
            )

    script_qa_result: dict[str, Any] = {"verdict": "FAIL", "issues": ["not_generated"]}
    short_script: dict[str, Any] = {}
    normalized_script_issues: list[Any] = []
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
    retention_plan: dict[str, Any] = {}
    spoken_humanization: dict[str, Any] = {}
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
            "- Do not use unsafe/medical fear framing.",
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
            normalized_script_issues = _ctx.extras.get(
                "normalized_script_issues", normalized_script_issues
            )
            prev_script_hash = _ctx.extras.get("prev_script_hash")
            break
        if _script_result.signal is StageSignal.RESTART_SCRIPT:
            script_feedback = _ctx.extras["script_feedback"]
            prev_script_hash = _ctx.extras.get("prev_script_hash")
            # Carry the real QA result forward so a final exhausted-attempts
            # failure reports the actual blocker (e.g. audio-fit) instead of the
            # placeholder "not_generated". QA only runs after script validation
            # passes, so this stays the default placeholder on pure validation
            # rejections — which is correct.
            script_qa_result = _ctx.extras.get("script_qa_result", script_qa_result)
            normalized_script_issues = _ctx.extras.get(
                "normalized_script_issues", normalized_script_issues
            )
            continue

        short_script = _ctx.extras["short_script"]
        script_qa_result = _ctx.extras.get("script_qa_result", script_qa_result)
        normalized_script_issues = _ctx.extras.get(
            "normalized_script_issues", normalized_script_issues
        )
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
        anti_ai_regeneration_attempts = _ctx.extras["anti_ai_regeneration_attempts"]
        if _anti_ai_result.returns is not None:
            return _anti_ai_result.returns
        if _anti_ai_result.signal is StageSignal.RESTART_SCRIPT:
            script_feedback = _ctx.extras["script_feedback"]
            continue

        # Stage: Visual spans — report-only grouping over contiguous scenes
        # (spec v3.2.3 §18/§19). Acquires no media, changes no duration, and on
        # the report_only default never breaks the build; legacy render unchanged.
        _ctx.extras["short_scenes"] = short_scenes
        _ctx.extras["scene_pipeline_state"] = state
        _stage_visual_spans(_ctx)
        short_scenes = _ctx.extras["short_scenes"]

        # Stage: Visual acquisition — PR C metadata-only span search. In
        # report_only it writes shadow artifacts only and never changes assets,
        # schedules, final props, or render behavior.
        _ctx.extras["short_scenes"] = short_scenes
        _ctx.extras["visual_spans"] = _ctx.extras.get("visual_spans") or {}
        _stage_visual_acquisition(_ctx)

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

        # Stage: Visual local QA — PR D finalist download, deterministic local
        # analysis, final-duration revalidation, and bounded trim selection.
        _ctx.extras["short_scenes"] = short_scenes
        _local_qa_result = _stage_visual_local_qa(_ctx)
        if _local_qa_result.returns is not None:
            return _local_qa_result.returns

        # Stage: Visual beats — PR E bounded visual-plan selection over PR D
        # trims/assets. Disabled by default; report_only writes artifacts without
        # changing render activation.
        _ctx.extras["short_scenes"] = short_scenes
        _beats_result = _stage_visual_beats(_ctx)
        if _beats_result.returns is not None:
            return _beats_result.returns

        # Stage: Visual schedule — compile the schema-v2 frame contract from final
        # audio-corrected scene timing (spec §18/§22). report_only persists
        # artifacts only; it never activates the renderer or fails the build.
        _ctx.extras["short_scenes"] = short_scenes
        _schedule_result = _stage_visual_schedule(_ctx)
        if _schedule_result.returns is not None:
            return _schedule_result.returns

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
        status.update(
            {
                "hook": hook,
                "cover_text": cover_text,
                "duration_sec": round(duration_sec, 1),
                "qa_verdict": "PASS",
                "regeneration_attempts": total_regeneration_attempts,
                "qa_scenes_attempts": scenes_attempts,
                "qa_scenes_structural_attempts": structural_attempts,
                "qa_scenes_product_attempts": product_attempts,
            }
        )
        write_short_status(long_job_dir, short_id, status)

        if require_render_confirmation:
            _write_render_props(
                sd,
                short_scenes,
                channel_config,
                music_track,
                visual_schedule=_ctx.extras.get("visual_schedule"),
                scene_version=getattr(
                    _ctx.extras.get("scene_pipeline_state"), "current_scenes_version", None
                ),
            )
            update_stage("audio", "pending")
            update_stage("render", "pending")
            status.update(
                {
                    "status": "ready_for_render",
                    "rendered": False,
                    "uploaded": False,
                    "youtube_url": "",
                    "requires_user_review": False,
                    "requires_render_confirmation": True,
                    "video_path": None,
                    "cover_path": None,
                }
            )
            write_short_status(long_job_dir, short_id, status)
            return status

        # Mix and finalize audio
        try:
            check_stop()
            mix_fn(sd, narration_wav, music_track, channel_config, duration_sec)
            _write_render_props(
                sd,
                short_scenes,
                channel_config,
                music_track,
                visual_schedule=_ctx.extras.get("visual_schedule"),
                scene_version=getattr(
                    _ctx.extras.get("scene_pipeline_state"), "current_scenes_version", None
                ),
            )
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
    if script_qa_result["verdict"] not in ("PASS", "WARN") or scenes_qa_result["verdict"] not in (
        "PASS",
        "WARN",
    ):
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
        status.update(
            {
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
            }
        )
        write_short_status(long_job_dir, short_id, status)
        return status

    # --- Stage: render ---
    _stage_render(_ctx)

    # --- Stage: performance_memory (final, after render) ---
    _ctx.extras.update(
        {
            "short_script": short_script,
            "short_scenes": short_scenes,
            "retention_plan": retention_plan,
            "anti_ai_regeneration_attempts": anti_ai_regeneration_attempts,
        }
    )
    _stage_performance_memory(_ctx)
    _stage_visual_performance(_ctx)

    return status
