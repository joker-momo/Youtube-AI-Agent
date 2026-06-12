"""Backward-compatible facade for Shorts scene validation helpers.

The implementation is split under :mod:`video_agent.shorts.validation`; this
module keeps the original import path stable for callers and tests.
"""
from __future__ import annotations

from video_agent.shorts.validation import *  # noqa: F401,F403
