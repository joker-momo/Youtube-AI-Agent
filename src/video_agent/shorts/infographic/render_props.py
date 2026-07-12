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
    ken_burns: bool = False,
    show_engagement_cue: bool = True,
    engagement_cue_sec: float = 4.0,
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
        "kenBurns": ken_burns,
        # Cap the zoom so baked-in poster text is never cropped at any frame.
        "kenBurnsScaleMax": 1.02,
        # Legacy full-video banner stays OFF; it is superseded by the end cue and
        # kept only so stored render props remain valid.
        "showSubscribeCue": False,
        # Final-3s Like/Subscribe cue (2026-07 engagement spec): mounted only for
        # the last round(3*fps) frames, so it never covers the poster earlier.
        # The builder may shrink/disable the cue so it never overlaps narration
        # (P1-D): cue seconds = the audio tail AFTER the voice ends, capped at 4.
        "showEngagementCue": bool(show_engagement_cue),
        "engagementCueDurationSec": float(engagement_cue_sec),
        # Consumed by build_remotion_commands: which Remotion composition to render and
        # at what concurrency. concurrency MUST stay "auto" — the render-concurrency
        # HARD RULE forbids hardcoding a number; _render_concurrency maps "auto" to the
        # machine's core count.
        "render": {"composition": "InfographicShort", "concurrency": "auto"},
    }
