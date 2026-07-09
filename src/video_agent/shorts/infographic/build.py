"""Orchestrate the infographic-short pipeline (plan → poster → QA → voice → render)."""
from __future__ import annotations

import asyncio
import datetime
import re
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_agent.shorts import manifest as manifest_mod
from video_agent.shorts import paths
from video_agent.shorts.idea_store import read_short_ideas
from video_agent.shorts.infographic.plan import build_poster_plan
from video_agent.shorts.infographic.poster import generate_poster
from video_agent.shorts.infographic.qa import qa_poster
from video_agent.shorts.infographic.render_props import build_infographic_render_props
from video_agent.shorts.infographic.seo import build_infographic_seo
from video_agent.storage.atomic import atomic_write_json


def _wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 30.0


def _long_job_dir(short_dir: Path) -> Path:
    """The parent long-form job dir for a short at ``<job>/shorts/<short_id>``."""
    return Path(short_dir).parent.parent


def _public_short_ref(short_dir: Path, subdir: str, name: str) -> str:
    """staticFile ref under remotion/public: ``materialize_short_job_aliases`` publishes
    a short's files to ``remotion/public/jobs/<short_dir.name>/<subdir>/`` (keyed by the
    short's OWN dir name, flattened — NOT nested under the parent job id), so the render
    ref must use that same key. The render_fn is responsible for the materialize step."""
    return f"jobs/{Path(short_dir).name}/{subdir}/{name}"


def run_infographic_short(
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
    """Synchronous orchestrator. ``image_fn`` is async (the only awaited dep), so it is
    driven with a fresh ``asyncio.run`` per poster generation; ``llm_fn``/``tts_fn``/
    ``render_fn`` are plain sync callables — some (the real ``chatgpt_fn``) use
    ``asyncio.run`` internally, so this function must NOT itself run inside an event
    loop (nested ``asyncio.run`` raises)."""
    short_dir = Path(short_dir)
    short_dir.mkdir(parents=True, exist_ok=True)

    plan = build_poster_plan(channel_config, source, llm_fn)
    atomic_write_json(short_dir / "json" / paths.SHORT_POSTER_PLAN_FILE, plan)

    verdict: dict[str, Any]
    if read_text_fn is None:
        # QA disabled: generate the poster once and proceed (no text gate). The
        # AI-only garble risk is accepted; nothing blocks the render.
        asyncio.run(generate_poster(short_dir, plan, image_fn))
        verdict = {"verdict": "skipped", "missing": []}
    else:
        verdict = {"verdict": "qa_unavailable", "missing": []}
        for _ in range(max_poster_attempts):
            asyncio.run(generate_poster(short_dir, plan, image_fn))
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

    # SEO/title artifact (writes json/short_seo.json via the shipped Short SEO path:
    # 4 scroll-stopper formulas, <=40 chars, aligned with the poster hook_line).
    build_infographic_seo(_long_job_dir(short_dir), short_dir.name, plan, channel_config, llm_fn)

    props = build_infographic_render_props(
        poster_ref=_public_short_ref(short_dir, "assets", paths.SHORT_POSTER_IMAGE_NAME),
        audio_ref=_public_short_ref(short_dir, "audio", Path(audio_path).name),
        duration_sec=duration,
        # v1: no music bed. Shorts music lives per-short as assets/bgm.mp3 (materialized
        # by the narrated audio pipeline, which infographic does not run). Referencing a
        # non-existent staticFile would 404 the render. Voiceover is the audio; a proper
        # per-short bgm is a follow-up. Empty => InfographicShort skips the music track.
        music_track="",
        channel_name=str((channel_config.get("channel") or {}).get("name") or ""),
    )
    atomic_write_json(short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE, props)
    out = render_fn(short_dir, props)

    status["rendered"] = bool(Path(out).exists())
    status["status"] = "rendered" if status["rendered"] else "failed"
    status["video_path"] = f"{short_dir.name}/outputs/{Path(out).name}"
    atomic_write_json(short_dir / paths.SHORT_STATUS_FILE, status)
    return status


def _slug(text: str, *, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s[:max_len] or "short"


def render_selected_infographic_ideas(
    long_job_dir: Path,
    channel_config: dict,
    idea_ids: list[str],
    *,
    image_fn,
    llm_fn: Callable[..., str],
    tts_fn: Callable[..., Path],
    render_fn: Callable[..., Path],
    read_text_fn: Callable[[Path], str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build one infographic Short per selected idea (parent topic -> poster short).

    Mirrors ``render_selected_short_ideas`` but runs the infographic pipeline. Writes
    each short's status + a manifest entry tagged ``short_type="infographic"``.
    """
    long_job_dir = Path(long_job_dir)
    ideas_doc = read_short_ideas(long_job_dir)
    ideas_by_id = {str(i.get("idea_id")): i for i in ideas_doc.get("ideas") or []}
    selected = [ideas_by_id[i] for i in idea_ids if i in ideas_by_id]
    if not selected:
        raise ValueError("No valid idea IDs selected")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    results: list[dict[str, Any]] = []
    for n, idea in enumerate(selected, start=1):
        idea_id = str(idea.get("idea_id"))
        title = str(idea.get("title") or "")
        source = {"topic": idea.get("topic") or title, "title": title}
        short_id = f"short-{n:02d}_{idea_id}_{ts}_{_slug(title or idea_id)}"
        short_dir = long_job_dir / "shorts" / short_id
        status = run_infographic_short(
            short_dir, channel_config, source,
            image_fn=image_fn, llm_fn=llm_fn, tts_fn=tts_fn, render_fn=render_fn,
            read_text_fn=read_text_fn,
        )
        status.update({"idea_id": idea_id, "short_id": short_id, "short_type": "infographic"})
        manifest_mod.write_short_status(long_job_dir, short_id, status)
        results.append({
            "short_id": short_id, "idea_id": idea_id, "short_type": "infographic",
            "status": status.get("status"), "rendered": status.get("rendered", False),
            "video_path": status.get("video_path"),
        })

    try:
        doc = manifest_mod.read_manifest(long_job_dir) or {}
    except FileNotFoundError:
        doc = {}
    doc["shorts"] = list(doc.get("shorts") or []) + results
    manifest_mod.write_manifest(long_job_dir, doc)
    return {"shorts": results}
