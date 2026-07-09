"""Narration text for an infographic short (read the poster) + TTS synth."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def build_narration_text(plan: dict[str, Any]) -> str:
    parts: list[str] = []
    hook = str(plan.get("hook_line") or plan.get("title") or "").strip()
    if hook:
        parts.append(hook)
    labels = [
        str(i.get("label") or "").strip()
        for i in (plan.get("items") or [])
        if isinstance(i, dict) and str(i.get("label") or "").strip()
    ]
    if labels:
        parts.append("Apunta esto: " + ", ".join(labels) + ".")
    cta = str(plan.get("cta") or "").strip()
    if cta:
        parts.append(cta)
    return "\n\n".join(parts).strip()


def synthesize_infographic_voiceover(short_dir: Path, plan: dict[str, Any], channel_config: dict) -> Path:
    """Synthesize narration to <short_dir>/audio/short_narration.wav via the shorts
    MeloTTS path, reusing shorts.audio.synthesize_short_narration with a single-scene doc."""
    from video_agent.shorts.audio import synthesize_short_narration

    text = build_narration_text(plan)
    single_scene = {"scenes": [{"id": "s01", "narration": text}], "total_duration_sec": 0}
    return synthesize_short_narration(Path(short_dir), single_scene, channel_config)
