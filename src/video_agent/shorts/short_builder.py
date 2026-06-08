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



def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _update_short_stage(status: dict[str, Any], stage_name: str, new_status: str, *, now_str: str | None = None, **kwargs) -> None:
    now_str = now_str or datetime.datetime.now(datetime.timezone.utc).isoformat()
    for s in status["stages"]:
        if s["name"] != stage_name:
            continue

        previous_status = str(s.get("status") or "pending")
        s["status"] = new_status

        if new_status == "pending":
            s["started_at"] = None
            s["completed_at"] = None
            s["actual_seconds"] = None
            s.pop("error", None)
            s.pop("qa_verdict", None)
        elif new_status == "in_progress":
            if previous_status != "in_progress" or not s.get("started_at"):
                s["started_at"] = now_str
            s["completed_at"] = None
            s["actual_seconds"] = None
            s.pop("error", None)
            s.pop("qa_verdict", None)
        elif new_status in ("completed", "failed", "skipped"):
            if not s.get("started_at"):
                s["started_at"] = now_str
            s["completed_at"] = now_str
            try:
                from datetime import datetime as dt
                t_start = dt.fromisoformat(str(s["started_at"]).replace("Z", "+00:00"))
                t_end = dt.fromisoformat(now_str.replace("Z", "+00:00"))
                s["actual_seconds"] = max(0, int((t_end - t_start).total_seconds()))
            except Exception:
                s["actual_seconds"] = 1

        for k, v in kwargs.items():
            s[k] = v
        break

    status["updated_at"] = now_str
    status["heartbeat_at"] = now_str


def _cover_text(hook: str, max_words: int) -> str:
    words = [w for w in str(hook).strip().strip("¿?¡!.,").split() if w]
    return " ".join(words[:max_words]).upper()


def build_script_compression_feedback(short_script: dict[str, Any] | None) -> str:
    script = short_script or {}
    contract = script.get("idea_contract") or {}
    allowed_points = allowed_spoken_points_from_contract(contract)
    count_label = str(contract.get("count_label") or "items").strip() or "items"
    point_line = (
        f"- Keep all {allowed_points} promised {count_label}."
        if allowed_points
        else "- Keep 3-4 spoken points if it improves retention and no locked count exists."
    )
    return "\n".join([
        "SCRIPT COMPRESSION REQUIRED:",
        "- Scene-level narration fit failed after 2 attempts.",
        point_line,
        "- Make each item shorter and more natural.",
        "- Move supporting detail to on-screen text, captions, visual action, or layout_payload.",
        "- Use only source-supported language from the current idea.",
        "- If it still cannot fit without rushed narration or poor readability, return split_recommended.",
        "- Do not reduce the promised count unless adaptation_allowed=true.",
    ])


HARD_SCENE_VALIDATION_TYPES = {
    "missing_item_coverage",
    "unknown_item_coverage",
    "layout",
    "payload",
    "audio_fit",
    "source_support",
    "safety",
    "duration_range",
    "duration_cap",
    "scene_narration_fit",
    "empty_scenes",
    "first_scene_layout",
    "last_scene_cta",
}


def should_fallback_to_gemini_scene_qa(issues: list[validate_scenes.SceneValidationIssue]) -> bool:
    """Allow scene QA fallback only when deterministic issues are genuinely soft."""
    if not issues:
        return True
    for issue in issues:
        if issue.severity in {"blocking_error", "repairable_error"}:
            if issue.type in {"slideshow_risk", "visual_only_unreadable"}:
                return False
            return False
        if issue.type in HARD_SCENE_VALIDATION_TYPES:
            return False
    return True


# -- default real side-effect implementations (wired lazily) ----------------

def _default_llm_fn(kind: str, prompt: str) -> str:  # pragma: no cover - needs browser
    raise NotImplementedError("llm_fn must be injected (browser ChatGPT sender).")


def _default_tts_fn(short_dir: Path, short_scenes: dict, channel_config: dict) -> Path:
    from video_agent.shorts.audio import synthesize_short_narration

    return synthesize_short_narration(short_dir, short_scenes, channel_config)


def _default_mix_fn(short_dir: Path, narration_wav: Path, music_track: str, channel_config: dict, duration_sec: float) -> Path:
    from video_agent.shorts.audio_mixer import mix_short_audio

    return mix_short_audio(short_dir, narration_wav, music_track, channel_config, duration_sec)


