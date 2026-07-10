"""Orchestrate static infographic Shorts (plan → poster → QA → music → render)."""
from __future__ import annotations

import asyncio
import datetime
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from video_agent.shorts import manifest as manifest_mod
from video_agent.shorts import music_selector, paths
from video_agent.shorts.idea_store import read_short_ideas, write_studio_render_run
from video_agent.shorts.infographic.plan import build_poster_plan
from video_agent.shorts.infographic.poster import generate_poster
from video_agent.shorts.infographic.qa import qa_poster
from video_agent.shorts.infographic.render_props import build_infographic_render_props
from video_agent.shorts.infographic.seo import build_infographic_seo
from video_agent.storage.atomic import atomic_write_json

DEFAULT_STATIC_DURATION_SEC = 15.0
_ORIGINAL_PROCEDURAL_SOURCE = "procedural_original"


def _static_options(channel_config: dict, source: dict) -> tuple[float, bool, str]:
    cfg = ((channel_config.get("shorts") or {}).get("infographic") or {})
    duration_sec = float(cfg.get("duration_sec", DEFAULT_STATIC_DURATION_SEC))
    if not 5.0 <= duration_sec <= 60.0:
        raise ValueError("shorts.infographic.duration_sec must be between 5 and 60 seconds")
    music_source = str(cfg.get("music_source") or _ORIGINAL_PROCEDURAL_SOURCE).strip().lower()
    if music_source == _ORIGINAL_PROCEDURAL_SOURCE:
        music_track = _ORIGINAL_PROCEDURAL_SOURCE
    elif music_source == "library":
        music_track = str(
            cfg.get("music_track")
            or music_selector.select_music_track(source.get("pillar") or source.get("topic") or "", channel_config)
        )
    else:
        raise ValueError("shorts.infographic.music_source must be procedural_original or library")
    return duration_sec, bool(cfg.get("ken_burns", False)), music_track


