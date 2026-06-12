"""Stage-run/promote/auto routes plus scene asset generation.

Extracted from ``_legacy.py``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from video_agent.orchestrator import load_job
from video_agent.orchestrator.browser_client import BrowserClient, BrowserClientError
from video_agent.orchestrator.orchestrator import StageError
from video_agent.orchestrator.stages import (
    StageInputMissingError,
    auto_idea_research_stage,
    auto_scenes_qa_stage,
    auto_scenes_stage,
    auto_script_qa_stage,
    auto_script_stage,
    auto_seo_qa_stage,
    auto_seo_stage,
    auto_thumbnail_image_stage,
    generate_scene_asset,
    promote_scenes_stage,
    promote_seo_stage,
    promote_script_stage,
    run_render_stage,
    run_persona_eval_stage,
    run_review_stage,
    run_scenes_stage,
    run_seo_stage,
    run_script_stage,
    run_whisper_timestamps_stage,
)
from video_agent.web.approval_flow import set_approval

from video_agent.web.routes._common import (
    RawScriptRequest,
    _enqueue_stage_command,
    _handle_browser_client_error,
    _one_shot_with_briefing,
    _safe_job_dir,
    get_browser_client,
    get_channel_path,
    get_jobs_root,
)

router = APIRouter()


@router.post("/jobs/{job_id}/stages/script/run")
def post_run_script(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_script_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/script/promote")
def post_promote_script(
    job_id: str,
    payload: RawScriptRequest,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = promote_script_stage(job_dir, channel_path, payload.raw_response)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    set_approval(job_dir, "script_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/scenes/run")
def post_run_scenes(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_scenes_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/scenes/promote")
def post_promote_scenes(
    job_id: str,
    payload: RawScriptRequest,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = promote_scenes_stage(job_dir, channel_path, payload.raw_response)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    set_approval(job_dir, "scenes_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/seo/run")
def post_run_seo(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_seo_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/seo/promote")
def post_promote_seo(
    job_id: str,
    payload: RawScriptRequest,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = promote_seo_stage(job_dir, channel_path, payload.raw_response)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    set_approval(job_dir, "seo_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/whisper_timestamps/run")
def post_run_whisper_timestamps(
    job_id: str,
    async_: bool = Query(False, alias="async"),
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if async_:
        return _enqueue_stage_command(
            job_id=job_id,
            jobs_root=jobs_root,
            command="stage_whisper_timestamps",
        )
    try:
        output = run_whisper_timestamps_stage(job_dir)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/render/run")
def post_run_render(
    job_id: str,
    async_: bool = Query(False, alias="async"),
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if async_:
        return _enqueue_stage_command(
            job_id=job_id,
            jobs_root=jobs_root,
            command="stage_render",
        )
    try:
        output = run_render_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.get("/jobs/{job_id}/stages/render/progress")
def get_render_progress(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    """Return current Remotion render progress. Polling-friendly — always 200."""
    job_dir = _safe_job_dir(jobs_root, job_id)
    progress_path = job_dir / "json" / "render_progress.json"
    if not progress_path.exists():
        progress_path = job_dir / "render_progress.json"
    if not progress_path.exists():
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}
    try:
        import json as _json
        return _json.loads(progress_path.read_text(encoding="utf-8"))
    except Exception:
        return {"percent": 0, "frame": 0, "total_frames": 0, "fps": 0.0, "eta": ""}


@router.get("/jobs/{job_id}/stages/whisper_timestamps/progress")
def get_whisper_progress(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    """Latest WHISPER_STAGE_PROGRESS event from events.jsonl.

    Worker emits per-stage heartbeats (synth → load → transcribe → map);
    transcribe heartbeats include ``estimated_pct``. UI uses that for a
    live progress bar similar to render.
    """
    import json

    job_dir = _safe_job_dir(jobs_root, job_id)
    log_path = job_dir / "events.jsonl"
    if not log_path.exists():
        return {"percent": 0, "step": "", "elapsed_sec": 0}
    latest_step = ""
    latest_pct = 0.0
    latest_elapsed = 0.0
    latest_meta = ""
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line or "WHISPER_STAGE_PROGRESS" not in line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            data = obj.get("data") or {}
            latest_step = str(data.get("step") or latest_step)
            if data.get("estimated_pct") is not None:
                try:
                    latest_pct = float(data["estimated_pct"])
                except Exception:
                    pass
            if data.get("elapsed_sec") is not None:
                try:
                    latest_elapsed = float(data["elapsed_sec"])
                except Exception:
                    pass
            # Coarse step → percent floor so non-transcribe steps still
            # render a bar.
            floor_map = {
                "start": 1,
                "synthesizing_narration_audio": 5,
                "narration_audio_ready": 15,
                "audio_info": 20,
                "loading_whisper_model_tiny": 25,
                "whisper_model_loaded": 30,
                "transcribing_audio": 35,
                "transcription_complete": 90,
                "mapping_words_to_scenes": 95,
                "scene_mapping_complete": 98,
                "timestamps_written": 100,
            }
            floor = floor_map.get(latest_step, 0)
            if floor > latest_pct:
                latest_pct = float(floor)
            if data.get("audio_duration_sec"):
                latest_meta = f"audio {data['audio_duration_sec']}s"
            elif data.get("total_words"):
                latest_meta = f"{data['total_words']} words"
    except Exception:
        pass
    return {
        "percent": round(min(max(latest_pct, 0), 100), 1),
        "step": latest_step,
        "elapsed_sec": latest_elapsed,
        "meta": latest_meta,
    }


# Legacy Shorts render progress route removed per spec v5 §2.1. The Shorts
# Autopilot owns shorts/ now; see /jobs/{id}/shorts (status summary) and
# routes/shorts.py.


@router.post("/jobs/{job_id}/stages/review/run")
def post_run_review(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_review_stage(job_dir)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/persona_eval/run")
def post_run_persona_eval(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = run_persona_eval_stage(job_dir, channel_path)
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/idea_research/auto")
async def post_auto_idea_research(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_idea_research_stage(
            job_dir,
            channel_path,
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    # Fresh research run invalidates prior manual confirmation.
    set_approval(job_dir, "idea_research", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/script/auto")
async def post_auto_script(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_script_stage(job_dir, channel_path, _one_shot_with_briefing(client, "chatgpt", "writing", channel_path, job_dir))
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    set_approval(job_dir, "script_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/scenes/auto")
async def post_auto_scenes(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_scenes_stage(job_dir, channel_path, _one_shot_with_briefing(client, "chatgpt", "writing", channel_path, job_dir))
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    set_approval(job_dir, "scenes_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/seo/auto")
async def post_auto_seo(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_seo_stage(job_dir, channel_path, _one_shot_with_briefing(client, "chatgpt", "writing", channel_path, job_dir))
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    set_approval(job_dir, "seo_promote", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/script_qa/auto")
async def post_auto_script_qa(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_script_qa_stage(
            job_dir, channel_path,
            _one_shot_with_briefing(client, "gemini", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/scenes_qa/auto")
async def post_auto_scenes_qa(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_scenes_qa_stage(
            job_dir, channel_path,
            _one_shot_with_briefing(client, "gemini", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/seo_qa/auto")
async def post_auto_seo_qa(
    job_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        output = await auto_seo_qa_stage(
            job_dir, channel_path,
            _one_shot_with_briefing(client, "gemini", "qa", channel_path, job_dir),
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/stages/thumbnail_image/auto")
async def post_auto_thumbnail_image(
    job_id: str,
    async_: bool = Query(False, alias="async"),
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    if async_:
        return _enqueue_stage_command(
            job_id=job_id,
            jobs_root=jobs_root,
            command="stage_thumbnail_image_auto",
        )
    try:
        output = await auto_thumbnail_image_stage(
            job_dir,
            channel_path,
            client.generate_image,
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    set_approval(job_dir, "thumbnail_image", False)
    state = load_job(job_dir)
    return {"output": str(output.relative_to(job_dir)), "state": state.to_dict()}


@router.post("/jobs/{job_id}/scenes/{scene_id}/generate_asset")
async def post_generate_scene_asset(
    job_id: str,
    scene_id: str,
    jobs_root: Path = Depends(get_jobs_root),
    channel_path: Path = Depends(get_channel_path),
    client: BrowserClient = Depends(get_browser_client),
) -> dict:
    """Generate one ChatGPT image for a single scene.

    Uses ``scenes.json[scene_id].visual_prompt`` to build the image
    prompt, saves the PNG under ``jobs/<id>/assets/<scene_id>.png``,
    and patches ``scenes.json`` so ``asset_refs.primary`` points at
    the new file. The next render run picks it up via the
    ``_find_asset_refs_primary`` priority path.
    """
    job_dir = _safe_job_dir(jobs_root, job_id)
    if not (job_dir / "job.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    try:
        result = await generate_scene_asset(
            job_dir,
            channel_path,
            scene_id,
            client.generate_image,
        )
    except StageInputMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BrowserClientError as exc:
        raise _handle_browser_client_error(exc) from exc
    return result
