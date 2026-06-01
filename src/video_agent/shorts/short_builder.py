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
    claude_fn: Callable[[str], str] | None = None,
    tts_fn: Callable[..., Path] = _default_tts_fn,
    mix_fn: Callable[..., Path] = _default_mix_fn,
    render_fn: Callable[..., Path] = _default_render_fn,
    cover_fn: Callable[..., Path] = _default_cover_fn,
    long_video_url: str = "",
) -> dict[str, Any]:
    short_id = short_plan["short_id"]
    sd = paths.short_dir(long_job_dir, short_id)
    sd.mkdir(parents=True, exist_ok=True)
    paths.short_tmp_dir(long_job_dir, short_id).mkdir(parents=True, exist_ok=True)

    ap = (channel_config.get("shorts") or {}).get("autopilot") or {}
    max_regen = int(ap.get("max_regeneration_attempts", 2))
    music_track = short_plan.get("music_track")
    cover_words = int(((channel_config.get("shorts") or {}).get("cover") or {}).get("text_max_words", 5))

    atomic_write_json(sd / paths.SHORT_IDEA_FILE, short_plan)

    qa_result: dict[str, Any] = {"verdict": "FAIL", "issues": ["not_generated"]}
    short_script: dict[str, Any] = {}
    short_scenes: dict[str, Any] = {}
    feedback = ""
    attempts = 0

    for attempt in range(max_regen + 1):  # initial + N regenerations
        attempts = attempt + 1
        plan_for_prompt = {**short_plan, "source_long_job_id": long_job_dir.name}
        short_script = short_script_builder.build_short_script(
            long_job_dir, plan_for_prompt, channel_config, llm_fn,
            feedback=feedback, attempt=attempts,
        )
        short_scenes = short_scene_builder.build_short_scenes(
            long_job_dir, plan_for_prompt, short_script, channel_config, llm_fn,
            attempt=attempts,
        )

        sm = source_map.build_source_map(long_job_dir, short_plan, short_script, channel_config, long_video_url)
        atomic_write_json(sd / paths.SHORT_SOURCE_MAP_FILE, sm)

        short_seo_builder.build_short_seo(
            long_job_dir, short_id, plan_for_prompt, short_script, channel_config, llm_fn, long_video_url
        )

        qa_result = qa.run_short_qa(
            long_job_dir, short_id, channel_config,
            music_track=music_track, claude_fn=claude_fn, attempt=attempts,
        )
        atomic_write_json(sd / paths.SHORT_QA_FILE, qa_result)

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

    base = {
        "short_id": short_id,
        "source_long_job_id": long_job_dir.name,
        "format": short_plan.get("format"),
        "hook": hook,
        "cover_text": cover_text,
        "duration_sec": round(duration_sec, 1),
        "score": short_plan.get("score"),
        "qa_verdict": qa_result["verdict"],
        "regeneration_attempts": attempts - 1,
        "music_track": music_track,
        "voice": {
            "provider": (channel_config.get("shorts") or {}).get("tts", {}).get("provider", "kokoro"),
            "voice_id": (channel_config.get("shorts") or {}).get("tts", {}).get("voice_id", "ef_dora"),
            "speed": (channel_config.get("shorts") or {}).get("tts", {}).get("speed", 1.07),
        },
    }

    if qa_result["verdict"] != "PASS":
        status = {
            **base,
            "status": "needs_review",
            "rendered": False,
            "uploaded": False,
            "youtube_url": "",
            "requires_user_review": True,
        }
        write_short_status(long_job_dir, short_id, status)
        return status

    # QA PASS → produce audio, mix, render props, video, cover.
    narration_wav = tts_fn(sd, short_scenes, channel_config)
    mix_fn(sd, narration_wav, music_track, channel_config, duration_sec)
    _write_render_props(sd, short_scenes, channel_config, music_track)
    video_path = render_fn(sd, channel_config)
    cover_path = cover_fn(sd, channel_config)

    status = {
        **base,
        "status": "rendered",
        "rendered": True,
        "uploaded": False,
        "youtube_url": "",
        "requires_user_review": False,
        "video_path": f"shorts/{short_id}/{paths.SHORT_VIDEO_FILE}",
        "cover_path": f"shorts/{short_id}/{paths.SHORT_COVER_FILE}",
    }
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
