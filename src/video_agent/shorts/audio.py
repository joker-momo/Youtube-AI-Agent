"""Kokoro narration synthesis for a Short, scoped to the short folder.

Reuses the long-form ``prepare_assets`` pipeline (Kokoro TTS + vertical stock
assets) but runs it inside ``shorts/short-XX/`` so the Short is a self-contained
mini-job for the Remotion renderer. Uses the Shorts faster voice preset
(Kokoro ef_dora, speed 1.07) from ``channel_config.shorts.tts``.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from video_agent.shorts import paths


def synthesize_short_narration(short_dir: Path, short_scenes: dict, channel_config: dict) -> Path:
    from video_agent.contracts import repo_root
    from video_agent.stages.assets import prepare_assets
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

    prepare_assets(
        job_dir=short_dir,
        style_dna=style_dna,
        scene_doc=short_scenes,
        visual_config=visuals,
        tts_config=tts_config,
        channel_id=channel_id,
    )

    src = short_dir / "assets" / "narration.wav"
    audio_dir = short_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    dst = audio_dir / "short_narration.wav"
    if src.exists():
        shutil.copyfile(src, dst)
    return dst
