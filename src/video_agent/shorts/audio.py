"""Kokoro narration synthesis for a Short, scoped to the short folder.

Reuses the long-form ``prepare_assets`` pipeline (Kokoro TTS + vertical stock
assets) but runs it inside ``shorts/short-XX/`` so the Short is a self-contained
mini-job for the Remotion renderer. Uses the Shorts faster voice preset
(Kokoro ef_dora, speed 1.07) from ``channel_config.shorts.tts``.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from video_agent.shorts import paths


def _short_asset_context(short_dir: Path, channel_config: dict) -> dict[str, Any]:
    """Common style/visual/tts kwargs shared by the background + TTS passes."""
    from video_agent.contracts import repo_root
    from video_agent.utils.json_io import read_json

    style_dna_path = (channel_config.get("style_dna") or {}).get("path")
    style_dna: dict[str, Any] = {}
    if style_dna_path:
        p = repo_root() / style_dna_path
        if p.exists():
            style_dna = read_json(p)

    visuals = dict(channel_config.get("visuals") or {})
    if (channel_config.get("shorts") or {}).get("source", {}).get("prefer_vertical_assets", True):
        visuals["orientation"] = "portrait"

    tts_config = (channel_config.get("shorts") or {}).get("tts") or channel_config.get("tts")
    # The Remotion ShortVideo renders ONE narration track at frame 0 while each
    # scene is timed by its planned ``duration_sec``. Audio/video only stay in
    # sync when every scene's audio block equals its planned duration, so the
    # Shorts path must pad per-scene audio (dynamic_sync=False) rather than
    # shrink scene durations to the raw speech length. Force it here, copy-on-
    # write so the caller's config is untouched.
    tts_config = {**(tts_config or {}), "dynamic_sync": False}
    channel_id = (channel_config.get("channel") or {}).get("id", "unknown-channel")
    return {
        "style_dna": style_dna,
        "visual_config": visuals,
        "tts_config": tts_config,
        "channel_id": channel_id,
    }


def synthesize_short_backgrounds(
    short_dir: Path,
    short_scenes: dict,
    channel_config: dict,
    on_scene_resolved: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Resolve every scene's background (Pexels video/photo, ChatGPT image, or
    placeholder) WITHOUT synthesizing audio. Runs as its own pipeline stage so the
    UI can report, scene-by-scene, which source was used."""
    from video_agent.stages.assets import prepare_assets

    ctx = _short_asset_context(short_dir, channel_config)
    # Lazy AI policy: scenes covered by a span that already has a provisional
    # Pexels VIDEO finalist skip the ChatGPT tier now (span video supersedes the
    # per-scene background at render). AI is only generated later, for spans whose
    # video gets rejected by visual_local_qa. Saves ~10 min of wasted image-gen.
    _mark_video_covered_scenes(short_dir, short_scenes)
    # Log AI image-gen prompts to the Short's prompt history (same JSONL the
    # ChatGPT/Gemini calls write to). short_dir is this Short's folder, so the
    # history file is json/llm_history.jsonl inside it.
    llm_history_path = short_dir / paths.SHORT_JSON_SUBDIR / paths.SHORT_LLM_HISTORY_FILE

    prepare_assets(
        job_dir=short_dir,
        scene_doc=short_scenes,
        render_backgrounds=True,
        render_tts=False,
        on_scene_resolved=on_scene_resolved,
        llm_history_path=llm_history_path,
        **ctx,
    )


def _spans_with_provisional_video(short_dir: Path) -> set[str]:
    """Scene ids covered by a span that already has a provisional Pexels VIDEO
    finalist (read from acquisition artifacts written before the background stage)."""
    from video_agent.utils.json_io import read_json

    jd = short_dir / paths.SHORT_JSON_SUBDIR
    try:
        spans = (read_json(jd / "visual_spans.json") or {}).get("spans") or []
        sel = (read_json(jd / "visual_span_asset_selection.json") or {}).get("spans") or []
    except Exception:
        return set()
    has_video = {
        s.get("visual_span_id")
        for s in sel
        if s.get("provisional_candidate_id")
    }
    covered: set[str] = set()
    for sp in spans:
        if (sp.get("id") or sp.get("visual_span_id")) in has_video:
            covered.update(str(x) for x in (sp.get("scene_ids") or []))
    return covered


def _mark_video_covered_scenes(short_dir: Path, short_scenes: dict) -> None:
    covered = _spans_with_provisional_video(short_dir)
    for scene in short_scenes.get("scenes") or []:
        scene["_skip_ai_fallback"] = scene.get("id") in covered


def regen_fallback_backgrounds(
    short_dir: Path, short_scenes: dict, channel_config: dict, scene_ids: set[str]
) -> None:
    """Lazy second pass: generate ChatGPT images ONLY for the given scenes (spans
    whose Pexels video was rejected by visual_local_qa), merging into the existing
    manifest/report. No-op when scene_ids is empty."""
    if not scene_ids:
        return
    from video_agent.stages.assets import prepare_assets

    ctx = _short_asset_context(short_dir, channel_config)
    for scene in short_scenes.get("scenes") or []:
        if scene.get("id") in scene_ids:
            scene["_skip_ai_fallback"] = False
    llm_history_path = short_dir / paths.SHORT_JSON_SUBDIR / paths.SHORT_LLM_HISTORY_FILE
    prepare_assets(
        job_dir=short_dir,
        scene_doc=short_scenes,
        render_backgrounds=True,
        render_tts=False,
        llm_history_path=llm_history_path,
        only_scene_ids=set(scene_ids),
        **ctx,
    )


def synthesize_short_narration(short_dir: Path, short_scenes: dict, channel_config: dict) -> Path:
    """Synthesize the Short's narration track. Backgrounds are resolved earlier by
    ``synthesize_short_backgrounds`` (separate stage), so this pass skips visuals."""
    from video_agent.stages.assets import prepare_assets

    ctx = _short_asset_context(short_dir, channel_config)
    prepare_assets(
        job_dir=short_dir,
        scene_doc=short_scenes,
        render_backgrounds=False,
        render_tts=True,
        **ctx,
    )

    src = short_dir / "assets" / "narration.wav"
    audio_dir = short_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    dst = audio_dir / "short_narration.wav"
    if src.exists():
        shutil.copyfile(src, dst)
    return dst