def prepare_infographic_music_bed(
    short_dir: Path, music_track: str, channel_config: dict, duration_sec: float
) -> Path:
    """Create a self-contained music-only bed from the configured source."""
    cfg = ((channel_config.get("shorts") or {}).get("infographic") or {})
    music_source = str(cfg.get("music_source") or _ORIGINAL_PROCEDURAL_SOURCE).strip().lower()
    if music_source == _ORIGINAL_PROCEDURAL_SOURCE:
        from video_agent.shorts.original_bgm import create_original_bgm

        return create_original_bgm(
            short_dir,
            duration_sec=duration_sec,
            seed_key=Path(short_dir).name,
            bitrate=str(cfg.get("music_bitrate", "192k")),
        )
    if music_source != "library":
        raise ValueError("shorts.infographic.music_source must be procedural_original or library")

    # Existing channels can explicitly keep their licensed library bed.
    from video_agent.shorts.audio_mixer import resolve_music_file

    music_file = resolve_music_file(music_track, channel_config)
    if music_file is None or not music_file.exists():
        raise RuntimeError(f"Infographic Short requires an available music track: {music_track}")
    audio_dir = Path(short_dir) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    out_path = audio_dir / "infographic_bgm.m4a"
    fade_out_sec = min(float(cfg.get("music_fade_out_sec", 0.45)), duration_sec)
    fade_out_start = max(0.0, duration_sec - fade_out_sec)
    subprocess.run(
        [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(music_file),
            "-t", f"{duration_sec:.2f}",
            "-af", (
                f"volume={float(cfg.get('music_volume_db', -14.0))}dB,"
                f"afade=t=in:st=0:d={float(cfg.get('music_fade_in_sec', 0.16)):.2f},"
                f"afade=t=out:st={fade_out_start:.2f}:d={fade_out_sec:.2f}"
            ),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


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
    render_fn: Callable[..., Path],
    music_fn: Callable[..., Path] | None = None,
    max_poster_attempts: int = 3,
) -> dict[str, Any]:
    """Synchronous orchestrator. ``image_fn`` is async (the only awaited dep), so it is
    driven with a fresh ``asyncio.run`` per poster generation; ``llm_fn``/``music_fn``/
    ``render_fn`` are plain sync callables — some (the real ``chatgpt_fn``) use
    ``asyncio.run`` internally, so this function must NOT itself run inside an event
    loop (nested ``asyncio.run`` raises)."""
    short_dir = Path(short_dir)
    short_dir.mkdir(parents=True, exist_ok=True)

    # Live progress: the Renders tab reads short_status.json, so each stage
    # transition is persisted immediately — an in-flight short must be visible
    # in the UI, not appear only when the whole build finishes.
    stage_names = ("plan", "poster", "poster_qa", "music", "seo", "render_props", "render")

    def _progress(current: str) -> None:
        idx = stage_names.index(current)
        atomic_write_json(short_dir / paths.SHORT_STATUS_FILE, {
            "short_type": "infographic",
            "status": "generating",
            "rendered": False,
            "stages": [
                {"name": name, "status": (
                    "completed" if n < idx else "in_progress" if n == idx else "pending"
                )}
                for n, name in enumerate(stage_names)
            ],
        })

    _progress("plan")
    plan = build_poster_plan(channel_config, source, llm_fn)
    atomic_write_json(short_dir / "json" / paths.SHORT_POSTER_PLAN_FILE, plan)

    _progress("poster")
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
    _progress("poster_qa")
    atomic_write_json(short_dir / "json" / paths.SHORT_POSTER_QA_FILE, verdict)

    status: dict[str, Any] = {
        "short_type": "infographic",
        "poster_format": plan.get("poster_format"),
        "rendered": False,
    }
    if verdict["verdict"] not in ("pass", "skipped"):
        status["status"] = "needs_manual_review"
        status["qa"] = verdict
        status["stages"] = [
            {"name": name, "status": "completed" if name in ("plan", "poster", "poster_qa") else "pending"}
            for name in stage_names
        ]
        atomic_write_json(short_dir / paths.SHORT_STATUS_FILE, status)
        return status

    _progress("music")
    duration, ken_burns, music_track = _static_options(channel_config, source)
    music_fn = music_fn or prepare_infographic_music_bed
    audio_path = music_fn(short_dir, music_track, channel_config, duration)

    _progress("seo")
    # SEO/title artifact (writes json/short_seo.json via the shipped Short SEO path:
    # 4 scroll-stopper formulas, <=40 chars, aligned with the poster hook_line).
    build_infographic_seo(_long_job_dir(short_dir), short_dir.name, plan, channel_config, llm_fn)

    _progress("render_props")
    props = build_infographic_render_props(
        poster_ref=_public_short_ref(short_dir, "assets", paths.SHORT_POSTER_IMAGE_NAME),
        audio_ref=_public_short_ref(short_dir, "audio", Path(audio_path).name),
        duration_sec=duration,
        music_track="",
        channel_name=str((channel_config.get("channel") or {}).get("name") or ""),
        ken_burns=ken_burns,
    )
    atomic_write_json(short_dir / "json" / paths.SHORT_RENDER_PROPS_FILE, props)
    _progress("render")
    out = render_fn(short_dir, props)

    status["rendered"] = bool(Path(out).exists())
    status["stages"] = [
        {"name": name, "status": "completed" if status["rendered"] or name != "render" else "failed"}
        for name in stage_names
    ]
    status["status"] = "rendered" if status["rendered"] else "failed"
    status["video_path"] = f"{short_dir.name}/outputs/{Path(out).name}"
    status["audio_mode"] = "music_only"
    status["music_track"] = music_track
    status["music_source"] = str(
        ((channel_config.get("shorts") or {}).get("infographic") or {}).get("music_source")
        or _ORIGINAL_PROCEDURAL_SOURCE
    )
    status["duration_sec"] = duration
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
    render_fn: Callable[..., Path],
    music_fn: Callable[..., Path] | None = None,
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
        source = {
            "topic": idea.get("topic") or title,
            "title": title,
            "pillar": idea.get("pillar") or "",
            # The idea was conceived FOR a poster layout; seed the plan with it
            # (alias-mapped from legacy narrated formats) instead of letting the
            # plan LLM re-pick a random one.
            "poster_format": str(idea.get("format") or ""),
        }
        short_id = f"short-{n:02d}_{idea_id}_{ts}_{_slug(title or idea_id)}"
        short_dir = long_job_dir / "shorts" / short_id
        status = run_infographic_short(
            short_dir, channel_config, source,
            image_fn=image_fn, llm_fn=llm_fn, music_fn=music_fn, render_fn=render_fn,
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
    # Recompute the top-level status: a stale "failed" from an earlier run must
    # not shadow a Short that just rendered successfully.
    if any(entry.get("rendered") for entry in doc["shorts"]):
        doc["status"] = "completed"
    elif results:
        doc["status"] = "failed"
    manifest_mod.write_manifest(long_job_dir, doc)

    # The Studio job badge reads studio_render_run.json BEFORE the manifest, so
    # this run must overwrite any stale doc left by an earlier narrated attempt.
    rendered_count = sum(1 for r in results if r.get("rendered"))
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_studio_render_run(long_job_dir, {
        "schema_version": "studio_render_run.v1",
        "source_long_job_id": long_job_dir.name,
        "mode": "synthesis_ideas",
        "generation_id": ideas_doc.get("generation_id"),
        "status": "completed" if rendered_count == len(results) and results else (
            "completed_with_warnings" if rendered_count else "failed"
        ),
        "started_at": now,
        "completed_at": now,
        "selected_idea_count": len(selected),
        "attempted_render_count": len(results),
        "rendered_count": rendered_count,
        "needs_review_count": sum(1 for r in results if r.get("status") == "needs_manual_review"),
        "failed_count": sum(1 for r in results if not r.get("rendered")),
        "skipped_count": 0,
        "blocked_count": 0,
        "warnings": [],
        "errors": [],
    })
    return {"shorts": results}
