"""TTS pacing report + audio QA threshold logic."""
from __future__ import annotations

import re
from typing import Any, Iterable

from video_agent.qa.scene_duration import (
    LONG_SCENE_WARNING_SEC,
    validate_scenes_durations,
)


def _count_words(text: str) -> int:
    if not isinstance(text, str):
        return 0
    return len(re.findall(r"\w+", text, flags=re.UNICODE))


def build_tts_report(
    scenes: Iterable[dict[str, Any]],
    audio_metadata: dict[str, Any],
    tts_config: dict[str, Any],
) -> dict[str, Any]:
    scenes_list = list(scenes)
    total_words = 0
    long_scenes = []
    for s in scenes_list:
        dur = float(s.get("duration_sec") or 0.0)
        total_words += _count_words(str(s.get("narration") or ""))
        if dur > LONG_SCENE_WARNING_SEC:
            long_scenes.append({"scene_id": s.get("id"), "duration_sec": round(dur, 2)})

    total_audio_sec = float(audio_metadata.get("duration_sec") or 0.0)
    if total_audio_sec <= 0:
        total_audio_sec = sum(float(s.get("duration_sec") or 0.0) for s in scenes_list)
    minutes = total_audio_sec / 60.0 if total_audio_sec > 0 else 0
    estimated_wpm = round(total_words / minutes, 1) if minutes > 0 else 0
    scene_count = len(scenes_list)
    avg_scene = round(total_audio_sec / scene_count, 2) if scene_count else 0

    humanize = tts_config.get("humanize") or {}
    cfg_snapshot = {
        "speed": tts_config.get("speed"),
        "pace_wpm": tts_config.get("pace_wpm"),
        "pause_sentence_ms": humanize.get("pause_sentence_ms"),
        "pause_paragraph_ms": humanize.get("pause_paragraph_ms"),
    }

    warnings = validate_scenes_durations(scenes_list)

    return {
        "total_audio_sec": round(total_audio_sec, 2),
        "estimated_words": total_words,
        "estimated_wpm": estimated_wpm,
        "scene_count": scene_count,
        "avg_scene_audio_sec": avg_scene,
        "long_scenes": long_scenes,
        "config": cfg_snapshot,
        "warnings": warnings,
    }


def audio_qa_report(
    integrated_lufs: float,
    true_peak_dbtp: float,
    bitrate_kbps: int | None = None,
    duration_sec: float | None = None,
    codec: str | None = None,
    sample_rate: int | None = None,
) -> dict[str, Any]:
    """Build an audio-QA report with threshold warnings.

    Warns when:
    - true_peak_dbtp > -1.0
    - integrated_lufs > -13.0
    - integrated_lufs < -18.0
    - bitrate_kbps < 128 for mixed audio
    """
    warnings: list[str] = []
    if true_peak_dbtp is not None and true_peak_dbtp > -1.0:
        warnings.append(
            f"true peak {true_peak_dbtp:.2f} dBTP exceeds -1.0; risk of clipping."
        )
    if integrated_lufs is not None and integrated_lufs > -13.0:
        warnings.append(
            f"integrated loudness {integrated_lufs:.2f} LUFS exceeds -13.0; too hot."
        )
    if integrated_lufs is not None and integrated_lufs < -18.0:
        warnings.append(
            f"integrated loudness {integrated_lufs:.2f} LUFS below -18.0; too quiet."
        )
    if bitrate_kbps is not None and bitrate_kbps < 128:
        warnings.append(
            f"bitrate {bitrate_kbps}k below 128k for final mixed audio."
        )
    return {
        "integrated_lufs": integrated_lufs,
        "true_peak_dbtp": true_peak_dbtp,
        "duration_sec": duration_sec,
        "codec": codec,
        "bitrate": f"{bitrate_kbps}k" if bitrate_kbps is not None else None,
        "sample_rate": sample_rate,
        "warnings": warnings,
    }
