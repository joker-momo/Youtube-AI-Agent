"""Build one Short end to end: generate → QA (regen loop) → audio → mix → render.

All side-effecting steps (LLM, Kokoro TTS, ffmpeg mix, Remotion render, cover)
are injected so the orchestration is unit-testable; real implementations are the
defaults used by the autopilot.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import (
    paths,
    qa,
    short_scene_builder,
    short_script_builder,
    short_seo_builder,
    source_map,
)
from video_agent.shorts.manifest import write_short_status
from video_agent.storage.atomic import atomic_write_json


def _parse(raw: str) -> dict:
    from video_agent.operator import extract_json_objects

    objs = extract_json_objects(raw or "")
    return objs[0] if objs else {}


def _cover_text(hook: str, max_words: int) -> str:
    words = [w for w in str(hook).strip().strip("¿?¡!.,").split() if w]
    return " ".join(words[:max_words]).upper()


# -- default real side-effect implementations (wired lazily) ----------------

def _default_llm_fn(kind: str, prompt: str) -> str:  # pragma: no cover - needs browser
    raise NotImplementedError("llm_fn must be injected (browser ChatGPT sender).")


def _default_tts_fn(short_dir: Path, short_scenes: dict, channel_config: dict) -> Path:
    from video_agent.shorts.audio import synthesize_short_narration

    return synthesize_short_narration(short_dir, short_scenes, channel_config)


def _default_mix_fn(short_dir: Path, narration_wav: Path, music_track: str, channel_config: dict, duration_sec: float) -> Path:
    from video_agent.shorts.audio_mixer import mix_short_audio

    return mix_short_audio(short_dir, narration_wav, music_track, channel_config, duration_sec)


def _default_render_fn(short_dir: Path, channel_config: dict) -> Path:
    from video_agent.shorts.renderer import render_short_video

    return render_short_video(short_dir, channel_config)


def _default_cover_fn(short_dir: Path, channel_config: dict) -> Path:
    from video_agent.shorts.renderer import render_short_cover

    return render_short_cover(short_dir, channel_config)


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
    long_video_url: str = "",
    require_render_confirmation: bool = False,
    source_artifacts: dict | None = None,
) -> dict[str, Any]:
    import datetime

    short_id = short_plan["short_id"]
    sd = paths.short_dir(long_job_dir, short_id)
    sd.mkdir(parents=True, exist_ok=True)
    paths.short_tmp_dir(long_job_dir, short_id).mkdir(parents=True, exist_ok=True)

    ap = (channel_config.get("shorts") or {}).get("autopilot") or {}
    max_regen = int(ap.get("max_regeneration_attempts", 2))
    music_track = short_plan.get("music_track")
    cover_words = int(((channel_config.get("shorts") or {}).get("cover") or {}).get("text_max_words", 5))

    atomic_write_json(sd / paths.SHORT_IDEA_FILE, short_plan)

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
        "music_track": music_track,
        "source_scene_ids": short_plan.get("source_scene_ids") or short_plan.get("scene_ids") or [],
        "voice": {
            "provider": (channel_config.get("shorts") or {}).get("tts", {}).get("provider", "kokoro"),
            "voice_id": (channel_config.get("shorts") or {}).get("tts", {}).get("voice_id", "ef_dora"),
            "speed": (channel_config.get("shorts") or {}).get("tts", {}).get("speed", 1.07),
        },
    }

    stages = [
        {"name": "script", "label": "Short Script", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "scenes", "label": "Short Scenes", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "seo", "label": "Short SEO", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "qa", "label": "Quality Assurance", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "audio", "label": "Audio TTS & Mix", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
        {"name": "render", "label": "Video & Cover Render", "status": "pending", "started_at": None, "completed_at": None, "actual_seconds": None},
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
    }

    def update_stage(stage_name: str, new_status: str, **kwargs):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for s in status["stages"]:
            if s["name"] == stage_name:
                s["status"] = new_status
                if new_status == "in_progress" and not s.get("started_at"):
                    s["started_at"] = now_str
                elif new_status in ("completed", "failed", "skipped"):
                    if not s.get("started_at"):
                        s["started_at"] = now_str
                    s["completed_at"] = now_str
                    try:
                        from datetime import datetime as dt
                        t_start = dt.fromisoformat(s["started_at"].replace("Z", "+00:00"))
                        t_end = dt.fromisoformat(now_str.replace("Z", "+00:00"))
                        s["actual_seconds"] = max(0, int((t_end - t_start).total_seconds()))
                    except Exception:
                        s["actual_seconds"] = 1
                for k, v in kwargs.items():
                    s[k] = v
                break
        status["updated_at"] = now_str
        write_short_status(long_job_dir, short_id, status)

    qa_result: dict[str, Any] = {"verdict": "FAIL", "issues": ["not_generated"]}
    short_script: dict[str, Any] = {}
    short_scenes: dict[str, Any] = {}
    feedback = ""
    attempts = 0

    for attempt in range(max_regen + 1):  # initial + N regenerations
        attempts = attempt + 1
        plan_for_prompt = {**short_plan, "source_long_job_id": long_job_dir.name}
        
        # --- Stage 1: Script ---
        update_stage("script", "in_progress")
        try:
            short_script = short_script_builder.build_short_script(
                long_job_dir, plan_for_prompt, channel_config, llm_fn,
                source_artifacts=source_artifacts,
                feedback=feedback, attempt=attempts,
            )
            update_stage("script", "completed")
        except Exception as exc:
            update_stage("script", "failed")
            status["status"] = "failed"
            write_short_status(long_job_dir, short_id, status)
            raise exc

        # Update hook dynamically
        status["hook"] = str(short_script.get("hook") or "")
        write_short_status(long_job_dir, short_id, status)

        # --- Stage 2: Scenes ---
        update_stage("scenes", "in_progress")
        try:
            short_scenes = short_scene_builder.build_short_scenes(
                long_job_dir, plan_for_prompt, short_script, channel_config, llm_fn,
                attempt=attempts,
            )
            update_stage("scenes", "completed")
        except Exception as exc:
            update_stage("scenes", "failed")
            status["status"] = "failed"
            write_short_status(long_job_dir, short_id, status)
            raise exc

        # --- Stage 3: SEO & Source Map ---
        update_stage("seo", "in_progress")
        try:
            sm = source_map.build_source_map(long_job_dir, short_plan, short_script, channel_config, long_video_url)
            atomic_write_json(sd / paths.SHORT_SOURCE_MAP_FILE, sm)

            short_seo_builder.build_short_seo(
                long_job_dir, short_id, plan_for_prompt, short_script, channel_config, llm_fn, long_video_url
            )
            update_stage("seo", "completed")
        except Exception as exc:
            update_stage("seo", "failed")
            status["status"] = "failed"
            write_short_status(long_job_dir, short_id, status)
            raise exc

        # --- Stage 4: QA ---
        update_stage("qa", "in_progress")
        try:
            qa_result = qa.run_short_qa(
                long_job_dir, short_id, channel_config,
                music_track=music_track, gemini_fn=gemini_fn, attempt=attempts,
            )
            atomic_write_json(sd / paths.SHORT_QA_FILE, qa_result)
            verdict = qa_result.get("verdict", "FAIL")
            update_stage("qa", "completed" if verdict == "PASS" else "failed", qa_verdict=verdict)
        except Exception as exc:
            update_stage("qa", "failed")
            status["status"] = "failed"
            write_short_status(long_job_dir, short_id, status)
            raise exc

        if qa_result["verdict"] == "PASS":
            break
        feedback = "; ".join(qa_result.get("required_changes") or qa_result.get("issues") or [])

    hook = str(short_script.get("hook") or "")
    cover_text = _cover_text(hook, cover_words)
    duration_sec = float(
        short_scenes.get("total_duration_sec")
        or sum(float(s.get("duration_sec") or 0) for s in (short_scenes.get("scenes") or []))
        or short_script.get("target_duration_sec")
        or 0
    )

    # Save finalized metadata to status
    status.update({
        "hook": hook,
        "cover_text": cover_text,
        "duration_sec": round(duration_sec, 1),
        "qa_verdict": qa_result["verdict"],
        "regeneration_attempts": attempts - 1,
    })
    write_short_status(long_job_dir, short_id, status)

    if qa_result["verdict"] != "PASS":
        update_stage("audio", "skipped")
        update_stage("render", "skipped")
        status.update({
            "status": "needs_review",
            "rendered": False,
            "uploaded": False,
            "youtube_url": "",
            "requires_user_review": True,
        })
        write_short_status(long_job_dir, short_id, status)
        return status

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

    # QA PASS & No confirmation required → produce audio, mix, render props, video, cover.
    # --- Stage 5: Audio ---
    update_stage("audio", "in_progress")
    try:
        narration_wav = tts_fn(sd, short_scenes, channel_config)
        mix_fn(sd, narration_wav, music_track, channel_config, duration_sec)
        _write_render_props(sd, short_scenes, channel_config, music_track)
        update_stage("audio", "completed")
    except Exception as exc:
        update_stage("audio", "failed")
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    # --- Stage 6: Render ---
    update_stage("render", "in_progress")
    try:
        video_path = render_fn(sd, channel_config)
        cover_path = cover_fn(sd, channel_config)
        update_stage("render", "completed")
    except Exception as exc:
        update_stage("render", "failed")
        status["status"] = "failed"
        write_short_status(long_job_dir, short_id, status)
        raise exc

    status.update({
        "status": "rendered",
        "rendered": True,
        "uploaded": False,
        "youtube_url": "",
        "requires_user_review": False,
        "requires_render_confirmation": False,
        "video_path": f"shorts/{short_id}/{paths.SHORT_VIDEO_FILE}",
        "cover_path": f"shorts/{short_id}/{paths.SHORT_COVER_FILE}",
    })
    write_short_status(long_job_dir, short_id, status)
    return status


def _write_render_props(short_dir: Path, short_scenes: dict, channel_config: dict, music_track: str | None) -> None:
    rcfg = (channel_config.get("shorts") or {}).get("render") or {}
    props = {
        "composition": rcfg.get("composition", "ShortVideoStandard"),
        "resolution": rcfg.get("resolution", "1080x1920"),
        "fps": rcfg.get("fps", 30),
        "scenes": short_scenes.get("scenes") or [],
        "total_duration_sec": short_scenes.get("total_duration_sec"),
        "audio": "audio/short_mix.m4a",
        "music_track": music_track,
    }
    atomic_write_json(short_dir / paths.SHORT_RENDER_PROPS_FILE, props)
