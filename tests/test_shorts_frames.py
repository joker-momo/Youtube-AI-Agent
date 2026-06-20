"""Frame-contract parity tests (spec v3.2.3 §14.1).

Asserts ``seconds_to_frames`` matches JavaScript ``Math.round(seconds * fps)``
semantics, especially on ``.5`` boundaries where Python ``round()`` diverges.
"""
from __future__ import annotations

import math

import pytest

from video_agent.shorts.frames import seconds_to_frames


def _js_math_round(value: float) -> int:
    """Reproduce JavaScript ``Math.round``: round half toward +infinity."""
    return int(math.floor(value + 0.5))


@pytest.mark.parametrize(
    "seconds,fps,expected",
    [
        (2.0, 30, 60),
        (2.5, 30, 75),
        (1.5, 30, 45),
        (3.8, 30, 114),  # 114.0
        (0.05, 30, 2),   # 1.5 → 2 (JS rounds .5 up; Python round() would give 2 here too)
        (0.0166667, 30, 1),  # 0.5 → 1, not 0
        (0.001, 30, 1),  # clamp to minimum 1 frame
    ],
)
def test_known_values(seconds: float, fps: int, expected: int) -> None:
    assert seconds_to_frames(seconds, fps) == expected


def test_js_math_round_parity_on_half_boundaries() -> None:
    """The half-to-even divergence: Python round(2.5*1)=2, Math.round=3."""
    # Construct durations whose (seconds*fps) lands exactly on .5 boundaries.
    fps = 2
    for half in [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5]:
        seconds = half / fps  # seconds*fps == half exactly
        expected = max(1, _js_math_round(half))
        assert seconds_to_frames(seconds, fps) == expected, half


def test_does_not_use_python_round() -> None:
    # round(2.5) == 2 (banker's), but the schedule needs 3 frames for 2.5 frame-seconds.
    assert seconds_to_frames(2.5, 1) == 3
    assert round(2.5) == 2  # documents the divergence we avoid


def test_minimum_one_frame_for_positive_duration() -> None:
    assert seconds_to_frames(0.0001, 30) == 1
    assert seconds_to_frames(0.0, 30) == 1  # floor(0.5)=0 → clamped to 1


def test_invalid_fps_raises() -> None:
    with pytest.raises(ValueError):
        seconds_to_frames(1.0, 0)
    with pytest.raises(ValueError):
        seconds_to_frames(1.0, -30)
