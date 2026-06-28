"""Observability guard (review finding #3) for the duration-sync min-scene floor.

``_sync_scene_durations_from_audio`` clamps every scene to >= 3.0 s (a deliberate
readability floor — no scene may flash under 3 s). When the narration is short
relative to the scene count, that floor forces the scene timeline past the
measured narration, so the last scene lingers with no voice (trailing dead air).
The floor stays (quality), but the overflow must no longer be silent: it is logged
so the upstream mismatch (too many scenes / too little narration) surfaces for
review. No duration is changed by the warning.
"""

from __future__ import annotations

import logging

import numpy as np
import soundfile as sf

from video_agent.pipeline import (
    _sync_scene_durations_from_audio,
    _warn_if_timeline_exceeds_audio,
)


def test_warns_when_floor_pushes_timeline_past_audio():
    log = logging.getLogger("dur-sync-test")
    # Two scenes clamped to the 3.0 s floor -> 6.0 s timeline over 4.0 s narration.
    scenes = [{"id": "s1", "duration_sec": 3.0}, {"id": "s2", "duration_sec": 3.0}]
    assert _warn_if_timeline_exceeds_audio(log, scenes, 4.0, strategy="whisper") is True


def test_no_warn_within_tolerance():
    log = logging.getLogger("dur-sync-test")
    scenes = [{"id": "s1", "duration_sec": 5.0}, {"id": "s2", "duration_sec": 5.0}]
    # Timeline 10.0 s vs 10.02 s narration -> within sub-frame tolerance, no warn.
    assert _warn_if_timeline_exceeds_audio(log, scenes, 10.02, strategy="whisper") is False


def _write_narration(job_dir, seconds: float) -> None:
    assets = job_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    sr = 8000
    sf.write(str(assets / "narration.wav"), np.zeros(int(sr * seconds)), sr)


def test_strategy_b_overflow_emits_warning(tmp_path, caplog):
    # No whisper file -> Strategy B. 5 short scenes over 3.0 s narration: every
    # proportional share (~0.6 s) clamps up to the 3.0 s floor -> 15 s timeline.
    scene_doc = {"scenes": [{"id": f"s{i}", "duration_sec": 1.0} for i in range(5)]}
    _write_narration(tmp_path, 3.0)

    with caplog.at_level(logging.WARNING, logger="video_agent.pipeline"):
        _sync_scene_durations_from_audio(tmp_path, scene_doc)

    assert "dead air" in caplog.text
    assert "proportional" in caplog.text
    # Floor is preserved (quality): every scene is still >= 3.0 s.
    assert all(s["duration_sec"] >= 3.0 for s in scene_doc["scenes"])
