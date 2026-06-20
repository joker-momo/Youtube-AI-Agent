"""Canonical seconds→frames contract for the Shorts compiled visual timeline.

Spec v3.2.3 §14.1. The renderer (Remotion/JavaScript) computes frame counts with
``Math.round``. Python ``round()`` uses banker's rounding (round-half-to-even),
which diverges from ``Math.round`` on exact ``.5`` boundaries (e.g. ``round(2.5)``
is ``2`` in Python but ``3`` in JS). To keep the compiled schedule frame-identical
across the Python compiler and the JS renderer, we use explicit
``floor(x + 0.5)`` rounding — matching ``Math.round`` for the non-negative
durations the schedule deals with.

This is a pure helper with no rendering side effects; PR A ships it as the
Phase-2 frame-contract guardrail (the compiler in ``asset_schedule.py`` consumes
it in PR B).
"""
from __future__ import annotations

import math


def seconds_to_frames(seconds: float, fps: int) -> int:
    """Convert a positive duration in seconds to a frame count.

    Uses ``floor(seconds * fps + 0.5)`` (JS ``Math.round`` parity), never Python
    ``round()``. Always returns at least ``1`` frame for any positive request so a
    non-empty scene never compiles to a zero-length track.

    Args:
        seconds: Duration in seconds. Expected non-negative.
        fps: Frames per second. Must be a positive integer.

    Returns:
        Frame count, clamped to a minimum of 1.

    Raises:
        ValueError: If ``fps`` is not positive.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps!r}")
    return max(1, int(math.floor(seconds * fps + 0.5)))
