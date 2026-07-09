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
        # Cap the zoom so baked-in poster text is never cropped at any frame.
        "kenBurnsScaleMax": 1.02,
        # Overlays OFF by default: the poster already carries all text; a subscribe
        # cue / channel badge risks covering it (spec safe-area rule).
        "showSubscribeCue": False,
        # Consumed by build_remotion_commands: which Remotion composition to render and
        # at what concurrency. concurrency MUST stay "auto" — the render-concurrency
        # HARD RULE forbids hardcoding a number; _render_concurrency maps "auto" to the
        # machine's core count.
        "render": {"composition": "InfographicShort", "concurrency": "auto"},
    }
