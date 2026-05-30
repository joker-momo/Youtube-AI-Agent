"""Automatic background-music selection for Shorts (local YouTube Studio tracks)."""
from __future__ import annotations

# Pillar / topic → music-library track key. See spec §15.1.
_PILLAR_TO_TRACK = {
    "movement": "shorts_movement",
    "exercise": "shorts_movement",
    "routine": "shorts_movement",
    "food": "shorts_daily_habit",
    "daily_habits": "shorts_daily_habit",
    "movement_light": "shorts_daily_habit",
    "sleep": "shorts_sleep_stress",
    "stress": "shorts_sleep_stress",
    "calm": "shorts_sleep_stress",
    "mental_load": "shorts_sleep_stress",
    "energy": "shorts_sleep_stress",
    "menopause": "shorts_sleep_stress",
    "sleep_deep": "shorts_deep_calm",
    "reflective": "shorts_deep_calm",
    "night": "shorts_deep_calm",
}

FALLBACK_TRACK = "shorts_sleep_stress"


def select_music_track(pillar_or_topic: str, channel_config: dict) -> str:
    """Return a music-library track key for the detected pillar/topic."""
    key = str(pillar_or_topic or "").strip().lower()
    track = _PILLAR_TO_TRACK.get(key, FALLBACK_TRACK)
    # Honour a track only if it exists in the library; else fall back.
    tracks = ((channel_config.get("music_library") or {}).get("tracks")) or {}
    if tracks and track not in tracks and FALLBACK_TRACK in tracks:
        return FALLBACK_TRACK
    return track
