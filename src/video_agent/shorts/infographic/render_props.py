"""Render props for the Remotion InfographicShort composition."""
from __future__ import annotations

from typing import Any

_FPS = 30


def build_infographic_render_props(
    *,
    poster_ref: str,
    audio_ref: str,
    duration_sec: float,
    music_track: str,
    channel_name: str,
) -> dict[str, Any]:
    return {
        "poster": poster_ref,
        "audio": audio_ref,
        "music": music_track,
        "channelName": channel_name,
        "width": 1080,
        "height": 1920,
        "fps": _FPS,
        "durationInFrames": max(1, round(float(duration_sec) * _FPS)),
        "kenBurns": True,
        "showSubscribeCue": True,
    }