def _default_render_fn(short_dir: Path, channel_config: dict, stop_request_path: Path | None = None) -> Path:
    from video_agent.shorts.renderer import render_short_video

    return render_short_video(short_dir, channel_config, stop_request_path=stop_request_path)


def _default_cover_fn(short_dir: Path, channel_config: dict) -> Path:
    from video_agent.shorts.renderer import render_short_cover

    return render_short_cover(short_dir, channel_config)


def _default_thumbnail_fn(long_job_dir: Path, short_id: str, channel_config: dict) -> Path | None:
    """No-op default: skip thumbnail generation unless an image_fn is injected."""
    return None


# Average product score (0-10) at/above which a soft-only scene-QA FAIL is
# auto-passed as WARN instead of regenerated (spec §6).
SCORE_AUTOPASS_AVERAGE = 8.5

MAX_QA_RETRIES_PER_STAGE = 1
MAX_SCENE_REGEN_ATTEMPTS = 2
MAX_SCRIPT_REGEN_ATTEMPTS = 1
MAX_PROVIDER_RETRIES_PER_CALL = 3


_HARD_QA_ISSUE_MARKERS = (
    "safety", "source_fidelity", "source_support", "idea", "schema",
    "layout", "contract", "audio_fit", "duration_cap", "product_quality",
    "missing_item", "first_scene", "last_scene", "empty_scenes",
)


def _scene_qa_has_hard_fail(result: dict[str, Any]) -> bool:
    """True when a scene-QA result carries a blocking/repairable issue that must
    be regenerated. Soft suggestions (severity warning, no required changes) are
    not hard fails."""
    for item in (result.get("issues") or []):
        if not isinstance(item, dict):
            # Bare string issue: treat as soft suggestion.
            continue
        severity = str(item.get("severity") or "").lower()
        if severity in {"blocking_error", "repairable_error", "major"}:
            return True
        itype = str(item.get("type") or "").lower()
        if any(marker in itype for marker in _HARD_QA_ISSUE_MARKERS):
            return True
    # A concrete required change is a hard ask.
    return bool(result.get("required_changes"))


def has_hard_fail(result: dict[str, Any]) -> bool:
    if result.get("provider") == "rule_based" and result.get("verdict") == "FAIL":
        return True
    issues = result.get("issues") or []
    for item in issues:
        if isinstance(item, str):
            item_lower = item.lower()
            if any(m in item_lower for m in ["safety", "source_fidelity", "source_support", "health_claim", "disclaimer", "medical", "contract"]):
                return True
            continue
        itype = str(item.get("type") or "").lower()
        severity = str(item.get("severity") or "").lower()
        detail = str(item.get("detail") or "").lower()
        if severity == "blocking_error":
            return True
        hard_markers = {
            "safety", "source_fidelity", "source_support", "idea", "schema",
            "layout", "contract", "first_scene", "empty_scenes", "greeting",
            "disclaimer", "medical", "overclaim", "narration", "source_map"
        }
        if any(m in itype for m in hard_markers):
            if "product_quality" in itype:
                continue
            return True
        if any(m in detail for m in ["safety", "source_fidelity", "source_support", "health claim", "medical overclaim"]):
            return True
    return False


def check_and_apply_auto_pass(qa_result: dict[str, Any]) -> bool:
    verdict = qa_result.get("verdict", "FAIL")
    if verdict in {"PASS", "WARN"}:
        return True
    if has_hard_fail(qa_result):
        return False
    from video_agent.shorts.qa import parse_defensive_score
    p_scores = qa_result.get("product_scores") or {}
    avg_product = 0.0
    if p_scores:
        vals = [parse_defensive_score(v) for v in p_scores.values()]
        avg_product = sum(vals) / len(vals) if vals else 0.0
    q_scores = qa_result.get("scores") or {}
    avg_quality = 0.0
    if q_scores:
        vals = [parse_defensive_score(v) for v in q_scores.values()]
        avg_quality = sum(vals) / len(vals) if vals else 0.0
    if avg_product >= 8.5 or avg_quality >= 85:
        qa_result["verdict"] = "WARN"
        qa_result["forced_pass_reason"] = "high_score_no_hard_fail"
        return True
    return False



