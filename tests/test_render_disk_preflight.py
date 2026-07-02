"""Regression: bug-441 — render must preflight ALL involved volumes.

A mid-encode ENOSPC kills the whole (non-resumable) render, so the check
covers .render_tmp, the OS temp dir, and the output volume."""
from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.stages.render import _assert_render_disk_space


def test_preflight_passes_on_healthy_disk(tmp_path: Path):
    _assert_render_disk_space(min_gb=0.001, output_path=tmp_path / "outputs" / "video.mp4")
    assert (tmp_path / "outputs").exists()


def test_preflight_fails_when_output_volume_below_threshold(tmp_path: Path):
    with pytest.raises(RuntimeError, match="render output|render temp"):
        _assert_render_disk_space(
            min_gb=10_000_000.0,  # absurd requirement: no volume qualifies
            output_path=tmp_path / "outputs" / "video.mp4",
        )
