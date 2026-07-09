"""Orchestrate the infographic-short pipeline (plan → poster → QA → voice → render)."""
from __future__ import annotations

import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_agent.shorts import paths
from video_agent.shorts.infographic.plan import build_poster_plan
from video_agent.shorts.infographic.poster import generate_poster
from video_agent.shorts.infographic.qa import qa_poster
from video_agent.shorts.infographic.render_props import build_infographic_render_props
from video_agent.storage.atomic import atomic_write_json


def _wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 30.0


async def run_infographic_short(
    short_dir: Path,
    channel_config: dict,
    source: dict,
    *,
    image_fn,
    llm_fn: Callable[..., str],
    read_text_fn: Callable[[Path], str] | None = None,
    tts_fn: Callable[..., Path],
    render_fn: Callable[..., Path],
    max_poster_attempts: int = 3,
) -> dict[str, Any]:
    short_dir = Path(short_dir)
    short_dir.mkdir(parents=True, exist_ok=True)

    plan = build_poster_plan(channel_config, source, llm_fn)
    atomic_write_json(short_dir / "json" / paths.SHORT_POSTER_PLAN_FILE, plan)

    verdict: dict[str, Any]
    if read_text_fn is None:
        # QA disabled: generate the poster once and proceed (no text gate). The
        # AI-only garble risk is accepted; nothing blocks the render.
        await generate_poster(short_dir, plan, image_fn)
        verdict = {"verdict": "skipped", "missing": []}
    else:
        verdict = {"verdict": "qa_unavailable", "missing": []}
        for _ in range(max_poster_attempts):
            await generate_poster(short_dir, plan, image_fn)
            verdict = qa_poster(
                short_dir / "assets" / paths.SHORT_POSTER_IMAGE_NAME, plan, read_text_fn=read_text_fn
            )
            if verdict["verdict"] == "pass":
                break
    atomic_write_json(short_dir / "json" / paths.SHORT_POSTER_QA_FILE, verdict)

    status: dict[str, Any] = {
        "short_type": "infographic",
        "poster_format": plan.get("poster_format"),
        "rendered": False,
    }
    if verdict["verdict"] not in ("pass", "skipped"):
        status["status"] = "needs_manual_review"
        status["qa"] = verdict
        atomic_write_json(short_dir / paths.SHORT_STATUS_FILE, status)
        return status

    audio_path = tts_fn(short_dir, plan, channel_config)
    duration = _wav_seconds(Path(audio_path)) + 0.6

    props = build_infographic_render_props(
        poster_ref=f"jobs/{short_dir.name}/assets/{paths.SHORT_POSTER_IMAGE_NAME}",
        audio_ref=f"jobs/{short_dir.name}/audio/{Path(audio_path).name}",
        duration_sec=duration,
        music_track=str((channel_config.get("shorts") or {}).get("music_track") or "shorts_sleep_stress"),
        channel_name=str((channel_config.get("channel") or {}).get("name") or ""),
    )
    atomic_write_json(short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE, props)
    out = render_fn(short_dir, props)

    status["rendered"] = bool(Path(out).exists())
    status["status"] = "rendered" if status["rendered"] else "failed"
    status["video_path"] = f"{short_dir.name}/outputs/{Path(out).name}"
    atomic_write_json(short_dir / paths.SHORT_STATUS_FILE, status)
    return status