def _normalized_script_hash(script: dict[str, Any]) -> str:
    from video_agent.shorts.quality_hash import stable_hash
    hook = str(script.get("hook") or "").strip().lower()
    narration = str(script.get("narration") or "").strip().lower()
    cta = str(script.get("cta") or "").strip().lower()
    idea_items = script.get("idea_items") or script.get("points") or script.get("checklist") or []
    if isinstance(idea_items, list):
        idea_items = [str(item).strip().lower() for item in idea_items]
    norm = {
        "hook": hook,
        "narration": narration,
        "cta": cta,
        "idea_items": idea_items
    }
    return stable_hash(norm)


def _normalized_scene_hash(short_scenes: dict[str, Any]) -> str:
    """Stable hash of the render-relevant scene fields, used to detect a retry
    loop where the generator keeps emitting the same output (spec §8)."""
    from video_agent.shorts.quality_hash import stable_hash

    norm = []
    for sc in (short_scenes.get("scenes") or []):
        payload = sc.get("layout_payload") or {}
        norm.append({
            "layout": sc.get("layout"),
            "narration": (sc.get("narration") or "").strip().lower(),
            "caption": (sc.get("caption") or "").strip().lower(),
            "on_screen_text": (sc.get("on_screen_text") or "").strip().lower(),
            "duration_sec": round(float(sc.get("duration_sec") or 0.0), 1),
            "title": (payload.get("title") or ""),
            "items": payload.get("items") or payload.get("bullets") or [],
        })
    return stable_hash(norm)


def _scene_duration_sum(scenes_doc: dict[str, Any]) -> float:
    return round(
        sum(float(scene.get("duration_sec") or 0.0) for scene in ((scenes_doc or {}).get("scenes") or [])),
        1,
    )


def _snapshot_scene_durations(scenes_doc: dict[str, Any]) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    for index, scene in enumerate((scenes_doc or {}).get("scenes") or []):
        scene_id = str(scene.get("id") or scene.get("scene_id") or index)
        snapshot[scene_id] = float(scene.get("duration_sec") or 0.0)
    return snapshot


def _restore_scene_durations(scenes_doc: dict[str, Any], snapshot: dict[str, float]) -> None:
    if not snapshot:
        return
    for index, scene in enumerate((scenes_doc or {}).get("scenes") or []):
        scene_id = str(scene.get("id") or scene.get("scene_id") or index)
        if scene_id in snapshot:
            scene["duration_sec"] = snapshot[scene_id]
    scenes_doc["total_duration_sec"] = _scene_duration_sum(scenes_doc)


def record_retry_event(
    recorder: llm_history.LLMHistoryRecorder,
    reason: str,
    scope: str,
    attempt: int,
    max_attempts: int,
    hard_fail: bool,
    source_stage: str,
    details: dict | None = None
) -> None:
    payload = {
        "retry_reason": reason,
        "retry_scope": scope,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "hard_fail": hard_fail,
        "source_stage": source_stage,
    }
    if details:
        payload.update(details)
    recorder.record_event(
        "deterministic",
        "retry_classification",
        payload,
        ok=True,
    )


def wrap_llm_with_provider_retries(original_llm_fn: Callable[..., str], recorder: llm_history.LLMHistoryRecorder, provider: str) -> Callable[..., str]:
    def wrapped(*args: Any, **kwargs: Any) -> str:
        from video_agent.shorts.short_scene_builder import is_provider_error_text, ChatGPTProviderError
        from video_agent.shorts.llm_history import _guess_kind

        prompt = ""
        if len(args) == 2:
            prompt = str(args[1])
        elif len(args) == 1:
            prompt = str(args[0])

        stage_name = _guess_kind(provider, prompt)

        last_error = None
        for attempt in range(1, MAX_PROVIDER_RETRIES_PER_CALL + 1):
            try:
                res = original_llm_fn(*args, **kwargs)
                if is_provider_error_text(res):
                    raise ChatGPTProviderError("Provider error text returned by LLM", snippet=res[:200])
                return res
            except Exception as exc:
                last_error = exc
                record_retry_event(
                    recorder,
                    reason="provider_error",
                    scope="provider_only",
                    attempt=attempt,
                    max_attempts=MAX_PROVIDER_RETRIES_PER_CALL,
                    hard_fail=True,
                    source_stage=stage_name,
                    details={"error": str(exc)}
                )
                if attempt == MAX_PROVIDER_RETRIES_PER_CALL:
                    raise exc
        raise last_error or RuntimeError("Provider retries exhausted")
    return wrapped


def build_short(
    long_job_dir: Path,
    short_plan: dict,
    channel_config: dict,
    *,
    llm_fn: Callable[..., str] = _default_llm_fn,
    gemini_fn: Callable[[str], str] | None = None,
    tts_fn: Callable[..., Path] = _default_tts_fn,
    mix_fn: Callable[..., Path] = _default_mix_fn,
    render_fn: Callable[..., Path] = _default_render_fn,
    cover_fn: Callable[..., Path] = _default_cover_fn,
    thumbnail_fn: Callable[..., Path | None] = _default_thumbnail_fn,
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
            tts_fn=tts_fn,
            mix_fn=mix_fn,
            render_fn=render_fn,
            cover_fn=cover_fn,
            thumbnail_fn=thumbnail_fn,
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
    tts_fn: Callable[..., Path] = _default_tts_fn,
    mix_fn: Callable[..., Path] = _default_mix_fn,
    render_fn: Callable[..., Path] = _default_render_fn,
    cover_fn: Callable[..., Path] = _default_cover_fn,
    thumbnail_fn: Callable[..., Path | None] = _default_thumbnail_fn,
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
        {"name": "seo", "label": "Short SEO", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "thumbnail", "label": "Thumbnail", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "audio", "label": "Audio TTS & Mix", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
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
    script_feedback = ""
    script_attempts = 0
    scenes_attempts = 0
    structural_attempts = 0
    product_attempts = 0
    total_regeneration_attempts = 0

    plan_for_prompt = {**short_plan, "source_long_job_id": long_job_dir.name}
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

    update_stage("retention_plan", "in_progress")
    try:
        check_stop()
        retention_plan = retention_plan_builder.build_retention_plan(
            long_job_dir,
            plan_for_prompt,
            channel_config,
            llm_fn,
            source_artifacts=source_artifacts,
        )
        update_stage(
            "retention_plan",
            "completed",
            generation_mode=retention_plan.get("generation_mode"),
            input_hash=retention_plan.get("input_hash"),
        )
    except Exception as exc:
        update_stage("retention_plan", "failed", error=str(exc))
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    prev_script_hash = None
    # We use a while loop for script generation, allowing loop back on audio_fit failure
    while script_attempts < max_regen + 1:
        script_attempts += 1
        total_regeneration_attempts = (script_attempts - 1) + max(0, scenes_attempts - 1)

        # --- Stage 1: Script ---
        update_stage("script", "in_progress")
        try:
            check_stop()
            short_script = short_script_builder.build_short_script(
                long_job_dir, plan_for_prompt, channel_config, llm_fn,
                source_artifacts=source_artifacts,
                retention_plan=retention_plan,
                feedback=script_feedback, attempt=script_attempts,
            )
            update_stage("script", "completed")
        except Exception as exc:
            update_stage("script", "failed")
            status["status"] = "failed"
            write_short_status(long_job_dir, short_id, status)
            raise exc

        cur_script_hash = _normalized_script_hash(short_script)
        if prev_script_hash is not None and cur_script_hash == prev_script_hash:
            script_qa_result = qa.run_short_script_qa(
                long_job_dir, short_id, channel_config,
                music_track=music_track, gemini_fn=gemini_fn, attempt=script_attempts,
            )
            check_and_apply_auto_pass(script_qa_result)
            atomic_write_json(_jd / paths.SHORT_SCRIPT_QA_FILE, script_qa_result)
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
                break
            else:
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
                    "regeneration_attempts": script_attempts,
                })
                write_short_status(long_job_dir, short_id, status)
                return status

        prev_script_hash = cur_script_hash

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
            script_feedback = generate_cumulative_feedback(script_retry_memory, script_attempts + 1)
            continue


        update_stage("spoken_humanization", "in_progress")
        try:
            check_stop()
            spoken_humanization = humanization.build_spoken_humanization(
                long_job_dir,
                short_id,
                short_script,
                retention_plan,
                channel_config,
                llm_fn,
            )
            update_stage(
                "spoken_humanization",
                "completed",
                generation_mode=spoken_humanization.get("generation_mode"),
                rewrite_discarded=spoken_humanization.get("rewrite_discarded", False),
            )
        except Exception as exc:
            update_stage("spoken_humanization", "failed", error=str(exc))
            status["status"] = "failed"
            write_short_status(long_job_dir, short_id, status)
            raise exc

        # Script passed! Reset and run the Scenes loop
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
        while scenes_attempts < _scenes_loop_ceiling:
            scenes_attempts += 1
            total_regeneration_attempts = (script_attempts - 1) + (scenes_attempts - 1)
            status["qa_scenes_attempts"] = scenes_attempts
            write_short_status(long_job_dir, short_id, status)

            # --- Stage 3: Scenes ---
            update_stage("scenes", "in_progress")
            try:
                check_stop()
                short_scenes = short_scene_builder.build_short_scenes(
                    long_job_dir, plan_for_prompt, short_script, channel_config, llm_fn,
                    retention_plan=retention_plan,
                    spoken_humanization=spoken_humanization,
                    feedback=scenes_feedback, attempt=scenes_attempts,
                )
                update_stage("scenes", "completed")
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
                if prev_scene_hash is not None and cur_scene_hash == prev_scene_hash:
                    # Run deterministic repairs first so they are present in final scenes
                    scenes = short_scenes.get("scenes") or []
                    for scene in scenes:
                        validate_scenes.repair_scene_duration_if_possible(scene)
                    validate_scenes.repair_weak_hook_motion(scenes)
                    _idea_items = (
                        short_script.get("idea_items")
                        or short_script.get("points")
                        or short_script.get("checklist")
                        or []
                    )
                    if isinstance(_idea_items, list):
                        for item in _idea_items:
                            item_str = str(item).strip()
                            if item_str:
                                validate_scenes.repair_visual_only_unreadable(scenes, item_str)

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
                    if attempt_1_failed_layout_schema and max_regen >= 2:
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
                        best_scene_candidate = dict(short_scenes)
                        best_scene_candidate_qa = {"verdict": "WARN", "collapsed": True}
                        scenes_qa_result = {"verdict": "WARN", "collapsed": True, "qa_pass": True}
                        scenes_passed = True
                        state.latest_scene_validation_ok = True
                        state.latest_scene_validation_version = state.current_scenes_version
                        state.latest_scene_qa_ok = True
                        state.latest_scene_qa_version = state.current_scenes_version
                        atomic_write_json(_jd / paths.SHORT_SCENES_FILE, short_scenes)
                        update_stage("qa_scenes", "completed", qa_verdict="WARN")
                        scene_collapsed = True
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
                            "failure_reason": f"Scene QA retry collapse failed hard blocker: {[b.get('detail') if isinstance(b, dict) else getattr(b, 'detail', '') for b in remaining_blockers]}",
                            "regeneration_attempts": total_regeneration_attempts,
                        })
                        write_short_status(long_job_dir, short_id, status)
                        save_retry_memory(scene_retry_memory, scene_memory_file)
                        return status
                prev_scene_hash = cur_scene_hash
            except short_scene_builder.ChatGPTProviderError as exc:
                # Provider/browser failure — NOT a creative scene-QA failure.
                # The recovery-wrapped llm_fn already cleared cookies + reopened a
                # fresh temp chat; here we just keep it off the creative budget and
                # surface a non-QA failure if the provider keeps erroring.
                provider_error_attempts += 1
                snippet = getattr(exc, "snippet", "")
                update_stage("scenes", "failed", error="chatgpt_provider_error")
                _recorder.record_event(
                    "chatgpt",
                    "provider_error",
                    {
                        "event": "chatgpt_provider_error",
                        "stage": "scene_generation",
                        "action": "clear_browser_state_and_retry",
                        "attempt": provider_error_attempts,
                        "error_snippet": snippet,
                    },
                    ok=False,
                )
                atomic_write_json(_jd / paths.SHORT_FAILURE_REPORT_FILE, {
                    "stage": "scene_generation",
                    "type": "chatgpt_provider_error",
                    "attempt": provider_error_attempts,
                    "detail": "ChatGPT returned provider-error text instead of scene JSON.",
                    "error_snippet": snippet,
                })
                if provider_error_attempts > max_chatgpt_provider_retries:
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
                        "regeneration_attempts": total_regeneration_attempts,
                    })
                    write_short_status(long_job_dir, short_id, status)
                    return status
                # Do NOT consume the scenes/creative budget for a provider error.
                scenes_attempts -= 1
                continue
            except Exception as exc:
                update_stage("scenes", "failed")
                status["status"] = "failed"
                write_short_status(long_job_dir, short_id, status)
                raise exc

            scenes = short_scenes.get("scenes") or []
            for scene in scenes:
                validate_scenes.repair_scene_duration_if_possible(scene)

            # Deterministic repair: weak hook motion (spec §10.1)
            if validate_scenes.repair_weak_hook_motion(scenes):
                state.current_scenes_version += 1
                state.latest_scene_validation_ok = False
                state.latest_scene_validation_version = None

            # Deterministic repair: visual_only_unreadable (spec §10.2)
            # Extract required idea items from the script
            _idea_items = (
                short_script.get("idea_items")
                or short_script.get("points")
                or short_script.get("checklist")
                or []
            )
            if isinstance(_idea_items, list):
                for item in _idea_items:
                    item_str = str(item).strip()
                    if item_str and validate_scenes.repair_visual_only_unreadable(scenes, item_str):
                        state.current_scenes_version += 1
                        state.latest_scene_validation_ok = False
                        state.latest_scene_validation_version = None

            update_stage("visual_rhythm_plan", "in_progress")
            try:
                check_stop()
                visual_rhythm_plan = visual_rhythm.build_visual_rhythm_plan(
                    long_job_dir,
                    short_id,
                    short_scenes,
                    retention_plan,
                    channel_config,
                )
                rhythm_candidate = visual_rhythm.apply_visual_rhythm_to_scenes(short_scenes, visual_rhythm_plan)
                rhythm_issues = validate_scenes.validate_scene_structure(
                    rhythm_candidate.get("scenes") or [],
                    scenes_doc=rhythm_candidate,
                    script=short_script,
                    attempt=scenes_attempts,
                )
                if validate_scenes.has_blocking_or_repairable(rhythm_issues):
                    update_stage("visual_rhythm_plan", "completed", qa_verdict="WARN", discarded=True)
                else:
                    short_scenes = rhythm_candidate
                    scenes = short_scenes.get("scenes") or []
                    state.current_scenes_version += 1
                    update_stage(
                        "visual_rhythm_plan",
                        "completed",
                        qa_verdict="PASS",
                        generation_mode=visual_rhythm_plan.get("generation_mode"),
                    )
            except Exception as exc:
                update_stage("visual_rhythm_plan", "failed", error=str(exc))
                status["status"] = "failed"
                write_short_status(long_job_dir, short_id, status)
                raise exc

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
            if hard_errors and all(i.type in ("duration_cap", "scene_narration_fit") for i in hard_errors):
                repaired_any = False
                for issue in hard_errors:
                    if issue.scene_id:
                        scene_to_fix = next((s for s in scenes if str(s.get("id") or s.get("scene_id") or "") == issue.scene_id), None)
                        if scene_to_fix:
                            res = validate_scenes.repair_scene_duration_if_possible(scene_to_fix)
                            if res in ("auto_shortened", "auto_extended", "auto_shortened_cta"):
                                repaired_any = True
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
                        return status

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
                    break

                save_retry_memory(scene_retry_memory, scene_memory_file)
                scenes_feedback = generate_cumulative_feedback(
                    scene_retry_memory, scenes_attempts + 1,
                    candidate_summary=f"Scenes attempt {scenes_attempts} failed deterministic validation."
                )
                continue

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
                    return status
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
            scenes_feedback = generate_cumulative_feedback(
                scene_retry_memory, scenes_attempts + 1,
                candidate_summary=candidate_summary
            )


        if escalate_to_script:
            script_feedback = build_script_compression_feedback(short_script)
            atomic_write_json(_jd / paths.SHORT_FAILURE_REPORT_FILE, {
                "stage": "scenes",
                "attempt": scenes_attempts,
                "detail": "Escalating to script compression due to repeated scene_narration_fit failures.",
                "feedback": script_feedback,
            })
            continue

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
            break

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

        if anti_ai_review.get("verdict") == "FAIL":
            anti_ai_regeneration_attempts += 1
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
                script_feedback = generate_cumulative_feedback(
                    script_retry_memory,
                    script_attempts + 1,
                    candidate_summary=(
                        "ANTI-AI REVIEW FAILED: regenerate/refine script and downstream scenes once "
                        "while preserving the same short_id, source map, and idea contract."
                    ),
                )
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
                continue

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
            return status

        # Stage 6: Audio TTS & exact audio_fit check
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
                return status
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

        # Stage 5: SEO (Only runs after audio_fit passes!)
        update_stage("seo", "in_progress")
        try:
            check_stop()
            short_seo_builder.build_short_seo(
                long_job_dir, short_id, plan_for_prompt, short_script, channel_config, llm_fn, long_video_url,
                retention_plan=retention_plan,
            )
            update_stage("seo", "completed")
        except Exception as exc:
            update_stage("seo", "failed")
            status["status"] = "failed"
            write_short_status(long_job_dir, short_id, status)
            raise exc

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

        # Stage 5b: Thumbnail
        update_stage("thumbnail", "in_progress")
        try:
            check_stop()
            update_stage(
                "thumbnail",
                "completed",
                mode="disabled_for_shorts_render",
                image_generation_called=False,
                thumbnail_path=None,
            )
        except Exception as exc:
            update_stage("thumbnail", "failed", error=str(exc))

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
    if script_qa_result["verdict"] != "PASS" or scenes_qa_result["verdict"] not in ("PASS", "WARN"):
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
            "duration_sec": round(duration_sec, 1),
            "regeneration_attempts": total_regeneration_attempts,
            "qa_scenes_attempts": scenes_attempts,
            "qa_scenes_structural_attempts": structural_attempts,
            "qa_scenes_product_attempts": product_attempts,
        })
        write_short_status(long_job_dir, short_id, status)
        return status

    # --- Stage 6: Render ---
    update_stage("render", "in_progress")
    try:
        check_stop()
        # Pass the stop-request file so the Remotion render subprocess can be
        # SIGTERMed mid-render when the operator presses Stop. ``check_stop``
        # only fires between stages; without this the long render keeps running.
        stop_file = long_job_dir / ".stop_requested"
        try:
            video_path = render_fn(sd, channel_config, stop_request_path=stop_file)
        except TypeError:
            # Injected render_fn without the kwarg (back-compat).
            video_path = render_fn(sd, channel_config)
        check_stop()
        cover_path = cover_fn(sd, channel_config)
        update_stage("render", "completed")
    except Exception as exc:
        update_stage("render", "failed", error=str(exc))
        status["status"] = "failed"
        status["error"] = str(exc)
        write_short_status(long_job_dir, short_id, status)
        raise exc

    status.update({
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
    write_short_status(long_job_dir, short_id, status)
    update_stage("performance_memory", "in_progress")
    performance_memory.write_performance_memory(
        long_job_dir,
        short_id,
        plan_for_prompt,
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
    update_stage("performance_memory", "completed", memory_status="rendered")



    return status


def _write_render_props(short_dir: Path, short_scenes: dict, channel_config: dict, music_track: str | None) -> None:
    rcfg = (channel_config.get("shorts") or {}).get("render") or {}
    # Inherit performance + encoding tunables from the channel-wide render
    # config so Shorts also get VideoToolbox HW encode, Metal/ANGLE WebGL,
    # and proper concurrency on Mac. ``shorts.render`` overrides win.
    base_render = (channel_config.get("render") or {})
    duration_sec = _scene_duration_sum(short_scenes) or float(short_scenes.get("total_duration_sec") or 35)
    short_scenes["total_duration_sec"] = round(duration_sec, 1)
    render_block = {
        "composition": rcfg.get("composition", "ShortVideoStandard"),
        "thumbnail_composition": rcfg.get("thumbnail_composition", "ShortCover"),
        "resolution": rcfg.get("resolution", "1080x1920"),
        "fps": rcfg.get("fps", base_render.get("fps", 30)),
        "duration_sec": duration_sec,
        "codec": rcfg.get("codec", base_render.get("codec", "h264")),
        "video_bitrate": rcfg.get("video_bitrate", base_render.get("video_bitrate")),
        "gl": rcfg.get("gl", base_render.get("gl")),
        "concurrency": rcfg.get("concurrency", base_render.get("concurrency", "auto")),
    }
    props = {
        "render": render_block,
        # Keep these at the top level for ShortVideo.tsx + ShortCover.tsx
        # which read props.scenes / props.audio / props.music_track directly.
        "scenes": short_scenes.get("scenes") or [],
        "total_duration_sec": duration_sec,
        "audio": "audio/short_mix.m4a",
        "music_track": music_track,
    }
    jd = short_dir / paths.SHORT_JSON_SUBDIR
    jd.mkdir(parents=True, exist_ok=True)
    atomic_write_json(jd / paths.SHORT_RENDER_PROPS_FILE, props)
